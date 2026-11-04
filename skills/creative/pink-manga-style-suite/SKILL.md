---
name: pink-manga-style-suite
description: "Route reference-first pink manga illustration requests to face, bust or lower-body anti-drift drawing Skills; combine only the regions the user actually requests."
version: 0.1.0
metadata:
  hermes:
    tags: [creative, manga, illustration, reference-style, consistency, routing]
    category: creative
---

# Pink Manga Reference-First Style Suite

## Scope and linked Skills

This is the **entry point** for the user's three previously created illustration-style locks. Keep the specialized Skills separate, but apply them together when an adult full-body reference demands it:

- `../pink-manga-face-lock/SKILL.md`: eyes, eyelids, irises, expression, blush, mouth, line rhythm.
- `../pink-manga-bust-form-lock/SKILL.md`: bust silhouette, areola/nipple detail if specifically requested for an adult subject, skin transitions and localized highlights.
- `../pink-manga-lower-body-lock/SKILL.md`: soft belly, upper-thigh volume, stylized pelvic anatomy if specifically requested for an adult subject, local color/gloss.

## Selection rules

1. Always start by analyzing the **user-supplied REFERENCE**, not an imagined generic anime style.
2. **Eyes/face only**: activate face-lock only.
3. **Chest/bust only**: activate bust-form-lock only when requested.
4. **Belly/thighs/lower-body only**: activate lower-body-lock only when requested.
5. **Full character**: activate relevant locks by visible region and user intent, retaining original proportions, outfit, pose, crop, and character identity.
6. Do not insert explicit nudity or genital details into ordinary portraits or otherwise non-explicit scenes. Use adult anatomy details only when expressly requested for an adult depiction.
7. Preserve existing prompt content that does not conflict with the supplied reference. Prioritize the reference over generic beautifying instructions.

## Prompt order

```
REFERENCE FIRST > region-specific outline/structure > ink pressure and shapes
> skin hue, gradients and highlights > composition and remaining scene details
> suppression constraints
```

## Quality gate

- Do not confuse prompt adherence with proof of image similarity.
- Compare outputs visually against the actual reference, region by region.
- Detect style drift: uniform vector outlines, genericized features, hard symmetry,
  incorrect body volume, plastic sheen, wrong placement of highlights.
- Give the user a concise mismatch report and proposed edit before generating
  another image, unless the user specifically requests generation.
- The current Hermes prompt-only `image_generate` cannot truly image-condition
  on the reference. Use the reference in the provider only if supported.
- These Skills do not install an AI image model, retrain weights or require additional APIs.

## Image privacy

Keep original user reference images outside this public repository. This suite
contains instructions only. User-supplied images require explicit separate handling
in a private image-capable workflow.
