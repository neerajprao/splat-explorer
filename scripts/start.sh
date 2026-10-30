#!/usr/bin/env bash
# Start the web app (http://127.0.0.1:8000) and the reconstruction worker.
# Ctrl+C stops both. The worker is wrapped in `caffeinate` so the Mac does not
# sleep while a job is running (keep the lid open or the charger connected).

set -euo pipefail
cd "$(dirname "$0")/.."

[[ -d .venv ]] || { echo "Run scripts/setup_mac.sh first."; exit 1; }
# shellcheck disable=SC1091
source .venv/bin/activate

HOST="${SPLAT_HOST:-127.0.0.1}"
PORT="${SPLAT_PORT:-8000}"

python scripts/doctor.py >/dev/null || {
  echo "Some tools are missing. Run: python scripts/doctor.py"
  exit 1
}

cleanup() {
  echo
  echo "Stopping..."
  kill "${API_PID:-}" "${WORKER_PID:-}" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

python -m uvicorn --factory splat_explorer.api:create_app --host "$HOST" --port "$PORT" &
API_PID=$!

if command -v caffeinate >/dev/null 2>&1; then
  caffeinate -i python -m splat_explorer.worker &
else
  python -m splat_explorer.worker &
fi
WORKER_PID=$!

sleep 1
echo
echo "  Splat Explorer is running:  http://$HOST:$PORT"
echo "  Press Ctrl+C to stop."
echo
(sleep 2 && open "http://$HOST:$PORT" 2>/dev/null) &
wait
