#!/usr/bin/env bash
# HardlyListening — start the server.
set -euo pipefail
cd "$(dirname "$0")"
exec python3 server.py "$@"
