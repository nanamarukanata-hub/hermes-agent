"""Pink Ink Manga Hermes plugin. Prompt composition only; no image inference."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DATA_PATH = Path(__file__).with_name("style.json")


def build_style_prompt(subject: str, preset: str = "paper_coral") -> dict[str, Any]:
    """Return deterministic, renderer-agnostic prompt strings and palette."""
    if not isinstance(subject, str) or not subject.strip():
        raise ValueError("subject must be a non-empty string")
    if len(subject) > 2000:
        raise ValueError("subject is too long (max 2000 characters)")
    data = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    if preset not in data["presets"]:
        raise ValueError("unknown preset: " + str(preset))
    p = data["presets"][preset]
    return {
        "style_id": data["id"],
        "preset": preset,
        "positive_prompt": ", ".join([subject.strip(), *data["core"], p["background"], p["accents"]]),
        "negative_prompt": ", ".join(data["negative"]),
        "palette": p["palette"],
        "rendered_image": False,
        "note": "Prompt composition only. Image generation requires a separately connected renderer."
    }


def _tool_handler(args: dict[str, Any], **_: Any) -> str:
    try:
        return json.dumps(build_style_prompt(args.get("subject", ""), args.get("preset", "paper_coral")), ensure_ascii=False)
    except (ValueError, TypeError) as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def _slash_handler(raw_args: str) -> str:
    # syntax: /pink-ink subject text; default preset=paper_coral
    return _tool_handler({"subject": raw_args or "original character portrait"})


def register(ctx: Any) -> None:
    schema = {
        "name": "pink_ink_style_prompt",
        "description": "Compose a manga illustration style prompt from a user-supplied subject without any external API or model.",
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Content/subject to draw, kept separate from the style."},
                "preset": {"type": "string", "enum": ["paper_coral", "peach_line", "dark_rose"]}
            },
            "required": ["subject"],
            "additionalProperties": False
        }
    }
    ctx.register_tool(
        name="pink_ink_style_prompt", toolset="pink_ink_style", schema=schema,
        handler=_tool_handler, description="Generate Pink Ink manga art direction prompts."
    )
    ctx.register_command("pink-ink", handler=_slash_handler,
                         description="Compose Pink Ink manga prompts", args_hint="<subject>")
