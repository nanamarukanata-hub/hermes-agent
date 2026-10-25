"""Offline checks for bounded dependency bootstrapping on Render."""
import os
import subprocess
import unittest
from unittest.mock import patch

from muse_bridge import bootstrap


class BootstrapTests(unittest.TestCase):
    def test_existing_cli_is_not_reinstalled(self):
        with patch("muse_bridge.bootstrap.shutil.which", return_value="/bin/muse-cli"), \
             patch("muse_bridge.bootstrap.subprocess.run") as run:
            self.assertTrue(bootstrap.ensure_muse_cli_installed())
            run.assert_not_called()

    def test_pinned_install_succeeds(self):
        successful = subprocess.CompletedProcess([], 0, b"", b"")
        with patch("muse_bridge.bootstrap.shutil.which", side_effect=[None, "/venv/bin/muse-cli"]), \
             patch("muse_bridge.bootstrap.subprocess.run", return_value=successful) as run:
            self.assertTrue(bootstrap.ensure_muse_cli_installed())
            argv = run.call_args.args[0]
            self.assertEqual(argv[-1], "muse-cli==0.3.2")
            self.assertIn("--no-input", argv)
            self.assertLessEqual(run.call_args.kwargs["timeout"], 180)

    def test_package_installer_does_not_inherit_secrets(self):
        successful = subprocess.CompletedProcess([], 0, b"", b"")
        with patch.dict(os.environ, {
            "MUSE_BRIDGE_OAUTH_APPROVAL_KEY": "sensitive-oauth-value",
            "MUSE_BRIDGE_MUSE_COOKIES": "hatch_sess=sensitive-cookie-value",
        }), patch("muse_bridge.bootstrap.shutil.which",
                  side_effect=[None, "/venv/bin/muse-cli"]), \
             patch("muse_bridge.bootstrap.subprocess.run",
                   return_value=successful) as run:
            self.assertTrue(bootstrap.ensure_muse_cli_installed())
            env = run.call_args.kwargs["env"]
            self.assertNotIn("MUSE_BRIDGE_OAUTH_APPROVAL_KEY", env)
            self.assertNotIn("MUSE_BRIDGE_MUSE_COOKIES", env)

    def test_failure_keeps_server_unblocked(self):
        failed = subprocess.CompletedProcess([], 1, b"", b"")
        with patch("muse_bridge.bootstrap.shutil.which", return_value=None), \
             patch("muse_bridge.bootstrap.subprocess.run", return_value=failed):
            self.assertFalse(bootstrap.ensure_muse_cli_installed())

    def test_timeout_returns_false(self):
        with patch("muse_bridge.bootstrap.shutil.which", return_value=None), \
             patch("muse_bridge.bootstrap.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(["pip"], 180)):
            self.assertFalse(bootstrap.ensure_muse_cli_installed())


if __name__ == "__main__":
    unittest.main()
