"""Pytest fixtures for the secret-protection tests. Helper code lives in repo_helpers.py: two conftest.py files
(this one and tests/moderation/) cannot both be imported as `conftest`, so nothing imports from here."""
import pytest

from repo_helpers import GitRepo, _LazyScanner


@pytest.fixture(scope="session")
def scanner():
    return _LazyScanner()


@pytest.fixture
def repo(tmp_path):
    return GitRepo(tmp_path / "work")
