#!/usr/bin/env python3
"""Build a reference-first face-style prompt; no image generation or external calls."""
import argparse
import json
import re

BLOCKS = [
    "REFERENCE STYLE, NOT GENERIC ANIME. Reproduce the distinctive facial drawing grammar of the user's supplied REFERENCE. Maintain its face proportions, eyelid angles, iris construction, mouth silhouette, cheek blush, asymmetry, and variable-pressure linework. Do not beautify or genericize.",
    "EYES: narrow horizontally elongated half-lidded red eyes; upper lids heavy, dark and tapered, pointed outer lashes; shallow sclera; slight left-right asymmetry. Compact crimson irises with shaded tops and uneven narrow reflected highlights; no circular uniform iris discs.",
    "EXPRESSION: sly relaxed teasing grin, upper eyelids, eyebrows and mouth coordinated, rounded cheeks with broad diffuse blush and fine diagonal hatching, small crooked parted mouth and a tiny fang; not a cheerful wide-eyed open smile.",
    "LINES: organic variable ink pressure, strong upper-eye contour, delicate lower-eye and mouth accents. Preserve small hand-drawn irregularities rather than mechanically perfecting or vector-cleaning. Keep reference hairstyle, accessories, pose and costume unchanged unless explicitly requested.",
]
SUPPRESS = [
    "generic anime face", "large circular open eyes", "wide round-eyed expression", "symmetrical doll eyes",
    "uniform flat magenta iris", "single black dot pupil", "big glassy eye highlights",
    "large symmetric U-shaped mouth", "large symmetric D-shaped mouth", "stock innocent happy smile",
    "two isolated blush lines", "perfect facial symmetry", "uniform vector-clean line width",
    "airbrushed plastic shading", "unsolicited hair or accessory changes",
]
CONFLICTS = [
    "wide open circular eyes", "big round eyes", "large round eyes", "bright innocent anime eyes",
    "perfect symmetrical eyes", "huge shiny eyes", "generic kawaii face", "large u-shaped mouth",
    "cute big smile", "generic anime face", "uniform vector linework",
]


def build(subject, composition="", outfit="", base_positive="", base_negative=""):
    fields = (subject, composition, outfit, base_positive, base_negative)
    if any(not isinstance(v, str) or len(v) > 16000 for v in fields):
        raise ValueError("each input must be a string of at most 16000 characters")
    if not subject.strip():
        raise ValueError("subject must not be empty")
    legacy = base_positive.strip()
    removed = []
    for phrase in CONFLICTS:
        pattern = r"(?i)(?<![A-Za-z])" + re.escape(phrase) + r"(?![A-Za-z])"
        if re.search(pattern, legacy):
            removed.append(phrase)
            legacy = re.sub(pattern, "", legacy)
    legacy = re.sub(r"\s*,\s*,+", ", ", legacy).strip(" ,")
    positive = "\n\n".join(["[HIGHEST PRIORITY: REFERENCE FACE STYLE]"] + BLOCKS +
                            ["[KEEP SUBJECT, POSE, CLOTHES]", subject.strip(), composition.strip(),
                             outfit.strip(), "[OTHER NONCONFLICTING REQUESTS]", legacy])
    negative = ", ".join(SUPPRESS + ([base_negative.strip()] if base_negative.strip() else []))
    prompt_only = positive + "\n\n[REJECT THE FOLLOWING CHANGES — DO NOT DRAW THEM]\n" + negative
    return {
        "positive_prompt": positive,
        "negative_prompt": negative,
        "prompt_only_prompt": prompt_only,
        "removed_conflicts": removed,
        "reference_image_attached": False,
        "rendered": False,
        "automatic_style_validation": False,
        "note": "Prompt-only suppression is not a pixel-level reference lock; pass original image if the actual provider supports image conditioning.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("subject", "composition", "outfit", "base-positive", "base-negative"):
        parser.add_argument("--" + key, default="", required=key == "subject")
    args = parser.parse_args()
    print(json.dumps(build(args.subject, args.composition, args.outfit,
                           args.base_positive, args.base_negative), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
