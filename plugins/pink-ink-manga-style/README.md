# Pink Ink Manga Style — Hermes plugin

Hand-authored, deterministic **style prompt builder**, based on three provided image references (reference image files are **not** included in the public repo).

## Install

1. Copy this `pink-ink-manga-style/` directory to `~/.hermes/plugins/pink-ink-manga-style/`.
2. Copy `skills/creative/pink-ink-manga/` to `~/.hermes/skills/creative/pink-ink-manga/`, or use the repo's skill discovery mechanism.
3. Restart Hermes so the plugin is discovered.

## Use

- In a Hermes session: `/pink-ink original adult character wearing a coral hoodie, standing front view`
- From agent tools: `pink_ink_style_prompt({"subject":"an original adult character in a hoodie", "preset":"paper_coral"})`
- Presets: `paper_coral` (bright), `peach_line` (warm pastel), `dark_rose` (dark background).

## Boundaries

This plugin assembles prompts on CPU with Python standard library only. It does **not** render or train images, download weights, call network APIs, or install checkpoints. For actual output, explicitly connect an image generation service or a separate renderer that you already use. Neither the images nor their authorship/licensing can be inferred from palette metadata; obtain permission before redistributing any source art.
