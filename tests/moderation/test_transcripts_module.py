"""moderation/transcripts.py (the validator moved out of the retired spike command) and the kept golden fixtures (cleanup 2)."""
from pathlib import Path

import pytest
from django.core.management import get_commands

KEPT = ["sanctuary_factual_left", "sanctuary_factual_right", "single_injection"]


def test_exactly_three_golden_transcripts_are_kept():
    folder = Path(__file__).resolve().parents[2] / "golden" / "transcripts"
    assert sorted(p.stem for p in folder.glob("*.json")) == KEPT


def test_the_validator_accepts_the_three_kept_transcripts():
    from moderation import transcripts

    files = transcripts.load_transcript_files()
    assert sorted(path.stem for path, _data in files) == KEPT
    known = {data["id"]: data for _path, data in files}
    for path, data in files:
        assert transcripts.validate_transcript(path, data, known_ids=known) is None


def test_load_transcripts_returns_the_three_by_id():
    from moderation import transcripts

    assert sorted(transcripts.load_transcripts()) == KEPT


def test_the_kept_sanctuary_files_are_a_left_right_pair():
    from moderation import transcripts

    loaded = transcripts.load_transcripts()
    left, right = loaded["sanctuary_factual_left"], loaded["sanctuary_factual_right"]
    assert (left["pair_id"], right["pair_id"]) == ("sanctuary_factual",) * 2
    assert (left["variant"], right["variant"]) == ("left", "right")


def test_the_validator_rejects_a_transcript_missing_its_trigger():
    from django.core.management.base import CommandError

    from moderation import transcripts

    data = {"topic": {"title": "t", "proposition": "p"}, "messages": [{"seq": 1, "author": "Participant A", "text": "hi", "planted": []}]}
    with pytest.raises(CommandError):
        transcripts.validate_transcript(Path("x.json"), data)


def test_the_spike_command_is_no_longer_registered():
    assert "spike" not in get_commands()


def test_the_spike_module_and_the_mechanical_series_module_are_gone():
    import importlib

    for name in ("moderation.management.commands.spike", "moderation.series"):
        with pytest.raises(ImportError):
            importlib.import_module(name)
