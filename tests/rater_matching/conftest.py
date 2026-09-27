"""Fixtures for the step 14b tests (tests/rater_matching/). Nothing imports from this file; helpers live in matching14b_kit.py.

Matching never calls an LLM: a real Anthropic client can never be built here (the builder is replaced by a function that
fails the test) and the kill switch is off. Every test may use the database.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(autouse=True)
def _matching_environment(settings, monkeypatch, db):
    from moderation import llm

    settings.LLM_ENABLED = False
    settings.ANTHROPIC_API_KEY = ""

    def blocked(*args, **kwargs):
        raise AssertionError("a real Anthropic client was built in a test")

    monkeypatch.setattr(llm, "_build_real_client", blocked)
    llm.reset_client()
    yield
    llm.reset_client()
