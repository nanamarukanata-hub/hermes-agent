# Hermes adult-character roleplay style (opt-in)

This extends **the existing** RPConversation/Discord Gateway integration. It
does not replace the official Hermes gateway, a character card, or the
Obsidian-only Vault state. It does not require recurring AI tasks.

## Supported scope

- An expressly adult character can use a distinctive teasing/quick-witted
  dialogue profile (sass, warmth, vulnerability and initiative, 0–5).
- The existing character identity, first message, psychology, World Book and
  conversation history remain the sources of truth.
- Dialogue formatting collapses consecutive ♡ glyphs and avoids ending
  adjacent assistant turns with ♡. It applies only to opted-in adult cards.
- Scene beats and relationship signals have a small deterministic event graph.
  Only explicit calls advance the event; the system does **not** silently
  escalate relationship stages from arbitrary messages.
- One user/character's RP state cannot be reused as another's. Event and
  last-heart state are stored in the user's existing Obsidian Vault character
  state and survive gateway restarts and session resets.

## Character card

Add a field at the same level as `name` inside a SillyTavern `data` object:

```json
{
  "data": {
    "name": "ExistingAdultCharacter",
    "personality": "A confident, playful adult with a sarcastic sense of humor.",
    "adult_rp": {
      "enabled": true,
      "age": 24,
      "voice": "teasing with dry humor, sincere when trust is earned",
      "sass": 5,
      "warmth": 2,
      "vulnerability": 2,
      "initiative": 4
    }
  }
}
```

The `adult_rp` switch only activates when `enabled` is exactly `true`
and `age` is an integer at least 18. A missing, invalid, or underage age
does not activate this profile. Existing cards are unchanged.

## Events API

```python
from hermes_hybrid import CharacterCard, RPConversation, VaultStore

rp = RPConversation(VaultStore("/path/to/your/Obsidian/Vault"))
rp.set_active_card("discord-user-id", CharacterCard.from_sillytavern(card_dict))

# Optional. Explicit event triggers for a current adult character.
rp.trigger_adult_event("discord-user-id", "challenge")
rp.trigger_adult_event("discord-user-id", "honesty")
```

Available event names:
`challenge`, `comeback`, `honesty`, `mutual_trust`,
`reconcile`, `playful_reset`.

The five scene beats are `banter`, `rivalry`, `rapport`,
`closeness` and `reflection`. Trust/tension each stay within 0–10.

**Gateway integration:** `before_agent` injects the roleplay voice and
state into the prompt. The real `gateway/run.py` RP hook calls
`postprocess_reply` before `after_agent`, so the output sent and the
heart-termination state recorded by the Vault agree.

**Limitations:** The event API is callable from Python; there is no new Discord
slash command. There is no automatic live deployment. The package provides
non-graphic dialogue and scene state infrastructure; it does not ship sexual
act scripts or third-party erotica datasets.

## Regression checks

```bash
python -m pytest -q tests/test_adult_roleplay_style.py tests/test_gateway_rp_integration.py tests/test_hybrid.py
```
