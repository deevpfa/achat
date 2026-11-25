// Copies the CLI from the repo root into the extension, so the .vsix is self-contained.
// The repo's achat.py stays the single source; cli/ is a build artifact (gitignored).
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..", "..", "..");
const out = join(here, "..", "cli");
mkdirSync(out, { recursive: true });
for (const f of ["achat.py", "viewer.html"]) {
  copyFileSync(join(repo, f), join(out, f));
}
console.log(`bundled achat.py + viewer.html into ${out}`);
