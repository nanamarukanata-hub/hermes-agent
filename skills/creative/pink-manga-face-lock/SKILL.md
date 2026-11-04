---
name: pink-manga-face-lock
description: "REFERENCE-first pink manga face/eye/expression lock; suppress generic V7/V8 round eyes, stock smiles and vector-clean lines in Hermes image prompts."
version: 0.9.1
metadata:
  hermes:
    tags: [creative, manga, image-generation, reference-style, facial-expression]
    category: creative
---

# Pink Manga Face Lock — V9

## Intent

Apply this **before image generation** when the user selects the Pink Manga reference style or asks to correct the eyes, expression, or face-style drift visible between REFERENCE and V7/V8. This skill is **not an image generator**. It builds a prompt and optional negative terms for the image generation tool already configured in Hermes.

**Reference is the authority**: preserve the visual grammar of the user-provided REFERENCE, particularly the narrow eyes and deliberate asymmetry. Do not treat the request as "make the eyes cuter" or "make a more polished anime girl". Preserve outfit, pose, composition, and non-face attributes unless changed by the user.

## High-priority facial grammar

- **Eye apertures**: narrow, horizontally elongated, half-lidded; visibly shallow opening rather than large round circles. Slightly mismatched left/right tilt and opening.
- **Upper lids and lashes**: thick, dark, variable-pressure upper eyelids with tapered wedge-shaped outer lashes. Delicate lower lines. Bangs may interrupt brow contours.
- **Irises**: compact deep red/crimson with upper shadow and irregular, elongated bright highlights; no oversized uniformly magenta discs or single centered dot pupils.
- **Expression**: relaxed teasing/sly grin coordinated with heavy upper lids, eyebrows, cheeks, and mouth. No wide-eyed innocent default smile.
- **Mouth**: small slightly crooked opening, with tiny fang or pointed tooth; avoid a large symmetrical U/D-shaped pink hole.
- **Cheeks**: rounded, full facial proportions; blush distributed over cheek surfaces and sparse fine diagonal hatching, not two disconnected cosmetic streaks.
- **Linework**: organic ink pressure shifts, thicker upper eye contours, lighter facial detail; do not geometrically perfect, vector-smooth, or mechanically symmetrize.

## Hard suppression

Suppress: generic anime face, generic modern moe eyes, circular eye opening, symmetrical doll eyes, uniform magenta iris, huge glassy highlights, centered dot pupils, blank round stare, stock happy smile, symmetric large open U/D mouth, pristine unblushed cheeks, two standalone cheek lines, uniform vector line widths, airbrushed plastic skin, unsolicited changes to hair color or accessories.

**Subject fidelity and user instruction outrank defaults.** Do not impose pink hair, an accessory, clothing, a character identity, a pose, or a fixed expression on unrelated requests. If the user supplies an image explicitly, use that image as the reference. This skill cannot access reference images on its own; **do not claim the face is pixel-locked**.

## Usage

To build prompt payload:

```bash
python3 skills/creative/pink-manga-face-lock/scripts/lock_prompt.py \
  --subject 'character from reference' \
  --composition 'keep the supplied pose' \
  --outfit 'keep the supplied clothing' \
  --base-positive 'my existing image prompt'
```

The JSON output contains `positive_prompt`, `negative_prompt`, and `prompt_only_prompt`.

- If the existing API supports `negative_prompt`, pass positive and negative separately.
- **Hermes default `image_generate` is prompt-only** in the current repository: pass `prompt_only_prompt` as the single prompt; otherwise the negative instructions would be discarded.
- If the provider actually accepts reference-image conditioning, send the user's source image there as well. The default prompt-only pathway does **not** accept it.
- Before generating another test image, ask if the user actually wants one. If the user requested a ZIP, skill or GitHub update, do only that.

## Evaluation gate

Check eye opening, upper eyelid, iris structure, and coordinated expression **before** claiming an improvement. Secondary checks: mouth, blush placement, line rhythm, face ratios. Never claim automatic visual validation: script only assembles text; human inspection (or a separate image analysis process) is necessary.

## Privacy and limits

The original reference and comparison crops are intentionally **not committed** to this public repository. Keep private user artwork in their private workspace or a private asset store. This package does not train models, alter model weights, add GPUs, or add paid APIs. Prompt-only constraints may be ignored by an image generator; for genuine high-fidelity style transfer the actual generator must support reference conditioning, suitable training, or controlled editing.
