#!/usr/bin/env bash
# Fan-out/fan-in release gate: runs the full test suite first, then fans out the
# independent lint/sast/sca/dast checks. MCP integration tests create subprocess
# servers; isolating the suite prevents concurrent live servers from destabilizing
# native parser teardown on macOS. A single failure aborts with a non-zero exit and no
# git side effects have happened yet -- `make release`'s `_release: verify`
# dependency means nothing gets bumped, committed, tagged or pushed.
#
# This is a *local* pre-flight, not the authoritative gate: publish.yml reruns
# the same checks in CI (which is where the PyPI publish credential actually
# lives) before a pushed tag is allowed to reach `build`/`publish`.
set -u
cd "$(git rev-parse --show-toplevel)"

if ! make test; then
  echo "preflight: test failed -- release aborted, nothing committed or pushed."
  exit 1
fi

checks=(lint sast sca dast)
logdir=$(mktemp -d)
trap 'rm -rf "$logdir"' EXIT

declare -A pids
for check in "${checks[@]}"; do
  ( make "$check" >"$logdir/$check.log" 2>&1; echo $? >"$logdir/$check.exit" ) &
  pids[$check]=$!
done

for check in "${checks[@]}"; do
  wait "${pids[$check]}"
done

status=0
for check in "${checks[@]}"; do
  code=$(cat "$logdir/$check.exit")
  if [ "$code" -eq 0 ]; then
    echo "PASS  $check"
  else
    echo "FAIL  $check (exit $code)"
    echo "---- $check output ----"
    cat "$logdir/$check.log"
    echo "------------------------"
    status=1
  fi
done

if [ "$status" -ne 0 ]; then
  echo ""
  echo "preflight: one or more checks failed -- release aborted, nothing committed or pushed."
fi
exit "$status"
