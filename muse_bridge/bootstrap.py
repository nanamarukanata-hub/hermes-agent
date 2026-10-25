"""Install pinned personal Muse CLI on startup when Render build settings are unavailable.

The host's existing build command only syntax-checks Python. This opt-in
bridge runtime bootstraps the pinned dependency before serving HTTP, without
reading or echoing secrets. A failed install is non-fatal to OAuth/MCP health.
"""
from __future__ import annotations

import shutil
import subprocess
import sys

PACKAGE = "muse-cli==0.3.2"


def ensure_muse_cli_installed() -> bool:
    """Return True only if a functioning CLI is available.

    The subprocess output is never logged because build logs are public to
    workspace collaborators. Install is bounded and cannot execute requests
    to Muse without the separately configured Muse cookie.
    """
    if shutil.which("muse-cli"):
        return True
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
             "--no-input", "--no-cache-dir", PACKAGE],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0 and shutil.which("muse-cli") is not None
