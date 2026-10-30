"""Hermes-native Drive access: the env-token HTTP transport path.

Proves Hermes can build its Drive Vault WITHOUT the Muse connector
CLI: when GOOGLE_DRIVE_ACCESS_TOKEN is provisioned (by the operator,
through their own OAuth), drive_vault_from_env selects HttpDriveTransport.
No network is touched at construction time and no token value is logged.
"""
from __future__ import annotations

import pytest

from hermes_hybrid.drive_vault import (
    DriveAuthError, HttpDriveTransport, drive_vault_from_env)


def test_env_token_selects_http_transport(monkeypatch):
    monkeypatch.setenv("GOOGLE_DRIVE_ACCESS_TOKEN", "operator-provisioned-token")
    monkeypatch.setenv("HERMES_DRIVE_VAULT_ROOT_ID", "root-id-123")
    vault = drive_vault_from_env()
    assert isinstance(vault._t, HttpDriveTransport)
    assert vault._root_id == "root-id-123"


def test_missing_token_raises_auth_error_at_use(monkeypatch):
    monkeypatch.delenv("GOOGLE_DRIVE_ACCESS_TOKEN", raising=False)
    vault = drive_vault_from_env()
    assert isinstance(vault._t, HttpDriveTransport)
    with pytest.raises(DriveAuthError):
        vault._t._token_provider()


def test_injected_transport_still_wins(monkeypatch):
    from hermes_hybrid.drive_vault import FakeDriveTransport
    fake = FakeDriveTransport()
    fake.seed_root()
    vault = drive_vault_from_env(fake)
    assert vault._t is fake
