#!/bin/sh
# Install HardlyListening as a system service (SERVER-PKG-006).
#
#   ./install.sh                      install or upgrade
#   ./install.sh --dry-run            print every command, change nothing
#   ./install.sh uninstall            stop and remove the code, keep the data
#
# Two rules this script takes seriously, because both are unrecoverable when broken:
#
#   * it never deletes the database or the media tree — not on uninstall, not on upgrade, not
#     with a flag. Removing a library is an operator's decision made with `rm`, not a side effect
#     of uninstalling a service;
#   * it runs `check` before enabling anything, so a bad path fails here rather than in a restart
#     loop with journald as the only witness.
#
# `--dry-run` exists because the honest way to review an installer is to read what it would do.
set -eu

PREFIX=/opt/hardlylistening
DATA=/var/lib/hardlylistening
MUSIC=/srv/hardlylistening/music
CONF_DIR=/etc/hardlylistening
CONF="$CONF_DIR/hardlylistening.env"
UNIT=/etc/systemd/system/hardlylistening.service
USER_NAME=hardlylistening
ACTION=install
DRY=0

usage() {
  sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
}

for arg in "$@"; do
  case "$arg" in
    install|uninstall) ACTION="$arg" ;;
    --dry-run) DRY=1 ;;
    --prefix) shift; PREFIX="${1:?--prefix needs a path}" ;;
    --data)   shift; DATA="${1:?--data needs a path}" ;;
    --music)  shift; MUSIC="${1:?--music needs a path}" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "install.sh: unknown argument '$arg'" >&2; usage >&2; exit 2 ;;
  esac
done

# `run` is the one place a command is executed or merely printed. Everything below goes through
# it, so --dry-run cannot miss a step by accident.
run() {
  if [ "$DRY" = 1 ]; then
    printf '  + %s\n' "$*"
  else
    "$@"
  fi
}

say() { printf '%s\n' "$*"; }

HERE=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)   # the checkout this script lives in

say "HardlyListening install"
say "  code      $HERE -> $PREFIX"
say "  data      $DATA"
say "  music     $MUSIC"
say "  config    $CONF"
say "  unit      $UNIT"
[ "$DRY" = 1 ] && say "  mode      dry run, nothing will be changed"
say ""

if [ "$ACTION" = uninstall ]; then
  # Stop before touching the code, so the unit cannot restart against a half-removed tree.
  run systemctl disable --now hardlylistening.service || true
  run rm -f "$UNIT"
  run systemctl daemon-reload
  run rm -rf "$PREFIX"
  if [ "$DRY" = 1 ]; then
    say "Would remove $PREFIX and the unit."
  else
    say "Removed $PREFIX and the unit."
  fi
  say "Kept (deliberately):"
  printf '  %-28s %s\n' "$DATA" "database, caches and identity"
  printf '  %-28s %s\n' "$MUSIC" "the library"
  printf '  %-28s %s\n' "$CONF" "configuration, including secrets"
  say "Delete those yourself if that is what you want; this script never will."
  exit 0
fi

if [ "$DRY" != 1 ] && [ "$(id -u)" != 0 ]; then
  echo "install.sh: run as root (or pass --dry-run to see the steps)" >&2
  exit 1
fi

# -- account -----------------------------------------------------------------------------------
# A system account with the data directory as its home: provider caches land there, and nothing
# wants an interactive shell.
if ! id -u "$USER_NAME" >/dev/null 2>&1; then
  run useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin "$USER_NAME"
fi

# -- directories and permissions ---------------------------------------------------------------
run install -d -m 755 "$PREFIX"
run install -d -m 750 -o "$USER_NAME" -g "$USER_NAME" "$DATA"
run install -d -m 750 -o "$USER_NAME" -g "$USER_NAME" "$MUSIC" 2>/dev/null || true
run install -d -m 750 "$CONF_DIR"

# -- code --------------------------------------------------------------------------------------
# Copied, not moved: the checkout keeps working (the per-user unit runs from it), and an upgrade
# is this script again. Runtime state is excluded rather than copied and then ignored: a database
# that arrives in /opt is a database the next upgrade would overwrite.
if [ "$DRY" = 1 ]; then
  say "  + copy code (excluding .git, backups, tests, docs, deploy, node_modules, caches, database)"
else
  tar -C "$HERE" -cf - \
      --exclude=./.git --exclude=./backups --exclude=./tests --exclude=./docs \
      --exclude=./deploy --exclude=./node_modules --exclude=./__pycache__ \
      --exclude='./*.pyc' --exclude=./.cache --exclude=./artist-art \
      --exclude=./hardlylistening.db --exclude='./hardlylistening.db-*' . \
    | tar -C "$PREFIX" -xf -
  chmod 755 "$PREFIX/bin/hardlylistening-server"
fi

# -- configuration -----------------------------------------------------------------------------
if [ -f "$HERE/deploy/hardlylistening.env.example" ] && [ ! -f "$CONF" ]; then
  run install -m 640 -o root -g "$USER_NAME" "$HERE/deploy/hardlylistening.env.example" "$CONF"
  if [ "$DRY" = 1 ]; then
    say "  a starting config, with defaults for the paths above"
  else
    say "  wrote a starting config at $CONF — set HARDLYLISTENING_PUBLIC_ORIGIN before exposing it"
  fi
elif [ -f "$CONF" ]; then
  say "  kept the existing $CONF"
fi
# The example's paths are the defaults; a --prefix/--data/--music that differs has to land in the
# file, or `check` would probe one place and the service would use another.
if [ "$DRY" != 1 ] && [ -f "$CONF" ]; then
  sed -i \
    -e "s#^HARDLYLISTENING_DATA_DIR=.*#HARDLYLISTENING_DATA_DIR=$DATA#" \
    -e "s#^HARDLYLISTENING_MUSIC_DIR=.*#HARDLYLISTENING_MUSIC_DIR=$MUSIC#" "$CONF"
fi

# -- probe before enabling ---------------------------------------------------------------------
# The unit's ExecStartPre would catch this anyway; running it here is what turns "the service is
# failing" into a line of output in front of the person who can fix it.
if [ "$DRY" = 1 ]; then
  say "  + HARDLYLISTENING_DATA_DIR=$DATA HARDLYLISTENING_MUSIC_DIR=$MUSIC \\"
  say "      $PREFIX/bin/hardlylistening-server check"
else
  if ! env HARDLYLISTENING_DATA_DIR="$DATA" HARDLYLISTENING_MUSIC_DIR="$MUSIC" \
        "$PREFIX/bin/hardlylistening-server" check; then
    echo "install.sh: check failed — fix the reported line before starting the service" >&2
    exit 1
  fi
fi

# -- unit --------------------------------------------------------------------------------------
run install -m 644 "$HERE/deploy/hardlylistening.service" "$UNIT"
run systemctl daemon-reload
run systemctl enable --now hardlylistening.service

say ""
say "Done."
say "  status  systemctl status hardlylistening"
say "  logs    journalctl -u hardlylistening -f"
say "  port    127.0.0.1:8470 (put a reverse proxy in front; see docs/server-packaging.md §7)"