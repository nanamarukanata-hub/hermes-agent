---
name: pink-manga-bust-form-lock
description: "Reference-first illustration constraints for adult bust shape, nipple/areola detail, pink skin gradients, specular highlights and soft manga ink lines; use only when the user explicitly asks for this anatomy study."
version: 0.1.0
metadata:
  hermes:
    tags: [creative, manga, illustration, anatomy, reference-style, shading]
    category: creative
---

# Pink Manga Bust Form Lock

## Purpose

Translate a provided **adult-character** manga bust reference into faithful, reusable drawing guidance. Combine with `pink-manga-face-lock` only if the user explicitly asks for both. This is a **textual art-direction skill**, not an image renderer, weight adapter, image-to-image conditioner, or training dataset.

The user's uploaded drawing is the reference authority; do not substitute generic manga anatomy or change the pose, framing, body proportions, or clothing independently. If a figure's age is ambiguous or youthful, do not create exposed nipple/areola content.

## Visual grammar from the user's current reference

1. **Breast silhouette:** paired, soft rounded upper masses with organic, slightly irregular outlines. The curving borders near the chest/axilla are tapered, not mechanically mirrored. The central lower region has subtle horizontal skin-fold lines indicating compressible volume rather than hard straight separations.
2. **Nipple and areola:** small rounded, warm-pink areolar regions near the upper outer parts of the current crop, with softly uneven transition into peach skin. Keep the nipple mound subtly raised in the center and define it through controlled hue shifts, a tiny contour or local cast shadow, not a bold black ring or large hard disk. Preserve the reference's stylized proportions; do not automatically enlarge, darken or sharpen anatomy.
3. **Areola boundary:** avoid a single hard-edged geometric circular decal. Use a diffused rosy circumference and gentle tonal variation, a slightly warmer center, with selective fine linework only where the reference clearly supports it.
4. **Highlights and gloss:** highlights are concentrated, small pale white-to-pink heart-like marks at the upper areolar highlights, blending into a broader but soft sheen over the surrounding warm skin. Preserve tiny wet-looking glints and sweat-drop accents where actually visible. Avoid covering the entire breast with uniform shiny plastic gloss, metallic gradients, or large white glare patches.
5. **Skin gradients:** luminous ivory/peach base, gently desaturated pink blush at the raised forms, gradual falloff toward the belly/chest center; low-contrast, soft, round gradients. Avoid uniform flat flesh color, overdone airbrushing, and high-frequency pore texture.
6. **Inked contours and compression:** variable-weight thin black linework, tapered hatching at compressive folds, minimal construction lines; emphasize soft deformation, not rigid circles and symmetry.
7. **Crop and context:** reference is a tight close-up. Cropping does not authorize inventing unobserved body parts, nudity elsewhere, character identity, sexual actions or a background scene. Keep the user's requested framing.

## Priority order

- Provided reference anatomy and locally distinctive ink style
- Requested pose, framing, clothing and degree of exposure
- Nipple/areola geometry and highlight placement
- Skin gradients and fold lines
- Optional decorative details (small accent glints, sweat), only if in reference or requested

## Suppress common image-generation drift

- mirrored circular breasts with rigid identical curvature
- exaggerated nipple projection or enlarged areola
- thick outlined rings or hard cutout areola disks
- oversaturated uniformly bright pink coloring
- nipple completely missing when the request explicitly requires one
- uniform oily/plastic gloss across the whole torso
- oversized hard white glares in unrelated areas
- flat, unmodeled skin and repetitive texture
- generic anime cel shading replacing smooth reference gradients
- floating decorative heart graphics detached from the painted surface
- inventing additional nudity or explicit sexual activity

Do not claim this negative list actually controls a provider unless it is passed to the provider through an interface it supports.

## Prompt assembly

When prompted for a **permitted adult anatomy illustration**, prepend:

> REFERENCE-FIRST MANGA ANATOMY STUDY. Preserve the original curved breast outline, soft tissue folds and non-mirrored contours. Render small softly blended rosy areolae and gently modeled nipple forms with delicate shading and no bold circular stamp. Preserve fine variable-weight ink lines. Use a pale peach skin gradient, restrained blush on elevated forms, and small pale heart-like specular highlights placed where they occur in the reference. Keep the style, anatomy, and framing faithful, not genericized. No unsolicited alterations to clothing, pose or exposure.

Attach suppression terms above only as `negative_prompt` if supported, or describe the constraints inside a single positive prompt for Hermes' default prompt-only image generation.

## Validation checklist (manual / vision model)

1. Do the left and right contours preserve the reference's slightly different bends?
2. Are the areolae softly transitioned into skin, with appropriately small nipple protrusion?
3. Are gloss accents **localized** rather than a uniform oiled-skin effect?
4. Are heart-like highlights actually within the reference's areolar sheen, not free-floating symbols?
5. Do folds use thin tapered ink marks rather than black geometric lines?
6. Did the output retain the user's crop and avoid extra nudity or unrequested scene elements?

A checklist is not a visual test or proof of model compliance. The source reference image is deliberately excluded from this public GitHub repository; for actual reference-image conditioning it must be supplied privately to a compatible image generation service.
