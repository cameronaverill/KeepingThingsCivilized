"""Step 9a: `export_conversation` text is deterministic and in the pinned format (brief 9a:
`json.dumps(bundle, indent=2, sort_keys=True, ensure_ascii=False)` plus a trailing newline) and reading changes nothing."""
import json

import exports_kit as kit
import pytest

pytestmark = pytest.mark.django_db

FLAGS = [
    pytest.param(dict(), id="default"),
    pytest.param(dict(include_identities=True), id="identities"),
    pytest.param(dict(include_raw=True), id="raw"),
    pytest.param(dict(include_identities=True, include_raw=True), id="both"),
]


class TestFormat:
    @pytest.mark.parametrize("which", ["h", "s", "e"])
    @pytest.mark.parametrize("flags", FLAGS)
    def test_the_text_is_the_pinned_dump_of_the_bundle(self, world, which, flags):
        conv = getattr(world, which)
        expected = json.dumps(kit.bundle(conv, **flags), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        assert kit.export_text(conv, **flags) == expected

    def test_one_trailing_newline_exactly(self, world):
        text = kit.export_text(world.h)
        assert text.endswith("}\n")
        assert not text.endswith("\n\n")

    def test_two_space_indent_and_sorted_top_level_keys(self, world):
        lines = kit.export_text(world.h).split("\n")
        assert lines[0] == "{"
        top = [line for line in lines if line.startswith('  "')]
        keys = [line.split('"')[1] for line in top]
        assert keys == sorted(keys)
        assert {"conversation", "topic", "participants", "messages", "runs", "cost_usd"} <= set(keys)

    def test_keys_are_sorted_at_every_depth(self, world):
        parsed = json.loads(kit.export_text(world.h, include_raw=True))
        assert kit.keys_sorted(parsed)

    def test_non_ascii_text_is_written_as_is(self, world):
        text = kit.export_text(world.h, include_raw=True)
        assert [s for s in kit.NON_ASCII_SAMPLES if s not in text] == []
        assert "\\u00c9" not in text
        assert "\\u65e5" not in text
        assert "\\ud83d" not in text.lower()

    @pytest.mark.parametrize("which", ["h", "s"])
    def test_every_timestamp_is_iso_8601_text(self, world, which):
        stamps = kit.timestamp_strings(json.loads(kit.export_text(getattr(world, which), include_raw=True)))
        assert len(stamps) >= 15
        assert [x for x in stamps if not (isinstance(x, str) and kit.ISO_8601_RE.match(x))] == []

    def test_the_text_round_trips_through_json(self, world):
        text = kit.export_text(world.h, include_raw=True)
        assert json.loads(text)["messages"][4]["content"] == kit.MSG5


class TestDeterminism:
    @pytest.mark.parametrize("which", ["h", "s", "e"])
    @pytest.mark.parametrize("flags", FLAGS)
    def test_two_calls_give_identical_text(self, world, which, flags):
        conv = getattr(world, which)
        assert kit.export_text(conv, **flags) == kit.export_text(conv, **flags)

    def test_unrelated_new_rows_change_nothing(self, world):
        before = kit.export_text(world.h, include_identities=True, include_raw=True)
        kit.light(run_statuses=["done", "failed"], cost="0.010000", user_messages=3)
        kit.make_call(None, world.s, cost="0.100000", prompt_version="late_row_v1")
        assert kit.export_text(world.h, include_identities=True, include_raw=True) == before

    def test_the_flags_change_the_text(self, world):
        plain = kit.export_text(world.h)
        assert kit.export_text(world.h, include_raw=True) != plain
        assert kit.export_text(world.h, include_identities=True) != plain

    def test_source_lists_do_not_depend_on_insertion_order(self, world):
        # The fixture adds every source in reverse order; the export lists issues by local_id and messages by seq_no.
        acts = kit.bundle(world.h)["runs"][1]["acts"]
        first, second = acts[0], acts[1]
        assert first[kit.KEYS.act_source_issues] == sorted(first[kit.KEYS.act_source_issues])
        assert second[kit.KEYS.act_source_messages] == sorted(second[kit.KEYS.act_source_messages])
        assert second[kit.KEYS.act_source_messages] == [1, 3]

    def test_runs_and_calls_keep_creation_order(self, world):
        runs = kit.bundle(world.h)["runs"]
        assert [r["id"] for r in runs] == sorted(r["id"] for r in runs)
        assert [c["attempt"] for c in runs[2]["llm_calls"]] == [1, 2]


class TestSignatures:
    """The flags are keyword-only and default to off (brief 9a: `get_conversation_bundle(conversation_id, *, ...)`)."""

    @pytest.mark.parametrize("name", ["get_conversation_bundle", "export_conversation"])
    def test_flags_are_keyword_only_and_default_false(self, name):
        import inspect

        from moderation import queries

        params = inspect.signature(getattr(queries, name)).parameters
        assert list(params)[0] == "conversation_id"
        assert params["include_identities"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["include_identities"].default is False
        assert params["include_raw"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["include_raw"].default is False

    def test_a_positional_flag_is_a_type_error(self, world):
        from moderation.queries import export_conversation, get_conversation_bundle

        with pytest.raises(TypeError):
            get_conversation_bundle(world.h.pk, True)
        with pytest.raises(TypeError):
            export_conversation(world.h.pk, True)


class TestReadOnly:
    @pytest.mark.parametrize("which", ["h", "s", "e"])
    @pytest.mark.parametrize("flags", FLAGS)
    def test_exporting_changes_no_row(self, world, which, flags):
        before = kit.db_state()
        kit.export_text(getattr(world, which), **flags)
        kit.bundle(getattr(world, which), **flags)
        assert kit.db_state() == before

    def test_listing_changes_no_row(self, world):
        from moderation.queries import list_conversations

        before = kit.db_state()
        list_conversations()
        list_conversations(source="human", has_runs=True)
        assert kit.db_state() == before
