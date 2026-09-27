"""Step 9a: `manage.py export_conversation` and `manage.py export_all` (brief 9a), driven with `call_command`.

The commands write to stdout or to files under pytest's tmp folder; they never touch the network, the LLM or `.env`."""
import builtins
import io
import json
import os
from pathlib import Path

import exports_kit as kit
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

pytestmark = pytest.mark.django_db

REPO = Path(__file__).resolve().parents[2]
COMMAND_FILES = [
    REPO / "moderation" / "management" / "commands" / "export_conversation.py",
    REPO / "moderation" / "management" / "commands" / "export_all.py",
    REPO / "moderation" / "queries.py",
]


def run(name, *args):
    out, err = io.StringIO(), io.StringIO()
    call_command(name, *[str(a) for a in args], stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


def read(path):
    return Path(path).read_text(encoding="utf-8")


def names(directory):
    return sorted(p.name for p in Path(directory).iterdir())


def hits(text):
    return [s for s in kit.IDENTITY_STRINGS if s in text]


def canon_listing(rows):
    return json.loads(json.dumps(rows))


class TestExportConversationToStdout:
    def test_stdout_is_the_export_text_exactly(self, world):
        out, err = run("export_conversation", world.h.pk)
        assert out == kit.export_text(world.h)
        assert err == ""

    def test_the_output_is_json_for_that_conversation(self, world):
        out, _ = run("export_conversation", world.s.pk)
        assert json.loads(out)["conversation"]["id"] == world.s.pk

    def test_default_output_holds_no_identity(self, world):
        out, _ = run("export_conversation", world.h.pk)
        assert hits(out) == []

    def test_include_identities(self, world):
        out, _ = run("export_conversation", world.h.pk, "--include-identities")
        assert out == kit.export_text(world.h, include_identities=True)
        assert {kit.USER_A["username"], kit.USER_A["email"], kit.USER_B["username"], kit.USER_B["email"]} <= set(hits(out))

    def test_include_raw(self, world):
        out, _ = run("export_conversation", world.h.pk, "--include-raw")
        assert out == kit.export_text(world.h, include_raw=True)
        assert "not json at all" in out
        assert "not json at all" not in run("export_conversation", world.h.pk)[0]

    def test_both_flags(self, world):
        out, _ = run("export_conversation", world.h.pk, "--include-identities", "--include-raw")
        assert out == kit.export_text(world.h, include_identities=True, include_raw=True)

    def test_raw_output_still_holds_no_identity_without_the_flag(self, world):
        out, _ = run("export_conversation", world.h.pk, "--include-raw")
        assert hits(out) == []

    def test_changes_no_row(self, world):
        before = kit.db_state()
        run("export_conversation", world.h.pk, "--include-identities", "--include-raw")
        assert kit.db_state() == before


class TestExportConversationToFile:
    def test_output_writes_the_same_text(self, world, tmp_path):
        target = tmp_path / "one.json"
        out, err = run("export_conversation", world.h.pk, "--output", target)
        assert read(target) == kit.export_text(world.h)
        assert hits(out + err) == []

    def test_the_file_honours_the_flags(self, world, tmp_path):
        target = tmp_path / "both.json"
        run("export_conversation", world.h.pk, "--include-identities", "--include-raw", "--output", target)
        assert read(target) == kit.export_text(world.h, include_identities=True, include_raw=True)

    def test_stdout_does_not_repeat_the_json_when_a_file_is_written(self, world, tmp_path):
        out, _ = run("export_conversation", world.h.pk, "--output", tmp_path / "quiet.json")
        assert "\"conversation\"" not in out

    def test_non_ascii_text_is_written_as_utf8(self, world, tmp_path):
        target = tmp_path / "unicode.json"
        run("export_conversation", world.h.pk, "--output", target)
        raw = target.read_bytes()
        assert "日本語".encode("utf-8") in raw

    def test_parent_folders_are_created_and_an_existing_file_is_replaced(self, world, tmp_path):
        target = tmp_path / "deep" / "er" / "x.json"
        run("export_conversation", world.h.pk, "--output", target)
        target.write_text("stale", encoding="utf-8")
        run("export_conversation", world.h.pk, "--output", target)
        assert read(target) == kit.export_text(world.h)


class TestExportConversationErrors:
    def test_a_missing_conversation_is_a_clear_command_error(self, world):
        with pytest.raises(CommandError) as caught:
            run("export_conversation", 987654)
        assert "987654" in str(caught.value)
        assert "Traceback" not in str(caught.value)
        assert "DoesNotExist" not in str(caught.value)

    def test_a_missing_conversation_writes_no_file(self, world, tmp_path):
        target = tmp_path / "nothing.json"
        with pytest.raises(CommandError):
            run("export_conversation", 987654, "--output", target)
        assert not target.exists()

    def test_a_missing_conversation_exits_non_zero_from_the_command_line(self, world, monkeypatch, capsys):
        from django.db import connections

        monkeypatch.setattr(connections, "close_all", lambda: None)  # keep the test database open
        from django.core.management import load_command_class

        command = load_command_class("moderation", "export_conversation")
        with pytest.raises(SystemExit) as caught:
            command.run_from_argv(["manage.py", "export_conversation", "987654", "--skip-checks"])
        assert caught.value.code != 0
        assert "987654" in capsys.readouterr().err

    def test_a_non_numeric_id_is_a_command_error(self, world):
        with pytest.raises(CommandError):
            run("export_conversation", "abc")

    def test_no_id_is_a_command_error(self, world):
        with pytest.raises(CommandError):
            run("export_conversation")

    def test_id_zero_is_reported_as_not_found(self, world):
        with pytest.raises(CommandError):
            run("export_conversation", 0)


class TestExportAll:
    def test_writes_one_file_per_conversation_and_an_index(self, world, tmp_path):
        target = tmp_path / "all"
        run("export_all", "--output-dir", target)
        assert names(target) == sorted(
            [f"conversation_{c.pk}.json" for c in (world.h, world.s, world.e)] + ["index.json"]
        )

    def test_each_file_is_that_conversations_export(self, world, tmp_path):
        target = tmp_path / "all"
        run("export_all", "--output-dir", target)
        convs = (world.h, world.s, world.e)
        assert [read(target / f"conversation_{c.pk}.json") for c in convs] == [kit.export_text(c) for c in convs]

    def test_the_index_is_the_listing_newest_first(self, world, tmp_path):
        from moderation.queries import list_conversations

        target = tmp_path / "all"
        run("export_all", "--output-dir", target)
        index = json.loads(read(target / "index.json"))
        assert index == canon_listing(list_conversations())
        assert [row["id"] for row in index] == [world.e.pk, world.s.pk, world.h.pk]

    def test_the_index_is_deterministic_json_text(self, world, tmp_path):
        first, second = tmp_path / "one", tmp_path / "two"
        run("export_all", "--output-dir", first)
        run("export_all", "--output-dir", second)
        assert [read(first / n) for n in names(first)] == [read(second / n) for n in names(second)]
        assert names(first) == names(second)

    def test_the_index_uses_the_same_json_format_as_the_conversation_files(self, world, tmp_path):
        from moderation.queries import list_conversations

        target = tmp_path / "all"
        run("export_all", "--output-dir", target)
        rows = json.loads(json.dumps(list_conversations()))
        assert read(target / "index.json") == json.dumps(rows, indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    def test_default_files_hold_no_identity(self, world, tmp_path):
        target = tmp_path / "all"
        run("export_all", "--output-dir", target)
        assert [hits(read(target / n)) for n in names(target)] == [[]] * 4

    def test_include_identities_reaches_the_conversation_files_but_not_the_index(self, world, tmp_path):
        target = tmp_path / "all"
        run("export_all", "--output-dir", target, "--include-identities")
        assert read(target / f"conversation_{world.h.pk}.json") == kit.export_text(world.h, include_identities=True)
        assert kit.USER_A["username"] in read(target / f"conversation_{world.h.pk}.json")
        assert hits(read(target / "index.json")) == []

    def test_include_raw_reaches_the_conversation_files(self, world, tmp_path):
        target = tmp_path / "all"
        run("export_all", "--output-dir", target, "--include-raw")
        assert read(target / f"conversation_{world.h.pk}.json") == kit.export_text(world.h, include_raw=True)
        assert "not json at all" in read(target / f"conversation_{world.h.pk}.json")
        assert "not json at all" not in read(target / "index.json")
        assert hits(read(target / f"conversation_{world.h.pk}.json")) == []

    def test_both_flags(self, world, tmp_path):
        target = tmp_path / "all"
        run("export_all", "--output-dir", target, "--include-identities", "--include-raw")
        assert read(target / f"conversation_{world.s.pk}.json") == kit.export_text(
            world.s, include_identities=True, include_raw=True
        )

    def test_no_stdout_or_stderr_carries_an_identity(self, world, tmp_path):
        out, err = run("export_all", "--output-dir", tmp_path / "all", "--include-identities")
        assert hits(out + err) == []

    def test_changes_no_row(self, world, tmp_path):
        before = kit.db_state()
        run("export_all", "--output-dir", tmp_path / "all", "--include-identities", "--include-raw")
        assert kit.db_state() == before

    def test_the_output_dir_is_required(self, world):
        with pytest.raises(CommandError):
            run("export_all")

    def test_a_missing_output_dir_is_created(self, world, tmp_path):
        target = tmp_path / "does" / "not" / "exist"
        run("export_all", "--output-dir", target)
        assert (target / "index.json").is_file()

    def test_an_output_dir_that_is_a_file_is_a_command_error(self, world, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("i am a file", encoding="utf-8")
        with pytest.raises(CommandError):
            run("export_all", "--output-dir", blocker)
        assert read(blocker) == "i am a file"

    def test_no_conversations_gives_an_empty_index(self, db, tmp_path):
        target = tmp_path / "empty"
        run("export_all", "--output-dir", target)
        assert names(target) == ["index.json"]
        assert json.loads(read(target / "index.json")) == []


class TestExportAllFilters:
    def test_source(self, world, tmp_path):
        target = tmp_path / "s"
        run("export_all", "--output-dir", target, "--source", "synthetic")
        assert names(target) == sorted([f"conversation_{world.s.pk}.json", "index.json"])
        assert [row["id"] for row in json.loads(read(target / "index.json"))] == [world.s.pk]

    def test_status(self, world, tmp_path):
        target = tmp_path / "s"
        run("export_all", "--output-dir", target, "--status", "closed")
        assert names(target) == sorted([f"conversation_{world.h.pk}.json", f"conversation_{world.s.pk}.json", "index.json"])
        assert [row["id"] for row in json.loads(read(target / "index.json"))] == [world.s.pk, world.h.pk]

    def test_status_open(self, world, tmp_path):
        target = tmp_path / "s"
        run("export_all", "--output-dir", target, "--status", "open")
        assert names(target) == sorted([f"conversation_{world.e.pk}.json", "index.json"])

    def test_experiment_by_name(self, world, tmp_path):
        target = tmp_path / "s"
        run("export_all", "--output-dir", target, "--experiment", "paired-rent-set")
        assert names(target) == sorted([f"conversation_{world.s.pk}.json", "index.json"])

    def test_filters_combine_with_and(self, world, tmp_path):
        target = tmp_path / "s"
        run("export_all", "--output-dir", target, "--source", "human", "--status", "closed")
        assert names(target) == sorted([f"conversation_{world.h.pk}.json", "index.json"])
        empty = tmp_path / "t"
        run("export_all", "--output-dir", empty, "--source", "synthetic", "--experiment", "observational-2026")
        assert names(empty) == ["index.json"]

    def test_the_index_matches_the_filtered_listing(self, world, tmp_path):
        from moderation.queries import list_conversations

        target = tmp_path / "s"
        run("export_all", "--output-dir", target, "--source", "human")
        assert json.loads(read(target / "index.json")) == canon_listing(list_conversations(source="human"))

    @pytest.mark.parametrize(
        "flag, value",
        [("--source", "robot"), ("--status", "archived"), ("--experiment", "no-such-experiment")],
    )
    def test_an_unknown_value_is_refused_and_writes_nothing(self, world, tmp_path, flag, value):
        target = tmp_path / "bad"
        with pytest.raises((CommandError, ValueError)):
            run("export_all", "--output-dir", target, flag, value)
        assert [n for n in (names(target) if target.exists() else []) if n.startswith("conversation_")] == []


class TestExportAllOverwrite:
    def test_an_existing_conversation_file_is_refused_and_left_alone(self, world, tmp_path):
        target = tmp_path / "keep"
        target.mkdir()
        existing = target / f"conversation_{world.h.pk}.json"
        existing.write_text("SENTINEL", encoding="utf-8")
        with pytest.raises(CommandError) as caught:
            run("export_all", "--output-dir", target)
        assert "--force" in str(caught.value)
        assert read(existing) == "SENTINEL"

    def test_an_existing_index_is_refused_and_left_alone(self, world, tmp_path):
        target = tmp_path / "keep"
        target.mkdir()
        (target / "index.json").write_text("SENTINEL", encoding="utf-8")
        with pytest.raises(CommandError) as caught:
            run("export_all", "--output-dir", target)
        assert "--force" in str(caught.value)
        assert read(target / "index.json") == "SENTINEL"

    def test_a_refusal_writes_nothing_at_all(self, world, tmp_path):
        # Pinned by the architect's amendment: the command aborts before writing anything.
        target = tmp_path / "keep"
        target.mkdir()
        (target / f"conversation_{world.s.pk}.json").write_text("SENTINEL", encoding="utf-8")
        with pytest.raises(CommandError):
            run("export_all", "--output-dir", target)
        assert names(target) == [f"conversation_{world.s.pk}.json"]

    def test_the_refusal_names_the_file(self, world, tmp_path):
        target = tmp_path / "keep"
        target.mkdir()
        (target / f"conversation_{world.e.pk}.json").write_text("SENTINEL", encoding="utf-8")
        with pytest.raises(CommandError) as caught:
            run("export_all", "--output-dir", target)
        assert f"conversation_{world.e.pk}.json" in str(caught.value)

    def test_running_twice_without_force_is_refused_the_second_time(self, world, tmp_path):
        target = tmp_path / "twice"
        run("export_all", "--output-dir", target)
        with pytest.raises(CommandError):
            run("export_all", "--output-dir", target)

    def test_force_overwrites_every_target(self, world, tmp_path):
        target = tmp_path / "force"
        target.mkdir()
        convs = (world.h, world.s, world.e)
        kit.write_files(target, {f"conversation_{c.pk}.json": "STALE" for c in convs} | {"index.json": "STALE"})
        run("export_all", "--output-dir", target, "--force")
        assert [read(target / f"conversation_{c.pk}.json") for c in convs] == [kit.export_text(c) for c in convs]
        assert [row["id"] for row in json.loads(read(target / "index.json"))] == [world.e.pk, world.s.pk, world.h.pk]

    def test_force_does_not_touch_other_files(self, world, tmp_path):
        target = tmp_path / "force"
        target.mkdir()
        (target / "notes.txt").write_text("mine", encoding="utf-8")
        (target / "conversation_999999.json").write_text("mine too", encoding="utf-8")
        run("export_all", "--output-dir", target, "--force")
        assert read(target / "notes.txt") == "mine"
        assert read(target / "conversation_999999.json") == "mine too"

    def test_other_files_in_the_folder_do_not_block_a_first_export(self, world, tmp_path):
        target = tmp_path / "shared"
        target.mkdir()
        (target / "notes.txt").write_text("mine", encoding="utf-8")
        run("export_all", "--output-dir", target)
        assert read(target / "notes.txt") == "mine"
        assert (target / "index.json").is_file()

    def test_force_with_flags_rewrites_with_the_flags(self, world, tmp_path):
        target = tmp_path / "force"
        run("export_all", "--output-dir", target)
        run("export_all", "--output-dir", target, "--force", "--include-identities")
        assert kit.USER_A["username"] in read(target / f"conversation_{world.h.pk}.json")


class TestTheCommandsNeverReadEnv:
    def test_no_dotenv_import_and_no_env_file_path_in_the_code(self, world):
        assert kit.dotenv_mentions(COMMAND_FILES) == []

    def test_no_env_file_is_opened_while_the_commands_run(self, world, tmp_path, monkeypatch):
        opened = []
        monkeypatch.setattr(builtins, "open", kit.open_spy(builtins.open, opened))
        monkeypatch.setattr(io, "open", kit.open_spy(io.open, opened))
        run("export_conversation", world.h.pk, "--include-identities", "--output", tmp_path / "x.json")
        run("export_all", "--output-dir", tmp_path / "all")
        assert [p for p in opened if os.path.basename(p).startswith(".env")] == []
        assert str(tmp_path / "x.json") in opened
