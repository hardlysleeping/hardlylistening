#!/usr/bin/env bash
# Restart HardlyListening when a Python file changes, verify health, roll back if it fails.
# Web assets need no restart, so a change that only touches web/ (or the download cache) exits
# without restarting.
#
# The whole tree is hashed, not just the repo root: `library/`, `social/`, `discovery/` and the
# rest are packages, and their changes were the ones that used to slip through unredeployed.
# `node_modules`, the download cache (temp ZIPs land inside the tree) and `backups/` are history
# rather than code, so they are skipped.
set -euo pipefail

# The four paths and the health address are overridable so the rollback can be exercised on a
# throwaway tree and a stub service (`tests/test_autodeploy.py`). Unset, they are exactly what the
# installed unit already uses: nothing about a real deploy changes.
repo=${AUTODEPLOY_REPO:-$HOME/apps/hardlylistening}
backups=${AUTODEPLOY_BACKUPS:-$HOME/hl-deploy-backups}
state=${AUTODEPLOY_STATE:-$HOME/.hl-autodeploy.pysum}
unit=${AUTODEPLOY_UNIT:-hardlylistening.service}
tries=${AUTODEPLOY_HEALTH_TRIES:-20}   # one second apart
health=${AUTODEPLOY_HEALTH:-http://127.0.0.1:8470/api/health}
stamp=$(date +%Y%m%d-%H%M%S)
# Every path below is relative to the tree (find, md5sum, `cp --parents` and the syntax
# check all read what `find` printed), so the script has to stand in the tree. The installed
# unit sets WorkingDirectory as well; this line is what makes that a convenience rather than
# a requirement, and what lets the rollback test run it from anywhere.
cd "$repo"

py_files() {
  ( cd "$repo" && find . -name '*.py' \
      -not -path './node_modules/*' \
      -not -path '*/__pycache__/*' \
      -not -path './.cache/*' \
      -not -path './backups/*' -print0 )
}

# Copy every Python file in a directory back over the repo, keeping its path.
restore_from() {
  ( cd "$1" && find . -name '*.py' -print0 | xargs -0 cp --parents -t "$repo" )
}

sum=$(py_files | sort -z | xargs -0 md5sum | md5sum | cut -d" " -f1)
[ -f "$state" ] || { echo "$sum" > "$state"; echo "autodeploy: baseline recorded, nothing restarted"; exit 0; }
if [ "$(cat "$state")" = "$sum" ]; then echo "autodeploy: no python change"; exit 0; fi

backup="$backups/$stamp"
# Two changes inside one second would otherwise share a backup directory and the second
# would overwrite the first — which is the copy a rollback would need.
if [ -d "$backup" ]; then backup="$backup-$$"; fi
mkdir -p "$backup"
py_files | xargs -0 cp --parents -t "$backup"

# A file that cannot compile would still let the service start (lazy imports) and pass health, so
# check syntax before touching the running service. `ast.parse` rather than `py_compile`: it reads
# the same grammar and writes no .pyc into the tree, which would fire the path unit again.
bad=""
while IFS= read -r -d '' file; do
  python3 -c 'import ast,sys; ast.parse(open(sys.argv[1], encoding="utf-8").read(), sys.argv[1])' "$file" 2>/dev/null \
    || bad="$bad ${file#./}"
done < <(py_files)
if [ -n "$bad" ]; then
  prev=$(ls -1d "$backups"/*/ 2>/dev/null | sort | tail -2 | head -1)
  if [ -n "${prev:-}" ] && [ "$prev" != "$backup/" ]; then
    restore_from "$prev"
  fi
  echo "autodeploy: syntax error in$bad — restored the previous python, service untouched" >&2
  exit 1
fi

export XDG_RUNTIME_DIR="/run/user/$(id -u)"
systemctl --user restart "$unit"

for _ in $(seq 1 "$tries"); do
  code=$(curl -s -o /dev/null -w '%{http_code}' "$health" || true)
  if [ "$code" = "200" ]; then echo "$sum" > "$state"; echo "autodeploy: healthy after restart ($stamp)"; exit 0; fi
  sleep 1
done

echo "autodeploy: health failed, rolling back to the previous python" >&2
prev=$(ls -1d "$backups"/*/ 2>/dev/null | sort | tail -2 | head -1)
if [ -n "${prev:-}" ] && [ "$prev" != "$backup/" ]; then
  restore_from "$prev"
  systemctl --user restart "$unit"
fi
exit 1