// Executed inside the editor's extension host by runTest.ts.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readdirSync, readFileSync } from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";
import type { AchatApi } from "../../src/extension";
import { ParticipantItem, RoomItem } from "../../src/roomsTree";

const rooms = process.env.ACHAT_IT_ROOMS!;
const repo = process.env.ACHAT_IT_REPO!;
const python = process.platform === "win32" ? "python" : "python3";

async function until(what: string, check: () => boolean, timeoutMs = 8000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!check()) {
    if (Date.now() > deadline) {
      throw new Error(`timed out waiting for: ${what}`);
    }
    await new Promise((r) => setTimeout(r, 100));
  }
}

function bodies(): string[] {
  const dir = path.join(rooms, "r", "msgs");
  return readdirSync(dir)
    .filter((f) => /^\d{6}-/.test(f))
    .sort()
    .map((f) => readFileSync(path.join(dir, f), "utf8").split("\n---\n").slice(1).join("\n---\n").trim());
}

export async function run(): Promise<void> {
  const report: Record<string, unknown> = { editor: `${vscode.env.appName} ${vscode.version}` };
  const step = async (name: string, fn: () => Promise<void> | void) => {
    await fn();
    (report.passed as string[] | undefined) ? (report.passed as string[]).push(name) : (report.passed = [name]);
  };
  try {
    const ext = vscode.extensions.all.find((e) => e.packageJSON?.name === "achat" && e.packageJSON?.publisher === "achat");
    assert.ok(ext, "extension is loaded");
    const api = (await ext.activate()) as AchatApi;

    await step("installs the bundled CLI at a stable per-user path", () => {
      const expected = path.join(process.env.HOME!, ".achat", "bin", "achat.py");
      assert.equal(api.state.cli?.config.script, expected);
      assert.equal(readFileSync(expected, "utf8"), readFileSync(path.join(repo, "achat.py"), "utf8"));
    });

    await step("activates without errors and finds the rooms", () => {
      assert.equal(api.state.error, undefined, api.state.error);
      assert.equal(api.state.rooms?.rooms_dir, rooms);
      assert.deepEqual(api.state.rooms?.rooms.map((r) => r.name), ["r"]);
    });

    await step("contributes every command", async () => {
      const all = new Set(await vscode.commands.getCommands(true));
      for (const id of ["newRoom", "openRoom", "copyJoin", "startBridge", "closeRoom", "setupMcp", "refresh", "showLog"]) {
        assert.ok(all.has(`achat.${id}`), `achat.${id}`);
      }
    });

    await step("tree shows rooms and their participants", () => {
      const top = api.tree.getChildren();
      assert.equal(top.length, 1);
      assert.ok(top[0] instanceof RoomItem);
      assert.equal(top[0].label, "r");
      const people = api.tree.getChildren(top[0]);
      assert.deepEqual(people.map((p) => (p as ParticipantItem).participant), ["alpha", "beta"]);
      assert.equal(people[0].description, "speaks next");
    });

    await step("opens a conversation panel", async () => {
      await vscode.commands.executeCommand("achat.openRoom", "r");
      const panel = api.panels.get("r");
      assert.ok(panel);
      const t0 = Date.now();
      try {
        await until("the webview script runs and renders the (empty) room", () => panel.rendered === 0, 30000);
      } catch (e) {
        throw new Error(`${(e as Error).message}; received from webview: ${JSON.stringify(panel.received)}; errors: ${JSON.stringify(panel.webviewErrors)}; visible: ${panel.panel.visible}, active: ${panel.panel.active}, reloads: ${panel.reloads}`);
      }
      report.webviewFirstRenderMs = Date.now() - t0;
      report.webviewReloads = panel.reloads;
      report.webviewMessages = panel.received.slice();
    });

    await step("interjecting from the panel writes a human message (text via stdin, kept literal)", async () => {
      const panel = api.panels.get("r")!;
      const text = "from the IDE: it's $HOME `uname` \"ok\"";
      await panel.handle({ type: "send", text, end: false });
      assert.deepEqual(bodies(), [text]);
      await until("the webview shows the new message", () => panel.rendered === 1);
    });

    await step("ignores malformed messages from the webview", async () => {
      const panel = api.panels.get("r")!;
      await panel.handle({ type: "send", text: 42 });
      await panel.handle({ type: "rm -rf" });
      await panel.handle(null);
      assert.equal(bodies().length, 1);
    });

    await step("picks up messages written by other processes (an agent) within seconds", async () => {
      // A real agent waits (and so reads the human's interjection) before it may send.
      const cli = (...args: string[]) => execFileSync(python, [path.join(repo, "achat.py"), ...args, `--dir=${rooms}`], { encoding: "utf8" });
      assert.match(cli("wait", "--room=r", "--as=alpha", "--timeout=0"), /from the IDE/);
      cli("send", "--room=r", "--as=alpha", "hello from an agent");
      await until("tree status updates", () => api.state.rooms?.rooms[0].status.turns === 1);
      assert.equal(api.state.rooms?.rooms[0].status.next, "beta");
      await until("the open panel shows the agent's message too", () => api.panels.get("r")!.rendered === 2);
      assert.deepEqual(api.panels.get("r")!.webviewErrors, []);
    });

    await step("the panel starts reliably: 6 fresh loads, no watchdog reloads", async () => {
      const times: number[] = [];
      for (let i = 0; i < 6; i++) {
        api.panels.get("r")!.panel.dispose();
        const t0 = Date.now();
        await vscode.commands.executeCommand("achat.openRoom", "r");
        const p = api.panels.get("r")!;
        await until(`load ${i + 1} renders`, () => p.rendered === 2, 20000);
        assert.equal(p.reloads, 0, `load ${i + 1} needed the watchdog`);
        assert.deepEqual(p.webviewErrors, []);
        times.push(Date.now() - t0);
      }
      report.reliabilityLoadsMs = times;
    });

    await step("closing the room from the IDE", async () => {
      await api.state.requireCli().close("r");
      await api.state.refresh();
      assert.match(api.state.rooms?.rooms[0].status.closed ?? "", /ended by human/);
      assert.equal(api.tree.getChildren()[0].contextValue, "achatRoomClosed");
    });

    report.nativeMcp = api.nativeMcp;
    report.ok = true;
  } catch (e) {
    report.ok = false;
    report.error = e instanceof Error ? `${e.message}\n${e.stack}` : String(e);
    throw e;
  } finally {
    const out = process.env.ACHAT_IT_RESULT;
    if (out) {
      require("node:fs").writeFileSync(out, JSON.stringify(report, null, 2));
    }
    console.log("ACHAT_IT_REPORT " + JSON.stringify(report));
  }
}
