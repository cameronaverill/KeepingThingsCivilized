"""Step 21, part 2: the one new tunable."""
import re
from pathlib import Path

import dim21_kit as dk

ROOT = Path(__file__).resolve().parents[2]


def test_the_default_is_four_and_is_an_int():
    from config import tunables

    assert tunables.AGREEMENT_MAP_EVERY_N_USER_MESSAGES == 4
    assert type(tunables.AGREEMENT_MAP_EVERY_N_USER_MESSAGES) is int


def test_django_settings_carry_it():
    from django.conf import settings

    assert settings.AGREEMENT_MAP_EVERY_N_USER_MESSAGES == 4


def test_the_tunable_has_an_explaining_comment_that_mentions_off():
    text = (ROOT / "config" / "tunables.py").read_text(encoding="utf-8")
    lines = text.splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith("AGREEMENT_MAP_EVERY_N_USER_MESSAGES"))
    comment = " ".join(lines[max(0, index - 4):index + 1]).lower()
    assert "n-th" in comment or "nth" in comment or "every n" in comment
    assert "0" in comment and "none" in comment


def test_the_tunable_name_appears_in_the_readme_table():
    assert "AGREEMENT_MAP_EVERY_N_USER_MESSAGES" in (ROOT / "README.md").read_text(encoding="utf-8")


def test_the_pipeline_source_does_not_hard_code_the_cadence():
    source = (ROOT / "moderation" / "pipeline.py").read_text(encoding="utf-8")
    assert dk.TUNABLE in source
    assert not re.search(r"%\s*4\b", source)
