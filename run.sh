#!/usr/bin/env bash
# Start Groundish News and open it in a browser.
set -euo pipefail
cd "$(dirname "$0")"
PORT="${1:-8000}"
python3 server.py "$PORT" &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT INT TERM
sleep 1
command -v open >/dev/null && open "http://127.0.0.1:$PORT" || true
wait $SERVER
