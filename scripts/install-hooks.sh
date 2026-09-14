#!/bin/sh
# Installs this repo's git hooks (they live under scripts/git-hooks/ so they're
# versioned; .git/hooks/ itself is never tracked by git). Run once after cloning,
# and again if a hook script changes.
set -e
ROOT="$(git rev-parse --show-toplevel)"
for hook in "$ROOT"/scripts/git-hooks/*; do
    name="$(basename "$hook")"
    cp "$hook" "$ROOT/.git/hooks/$name"
    chmod +x "$ROOT/.git/hooks/$name"
    echo "installed .git/hooks/$name"
done
