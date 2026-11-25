// Runs the integration suite inside a real editor (VS Code or a fork) with an isolated
// profile, a temp workspace and a temp rooms folder. Nothing touches the user's setup.
//
//   ACHAT_TEST_EDITOR="/Applications/Visual Studio Code.app/Contents/MacOS/Code" npm run test:integration
//
// Without ACHAT_TEST_EDITOR, @vscode/test-electron downloads a stable VS Code.

import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { runTests } from "@vscode/test-electron";

async function main(): Promise<void> {
  const ext = path.resolve(__dirname, "..", "..", "..");
  const repo = path.resolve(ext, "..", "..");
  const tmp = realpathSync(mkdtempSync(path.join(os.tmpdir(), "achat-it-")));
  const rooms = path.join(tmp, "rooms");
  const workspace = path.join(tmp, "workspace");
  mkdirSync(path.join(workspace, ".vscode"), { recursive: true });
  writeFileSync(
    path.join(workspace, ".vscode", "settings.json"),
    // No scriptPath: exercise the default flow (bundled CLI installed to ~/.achat/bin), with a temp HOME.
    JSON.stringify({ "achat.roomsDir": rooms }, null, 2),
  );
  const python = process.platform === "win32" ? "python" : "python3";
  execFileSync(python, [path.join(repo, "achat.py"), "new", "r", "--participants=alpha,beta", "--topic=integration", `--dir=${rooms}`]);

  const resultFile = path.join(tmp, "result.json");
  mkdirSync(path.join(tmp, "home"), { recursive: true });
  const code = await runTests({
    vscodeExecutablePath: process.env.ACHAT_TEST_EDITOR || undefined,
    extensionDevelopmentPath: ext,
    extensionTestsPath: path.join(__dirname, "suite"),
    extensionTestsEnv: { ACHAT_IT_ROOMS: rooms, ACHAT_IT_REPO: repo, ACHAT_IT_RESULT: resultFile, HOME: path.join(tmp, "home") },
    launchArgs: [
      workspace,
      "--disable-extensions",
      `--user-data-dir=${path.join(tmp, "user-data")}`,
      `--extensions-dir=${path.join(tmp, "extensions")}`,
      "--skip-welcome",
      "--skip-release-notes",
      "--disable-workspace-trust",
    ],
  });
  console.log(`integration suite exit code: ${code}`);
  if (code === 0) {
    rmSync(tmp, { recursive: true, force: true }); // ~6 MB of editor profile per run
  } else {
    console.log(`kept for debugging: ${tmp}`);
  }
  process.exit(code);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
