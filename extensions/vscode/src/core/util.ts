// Pure helpers (no `vscode` import) so they can be unit-tested with `node --test`.

import { createHash } from "node:crypto";
import { promises as fs } from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

export function expandHome(p: string): string {
  return p === "~" || p.startsWith("~/") || p.startsWith("~\\") ? path.join(os.homedir(), p.slice(1)) : p;
}

/**
 * A cheap fingerprint of every room's state: room folders plus their message file names.
 * Message files are immutable and append-only, so any new message or room changes it.
 * Plain readdir polling works everywhere (network drives, folders outside the workspace),
 * which editor file watchers don't guarantee.
 */
export async function roomsSignature(roomsDir: string): Promise<string> {
  let entries: string[];
  try {
    entries = (await fs.readdir(roomsDir, { withFileTypes: true })).filter((e) => e.isDirectory()).map((e) => e.name).sort();
  } catch {
    return "";
  }
  const parts: string[] = [];
  for (const name of entries) {
    try {
      const msgs = (await fs.readdir(path.join(roomsDir, name, "msgs"))).filter((f) => /^\d{6}-/.test(f)).sort();
      parts.push(`${name}:${msgs.length}:${msgs[msgs.length - 1] ?? ""}`);
    } catch {
      parts.push(`${name}:-`);
    }
  }
  return parts.join("|");
}

/** Model names from `ollama list` (first column, header skipped). */
export function parseOllamaList(stdout: string): string[] {
  return stdout
    .split(/\r?\n/)
    .slice(1)
    .map((l) => l.trim().split(/\s+/)[0])
    .filter((n): n is string => !!n);
}

/** The managed copy of achat.py lives at a stable, version-independent path. */
export function defaultInstallDir(): string {
  return path.join(os.homedir(), ".achat", "bin");
}

async function sha256(file: string): Promise<string | null> {
  try {
    return createHash("sha256").update(await fs.readFile(file)).digest("hex");
  } catch {
    return null;
  }
}

/**
 * Copies the CLI bundled in the extension (achat.py + viewer.html) to `targetDir`, replacing
 * it only when the content differs. Writes go through a temp file + rename, so an agent
 * running achat.py at that moment never sees a half-written script. Returns the script path.
 */
export async function installBundledCli(bundleDir: string, targetDir = defaultInstallDir()): Promise<string> {
  await fs.mkdir(targetDir, { recursive: true });
  for (const file of ["achat.py", "viewer.html"]) {
    const src = path.join(bundleDir, file);
    const dst = path.join(targetDir, file);
    if ((await sha256(src)) === (await sha256(dst))) {
      continue;
    }
    const tmp = path.join(targetDir, `.${file}.${process.pid}.${Date.now()}.tmp`);
    await fs.copyFile(src, tmp);
    if (file === "achat.py") {
      await fs.chmod(tmp, 0o755);
    }
    await fs.rename(tmp, dst);
  }
  return path.join(targetDir, "achat.py");
}

export interface McpTarget {
  id: string;
  label: string;
  detail: string;
  snippet: string;
  configFile?: string;
}

function jsonArgs(python: string, args: string[]): string {
  return JSON.stringify({ mcpServers: { achat: { command: python, args } } }, null, 2);
}

function tomlString(s: string): string {
  return JSON.stringify(s); // a JSON string is a valid TOML basic string
}

/** Ready-to-paste MCP configuration for each agent, with the user's real paths. */
export function mcpTargets(python: string, mcpArgs: string[]): McpTarget[] {
  const home = os.homedir();
  const shellCmd = [python, ...mcpArgs].map((a) => (/^[\w./:=@-]+$/.test(a) ? a : `'${a.replace(/'/g, "'\\''")}'`)).join(" ");
  return [
    {
      id: "claude",
      label: "Claude Code",
      detail: "runs `claude mcp add` (user scope: every project)",
      snippet: `claude mcp add --scope user achat -- ${shellCmd}`,
    },
    {
      id: "codex",
      label: "Codex",
      detail: "~/.codex/config.toml",
      snippet: `[mcp_servers.achat]\ncommand = ${tomlString(python)}\nargs = [${mcpArgs.map(tomlString).join(", ")}]\n`,
      configFile: path.join(home, ".codex", "config.toml"),
    },
    {
      id: "gemini",
      label: "Gemini CLI",
      detail: "~/.gemini/settings.json",
      snippet: jsonArgs(python, mcpArgs),
      configFile: path.join(home, ".gemini", "settings.json"),
    },
    {
      id: "cursor",
      label: "Cursor",
      detail: "~/.cursor/mcp.json",
      snippet: jsonArgs(python, mcpArgs),
      configFile: path.join(home, ".cursor", "mcp.json"),
    },
    {
      id: "antigravity",
      label: "Antigravity",
      detail: "~/.gemini/antigravity/mcp_config.json",
      snippet: jsonArgs(python, mcpArgs),
      configFile: path.join(home, ".gemini", "antigravity", "mcp_config.json"),
    },
    {
      id: "other",
      label: "Other MCP client (Windsurf, Zed, ...)",
      detail: "standard mcpServers JSON",
      snippet: jsonArgs(python, mcpArgs),
    },
  ];
}
