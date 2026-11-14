#!/bin/bash
# Baseline record: every node test in the tree, one line of verdict each.
cd "$(dirname "$0")/.." || exit 1
export NODE_PATH="${NODE_PATH:-$HOME/apps/ada-bot/node_modules}"
out=/tmp/opencode/baseline-tests.txt
: > "$out"
for t in tests/test_*.cjs; do
  start=$(date +%s)
  if timeout 180 node "$t" > /tmp/opencode/last-test.log 2>&1; then
    verdict=PASS
  else
    verdict=FAIL
  fi
  echo "$verdict $t $(( $(date +%s) - start ))s" | tee -a "$out"
done
for s in tests/visual/snap.cjs tests/visual/library.cjs; do
  start=$(date +%s)
  if timeout 600 node "$s" > /tmp/opencode/last-test.log 2>&1; then verdict=PASS; else verdict=FAIL; fi
  echo "$verdict $s $(( $(date +%s) - start ))s" | tee -a "$out"
done
echo "DONE" | tee -a "$out"