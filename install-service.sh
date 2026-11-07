#!/usr/bin/env bash
# Install HardlyListening as a systemd user service, started automatically at
# login (autostart is on by default).
#
#   ./install-service.sh          install + start + enable autostart
#   ./install-service.sh remove   stop + uninstall
#
# Safe to re-run: it rewrites the unit file and restarts the service.

set -euo pipefail

UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT="$UNIT_DIR/hardlylistening.service"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${HARDLYLISTENING_PORT:-8470}"

PYTHON="/usr/bin/python3"
if [ ! -x "$PYTHON" ]; then
  PYTHON="$(command -v python3 || true)"
fi
if [ -z "$PYTHON" ]; then
  echo "error: no python3 on PATH" >&2
  exit 1
fi
# The app is stdlib-only, but it must not be pinned to whichever interpreter
# happened to be active in the shell that ran this script (a venv, a tool
# install). Prefer the system interpreter and check what actually gets used.
"$PYTHON" -c 'import sqlite3, ssl, subprocess' 2>/dev/null || {
  echo "error: $PYTHON cannot import sqlite3/ssl" >&2
  exit 1
}
if ! command -v yt-dlp >/dev/null 2>&1; then
  echo "error: yt-dlp is not on PATH — nothing can be downloaded without it." >&2
  exit 1
fi
command -v ffmpeg >/dev/null 2>&1 || echo "note: ffmpeg is missing; audio conversion will fail."

if [ "${1:-install}" = "remove" ]; then
  systemctl --user disable --now hardlylistening.service 2>/dev/null || true
  rm -f "$UNIT"
  systemctl --user daemon-reload
  echo "Removed. Your queue and downloads in ~/Music/HardlyListening are untouched."
  exit 0
fi

mkdir -p "$UNIT_DIR"

cat > "$UNIT" <<EOF
[Unit]
Description=HardlyListening (playlist download queue — SoundCloud, Apple Music, yt-dlp)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$HERE
ExecStart=$PYTHON $HERE/server.py --port $PORT --host 0.0.0.0
# A unit does not inherit the login shell's PATH: yt-dlp lives in ~/.local/bin
# and ffmpeg in /usr/bin, so both have to be spelled out or downloads fail.
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=5

NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now hardlylistening.service
# `enable --now` leaves an already-running service alone, so a re-run would keep
# the OLD ExecStart. Restart explicitly so the unit file on disk is what runs.
systemctl --user restart hardlylistening.service

echo
echo "Installed, started and enabled at login."
echo "  URL:    http://127.0.0.1:$PORT"
echo "  Status: systemctl --user status hardlylistening"
echo "  Logs:   journalctl --user -u hardlylistening -f"
