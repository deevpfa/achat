#!/usr/bin/env bash
# One-time setup to publish the achat extension to the VS Code Marketplace and Open VSX.
#
# Before running it, create the accounts and tokens in your browser (see RELEASING.md):
#   - Open VSX account (sign the Eclipse publisher agreement) + access token   (Cursor, Windsurf, Antigravity)
#   - optional: VS Code Marketplace publisher + Azure DevOps token, scope "Marketplace: Manage"   (VS Code)
#     Leave its prompt empty to publish to Open VSX only; run the script again later to add it.
#
# Then:   scripts/setup-store-publishing.sh            (asks for the publisher id and both tokens)
#         scripts/setup-store-publishing.sh --dry-run  (shows what it would do, changes nothing)
#
# Tokens are read without echo and passed through environment variables and stdin, never as
# command-line arguments (which other processes on the machine can read).
set -euo pipefail

DRY=0
[[ "${1:-}" == "--dry-run" ]] && DRY=1
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXT="$REPO_ROOT/extensions/vscode"
GH_REPO="$(cd "$REPO_ROOT" && gh repo view --json nameWithOwner --jq .nameWithOwner)"

run() {
  if [[ $DRY == 1 ]]; then echo "  [dry-run] $*"; else "$@"; fi
}

read -rp "Publisher id (the same for the Marketplace and the Open VSX namespace): " PUB
if [[ ! "$PUB" =~ ^[a-z0-9][a-z0-9-]{1,62}$ ]]; then
  echo "Invalid publisher id: use lowercase letters, digits and '-'." >&2
  exit 1
fi
read -rsp "Open VSX token (OVSX_PAT): " OVSX_PAT; echo
read -rsp "VS Code Marketplace token (VSCE_PAT, press Enter to skip): " VSCE_PAT; echo
[[ -n "$OVSX_PAT" ]] || { echo "The Open VSX token is required." >&2; exit 1; }
export VSCE_PAT OVSX_PAT

CUR="$(node -p "require('$EXT/package.json').version")"
NEXT="$(node -p "const [a,b,c]='$CUR'.split('.').map(Number); [a,b,c+1].join('.')")"

if [[ -n "$VSCE_PAT" ]]; then
  echo "1/5 Checking the Marketplace token for publisher '$PUB'"
  run bash -c "cd '$EXT' && npx --yes @vscode/vsce verify-pat '$PUB'"
else
  echo "1/5 No Marketplace token: publishing to Open VSX only (VS Code users can still install the .vsix)"
fi

echo "2/5 Creating the Open VSX namespace '$PUB' (fine if it already exists)"
if [[ $DRY == 1 ]]; then
  echo "  [dry-run] npx ovsx create-namespace '$PUB'"
elif ! out="$(cd "$EXT" && npx --yes ovsx create-namespace "$PUB" 2>&1)"; then
  if ! echo "$out" | grep -qi "already exists"; then
    echo "$out" >&2
    echo "Could not create the Open VSX namespace. Did you sign the Publisher Agreement (log in with Eclipse) on open-vsx.org?" >&2
    exit 1
  fi
fi

echo "3/5 Storing the tokens as secrets of $GH_REPO"
if [[ $DRY == 1 ]]; then
  echo "  [dry-run] gh secret set OVSX_PAT${VSCE_PAT:+ / VSCE_PAT} --repo $GH_REPO (value via stdin)"
else
  printf %s "$OVSX_PAT" | gh secret set OVSX_PAT --repo "$GH_REPO"
  if [[ -n "$VSCE_PAT" ]]; then printf %s "$VSCE_PAT" | gh secret set VSCE_PAT --repo "$GH_REPO"; fi
fi

echo "4/5 Setting publisher '$PUB' and version $NEXT in extensions/vscode/package.json"
run node -e "
  const fs = require('fs'); const f = '$EXT/package.json';
  const p = JSON.parse(fs.readFileSync(f, 'utf8'));
  p.publisher = '$PUB'; p.version = '$NEXT';
  fs.writeFileSync(f, JSON.stringify(p, null, 2) + '\n');"

echo "5/5 Committing and tagging v$NEXT; the Release workflow publishes to every store with a token"
run git -C "$REPO_ROOT" commit -am "Publish the extension as $PUB.achat $NEXT"
run git -C "$REPO_ROOT" tag -a "v$NEXT" -m "achat extension $NEXT"
run git -C "$REPO_ROOT" push
run git -C "$REPO_ROOT" push origin "v$NEXT"

echo
if [[ $DRY == 1 ]]; then
  echo "Dry run: nothing was changed."
else
  echo "Done. Follow the publish run with:  gh run watch --repo $GH_REPO"
  echo "Then: https://open-vsx.org/extension/$PUB/achat"
  if [[ -n "$VSCE_PAT" ]]; then echo "      https://marketplace.visualstudio.com/items?itemName=$PUB.achat"; fi
fi
