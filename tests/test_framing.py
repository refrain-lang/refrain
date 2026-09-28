# Copyright 2026 Refrain Language Authors. Apache-2.0.
"""Framing gate for the biosignal reframe.

Refrain describes biosignal *training paradigms*, not specifically clinical
neurofeedback. These assertions pin the wording on the living, user-facing
surfaces so the category framing cannot quietly come back.

Dated specs and plans under docs/superpowers/ are historical record and are
deliberately NOT covered here.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Living, user-facing surfaces. Everything else in the tree is either code,
# tests, or dated historical record.
LIVING_DOCS = [
    "README.md",
    "pyproject.toml",
    "src/refrain/__init__.py",
    "docs/CONCEPT.md",
    "docs/SPEC.md",
    "docs/TOUR.md",
    "docs/PRIMITIVES.md",
    "docs/EMBEDDING.md",
    "docs/IR-JSON.md",
    "docs/RUST-CORE-HOST-BRIEF.md",
    "CONTRIBUTING.md",
]

# Role words. The general-wellness envelope has practitioners, and the person
# training is a person -- these are swept across the same living set so the
# normative spec and the host-facing docs cannot disagree about one contract.
RETIRED_ROLE_WORDS = ["clinician", "patient"]

# Phrases that name the OLD category. Substring match, case-insensitive.
# These are phrases, not single words: "clinical" alone is legitimate in
# "not a medical device" and in the `research_amp` hardware discussion.
RETIRED_PHRASES = [
    "clinical neurofeedback protocols",
    "clinical NF protocols",
    "for clinical neurofeedback",
]


@pytest.mark.parametrize("rel", LIVING_DOCS)
def test_no_retired_category_phrases(rel):
    text = (ROOT / rel).read_text(encoding="utf-8").lower()
    for phrase in RETIRED_PHRASES:
        assert phrase.lower() not in text, (
            f"{rel}: retired category phrase {phrase!r} — Refrain describes "
            f"biosignal training paradigms, not specifically clinical NF."
        )


@pytest.mark.parametrize("rel", LIVING_DOCS)
def test_no_retired_role_words(rel):
    text = (ROOT / rel).read_text(encoding="utf-8").lower()
    for word in RETIRED_ROLE_WORDS:
        assert word not in text, (
            f"{rel}: {word!r} — living docs say 'practitioner' and 'the person "
            f"training'. Leaving one doc on the old vocabulary makes the spec "
            f"and the host-facing docs disagree about the same contract."
        )


def test_readme_carries_the_new_subtitle():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "An open description language for biosignal training paradigms." in text


def test_readme_states_general_wellness_positioning():
    text = (ROOT / "README.md").read_text(encoding="utf-8").lower()
    assert "general-wellness" in text, "README must state the general-wellness envelope"
    assert "not a medical device" in text, "the honest disclaimer stays"
