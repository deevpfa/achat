import { execFile } from "node:child_process";
import * as vscode from "vscode";
import { NAME_RE } from "./core/cli";
import { RoomSummary } from "./core/types";
import { mcpTargets, parseOllamaList } from "./core/util";
import { PanelManager } from "./panel";
import { ParticipantItem, RoomItem } from "./roomsTree";
import { AchatState } from "./state";

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function which(cmd: string, args: string[]): Promise<string | undefined> {
  return new Promise((resolve) => {
    execFile(cmd, args, { timeout: 10_000 }, (err, stdout) => resolve(err ? undefined : stdout));
  });
}

async function pickRoom(state: AchatState, arg: unknown, onlyOpen = false): Promise<RoomSummary | undefined> {
  const rooms = (state.rooms?.rooms ?? []).filter((r) => !onlyOpen || !r.status.closed);
  const name = arg instanceof RoomItem || arg instanceof ParticipantItem ? arg.room.name : typeof arg === "string" ? arg : undefined;
  if (name) {
    return rooms.find((r) => r.name === name) ?? state.rooms?.rooms.find((r) => r.name === name);
  }
  if (!rooms.length) {
    void vscode.window.showInformationMessage(onlyOpen ? "There are no open rooms." : "There are no rooms yet.");
    return undefined;
  }
  const pick = await vscode.window.showQuickPick(
    rooms.map((r) => ({ label: r.name, description: r.topic, detail: r.participants.join(" → "), room: r })),
    { placeHolder: "Room" },
  );
  return pick?.room;
}

async function pickParticipant(room: RoomSummary, arg: unknown, placeHolder: string): Promise<string | undefined> {
  if (arg instanceof ParticipantItem) {
    return arg.participant;
  }
  if (typeof arg === "string" && room.participants.includes(arg)) {
    return arg;
  }
  return vscode.window.showQuickPick(room.participants, { placeHolder });
}

