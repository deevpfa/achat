import * as path from "node:path";
import * as vscode from "vscode";
import { AchatCli, CliConfig, compareVersions, MIN_ACHAT_VERSION } from "./core/cli";
import { RoomList } from "./core/types";
import { expandHome, installBundledCli, roomsSignature } from "./core/util";

const POLL_MS = 1000;

/**
 * Owns the CLI handle and the rooms snapshot. Everything else (tree, panels, MCP
 * provider) subscribes to its events instead of talking to achat.py on its own schedule.
 */
export class AchatState implements vscode.Disposable {
  cli: AchatCli | undefined;
  error: string | undefined;
  rooms: RoomList | undefined;

  private readonly roomsChanged = new vscode.EventEmitter<void>();
  readonly onDidChangeRooms = this.roomsChanged.event;
  private readonly configChanged = new vscode.EventEmitter<void>();
  readonly onDidChangeConfig = this.configChanged.event;

  private timer: NodeJS.Timeout | undefined;
  private signature = "";
  private polling = false;
  private readonly disposables: vscode.Disposable[] = [];

  constructor(
    private readonly context: vscode.ExtensionContext,
    readonly log: vscode.OutputChannel,
  ) {
    this.disposables.push(
      this.roomsChanged,
      this.configChanged,
      vscode.workspace.onDidChangeConfiguration(async (e) => {
        if (e.affectsConfiguration("achat")) {
          await this.init();
          this.configChanged.fire();
        }
      }),
    );
  }

  /** (Re)builds the CLI from settings, checks it works, loads rooms, starts polling. */
  async init(): Promise<void> {
    this.stop();
    this.cli = undefined;
    this.error = undefined;
    this.rooms = undefined;
    this.signature = "";
    try {
      const cfg = await this.resolveConfig();
      const cli = new AchatCli(cfg);
      const version = await cli.version();
      if (compareVersions(version, MIN_ACHAT_VERSION) < 0) {
        throw new Error(`${cfg.script} is achat ${version}; this extension needs ${MIN_ACHAT_VERSION} or newer.`);
      }
      this.cli = cli;
      this.log.appendLine(`achat ${version} · python: ${cfg.python} · script: ${cfg.script}`);
      const list = await this.refresh();
      this.log.appendLine(`rooms: ${list?.rooms_dir}`);
    } catch (e) {
      this.error = e instanceof Error ? e.message : String(e);
      this.log.appendLine(`ERROR: ${this.error}`);
    }
    this.roomsChanged.fire();
    this.timer = setInterval(() => void this.poll(), POLL_MS);
  }

  private async resolveConfig(): Promise<CliConfig> {
    const conf = vscode.workspace.getConfiguration("achat");
    const python = conf.get<string>("pythonPath")?.trim() || (process.platform === "win32" ? "python" : "python3");
    let script = expandHome(conf.get<string>("scriptPath")?.trim() ?? "");
    if (!script) {
      script = await installBundledCli(path.join(this.context.extensionPath, "cli"));
    }
    const roomsDir = expandHome(conf.get<string>("roomsDir")?.trim() ?? "") || undefined;
    return { python, script, roomsDir };
  }

  async refresh(): Promise<RoomList | undefined> {
    if (!this.cli) {
      return undefined;
    }
    const list = await this.cli.list();
    this.rooms = list;
    this.signature = await roomsSignature(list.rooms_dir);
    this.roomsChanged.fire();
    return list;
  }

  /** Cheap readdir fingerprint every second; achat.py is only called when something changed. */
  private async poll(): Promise<void> {
    if (this.polling || !this.cli || !this.rooms) {
      return;
    }
    this.polling = true;
    try {
      const sig = await roomsSignature(this.rooms.rooms_dir);
      if (sig !== this.signature) {
        await this.refresh();
      }
    } catch (e) {
      this.log.appendLine(`poll failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      this.polling = false;
    }
  }

  requireCli(): AchatCli {
    if (!this.cli) {
      throw new Error(this.error ?? "achat is not ready yet");
    }
    return this.cli;
  }

  private stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = undefined;
    }
  }

  dispose(): void {
    this.stop();
    this.disposables.forEach((d) => d.dispose());
  }
}
