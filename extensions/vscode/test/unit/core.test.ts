import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { promises as fs } from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { after, test } from "node:test";
import { AchatCli, AchatError, cleanStderr, compareVersions, Runner } from "../../src/core/cli";
import { parseRoomDetail, parseRoomList, ProtocolError } from "../../src/core/types";
import { installBundledCli, mcpTargets, parseOllamaList, roomsSignature } from "../../src/core/util";

const REPO = path.resolve(__dirname, "..", "..", "..", "..", "..");
const SCRIPT = path.join(REPO, "achat.py");
const PYTHON = process.platform === "win32" ? "python" : "python3";

const created: string[] = [];
after(() => Promise.all(created.map((d) => fs.rm(d, { recursive: true, force: true }))));

async function tmpdir(): Promise<string> {
  const dir = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), "achat-ext-")));
  created.push(dir);
  return dir;
}

test("compareVersions", () => {
  assert.equal(compareVersions("0.2.0", "0.2.0"), 0);
  assert.equal(compareVersions("0.10.0", "0.2.0"), 1);
  assert.equal(compareVersions("0.1.9", "0.2.0"), -1);
  assert.equal(compareVersions("1.0", "1.0.0"), 0);
});

test("cleanStderr strips achat's error label", () => {
  assert.equal(cleanStderr("ERROR — room 'x' not found\n"), "room 'x' not found");
  assert.equal(cleanStderr("STATUS: REJECTED — not your turn"), "STATUS: REJECTED — not your turn");
});

test("every value is passed as --opt=value, so a leading '-' can't become an option", async () => {
  const calls: string[][] = [];
  const runner: Runner = async (_file, args) => {
    calls.push(args);
    return { stdout: JSON.stringify({ name: "r", participants: ["a", "b"], max_turns: 1, max_chars: 1, status: { turns: 0, next: "a", closed: null, last_seq: 0 }, messages: [] }), stderr: "" };
  };
  const cli = new AchatCli({ python: "py", script: "/s/achat.py", roomsDir: "/rooms" }, runner);
  await cli.create({ name: "r", participants: ["a", "b"], topic: "--version is a trap", brief: "-x" });
  const args = calls[0];
  assert.equal(args[0], "/s/achat.py");
  assert.ok(args.includes("--topic=--version is a trap"));
  assert.ok(args.includes("--brief=-x"));
  assert.ok(args.includes("--dir=/rooms"));
  assert.deepEqual(cli.mcpArgs(), ["/s/achat.py", "mcp", "--dir=/rooms"]);
  await assert.rejects(cli.create({ name: "Bad Name", participants: ["a", "b"], topic: "" }), AchatError);
});

test("parsers reject malformed output instead of passing garbage to the UI", () => {
  assert.throws(() => parseRoomList({ rooms: [] }), ProtocolError);
  assert.throws(() => parseRoomList({ rooms_dir: "/x", rooms: [{ name: 1 }] }), ProtocolError);
  assert.throws(() => parseRoomDetail({ name: "r", participants: ["a"], max_turns: 1, max_chars: 1, status: {} }), ProtocolError);
});

test("real achat.py: create, list, read, send as human, close", async () => {
  const dir = await tmpdir();
  const cli = new AchatCli({ python: PYTHON, script: SCRIPT, roomsDir: dir });
  assert.match(await cli.version(), /^\d+\.\d+\.\d+$/);

  const created = await cli.create({ name: "r", participants: ["claude", "codex"], topic: "-starts with a dash", maxTurns: 4 });
  assert.equal(created.topic, "-starts with a dash");
  assert.equal(created.onboarding.codex.mcp, 'Join the achat room "r" as "codex".');
  assert.match(created.onboarding.claude.shell, /join .*--room r --as claude/);

  const list = await cli.list();
  assert.equal(list.rooms_dir, dir);
  assert.deepEqual(list.rooms.map((r) => r.name), ["r"]);

  const hostile = "it's \"quoted\" $HOME `uname` $(whoami)\n- a list item\nñ 🦙";
  await cli.sendAsHuman("r", hostile);
  const detail = await cli.room("r");
  assert.deepEqual(detail.messages.map((m) => [m.from, m.body]), [["human", hostile]]);
  assert.equal((await cli.room("r", 1)).messages.length, 0);

  await cli.close("r", "done here");
  const closed = await cli.room("r");
  assert.match(closed.status.closed ?? "", /ended by human/);
  await assert.rejects(cli.sendAsHuman("r", "too late"), (e: unknown) => e instanceof AchatError && /closed/.test(e.message));
  await assert.rejects(cli.room("missing"), (e: unknown) => e instanceof AchatError && /not found/.test(e.message));
});

