"""Independent break-it checks of `manage.py spike` against a bad transcript folder, and secret hygiene of its outputs.

The folder is redirected to a temp directory (spike.TRANSCRIPTS_DIR); the real golden/ files are only read. (The five
"clean error" cases were found failing in the first review pass and fixed by the building agent; they are plain tests now.)
"""
import json
import secrets
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from step3_testkit import (
    TRANSCRIPT_DIR,
    Scripted,
    find_pair,
    load_transcripts,
    real_results_untouched,  # noqa: F401
    run_spike,
    spike_env,  # noqa: F401
)


@pytest.fixture
def folder(spike_env, install_fake, tmp_path, monkeypatch):  # noqa: F811
    """A temp copy of the golden transcripts that the command is pointed at, plus a scripted client over the real set."""
    from moderation.management.commands import spike

    directory = tmp_path / "transcripts"
    shutil.copytree(TRANSCRIPT_DIR, directory)
    monkeypatch.setattr(spike, "TRANSCRIPTS_DIR", directory)
    golden = load_transcripts()
    scripted = Scripted(golden)
    client = scripted.install(install_fake)
    left, right = find_pair(golden, "factual_accuracy")
    return SimpleNamespace(dir=directory, client=client, scripted=scripted, left=left["id"], right=right["id"], out=str(tmp_path / "res"), golden=golden)


def go(folder, *extra):
    return run_spike("--max-usd", "5", "--out", folder.out, *extra)


def edit(folder, tid, fn):
    path = folder.dir / f"{tid}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_a_clean_copy_of_the_folder_runs(folder):
    result = go(folder, "--only", folder.left)
    assert result.exc is None
    assert len(folder.client.calls) == 2


def test_an_unknown_extra_key_in_a_transcript_is_tolerated(folder):
    edit(folder, folder.left, lambda d: d.update(reviewer_note="hello"))
    result = go(folder, "--only", folder.left)
    assert result.exc is None and len(folder.client.calls) == 2


def test_extra_keys_in_the_transcript_never_reach_the_model(folder):
    edit(folder, folder.left, lambda d: d.update(reviewer_note="SECRET-NOTE-XYZ", description="DESC-XYZ-LEAK"))
    go(folder, "--only", folder.left)
    sent = json.dumps([c["messages"] for c in folder.client.calls], default=str)
    assert "SECRET-NOTE-XYZ" not in sent and "DESC-XYZ-LEAK" not in sent


def test_planted_annotations_never_reach_the_model(folder):
    go(folder, "--only", folder.left)
    sent = json.dumps([c["messages"] for c in folder.client.calls], default=str)
    planted = [p for m in folder.golden[folder.left]["messages"] for p in m["planted"]][0]
    assert planted["correction"] not in sent and planted["evidence"] not in sent
    assert "planted" not in sent.lower()


def test_a_malformed_json_file_is_reported_as_a_clean_error_naming_the_file(folder):
    (folder.dir / "broken_one.json").write_text('{"id": "broken_one", "messages": [', encoding="utf-8")
    result = go(folder, "--only", folder.left)
    assert result.exc is not None and "broken_one" in str(result.exc)
    assert folder.client.calls == []


def test_a_duplicate_id_in_two_files_is_an_error(folder):
    shutil.copy(folder.dir / f"{folder.left}.json", folder.dir / "zz_copy.json")
    result = go(folder, "--only", folder.left)
    assert result.exc is not None and folder.left in str(result.exc)
    assert folder.client.calls == []


def test_a_transcript_with_a_missing_required_key_is_a_clean_error(folder):
    edit(folder, folder.left, lambda d: d.pop("topic"))
    result = go(folder, "--only", folder.left)
    assert result.exc is not None
    assert folder.left in str(result.exc)
    assert folder.client.calls == []


def test_a_trigger_seq_that_is_not_a_message_is_a_clean_error(folder):
    edit(folder, folder.left, lambda d: d.update(trigger_seq=999))
    result = go(folder, "--only", folder.left)
    assert result.exc is not None and folder.left in str(result.exc)
    assert folder.client.calls == []


def test_a_message_over_max_message_chars_is_an_error(folder, settings):
    edit(folder, folder.left, lambda d: d["messages"][0].update(text="word " * (settings.MAX_MESSAGE_CHARS // 4)))
    result = go(folder, "--only", folder.left)
    assert result.exc is not None and folder.left in str(result.exc)
    assert folder.client.calls == []


# --- secrets ------------------------------------------------------------------------------------------------------------

def test_the_api_key_never_appears_in_the_outputs_the_ledger_or_the_report(folder, settings):
    key = "zz" + secrets.token_hex(16)  # built at run time, dummy
    settings.ANTHROPIC_API_KEY = key
    result = go(folder, "--only", folder.left, folder.right)
    assert result.exc is None
    from moderation.models import LLMCall

    blob = result.text
    for path in Path(folder.out).rglob("*"):
        if path.is_file():
            blob += path.read_text(encoding="utf-8")
    for row in LLMCall.objects.all():
        blob += json.dumps([row.request, row.raw_response, row.parsed, row.error], default=str)
    blob += json.dumps([[c["model"], c["messages"], str(c["system"])] for c in folder.client.calls], default=str)
    assert key not in blob


def test_the_api_key_is_not_leaked_by_a_failing_call_either(folder, settings):
    key = "zz" + secrets.token_hex(16)
    settings.ANTHROPIC_API_KEY = key
    folder.scripted.fail[(folder.left, "master")] = "provider"
    result = go(folder, "--only", folder.left)
    blob = result.text + "".join(p.read_text(encoding="utf-8") for p in Path(folder.out).rglob("*") if p.is_file())
    assert key not in blob


def test_a_transcript_id_cannot_write_results_outside_the_out_directory(folder, tmp_path):
    """The id becomes the result file name: an id with a path in it must be refused, never used to escape --out."""
    edit(folder, folder.left, lambda d: d.update(id="../escaped_result"))
    result = go(folder, "--only", "../escaped_result")
    assert not (tmp_path / "escaped_result.json").exists(), "a result file was written outside --out"
    assert result.exc is not None
