"""Regression coverage for opt-in adult-character roleplay style.

No model calls, external content scraping, Discord token, or real user data.
"""
import pytest

from hermes_hybrid import CharacterCard, RPConversation, VaultStore
from hermes_hybrid.adult_roleplay_style import (
    AdultSceneEvents, AdultStyleProfile, format_heart_punctuation,
)


def _card(age=25, enabled=True):
    return CharacterCard.from_sillytavern({
        "data": {
            "name": "AdultChar",
            "description": "An adult stage performer.",
            "personality": "witty and teasing, sincere when trust grows",
            "adult_rp": {
                "enabled": enabled,
                "age": age,
                "voice": "confident, understated sarcasm",
                "sass": 5,
                "warmth": 2,
            },
        },
    })


def test_adult_opt_in_requires_explicit_adult_age():
    for invalid in [None, 17, True, "20"]:
        assert AdultStyleProfile.from_extra(
            {"adult_rp": {"enabled": True, "age": invalid}}
        ) is None
    assert AdultStyleProfile.from_extra({"adult_rp": {"age": 22}}) is None
    assert AdultStyleProfile.from_extra({"adult_rp": {"enabled": False, "age": 22}}) is None
    profile = AdultStyleProfile.from_extra(_card().extra)
    assert profile.age == 25
    assert profile.sass == 5


def test_heart_formatter_never_allows_adjacent_hearts():
    assert format_heart_punctuation("へえ♡♡   よかった♡ ♡") == "へえ♡   よかった♡"
    assert format_heart_punctuation("いいね♡", previous_ended_heart=True) == "いいね"
    assert format_heart_punctuation("♡でもね。", previous_ended_heart=True) == "♡でもね。"
    assert format_heart_punctuation("いいね♡", previous_ended_heart=False) == "いいね♡"


def test_event_transition_is_explicit_and_bounded():
    events = AdultSceneEvents()
    assert events.beat == "banter"
    for _ in range(100):
        events.transition("challenge")
    assert events.beat == "rivalry"
    assert events.tension == 10
    with pytest.raises(ValueError):
        events.transition("not-an-event")
    events.transition("honesty")
    assert events.beat == "rapport"
    assert events.trust == 1
    restored = AdultSceneEvents.from_dict(events.to_dict())
    assert restored.event_count == 101


def test_discord_rp_hook_state_survives_restart_and_enforces_two_turn_rule(tmp_path):
    path = tmp_path / "vault"
    user = "adult-user"
    rp = RPConversation(VaultStore(path))
    rp.set_active_card(user, _card())
    prompt = rp.before_agent(user, "session-1", "hello")
    assert "age explicitly declared: 25" in prompt
    assert "sass=5" in prompt
    first = rp.postprocess_reply(user, "あら♡♡")
    assert first == "あら♡"
    rp.after_agent(user, "session-1", "hello", first)

    restarted = RPConversation(VaultStore(path))
    assert restarted.load_active_state(user).card.extra["adult_rp"]["age"] == 25
    second_prompt = restarted.before_agent(user, "session-2", "again")
    assert "Current scene beat: banter" in second_prompt
    second = restarted.postprocess_reply(user, "そうね♡")
    assert second == "そうね"
    restarted.after_agent(user, "session-2", "again", second)
    assert restarted.load_active_state(user).adult_style_state["last_reply_ended_heart"] is False
    assert restarted.postprocess_reply(user, "次は♡") == "次は♡"
    restarted.trigger_adult_event(user, "challenge")
    restarted.trigger_adult_event(user, "honesty")
    assert "Current scene beat: rapport" in restarted.before_agent(user, "session-3", "trust")
    assert restarted.load_active_state(user).adult_style_state["event_count"] == 2


def test_no_opt_in_keeps_unmodified_output_and_isolation(tmp_path):
    rp = RPConversation(VaultStore(tmp_path / "vault"))
    rp.set_active_card("adult", _card())
    rp.set_active_card("unconfigured", CharacterCard(name="Ordinary"))
    rp.set_active_card("nonadult", _card(age=17))
    for user in ("unconfigured", "nonadult"):
        assert rp.postprocess_reply(user, "test♡♡") == "test♡♡"
        with pytest.raises(ValueError):
            rp.trigger_adult_event(user, "challenge")
        assert "Opt-in adult-character voice" not in rp.before_agent(user, "s", "hi")
    rp.trigger_adult_event("adult", "challenge")
    assert rp.load_active_state("unconfigured").adult_style_state == {}