test("real achat.py: attachments show up with a preview and can be fetched", async () => {
  const dir = await tmpdir();
  const cli = new AchatCli({ python: PYTHON, script: SCRIPT, roomsDir: dir });
  await cli.create({ name: "r", participants: ["a", "b"], topic: "" });
  const file = path.join(dir, "fix.diff");
  await fs.writeFile(file, "-old\n+new\n");
  execFileSync(PYTHON, [SCRIPT, "send", "--room=r", "--as=human", `--attach=${file}`, `--dir=${dir}`, "see the diff"]);
  const [att] = (await cli.room("r")).messages[0].attachments;
  assert.equal(att.name, "fix.diff");
  assert.equal(att.preview, "-old\n+new");
  assert.equal(await cli.attachmentText("r", att.sha256), "-old\n+new\n");
  await assert.rejects(cli.attachmentText("r", "../../etc"), AchatError);
});

test("a missing interpreter gives an actionable error", async () => {
  const cli = new AchatCli({ python: "definitely-not-a-python-xyz", script: SCRIPT });
  await assert.rejects(cli.version(), (e: unknown) => e instanceof AchatError && /achat.pythonPath/.test(e.message));
});

test("roomsSignature changes when a message or a room appears", async () => {
  const dir = await tmpdir();
  assert.equal(await roomsSignature(path.join(dir, "nope")), "");
  const cli = new AchatCli({ python: PYTHON, script: SCRIPT, roomsDir: dir });
  await cli.create({ name: "r", participants: ["a", "b"], topic: "" });
  const s1 = await roomsSignature(dir);
  await cli.sendAsHuman("r", "hi");
  const s2 = await roomsSignature(dir);
  assert.notEqual(s1, s2);
  await cli.create({ name: "s", participants: ["a", "b"], topic: "" });
  assert.notEqual(await roomsSignature(dir), s2);
});

test("installBundledCli copies once, updates on change, keeps the script executable", async () => {
  const bundle = await tmpdir();
  const target = path.join(await tmpdir(), "bin");
  await fs.writeFile(path.join(bundle, "achat.py"), "v1");
  await fs.writeFile(path.join(bundle, "viewer.html"), "<html>");
  const script = await installBundledCli(bundle, target);
  assert.equal(await fs.readFile(script, "utf8"), "v1");
  const mtime = (await fs.stat(script)).mtimeMs;
  await installBundledCli(bundle, target);
  assert.equal((await fs.stat(script)).mtimeMs, mtime, "unchanged content is not rewritten");
  await fs.writeFile(path.join(bundle, "achat.py"), "v2");
  await installBundledCli(bundle, target);
  assert.equal(await fs.readFile(script, "utf8"), "v2");
  if (process.platform !== "win32") {
    assert.ok(((await fs.stat(script)).mode & 0o111) !== 0);
  }
  assert.deepEqual((await fs.readdir(target)).sort(), ["achat.py", "viewer.html"], "no temp files left behind");
});

test("parseOllamaList", () => {
  const out = "NAME           ID              SIZE      MODIFIED\nllama3.2:3b    a80c4f17acd5    2.0 GB    7 months ago\nllama3.1:8b    46e0c10c039e    4.9 GB    7 months ago\n";
  assert.deepEqual(parseOllamaList(out), ["llama3.2:3b", "llama3.1:8b"]);
  assert.deepEqual(parseOllamaList(""), []);
});

test("MCP snippets are valid config with the real paths", () => {
  const targets = mcpTargets("python3", ["/Users/me/My Stuff/achat.py", "mcp"]);
  const byId = Object.fromEntries(targets.map((t) => [t.id, t]));
  assert.equal(byId.claude.snippet, "claude mcp add --scope user achat -- python3 '/Users/me/My Stuff/achat.py' mcp");
  const json = JSON.parse(byId.cursor.snippet);
  assert.deepEqual(json.mcpServers.achat, { command: "python3", args: ["/Users/me/My Stuff/achat.py", "mcp"] });
  assert.match(byId.codex.snippet, /^\[mcp_servers\.achat\]\ncommand = "python3"\nargs = \["\/Users\/me\/My Stuff\/achat.py", "mcp"\]\n$/);
});
