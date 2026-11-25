import { randomBytes } from "node:crypto";
import * as vscode from "vscode";
import { RoomDetail } from "./core/types";
import { AchatState } from "./state";

/** Messages the webview may send. Everything from the webview is validated: it renders agent text. */
type FromWebview =
  | { type: "boot" }
  | { type: "ready" }
  | { type: "send"; text: string; end: boolean }
  | { type: "copy"; participant: string; kind: "shell" | "mcp" }
  | { type: "bridge"; participant: string }
  | { type: "rendered"; messages: number }
  | { type: "webviewError"; message: string };

function parseFromWebview(m: unknown): FromWebview | undefined {
  if (typeof m !== "object" || m === null) {
    return undefined;
  }
  const o = m as Record<string, unknown>;
  switch (o.type) {
    case "boot":
      return { type: "boot" };
    case "ready":
      return { type: "ready" };
    case "send":
      return typeof o.text === "string" ? { type: "send", text: o.text, end: o.end === true } : undefined;
    case "copy":
      return typeof o.participant === "string" && (o.kind === "shell" || o.kind === "mcp")
        ? { type: "copy", participant: o.participant, kind: o.kind }
        : undefined;
    case "bridge":
      return typeof o.participant === "string" ? { type: "bridge", participant: o.participant } : undefined;
    case "rendered":
      return typeof o.messages === "number" ? { type: "rendered", messages: o.messages } : undefined;
    case "webviewError":
      return typeof o.message === "string" ? { type: "webviewError", message: o.message } : undefined;
    default:
      return undefined;
  }
}

export class ConversationPanel implements vscode.Disposable {
  private lastSeq = 0;
  /** How many messages the webview reports on screen (proves the UI script really runs). */
  rendered = -1;
  readonly webviewErrors: string[] = [];
  private forceReset = false;
  private onboarding: RoomDetail["onboarding"] = {};
  private refreshing: Promise<void> | undefined;
  private sendChain: Promise<void> = Promise.resolve(); // sends run one at a time, in order
  private dirty = false;
  private readonly disposables: vscode.Disposable[] = [];

  /** Times the watchdog had to reload a webview that never started. */
  reloads = 0;
  private booted = false;
  private watchdog: NodeJS.Timeout | undefined;

  constructor(
    readonly room: string,
    readonly panel: vscode.WebviewPanel,
    private readonly state: AchatState,
    private readonly mediaUri: vscode.Uri,
    private readonly onDispose: () => void,
  ) {
    this.render();
    this.disposables.push(
      panel.onDidDispose(() => this.dispose()),
      panel.webview.onDidReceiveMessage((m) => void this.handle(m)),
      state.onDidChangeRooms(() => void this.refresh()),
    );
  }

  private render(): void {
    this.booted = false;
    this.panel.webview.html = this.html(this.mediaUri);
    this.armWatchdog();
  }

  /**
   * Webviews occasionally fail to start (a known editor issue). If a visible panel hasn't
   * booted within 5 s, reload it, up to 3 times, and say so in the log.
   */
  private armWatchdog(): void {
    clearTimeout(this.watchdog);
    this.watchdog = setTimeout(() => {
      if (this.booted) {
        return;
      }
      if (!this.panel.visible) {
        this.armWatchdog(); // only judge it while someone can see it
        return;
      }
      if (this.reloads >= 3) {
        this.state.log.appendLine(`room ${this.room}: the conversation panel did not start after 3 reloads. Try "Developer: Reload Webviews".`);
        return;
      }
      this.reloads++;
      this.state.log.appendLine(`room ${this.room}: the conversation panel didn't start within 5 s; reloading it (attempt ${this.reloads}).`);
      this.render();
    }, 5000);
  }

  /** Coalesces refreshes: at most one in flight, plus one queued. */
  refresh(full = false): Promise<void> {
    if (full) {
      this.forceReset = true; // honoured by the next fetch, even if one is already in flight
    }
    if (this.refreshing) {
      this.dirty = true;
      return this.refreshing;
    }
    this.refreshing = (async () => {
      do {
        this.dirty = false;
        try {
          const after = this.forceReset ? 0 : this.lastSeq;
          this.forceReset = false;
          const detail = await this.state.requireCli().room(this.room, after);
          const reset = after === 0;
          if (reset) {
            this.lastSeq = 0;
          }
          this.onboarding = detail.onboarding;
          const { messages, ...meta } = detail;
          await this.panel.webview.postMessage({ type: "update", reset, room: meta, messages });
          if (messages.length) {
            this.lastSeq = messages[messages.length - 1].seq;
          }
        } catch (e) {
          await this.panel.webview.postMessage({ type: "error", message: e instanceof Error ? e.message : String(e) });
        }
      } while (this.dirty);
      this.refreshing = undefined;
    })();
    return this.refreshing;
  }

  /** The last messages received from the webview, for diagnostics. */
  readonly received: string[] = [];

