"""Minimal OAuth 2.1 authorization server for the Muse bridge.

This is an original, stdlib-only implementation of the discovery and
grant surface that OAuth-aware MCP clients (including ChatGPT's custom
MCP connector) expect, following the public MCP authorization pattern:

- RFC 9728 protected-resource metadata at
  ``/.well-known/oauth-protected-resource`` (and the ``/mcp`` variant),
- RFC 8414 authorization-server metadata at
  ``/.well-known/oauth-authorization-server``,
- RFC 7591 dynamic client registration at ``/register``,
- authorization-code grant with PKCE S256 at ``/authorize`` / ``/token``,
- refresh-token rotation at ``/token`` and revocation at ``/revoke``.

Single-owner model: there is no user database.  The person who opens
the ``/authorize`` page proves ownership by entering the bridge's
OAuth approval key (a value that lives only in a 0600 file or env
var on the bridge host, never in the repo, never in a log, and never
sent to ChatGPT).  The approval key is checked with a constant-time
comparison and is never stored in the OAuth state file.

Tokens are stored only as SHA-256 hashes in the state file
(``oauth_state.json``, mode 0600, atomic replace on write).
Authorization codes are single-use, 10 minutes; access tokens live
1 hour; refresh tokens live 30 days and rotate on every use (the old
refresh token and its access tokens stop working once rotated).
Scope is a single value, ``mcp:tools``: a token either may call the
bridge's MCP tools or it may not; there is no finer-grained scope.

This module never issues a public URL, never provisions TLS, and
never calls out to any provider.  Reaching it from ChatGPT requires
a public HTTPS endpoint in front of it (a TLS-terminating relay or
the Secure MCP Tunnel path documented in docs/MUSE-CHATGPT-BRIDGE.md).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

SCOPE = "mcp:tools"
_CODE_TTL = 10 * 60
_ACCESS_TTL = 60 * 60
_REFRESH_TTL = 30 * 24 * 60 * 60


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _pkce_s256(verifier: str) -> str:
    import base64

    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")


def _now() -> int:
    return int(time.time())


class OAuthServer:
    """OAuth authorization server state for one bridge instance."""

    def __init__(self, public_url: str, approval_key: str, state_dir: str | Path):
        self.public_url = public_url.rstrip("/")
        self.approval_key = approval_key
        self.state_path = Path(state_dir) / "oauth_state.json"
        self._state = self._load()

    # -- persistence -----------------------------------------------------
    def _load(self) -> dict:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("clients", {})
                data.setdefault("codes", {})
                data.setdefault("access", {})
                data.setdefault("refresh", {})
                return data
        except (OSError, json.JSONDecodeError):
            pass
        return {"clients": {}, "codes": {}, "access": {}, "refresh": {}}

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_name(f".{self.state_path.name}.tmp-{os.getpid()}")
        tmp.write_text(json.dumps(self._state, indent=2) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.state_path)
        os.chmod(self.state_path, 0o600)

    def _prune(self) -> None:
        now = _now()
        for table in ("codes", "access", "refresh"):
            for key in [k for k, v in self._state[table].items() if v.get("expires", 0) <= now]:
                del self._state[table][key]

    # -- metadata --------------------------------------------------------
    def protected_resource_metadata(self) -> dict:
        return {
            "resource": f"{self.public_url}/mcp",
            "authorization_servers": [self.public_url],
            "scopes_supported": [SCOPE],
            "bearer_methods_supported": ["header"],
        }

    def authorization_server_metadata(self) -> dict:
        return {
            "issuer": self.public_url,
            "authorization_endpoint": f"{self.public_url}/authorize",
            "token_endpoint": f"{self.public_url}/token",
            "registration_endpoint": f"{self.public_url}/register",
            "revocation_endpoint": f"{self.public_url}/revoke",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
            "scopes_supported": [SCOPE],
        }

    # -- dynamic client registration --------------------------------------
    def register_client(self, redirect_uris: list[str]) -> dict | None:
        uris = [u for u in (redirect_uris or []) if isinstance(u, str) and u]
        if not uris:
            return None
        client_id = secrets.token_urlsafe(24)
        self._state["clients"][client_id] = {"redirect_uris": uris, "created_at": _now()}
        self._prune()
        self._save()
        return {"client_id": client_id, "redirect_uris": uris,
                "client_id_issued_at": _now(), "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"], "token_endpoint_auth_method": "none"}

    # -- authorization ------------------------------------------------------
    def authorize(self, params: dict, approval_key: str) -> tuple[str | None, str | None]:
        """Validate an approval and issue a redirect URL, or (None, error)."""
        if not hmac.compare_digest(approval_key or "", self.approval_key):
            return None, "invalid_approval_key"
        client_id = params.get("client_id", "")
        client = self._state["clients"].get(client_id)
        if client is None:
            return None, "unauthorized_client"
        redirect_uri = params.get("redirect_uri", "")
        if redirect_uri not in client["redirect_uris"]:
            return None, "invalid_redirect_uri"
        if params.get("response_type") != "code":
            return None, "unsupported_response_type"
        if params.get("code_challenge_method") != "S256" or not params.get("code_challenge"):
            return None, "invalid_request"
        scope = params.get("scope") or SCOPE
        if scope != SCOPE:
            return None, "invalid_scope"
        code = secrets.token_urlsafe(32)
        self._state["codes"][_hash(code)] = {
            "client_id": client_id, "redirect_uri": redirect_uri,
            "code_challenge": params["code_challenge"], "scope": scope,
            "expires": _now() + _CODE_TTL, "used": False}
        self._prune()
        self._save()
        query = {"code": code}
        if params.get("state"):
            query["state"] = params["state"]
        sep = "&" if "?" in redirect_uri else "?"
        return f"{redirect_uri}{sep}{urlencode(query)}", None

    # -- token ------------------------------------------------------------
    def _issue_pair(self, client_id: str, scope: str) -> dict:
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        family = secrets.token_urlsafe(16)
        now = _now()
        self._state["access"][_hash(access)] = {
            "client_id": client_id, "scope": scope, "expires": now + _ACCESS_TTL,
            "family": family, "refresh_hash": _hash(refresh)}
        self._state["refresh"][_hash(refresh)] = {
            "client_id": client_id, "scope": scope, "expires": now + _REFRESH_TTL,
            "family": family}
        return {"access_token": access, "token_type": "Bearer", "expires_in": _ACCESS_TTL,
                "refresh_token": refresh, "scope": scope}

    def exchange_token(self, form: dict) -> tuple[dict | None, str | None]:
        grant = form.get("grant_type", "")
        if grant == "authorization_code":
            return self._exchange_code(form)
        if grant == "refresh_token":
            return self._exchange_refresh(form)
        return None, "unsupported_grant_type"

    def _exchange_code(self, form: dict) -> tuple[dict | None, str | None]:
        if form.get("client_secret"):
            return None, "invalid_client"
        if form.get("resource") and form["resource"] != f"{self.public_url}/mcp":
            return None, "invalid_target"
        code = form.get("code", "")
        entry = self._state["codes"].get(_hash(code))
        if entry is None or entry.get("used") or entry["expires"] <= _now():
            return None, "invalid_grant"
        if entry["client_id"] != form.get("client_id", ""):
            return None, "invalid_grant"
        if entry["redirect_uri"] != form.get("redirect_uri", ""):
            return None, "invalid_grant"
        if _pkce_s256(form.get("code_verifier", "")) != entry["code_challenge"]:
            return None, "invalid_grant"
        entry["used"] = True
        pair = self._issue_pair(entry["client_id"], entry["scope"])
        self._prune()
        self._save()
        return pair, None

    def _exchange_refresh(self, form: dict) -> tuple[dict | None, str | None]:
        if form.get("client_secret"):
            return None, "invalid_client"
        refresh = form.get("refresh_token", "")
        entry = self._state["refresh"].get(_hash(refresh))
        if entry is None or entry["expires"] <= _now():
            return None, "invalid_grant"
        if entry["client_id"] != form.get("client_id", ""):
            return None, "invalid_grant"
        scope = form.get("scope") or entry["scope"]
        if scope != entry["scope"]:
            return None, "invalid_scope"
        # Rotate: the presented refresh token (and its access tokens) die.
        family = entry["family"]
        del self._state["refresh"][_hash(refresh)]
        for key in [k for k, v in self._state["access"].items() if v.get("family") == family]:
            del self._state["access"][key]
        pair = self._issue_pair(entry["client_id"], scope)
        self._prune()
        self._save()
        return pair, None

    def validate_access_token(self, token: str) -> bool:
        entry = self._state["access"].get(_hash(token or ""))
        if entry is None or entry["expires"] <= _now():
            return False
        return entry["scope"] == SCOPE

    def revoke(self, token: str) -> bool:
        found = False
        for table in ("access", "refresh"):
            key = _hash(token or "")
            if key in self._state[table]:
                family = self._state[table][key].get("family")
                del self._state[table][key]
                found = True
                if table == "refresh" and family:
                    for k in [k for k, v in self._state["access"].items() if v.get("family") == family]:
                        del self._state["access"][k]
        if found:
            self._save()
        return found
