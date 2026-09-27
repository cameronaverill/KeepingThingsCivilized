"""Fixtures for the README checks (tests/readme_check/). Nothing imports from this file; helpers live in readme_kit.py.

These tests only read files (README.md, .env.example, config/tunables.py, the repository tree). They never touch the
database, the network or the real LLM client, and never read .env.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(scope="session")
def readme():
    import readme_kit

    return readme_kit.Readme.load()
