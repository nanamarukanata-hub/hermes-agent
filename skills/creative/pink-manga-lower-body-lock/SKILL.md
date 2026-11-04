---
name: pink-manga-lower-body-lock
description: "Reference-first illustration constraints for adult lower-body anatomy: soft belly volume, thick inner thighs, pelvic structure, vulva detail, localized gloss, blush and fluid styling in a simplified manga line-and-fill rendering."
version: 0.1.0
metadata:
  hermes:
    tags: [creative, manga, illustration, anatomy, reference-style, shading, lower-body]
    category: creative
---

# Pink Manga Lower Body Lock

## Purpose

Use this skill when the user wants a **reference-faithful adult lower-body illustration** matching the visual logic of the supplied manga image. It is an **art-direction skill only**. It does not render images, train weights, or guarantee exact visual replication.

This skill is intended for **adult-character anatomy only**. It should not be used to introduce explicit anatomy into ambiguous or youthful depictions.

## Core style target

The reference emphasizes:

- a **soft, rounded lower abdomen** with visible weight and gentle central fullness
- **thick upper thighs** with wide inner-thigh planes and smooth tapering curves
- a **simplified manga treatment of the vulva** using clean linework, warm pink gradients, and localized gloss
- **minimal but meaningful contour lines**, with sparse crease marks rather than dense realistic detail
- **small specular highlights, blush zones, and fluid accents** placed locally rather than spread everywhere

## Lower-abdomen grammar

1. **Belly shape**
   - Rounded, forward-soft abdomen with visible mass and a broad smooth surface.
   - The lower belly should feel heavy and cushioned, not athletic-flat or sharply segmented.
   - Preserve the reference's single broad belly form with very soft value transitions.
   - Keep the navel small and understated, with a tiny notch-like or slit-like indication rather than deep realistic cavity detail.

2. **Belly shading and blush**
   - Use very light ivory-to-peach skin with a gentle pink blush concentrated around the navel and central abdomen.
   - Shading remains low-contrast and air-light, never harsh or sculpted.
   - Avoid six-pack logic, dramatic underlighting, or hard cel-shaded stomach planes.

3. **Belly lines and folds**
   - A few fine crease lines may indicate softness or compression, especially where forms bunch slightly.
   - Lines should be thin and tapered, not dark structural grooves.
   - Preserve the reference's overall uncluttered surface.

## Thigh grammar

1. **Overall thigh volume**
   - Thighs should read as **thick, fleshy, and expansive**, with strong inward fullness toward the groin.
   - The outer contours should be broad and smooth.
   - The inner-thigh gap is minimal to absent; the emphasis is on compression and contact.

2. **Surface rendering**
   - Use wide clean skin surfaces with sparse crease lines near the hip/groin transitions.
   - Add only subtle tonal modeling; do not over-render muscles or kneecap anatomy when outside the crop.
   - Keep the reference's simplified manga silhouette over realism.

3. **Compression and softness**
   - Where thighs press toward the pelvis, the contours may flatten or bulge slightly.
   - Favor soft tissue logic rather than geometric symmetry.
   - Do not make both sides identical; preserve mild asymmetry.

## Genital-area grammar

1. **Overall treatment**
   - The vulva is stylized, simplified, and line-driven, not hyper-realistic.
   - Preserve a clear central opening structure with surrounding soft pink tissue and narrow crease definition.
   - Do not turn the anatomy into a generic symbol or a highly realistic medical rendering.

2. **Labial structure**
   - Use a delicate vertical arrangement with a central darker opening and softly pink outer tissue.
   - Linework should define the structure economically.
   - Outer tissue transitions should be smooth and lightly blushed, not heavily outlined with thick black borders.

3. **Color and gradients**
   - Tissue color should move from pale pink to deeper rosy pink near the opening.
   - The darker interior can be used for depth, but only within the opening area.
   - Avoid neon pink or oversaturated red.

4. **Gloss and moisture**
   - Highlights must be **localized and wet-looking**, placed as small vertical or curved white reflections close to the tissue surfaces.
   - Moisture should feel present but stylized; avoid turning the whole pelvic area into a glossy plastic sheet.
   - Preserve the reference's balance between softness, gloss, and line clarity.

5. **Fluid depiction**
   - If the user explicitly wants to match the reference, fluid can be shown as opaque white streams and droplets with smooth, gravity-led shapes.
   - Strands should feel organic and slightly uneven, not perfectly mirrored.
   - Keep fluid highlights simple and readable.
   - Do not add fluid unless requested or present in the source logic.

## Global skin rendering

- Base skin tone: pale ivory / warm peach.
- Blush placement: navel area, inner thighs, and tissue-adjacent zones where the reference indicates warmth.
- Gloss: small concentrated white reflections on skin bulges and wet surfaces.
- Contours: thin black manga outlines with light variation in weight.
- Secondary details: occasional small droplets or sweat marks only when they support the reference.

## Suppress common drift

Suppress:

- flat or athletic abdomen
- narrow thighs or strong thigh gap
- hard cel-shading blocks
- overly realistic genital anatomy
- thick black outline rings around soft tissue
- uniform glossy coating over all skin
- oversized white glare patches
- identical mirrored thigh contours
- heavy muscle definition
- plastic balloon-like body forms
- random extra explicit detail outside the crop
- unrequested fluids or unrequested sexual-action cues

## Prompt assembly guidance

When building a generation prompt, prioritize this structure:

1. **Reference-first statement**
2. **Belly shape and shading**
3. **Thigh volume and compression**
4. **Genital structure, color, and gloss**
5. **Optional fluid behavior if explicitly desired**
6. **Negative / suppression rules**

Suggested lead-in:

> REFERENCE-FIRST ADULT MANGA LOWER-BODY ILLUSTRATION. Preserve the soft heavy lower belly, thick inner thighs, and simplified vulva rendering of the supplied reference. Use pale peach skin, gentle central blush, delicate linework, localized glossy highlights, and soft pink tissue gradients. Keep the anatomy fleshy and rounded, not athletic or generic. Maintain the crop and composition, and preserve any explicitly requested fluid shapes as stylized opaque white strands and droplets.

## Manual validation checklist

1. Does the abdomen feel broad, soft, and heavy rather than flat?
2. Are the thighs thick and compressive, with minimal gap and smooth inner curves?
3. Is the genital rendering simplified and manga-stylized rather than hyper-realistic?
4. Are highlights small and localized instead of spread everywhere?
5. Are blush and pink gradients placed naturally around the navel, inner thighs, and tissue?
6. If fluid is present, does it follow gravity and remain stylized rather than chaotic?
7. Does the whole crop preserve the reference's softness and simplified line economy?

## Notes

- This public skill intentionally excludes the original reference image.
- For actual image-conditioned matching, the private reference image must be supplied separately to a compatible image model.
- This skill is designed to be paired selectively with other locks such as `pink-manga-face-lock` or `pink-manga-bust-form-lock` when the user asks for full-body consistency.