  async handle(raw: unknown): Promise<void> {
    this.received.push(JSON.stringify(raw)?.slice(0, 300) ?? String(raw));
    if (this.received.length > 50) {
      this.received.splice(0, this.received.length - 50); // bounded: panels can live for hours
    }
    const m = parseFromWebview(raw);
    if (!m) {
      return;
    }
    if (m.type === "boot") {
      this.booted = true;
      clearTimeout(this.watchdog);
    } else if (m.type === "rendered") {
      this.rendered = m.messages;
    } else if (m.type === "webviewError") {
      this.webviewErrors.push(m.message);
      this.state.log.appendLine(`webview error in room ${this.room}: ${m.message}`);
    } else if (m.type === "ready") {
      await this.refresh(true);
    } else if (m.type === "send") {
      this.sendChain = this.sendChain.then(() => this.send(m.text, m.end));
      await this.sendChain;
    } else if (m.type === "copy") {
      const how = this.onboarding[m.participant];
      if (how) {
        await vscode.env.clipboard.writeText(how[m.kind]);
        void vscode.window.showInformationMessage(
          m.kind === "mcp"
            ? `Copied. Paste it into ${m.participant}'s chat (an agent with achat's MCP server).`
            : `Copied. Paste it into ${m.participant}'s chat (an agent with a terminal: Claude Code, Codex, Gemini CLI...).`,
        );
      }
    } else if (m.type === "bridge") {
      await vscode.commands.executeCommand("achat.startBridge", this.room, m.participant);
    }
  }

  private async send(text: string, end: boolean): Promise<void> {
    try {
      if (end) {
        const ok = await vscode.window.showWarningMessage(
          `Close room "${this.room}" for every participant?`,
          { modal: true, detail: "Your message is posted as human and the room closes. Rooms can't be reopened." },
          "Close Room",
        );
        if (!ok) {
          await this.panel.webview.postMessage({ type: "cancelled" });
          return;
        }
      }
      await this.state.requireCli().sendAsHuman(this.room, text, end);
      await this.panel.webview.postMessage({ type: "sent" });
      await this.refresh();
    } catch (e) {
      await this.panel.webview.postMessage({ type: "error", message: e instanceof Error ? e.message : String(e) });
    }
  }

  private html(media: vscode.Uri): string {
    const w = this.panel.webview;
    // Hex, not base64: a nonce containing "+" or "/" made roughly half of all webview loads
    // run no script at all (found by the integration test). Alphanumeric nonces are what
    // VS Code's own samples use.
    const nonce = randomBytes(16).toString("hex");
    const css = w.asWebviewUri(vscode.Uri.joinPath(media, "panel.css"));
    const js = w.asWebviewUri(vscode.Uri.joinPath(media, "panel.js"));
    // No inline code, no remote content: agent text is untrusted and is rendered with textContent only.
    return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${w.cspSource}; script-src 'nonce-${nonce}'; img-src ${w.cspSource};">
<meta name="viewport" content="width=device-width, initial-scale=1">
<script nonce="${nonce}">
  // Runs first. If panel.js is blocked or fails to load, the reason reaches the "achat" log.
  (function () {
    var api = acquireVsCodeApi(); // may only be called once: panel.js reuses it
    window.__achatApi = api;
    var report = function (message) { api.postMessage({ type: "webviewError", message: message }); };
    document.addEventListener("securitypolicyviolation", function (e) {
      report("CSP blocked " + e.blockedURI + " (" + e.violatedDirective + ")");
    });
    window.addEventListener("error", function (e) {
      var t = e.target;
      if (t && t !== window && (t.src || t.href)) { report("failed to load " + (t.src || t.href)); }
    }, true);
    api.postMessage({ type: "boot" });
  })();
</script>
<link rel="stylesheet" href="${css}">
<title>achat</title>
</head>
<body>
<header id="head"></header>
<main id="stream" aria-live="polite"></main>
<form id="composer">
  <textarea id="text" rows="2" placeholder="Interject as human (Enter sends, Shift+Enter for a new line)"></textarea>
  <div class="actions">
    <button id="send" type="submit">Send</button>
    <button id="end" type="button" class="secondary" title="Send (or a default message) and close the room for everyone">Close room</button>
  </div>
  <div id="err" role="alert"></div>
</form>
<script nonce="${nonce}" src="${js}"></script>
</body>
</html>`;
  }

  dispose(): void {
    clearTimeout(this.watchdog);
    this.disposables.forEach((d) => d.dispose());
    this.onDispose();
  }
}

export class PanelManager implements vscode.Disposable {
  private readonly panels = new Map<string, ConversationPanel>();

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly state: AchatState,
  ) {}

  open(room: string): ConversationPanel {
    const existing = this.panels.get(room);
    if (existing) {
      existing.panel.reveal();
      return existing;
    }
    const media = vscode.Uri.joinPath(this.context.extensionUri, "media");
    const panel = vscode.window.createWebviewPanel("achat.conversation", `achat · ${room}`, vscode.ViewColumn.Active, {
      enableScripts: true,
      retainContextWhenHidden: true,
      localResourceRoots: [media],
    });
    panel.iconPath = { light: vscode.Uri.joinPath(media, "tab-light.svg"), dark: vscode.Uri.joinPath(media, "tab-dark.svg") };
    const cp = new ConversationPanel(room, panel, this.state, media, () => this.panels.delete(room));
    this.panels.set(room, cp);
    return cp;
  }

  get(room: string): ConversationPanel | undefined {
    return this.panels.get(room);
  }

  dispose(): void {
    [...this.panels.values()].forEach((p) => p.panel.dispose());
  }
}
