// Thin, typed wrapper around the achat.py CLI. The extension never reimplements the
// protocol (locks, turns, cursors): it calls achat.py, which is the single source of truth.

import { execFile } from "node:child_process";
import { parseRoomDetail, parseRoomList, RoomDetail, RoomList } from "./types";

export const MIN_ACHAT_VERSION = "0.2.0";
export const NAME_RE = /^[a-z0-9][a-z0-9_-]{0,31}$/;

export interface CliConfig {
  python: string;
  script: string;
  roomsDir?: string;
}

export class AchatError extends Error {
  constructor(
    message: string,
    readonly exitCode: number | null,
  ) {
    super(message);
  }
}

export interface NewRoom {
  name: string;
  participants: string[];
  topic: string;
  maxTurns?: number;
  maxChars?: number;
  brief?: string;
}

export type Runner = (
  file: string,
  args: string[],
  input: string | undefined,
  timeoutMs: number,
) => Promise<{ stdout: string; stderr: string }>;

/** Runs a process without a shell: arguments are never re-parsed, so no quoting issues. */
export const execRunner: Runner = (file, args, input, timeoutMs) =>
  new Promise((resolve, reject) => {
    const child = execFile(
      file,
      args,
      { timeout: timeoutMs, maxBuffer: 64 * 1024 * 1024, encoding: "utf8", env: { ...process.env, PYTHONIOENCODING: "utf-8" } },
      (err, stdout, stderr) => {
        if (err) {
          // execFile sets `code` to the exit status (number) or to a spawn error name ("ENOENT").
          const raw: unknown = (err as { code?: unknown }).code;
          const msg =
            raw === "ENOENT" ? `could not run "${file}" (not found). Set achat.pythonPath.` : cleanStderr(stderr) || err.message;
          reject(new AchatError(msg, typeof raw === "number" ? raw : null));
          return;
        }
        resolve({ stdout, stderr });
      },
    );
    child.stdin?.end(input ?? "");
  });

/** achat prints "ERROR — reason" / "STATUS: REJECTED — reason" on stderr. */
export function cleanStderr(stderr: string): string {
  return stderr.trim().replace(/^ERROR — /, "");
}

export function compareVersions(a: string, b: string): number {
  const pa = a.split(".").map((x) => parseInt(x, 10) || 0);
  const pb = b.split(".").map((x) => parseInt(x, 10) || 0);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const d = (pa[i] ?? 0) - (pb[i] ?? 0);
    if (d !== 0) {
      return d < 0 ? -1 : 1;
    }
  }
  return 0;
}

export class AchatCli {
  constructor(
    readonly config: CliConfig,
    private readonly runner: Runner = execRunner,
  ) {}

  /** `--dir` only when the user configured one; otherwise achat's own default applies. */
  dirArgs(): string[] {
    return this.config.roomsDir ? [`--dir=${this.config.roomsDir}`] : [];
  }

  private async run(args: string[], input?: string, timeoutMs = 30_000): Promise<string> {
    const { stdout } = await this.runner(this.config.python, [this.config.script, ...args], input, timeoutMs);
    return stdout;
  }

  private async json(args: string[]): Promise<unknown> {
    const out = await this.run(args);
    try {
      return JSON.parse(out);
    } catch {
      throw new AchatError(`achat printed invalid JSON: ${out.slice(0, 200)}`, null);
    }
  }

  async version(): Promise<string> {
    const out = await this.run(["--version"], undefined, 15_000);
    const m = /achat\s+(\d+\.\d+\.\d+)/.exec(out);
    if (!m) {
      throw new AchatError(`unexpected --version output: ${out.trim()}`, null);
    }
    return m[1];
  }

  async list(): Promise<RoomList> {
    return parseRoomList(await this.json(["list", "--json", ...this.dirArgs()]));
  }

  async room(name: string, after = 0): Promise<RoomDetail> {
    return parseRoomDetail(await this.json(["read", `--room=${name}`, "--json", `--after=${after}`, ...this.dirArgs()]));
  }

  async create(r: NewRoom): Promise<RoomDetail> {
    if (!NAME_RE.test(r.name)) {
      throw new AchatError(`invalid room name "${r.name}"`, null);
    }
    // `--opt=value` everywhere: a value starting with "-" must never be read as an option.
    const args = ["new", r.name, `--participants=${r.participants.join(",")}`, `--topic=${r.topic}`, "--json", ...this.dirArgs()];
    if (r.maxTurns !== undefined) {
      args.push(`--max-turns=${r.maxTurns}`);
    }
    if (r.maxChars !== undefined) {
      args.push(`--max-chars=${r.maxChars}`);
    }
    if (r.brief) {
      args.push(`--brief=${r.brief}`);
    }
    return parseRoomDetail(await this.json(args));
  }

  /** The text goes through stdin, never through argv. */
  async sendAsHuman(room: string, text: string, end = false): Promise<void> {
    await this.run(["send", `--room=${room}`, "--as=human", ...(end ? ["--end"] : []), ...this.dirArgs()], text);
  }

  /** Full text of an attachment (achat refuses binary files without --out). */
  async attachmentText(room: string, sha256: string): Promise<string> {
    if (!/^[0-9a-f]{8,64}$/.test(sha256)) {
      throw new AchatError(`invalid attachment id "${sha256}"`, null);
    }
    return this.run(["attachment", `--room=${room}`, sha256, ...this.dirArgs()]);
  }

  async close(room: string, reason?: string): Promise<void> {
    await this.run(["close", `--room=${room}`, ...(reason ? [`--reason=${reason}`] : []), ...this.dirArgs()]);
  }

  /** argv (after the interpreter) for a bridge process, to run in a terminal. */
  bridgeArgs(room: string, who: string, model: string, baseUrl?: string): string[] {
    const args = [this.config.script, "bridge", `--room=${room}`, `--as=${who}`, `--model=${model}`, ...this.dirArgs()];
    if (baseUrl) {
      args.push(`--base-url=${baseUrl}`);
    }
    return args;
  }

  /** argv (after the interpreter) for the MCP stdio server. */
  mcpArgs(): string[] {
    return [this.config.script, "mcp", ...this.dirArgs()];
  }
}
