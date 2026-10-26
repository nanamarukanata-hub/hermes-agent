#!/usr/bin/env bash
# Idempotent launcher. A watchdog can restart worker processes but cannot
# recreate a fully destroyed VM. Wire THIS launcher into an authorized
# provider boot hook separately; do not modify existing Muse Supervisor/Hook.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
ENV_FILE="${HERMES_RESIDENT_ENV_FILE:-$HOME/.config/hermes/colab-resident.env}"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing owner-authorized Hermes Drive worker environment file" >&2
  exit 2
fi
if [[ ! -O "$ENV_FILE" ]]; then
  echo "Environment file must be owned by the current user" >&2
  exit 2
fi
MODE="$(stat -c %a "$ENV_FILE")"
case "$MODE" in 600|400) ;; *) echo "Environment file must be mode 600 or 400" >&2; exit 2;; esac
# Must be controlled by owner; never source a file from Drive or a public repo.
set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a
if [[ -z "${HERMES_DRIVE_VAULT_ROOT_ID:-}" ]]; then
  echo "Pinned canonical Drive folder ID is required" >&2
  exit 2
fi
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/hermes"
mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"
# Existing instance is protected by the watchdog's own flock lock.
nohup bash "$ROOT/deploy/hermes-colab-watchdog.sh" \
  >>"$STATE_DIR/colab-resident-watchdog.log" 2>&1 </dev/null &
echo "Watchdog launch requested. Verify resident-worker.json and process status."
