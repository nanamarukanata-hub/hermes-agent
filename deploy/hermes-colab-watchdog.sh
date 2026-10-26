#!/usr/bin/env bash
# Restart ONLY the Hermes resident Drive worker, never Discord or Muse Supervisor.
# Call from a persistent VM boot hook/host supervisor if that is available.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

LOCK_DIR="${XDG_RUNTIME_DIR:-/tmp}"
LOCK="$LOCK_DIR/hermes-colab-resident-watchdog-$(id -u).lock"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "Hermes resident worker watchdog already active" >&2
  exit 0
fi

CHILD=""
STOP=0
on_stop() {
  STOP=1
  if [[ -n "$CHILD" ]]; then
    kill -TERM "$CHILD" 2>/dev/null || true
  fi
}
trap on_stop TERM INT
PYTHON="${HERMES_RESIDENT_PYTHON:-python3}"

while (( ! STOP )); do
  "$PYTHON" -m hermes_hybrid.resident_worker --interval 30 &
  CHILD=$!
  wait "$CHILD" || true
  CHILD=""
  if (( STOP )); then break; fi
  echo "Hermes resident worker exited; watchdog retry in 15 seconds" >&2
  sleep 15 &
  CHILD=$!
  wait "$CHILD" || true
  CHILD=""
done