export function registerCommands(context: vscode.ExtensionContext, state: AchatState, panels: PanelManager): void {
  const reg = (id: string, fn: (...args: unknown[]) => unknown) =>
    context.subscriptions.push(
      vscode.commands.registerCommand(id, async (...args: unknown[]) => {
        try {
          return await fn(...args);
        } catch (e) {
          state.log.appendLine(`${id} failed: ${errorText(e)}`);
          void vscode.window.showErrorMessage(`achat: ${errorText(e)}`);
          return undefined;
        }
      }),
    );

  reg("achat.refresh", () => state.init());
  reg("achat.showLog", () => state.log.show());

  reg("achat.newRoom", async () => {
    const cli = state.requireCli();
    const taken = new Set((state.rooms?.rooms ?? []).map((r) => r.name));
    const name = await vscode.window.showInputBox({
      title: "New achat room (1/3)",
      prompt: "Room name",
      placeHolder: "debate",
      validateInput: (v) =>
        !NAME_RE.test(v) ? "Lowercase letters, digits, '-' or '_' (max 32)" : taken.has(v) ? "A room with that name already exists" : undefined,
    });
    if (!name) {
      return;
    }
    const participantsRaw = await vscode.window.showInputBox({
      title: "New achat room (2/3)",
      prompt: "Participants in speaking order, comma-separated (you join as 'human' automatically)",
      placeHolder: "claude, codex",
      validateInput: (v) => {
        const ps = v.split(",").map((p) => p.trim()).filter(Boolean);
        if (ps.length < 2) return "At least 2 participants";
        const bad = ps.find((p) => !NAME_RE.test(p));
        if (bad) return `"${bad}": lowercase letters, digits, '-' or '_'`;
        if (ps.includes("human")) return "'human' is reserved for you";
        if (new Set(ps).size !== ps.length) return "Names must be unique";
        return undefined;
      },
    });
    if (!participantsRaw) {
      return;
    }
    const topic = await vscode.window.showInputBox({ title: "New achat room (3/3)", prompt: "Topic (what they should talk about)" });
    if (topic === undefined) {
      return;
    }
    const room = await cli.create({ name, participants: participantsRaw.split(",").map((p) => p.trim()).filter(Boolean), topic });
    await state.refresh();
    panels.open(room.name);
    const choice = await vscode.window.showInformationMessage(
      `Room "${room.name}" created. Bring each participant in: copy its join instruction and paste it into that agent's chat.`,
      "Copy Join Instruction",
    );
    if (choice) {
      await vscode.commands.executeCommand("achat.copyJoin", room.name);
    }
  });

  reg("achat.openRoom", async (arg) => {
    const room = await pickRoom(state, arg);
    if (room) {
      panels.open(room.name);
    }
  });

  reg("achat.copyJoin", async (arg, who) => {
    const room = await pickRoom(state, arg, true);
    if (!room) {
      return;
    }
    const participant = await pickParticipant(room, who ?? arg, "Which participant are you bringing in?");
    if (!participant) {
      return;
    }
    const choice = await vscode.window.showQuickPick(
      [
        { label: "Agent with a terminal", detail: "Claude Code, Codex, Gemini CLI, Cursor, Aider…: it runs achat.py commands", how: "shell" as const },
        { label: "Agent with achat's MCP server", detail: "a one-line instruction; set it up once with “achat: Set Up MCP for Agents”", how: "mcp" as const },
        { label: "Local model without tools", detail: "start a bridge (Ollama, LM Studio…) in a terminal instead", how: "bridge" as const },
      ],
      { placeHolder: `How will "${participant}" take part?` },
    );
    if (!choice) {
      return;
    }
    if (choice.how === "bridge") {
      await vscode.commands.executeCommand("achat.startBridge", room.name, participant);
      return;
    }
    const detail = await state.requireCli().room(room.name, Number.MAX_SAFE_INTEGER);
    const text = detail.onboarding[participant]?.[choice.how];
    if (!text) {
      throw new Error(`no join instruction for ${participant}`);
    }
    await vscode.env.clipboard.writeText(text);
    void vscode.window.showInformationMessage(`Copied: ${text.length > 90 ? text.slice(0, 90) + "…" : text}  — paste it into ${participant}'s chat.`);
  });

  reg("achat.startBridge", async (arg, who) => {
    const cli = state.requireCli();
    const room = await pickRoom(state, arg, true);
    if (!room) {
      return;
    }
    const participant = await pickParticipant(room, who ?? arg, "Which participant will the local model speak as?");
    if (!participant) {
      return;
    }
    const models = parseOllamaList((await which("ollama", ["list"])) ?? "");
    let model: string | undefined;
    if (models.length) {
      const other = "$(edit) Other model…";
      const pick = await vscode.window.showQuickPick([...models, other], { placeHolder: `Model for "${participant}" (from \`ollama list\`)` });
      model = pick === other ? await vscode.window.showInputBox({ prompt: "Model name as your server knows it" }) : pick;
    } else {
      model = await vscode.window.showInputBox({
        prompt: "Model name (Ollama isn't answering `ollama list`; is `ollama serve` running? Any OpenAI-compatible server works)",
        placeHolder: "llama3.2:3b",
      });
    }
    if (!model) {
      return;
    }
    const baseUrl = vscode.workspace.getConfiguration("achat").get<string>("bridgeBaseUrl")?.trim() || undefined;
    // The bridge runs as the terminal's process itself (no shell), so nothing is re-quoted.
    const term = vscode.window.createTerminal({
      name: `achat bridge · ${participant}@${room.name}`,
      shellPath: cli.config.python,
      shellArgs: cli.bridgeArgs(room.name, participant, model, baseUrl),
      iconPath: new vscode.ThemeIcon("hubot"),
    });
    term.show(true);
  });

  reg("achat.closeRoom", async (arg) => {
    const room = await pickRoom(state, arg, true);
    if (!room) {
      return;
    }
    const ok = await vscode.window.showWarningMessage(
      `Close room "${room.name}" for every participant?`,
      { modal: true, detail: "This posts a closing message as human. Rooms can't be reopened." },
      "Close Room",
    );
    if (ok) {
      await state.requireCli().close(room.name);
      await state.refresh();
    }
  });

  reg("achat.setupMcp", async () => {
    const cli = state.requireCli();
    const targets = mcpTargets(cli.config.python, cli.mcpArgs());
    const pick = await vscode.window.showQuickPick(
      targets.map((t) => ({ label: t.label, detail: t.detail, target: t })),
      { placeHolder: "Which agent should get achat's MCP tools? (VS Code's own agent mode already has them, if supported)" },
    );
    if (!pick) {
      return;
    }
    const t = pick.target;
    if (t.id === "claude" && (await which("claude", ["--version"]))) {
      const run = await vscode.window.showInformationMessage(
        "Register achat with Claude Code for all your projects?",
        { modal: true, detail: t.snippet },
        "Run in Terminal",
        "Copy Command",
      );
      if (run === "Run in Terminal") {
        const term = vscode.window.createTerminal({ name: "achat · MCP setup" });
        term.show();
        term.sendText(t.snippet);
      } else if (run === "Copy Command") {
        await vscode.env.clipboard.writeText(t.snippet);
      }
      return;
    }
    await vscode.env.clipboard.writeText(t.snippet);
    const actions = t.configFile ? ["Open Config File"] : [];
    const choice = await vscode.window.showInformationMessage(
      t.configFile
        ? `Copied the achat MCP config for ${t.label}. Merge it into ${t.configFile} and restart ${t.label}.`
        : `Copied the achat MCP config. Paste it into ${t.label === "Claude Code" ? "a terminal" : "your client's MCP settings"}.`,
      ...actions,
    );
    if (choice === "Open Config File" && t.configFile) {
      const uri = vscode.Uri.file(t.configFile);
      try {
        await vscode.window.showTextDocument(uri);
      } catch {
        await vscode.window.showTextDocument(uri.with({ scheme: "untitled" }));
      }
    }
  });
}
