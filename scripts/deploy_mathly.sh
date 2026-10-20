#!/usr/bin/env bash
# Deploys the current commit to the mathly Space (the math app) under its own name.
# A Space takes its name and emoji from the front matter of README.md, which ihki and mathly share; this pushes a
# copy of HEAD whose front matter says mathly, so the local README and the Arabic Space (origin) stay as they are.
# Usage: scripts/deploy_mathly.sh   (commit first; the remote `mathly` is https://huggingface.co/spaces/wzender/mathly)
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
if [ -n "$(git status --porcelain)" ]; then
  echo "There are uncommitted changes. Commit them first: only committed work is deployed." >&2
  exit 1
fi
tmp=$(mktemp -d)
trap 'git worktree remove --force "$tmp" >/dev/null 2>&1 || true; rm -rf "$tmp"' EXIT
git worktree add --detach "$tmp" HEAD >/dev/null
python3 - "$tmp/README.md" <<'PY'
import re, sys
path = sys.argv[1]
text = open(path, encoding="utf-8").read()
head, sep, rest = text.partition("\n---\n")   # the front matter: between the first two --- lines
own = {"title": "mathly", "emoji": "➗", "colorFrom": "blue", "colorTo": "gray"}
for key, value in own.items():
    head = re.sub(rf"^{key}:.*$", f"{key}: {value}", head, flags=re.M)
open(path, "w", encoding="utf-8").write(head + sep + rest)
PY
git -C "$tmp" commit -qam "mathly: the Space's own name"
git -C "$tmp" push --force mathly HEAD:main
echo "Pushed $(git rev-parse --short HEAD) to mathly. The build: https://huggingface.co/spaces/wzender/mathly?logs=build"
