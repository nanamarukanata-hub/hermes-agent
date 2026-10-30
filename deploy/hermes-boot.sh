#!/usr/bin/env bash
# Hermes-owned boot entry point (no Muse involvement).
#
# Starts the resident-worker watchdog if it is not already running.
# Register this from whatever boot mechanism the Hermes host provides:
#   * systemd: deploy/hermes-resident-system.service (calls this script)
#   * cron:    @reboot /opt/hermes/deploy/hermes-boot.sh
#   * rc.local / cloud-init user-data on a self-hosted machine
#
# The watchdog itself supervises the worker (restart with backoff);
# this script only guarantees the watchdog exists after a boot.
set -euo pipefail

WORKER_HOME="${HERMES_WORKER_HOME:-/home/hatch/hermes-colab-worker}"

if pgrep -f '[h]ermes-colab-worker/watchdog.sh' >/dev/null 2>&1; then
    echo "hermes watchdog already running"
    exit 0
fi

if [[ -x "$WORKER_HOME/start.sh" ]]; then
    "$WORKER_HOME/start.sh"
    echo "hermes watchdog started via $WORKER_HOME/start.sh"
else
    echo "start.sh not found at $WORKER_HOME" >&2
    exit 1
fi
