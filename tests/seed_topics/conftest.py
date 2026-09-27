"""Fixtures for the step 10a tests (tests/seed_topics/). Nothing imports from this file; helpers live in
seedtopics_kit.py."""
import pytest


@pytest.fixture(autouse=True)
def _seedtopics_state(db, settings):
    """Database access for every test, and the worker mode so nothing here can reach the moderation pipeline."""
    settings.MODERATION_RUN_MODE = "worker"
