"""Discord gateway adapter: pure decision functions, no network.

The official Hermes gateway already handles Discord DMs, channels,
threads, mentions, and per-user sessions. This module only adds the
RP-specific decisions on top of it, in a form that can be tested
without a Discord token or a live gateway:
- allow-list gating (empty allow-list = open, as in OpenChara);
- free-response channel vs. mention-required channel;
- per-user session keying (user + channel + character).

No message is sent and no token is read here.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GatewayConfig:
    allowed_user_ids: set[str] | None = None  # None/empty = open
    free_response_channel_ids: set[str] | None = None
    group_sessions_per_user: bool = True


def is_allowed(config: GatewayConfig, user_id: str) -> bool:
    if not config.allowed_user_ids:
        return True
    return user_id in config.allowed_user_ids


def should_respond(config: GatewayConfig, *, is_dm: bool, mentioned: bool,
                   channel_id: str, in_thread: bool = False) -> bool:
    if is_dm:
        return True
    if in_thread:
        return True
    if config.free_response_channel_ids and channel_id in config.free_response_channel_ids:
        return True
    return mentioned


def session_key(user_id: str, channel_id: str, character_id: str, per_user: bool = True) -> str:
    if per_user:
        return f"{user_id}-{channel_id}-{character_id}"
    return f"{channel_id}-{character_id}"
