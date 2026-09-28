"""Step 9a: an export round-trips the whole conversation (brief 9a, "Round trip test").

The fixture is built by `exports_kit.build_world()`:
  h  a closed human conversation (users A and B, 9 messages of which 2 are moderator posts, 6 runs: live and replay, done,
     failed and skipped, with issues, dispositions, acts with sources and 11 ledger rows, plus one unattached row)
  s  a closed synthetic paired conversation (planted phrases, no users, one live and two replay runs)
  e  a waiting conversation (one participant, nothing else)
"""
import json
from decimal import Decimal
from types import SimpleNamespace

import exports_kit as kit
import pytest

pytestmark = pytest.mark.django_db

FLAG_SETS = [
    pytest.param(dict(), id="default"),
    pytest.param(dict(include_raw=True), id="raw"),
    pytest.param(dict(include_identities=True), id="identities"),
    pytest.param(dict(include_identities=True, include_raw=True), id="identities+raw"),
]


def dec(value):
    return kit.canon(value, "cost_usd")


class TestBundleMatchesTheDatabase:
    @pytest.mark.parametrize("which", ["h", "s", "e"])
    @pytest.mark.parametrize("flags", FLAG_SETS)
    def test_every_stored_field_is_in_the_bundle(self, world, which, flags):
        conv = getattr(world, which)
        actual = kit.canon(kit.bundle(conv, **flags))
        expected = kit.canon(
            kit.expected_bundle(conv, identities=flags.get("include_identities", False), raw=flags.get("include_raw", False))
        )
        kit.assert_contains(actual, expected)

    @pytest.mark.parametrize("which", ["h", "s", "e"])
    @pytest.mark.parametrize("flags", FLAG_SETS)
    def test_the_exported_text_parses_to_the_same_content(self, world, which, flags):
        conv = getattr(world, which)
        from_text = json.loads(kit.export_text(conv, **flags))
        from_bundle = json.loads(json.dumps(kit.bundle(conv, **flags)))
        assert from_text == from_bundle

    @pytest.mark.parametrize("which", ["h", "s", "e"])
    def test_the_parsed_export_contains_every_stored_field(self, world, which):
        conv = getattr(world, which)
        expected = kit.canon(kit.expected_bundle(conv))
        kit.assert_contains(kit.parsed(conv), expected)

    def test_the_bundle_is_plain_json_data(self, world):
        text = json.dumps(kit.bundle(world.h, include_identities=True, include_raw=True))
        assert json.loads(text)["conversation"]["id"] == world.h.pk

    def test_top_level_keys(self, world):
        keys = set(kit.bundle(world.h))
        assert {"conversation", "topic", "participants", "messages", "runs"} <= keys


class TestKnownAnswersHumanConversation:
    """Values written out by hand from the fixture, so the comparison with the database is not the only check."""

    def test_conversation_and_topic(self, world):
        b = kit.canon(kit.bundle(world.h))
        c = b["conversation"]
        assert (c["id"], c["status"], c["source"], c["pair_id"], c["variant"], c["label_seed"]) == (
            world.h.pk, "closed", "human", "", "", 7719,
        )
        assert c["experiment"] == {"name": "observational-2026", "kind": "observational"}
        assert c["ended_by"] == "A"
        assert c["created_at"] == kit.at(0)
        topic = b["topic"]
        assert (topic["id"], topic["proposition"], topic["title"]) == (
            world.h.topic_id, "Cities should ban cars from downtown centres.", "",
        )
        assert topic["leans"] == {"compass": {"pro": {"economic": -0.4, "social": -0.2, "rationale": "Favors public space."}}}

    def test_participants(self, world):
        parts = kit.canon(kit.bundle(world.h))["participants"]
        assert [(p["label"], p["join_order"], p["joined_at"]) for p in parts] == [("A", 1, kit.at(1)), ("B", 2, kit.at(2))]

    def test_messages_in_order_with_references_by_seq_no(self, world):
        msgs = kit.bundle(world.h)["messages"]
        assert [m["seq_no"] for m in msgs] == [1, 2, 3, 4, 5, 6, 7, 8, 9]
        assert [m["author_type"] for m in msgs] == ["user"] * 3 + ["moderator"] + ["user"] * 3 + ["moderator", "user"]
        assert [m["participant"] for m in msgs] == ["A", "B", "A", None, "B", "A", "B", None, "A"]
        assert [m["in_reply_to"] for m in msgs] == [None, None, None, 3, None, None, None, 7, None]
        assert [m["content"] for m in msgs] == [
            kit.MSG1, kit.MSG2, kit.MSG3, kit.MSG4, kit.MSG5, kit.MSG6, kit.MSG7, kit.MSG8, kit.MSG9,
        ]
        assert msgs[3]["char_count"] == len(kit.MSG4)
        assert [m["planted"] for m in msgs] == [[]] * 9
        assert kit.canon(msgs)[0]["created_at"] == kit.at(3)

    def test_the_fixture_really_separates_primary_keys_from_seq_numbers(self, world):
        # Non-vacuity of the "by seq_no, never by pk" checks below.
        assert [m.pk == m.seq_no for m in world.h_msgs] == [False] * 9

    def test_run_rows_in_order_oldest_first(self, world):
        runs = kit.bundle(world.h)["runs"]
        r = world.h_runs
        assert [x["id"] for x in runs] == [r.r1.pk, r.r2.pk, r.r3.pk, r.r4.pk, r.r5.pk, r.r6.pk]
        assert [x["kind"] for x in runs] == ["live", "live", "live", "replay", "live", "live"]
        assert [x["status"] for x in runs] == ["done", "done", "failed", "done", "done", "skipped_budget"]
        assert [x["decision"] for x in runs] == ["no_intervention", "intervene", "", "intervene", "intervene", ""]
        assert [x["failure_reason"] for x in runs] == ["", "", "structural", "", "", "budget_exceeded"]
        assert [x["attempts"] for x in runs] == [1, 1, 2, 1, 1, 1]
        assert [x["replicate"] for x in runs] == [1, 1, 1, 2, 1, 1]
        assert [x["is_stale"] for x in runs] == [False, False, False, False, True, False]
        assert [x["replay_of"] for x in runs] == [None, None, None, r.r2.pk, None, None]

    def test_runs_refer_to_messages_by_seq_no(self, world):
        runs = kit.bundle(world.h)["runs"]
        assert [x[kit.KEYS.run_trigger] for x in runs] == [1, 3, 5, 3, 7, 9]
        assert [x["snapshot_seq"] for x in runs] == [1, 3, 5, 3, 7, 9]
        assert [x[kit.KEYS.run_posted] for x in runs] == [None, 4, None, None, 8, None]

    def test_run_text_and_json_fields(self, world):
        runs = kit.bundle(world.h)["runs"]
        assert runs[1]["rationale"] == "A source would help both readers."
        assert runs[2]["error"] == "The model answered twice with unusable output."
        assert runs[1]["config_snapshot"]["models"] == {"master": "claude-sonnet-5", "intervenor": "claude-sonnet-5"}
        assert runs[1]["config_snapshot"]["prompts"]["master"] == {"name": "master_v1", "sha256": "b" * 64}
        assert runs[1]["discussion_map"]["disagreements"] == [{"summary": "Effect on sales", "kind": "factual"}]
        assert runs[5]["discussion_map"] == {}

    def test_run_timings(self, world):
        timings = kit.canon(kit.bundle(world.h))["runs"][1]["timings"]
        assert timings == {
            "created_at": kit.at(21), "claimed_at": kit.at(21, 1), "started_at": kit.at(21, 2),
            "finished_at": kit.at(21, 30),
        }
        never_started = kit.canon(kit.bundle(world.h))["runs"][5]["timings"]
        assert (never_started["started_at"], never_started["finished_at"]) == (None, None)


class TestCharCount:
    def test_char_count_is_the_stored_count_not_the_raw_length(self, db):
        w = kit.light(user_messages=1)
        padded = "  Padded text with trailing spaces.  \r\n"
        kit.make_message(w.conv, "user", w.parts[1], padded)
        messages = kit.bundle(w.conv)["messages"]
        assert (messages[1]["content"], messages[1]["char_count"]) == (padded, 33)
        assert len(padded) == 39


class TestIssuesAndDispositions:
    def test_issues_of_the_intervening_run(self, world):
        issues = kit.bundle(world.h)["runs"][1]["issues"]
        m = kit.KEYS.issue_message
        assert [i["local_id"] for i in issues] == ["i1", "i2", "i3", "i4"]
        assert [i[m] for i in issues] == [3, 3, 3, 3]
        assert [i["issue_type"] for i in issues] == [
            "unsupported_claim", "possible_factual_error", "unsupported_claim", "abusive_language",
        ]
        assert [i["dimension"] for i in issues] == ["", "factual_accuracy", "", "abusiveness"]
        assert [i["intensity"] for i in issues] == [None, 2, None, 3]
        assert [i["confidence"] for i in issues] == [0.8, 0.55, 0.8, 0.9]
        assert [i["validity"] for i in issues] == ["valid", "valid", "rejected", "valid"]
        assert [i["rejection_reason"] for i in issues] == ["", "", "quote_not_found", ""]

    def test_quote_offsets_and_match_kind(self, world):
        issues = kit.bundle(world.h)["runs"][1]["issues"]
        first = kit.MSG3.index("nobody serious disagrees with me on this")
        assert [i["quote_match"] for i in issues] == ["exact", "normalized", "not_found", "exact"]
        assert (issues[0]["quote"], issues[0]["quote_start"], issues[0]["quote_end"]) == (
            "nobody serious disagrees with me on this", first, first + len("nobody serious disagrees with me on this"),
        )
        assert (issues[1]["quote"], issues[1]["quote_start"], issues[1]["quote_end"]) == (
            'Studies are "biased"', 0, len("Studies are “biased”"),
        )
        assert (issues[2]["quote"], issues[2]["quote_start"], issues[2]["quote_end"]) == ("the data proves it", None, None)

    def test_dispositions_and_reasons(self, world):
        issues = kit.bundle(world.h)["runs"][1]["issues"]
        assert [i["disposition"] for i in issues] == ["acted", "declined", None, "acted"]
        assert issues[0]["disposition_reason"] == "It matters for the discussion."
        assert issues[1]["disposition_reason"] == "Contested; left alone."
        assert issues[3]["disposition_reason"] == "Conduct needs a reminder."
        assert issues[2]["disposition_reason"] is None

    def test_an_issue_on_a_moderator_message_is_exported_rejected_with_its_seq(self, world):
        issues = kit.bundle(world.h)["runs"][4]["issues"]
        m = kit.KEYS.issue_message
        assert [(i["local_id"], i[m], i["validity"], i["rejection_reason"], i["disposition"]) for i in issues] == [
            ("i1", 7, "valid", "", "acted"),
            ("i2", 4, "rejected", "moderator_message", None),
        ]

    def test_the_same_local_id_in_different_runs_keeps_its_own_run(self, world):
        runs = kit.bundle(world.h)["runs"]
        replay = runs[3]["issues"]
        assert [(i["local_id"], i["disposition"], i["disposition_reason"]) for i in replay] == [
            ("i1", "declined", "Replay declined it.")
        ]
        original = runs[1]["issues"][0]
        assert (original["local_id"], original["disposition"]) == ("i1", "acted")

    def test_runs_without_issues_have_empty_lists(self, world):
        runs = kit.bundle(world.h)["runs"]
        assert [len(r["issues"]) for r in runs] == [0, 4, 0, 1, 2, 0]
        assert [len(r["acts"]) for r in runs] == [0, 3, 0, 1, 1, 0]


class TestActsAndSources:
    def test_acts_of_the_intervening_run(self, world):
        acts = kit.bundle(world.h)["runs"][1]["acts"]
        assert [a["order"] for a in acts] == [1, 2, 3]
        assert [a["act_type"] for a in acts] == ["request_information", "enforce_conduct", "enforce_process"]
        assert [a["tone"] for a in acts] == ["gentle", "firm", "neutral"]
        assert [a["text"] for a in acts] == [kit.ACT1, kit.ACT2, kit.ACT3_REJECTED]
        assert [(a["addressee"], a["subject"]) for a in acts] == [("A", "A"), ("all", "none"), ("all", "none")]
        assert [(a["validity"], a["rejection_reason"]) for a in acts] == [
            ("valid", ""), ("valid", ""), ("rejected", "names_participant"),
        ]

    def test_act_features_are_exported_as_computed_by_the_model(self, world):
        acts = kit.bundle(world.h)["runs"][1]["acts"]
        assert acts[0]["features"] == {
            "char_len": len(kit.ACT1), "word_count": len(kit.ACT1.split()), "is_question": True, "quotes_participant": True,
        }
        assert acts[1]["features"] == {
            "char_len": len(kit.ACT2), "word_count": len(kit.ACT2.split()), "is_question": False,
            "quotes_participant": False,
        }

    def test_sources_are_local_ids_and_seq_numbers_sorted(self, world):
        acts = kit.bundle(world.h)["runs"][1]["acts"]
        issues_key, messages_key = kit.KEYS.act_source_issues, kit.KEYS.act_source_messages
        assert [a[issues_key] for a in acts] == [["i1", "i2"], ["i4"], ["i3"]]
        assert [a[messages_key] for a in acts] == [[3], [1, 3], [3]]

    def test_sources_of_another_run_with_the_same_local_ids_stay_separate(self, world):
        runs = kit.bundle(world.h)["runs"]
        issues_key, messages_key = kit.KEYS.act_source_issues, kit.KEYS.act_source_messages
        assert (runs[3]["acts"][0][issues_key], runs[3]["acts"][0][messages_key]) == (["i1"], [3])
        assert (runs[4]["acts"][0][issues_key], runs[4]["acts"][0][messages_key]) == (["i1"], [7])


class TestSourcesAreSortedNotInDatabaseOrder:
    def test_the_fixture_orders_differ(self, db):
        w = kit.sources_out_of_order()
        assert (w.first.seq_no, w.second.seq_no) == (1, 2)
        assert (w.first.pk, w.second.pk) == (9001, 9000)

    def test_source_issues_by_local_id_and_source_messages_by_seq_no(self, db):
        w = kit.sources_out_of_order()
        act = kit.bundle(w.conv)["runs"][0]["acts"][0]
        assert act[kit.KEYS.act_source_issues] == ["a1", "z1"]
        assert act[kit.KEYS.act_source_messages] == [1, 2]

    def test_the_run_refers_to_its_trigger_by_seq_not_by_the_lower_pk(self, db):
        w = kit.sources_out_of_order()
        run = kit.bundle(w.conv)["runs"][0]
        assert (run[kit.KEYS.run_trigger], run["snapshot_seq"]) == (2, 2)
        assert [i[kit.KEYS.issue_message] for i in run["issues"]] == [2, 2]


class TestLlmCallsAndCosts:
    def test_calls_per_run_oldest_first(self, world):
        runs = kit.bundle(world.h)["runs"]
        assert [len(r["llm_calls"]) for r in runs] == [1, 3, 2, 2, 2, 1]
        assert [(c["agent"], c["attempt"]) for c in runs[1]["llm_calls"]] == [("master", 1), ("master", 2), ("intervenor", 1)]

    def test_call_fields(self, world):
        calls = kit.canon(kit.bundle(world.h))["runs"][1]["llm_calls"]
        assert [c["model"] for c in calls] == ["claude-sonnet-5"] * 3
        assert [c["prompt_version"] for c in calls] == ["master_v1", "master_v1", "intervenor_v1"]
        assert [c["prompt_sha256"] for c in calls] == ["a" * 64, "a" * 64, "c" * 64]
        assert [c["tokens_in"] for c in calls] == [1200, 1250, 900]
        assert [c["tokens_out"] for c in calls] == [300, 280, 200]
        assert [c["cache_write_tokens"] for c in calls] == [800, 0, 0]
        assert [c["cache_read_tokens"] for c in calls] == [0, 800, 700]
        assert [c["latency_ms"] for c in calls] == [2400, 1900, 1500]
        assert [c["status"] for c in calls] == ["ok", "ok", "ok"]
        assert [c["cost_usd"] for c in calls] == [Decimal("0.010000"), Decimal("0.002500"), Decimal("0.004125")]
        assert [c["reserved_usd"] for c in calls] == [Decimal("0.020000")] * 3

    def test_an_errored_and_a_refused_call_keep_status_and_error_code(self, world):
        runs = kit.canon(kit.bundle(world.h))["runs"]
        errored = runs[2]["llm_calls"][1]
        assert (errored["status"], errored["error_code"], errored["attempt"]) == ("error", "api_error", 2)
        refused = runs[5]["llm_calls"][0]
        assert (refused["status"], refused["reserved_usd"]) == ("refused_budget", Decimal("0.004000"))

    def test_cost_per_run_is_the_sum_of_its_calls_and_a_null_cost_counts_as_zero(self, world):
        runs = kit.canon(kit.bundle(world.h))["runs"]
        h = kit.H_RUN_COSTS
        assert [r["cost_usd"] for r in runs] == [h["r1"], h["r2"], h["r3"], h["r4"], h["r5"], h["r6"]]

    def test_conversation_total_includes_every_run_and_the_unattached_row(self, world):
        assert kit.canon(kit.bundle(world.h))["cost_usd"] == kit.H_TOTAL_COST
        assert kit.H_TOTAL_COST == sum(kit.H_RUN_COSTS.values()) + kit.H_UNATTACHED_COST

    def test_a_call_named_only_by_its_run_counts_for_the_conversation(self, world):
        # r1's ledger row has run_id set and conversation_id null.
        bundle = kit.canon(kit.bundle(world.h))
        assert bundle["runs"][0]["cost_usd"] == Decimal("0.001200")
        assert bundle["cost_usd"] == kit.H_TOTAL_COST

    def test_the_unattached_row_is_listed_once_and_not_under_a_run(self, world):
        bundle = kit.canon(kit.bundle(world.h))
        assert [(c["prompt_version"], c["agent"], c["cost_usd"]) for c in bundle["unattached_llm_calls"]] == [
            ("unattached_v1", "master", Decimal("0.000500"))
        ]
        under_runs = [c["prompt_version"] for r in bundle["runs"] for c in r["llm_calls"]]
        assert "unattached_v1" not in under_runs

    def test_costs_do_not_leak_between_conversations(self, world):
        h, s = kit.canon(kit.bundle(world.h)), kit.canon(kit.bundle(world.s))
        assert (h["cost_usd"], s["cost_usd"]) == (kit.H_TOTAL_COST, kit.S_TOTAL_COST)
        assert "s_marker_v9" not in kit.export_text(world.h, include_raw=True)
        assert "unattached_v1" not in kit.export_text(world.s, include_raw=True)

    def test_a_conversation_without_runs_or_calls_costs_nothing(self, world):
        assert kit.canon(kit.bundle(world.e))["cost_usd"] == Decimal("0.000000")


class TestRawFieldsOnlyOnRequest:
    def test_no_raw_field_anywhere_by_default(self, world):
        assert set(kit.RAW_KEYS) & set(kit.all_keys(kit.bundle(world.h))) == set()

    def test_no_raw_text_reaches_the_default_export(self, world):
        text = kit.export_text(world.h)
        markers = ("r3-a", "r4-b", "not json at all", "Be neutral", "unattached-raw")
        assert [m for m in markers if m in text] == []

    def test_raw_fields_present_on_every_call_with_include_raw(self, world):
        b = kit.bundle(world.h, include_raw=True)
        calls = [c for r in b["runs"] for c in r["llm_calls"]] + b["unattached_llm_calls"]
        assert len(calls) == 12
        assert [set(kit.RAW_KEYS) <= set(c) for c in calls] == [True] * 12

    def test_raw_values_are_the_stored_ones(self, world):
        calls = kit.bundle(world.h, include_raw=True)["runs"][1]["llm_calls"]
        assert calls[0]["request"] == {
            "system": "Be neutral. Élan.", "messages": [{"role": "user", "content": "Participant A: ..."}],
        }
        assert (calls[0]["raw_response"], calls[0]["parsed"]) == ("not json at all", None)
        assert (calls[1]["raw_response"], calls[1]["parsed"]) == ('{"issues": [1]}', {"issues": [1]})
        assert calls[2]["parsed"] == {"decision": "intervene"}

    def test_unattached_rows_honour_include_raw_too(self, world):
        plain = kit.bundle(world.h)["unattached_llm_calls"][0]
        raw = kit.bundle(world.h, include_raw=True)["unattached_llm_calls"][0]
        assert set(kit.RAW_KEYS).isdisjoint(plain)
        assert raw["request"] == {"marker": "unattached-raw"}


class TestPreviewCheckRawSuppression:
    """`_preview_call_ids` (moderation/queries.py) finds the unattached ledger rows a `PreviewCheck` names (its
    request embeds the participant's unposted draft, which can quote or reveal the other participant's own draft)
    and hides `request`/`raw_response`/`parsed` for exactly those rows, even with `include_raw=True`. Every other
    unattached row of the same conversation still gets its raw fields, so this is a targeted rule, not a blanket
    ban on raw fields for unattached rows."""

    @pytest.fixture
    def rows(self, db):
        conv = kit.make_conversation(source="synthetic")
        author = kit.make_participant(conv, "A", 1)
        preview_call = kit.make_call(
            None, conv, cost="0.000100", purpose="preview", request={"marker": "preview-draft"}, raw="{}", parsed={}
        )
        kit.make_preview_check(conv, author, preview_call)
        ordinary_call = kit.make_call(
            None, conv, cost="0.000200", purpose="golden", request={"marker": "ordinary"}, raw="{}", parsed={}
        )
        return SimpleNamespace(conv=conv, preview_call=preview_call, ordinary_call=ordinary_call)

    def test_the_preview_calls_raw_fields_are_absent_even_with_include_raw(self, rows):
        bundle = kit.bundle(rows.conv, include_raw=True)

        entry = bundle["unattached_llm_calls"][0]

        assert entry["id"] == rows.preview_call.pk
        assert set(kit.RAW_KEYS).isdisjoint(entry)

    def test_the_preview_calls_non_raw_fields_are_still_exported(self, rows):
        bundle = kit.bundle(rows.conv, include_raw=True)

        entry = bundle["unattached_llm_calls"][0]

        assert (entry["purpose"], entry["cost_usd"]) == ("preview", 0.0001)

    def test_an_ordinary_unattached_call_in_the_same_conversation_keeps_its_raw_fields(self, rows):
        bundle = kit.bundle(rows.conv, include_raw=True)

        entry = bundle["unattached_llm_calls"][1]

        assert entry["id"] == rows.ordinary_call.pk
        assert entry["request"] == {"marker": "ordinary"}


class TestSyntheticConversation:
    def test_conversation_and_experiment(self, world):
        c = kit.canon(kit.bundle(world.s))["conversation"]
        assert (c["source"], c["status"], c["pair_id"], c["variant"], c["label_seed"]) == (
            "synthetic", "closed", "rent-1", "left", 424242,
        )
        assert c["experiment"] == {"name": "paired-rent-set", "kind": "paired"}
        assert c["ended_by"] is None
        assert c["created_at"] == kit.at(100)

    def test_topic_with_title_and_leans(self, world):
        topic = kit.bundle(world.s)["topic"]
        assert (topic["title"], topic["proposition"]) == ("Rent control", "Cities should cap annual rent increases.")
        assert topic["leans"]["compass"]["pro"] == {"economic": -0.6, "social": -0.1, "rationale": "Sides with tenants."}
        assert topic["leans"]["compass"]["con"]["economic"] == 0.6

    def test_participants_have_no_user_so_no_pseudonym(self, world):
        parts = kit.bundle(world.s)["participants"]
        assert [(p["label"], p["join_order"], p["pseudonym"]) for p in parts] == [("A", 1, None), ("B", 2, None)]

    def test_planted_phrases_are_exported(self, world):
        planted = [m["planted"] for m in kit.bundle(world.s)["messages"]]
        assert planted[0] == [
            {"phrase": "rent control always destroys housing supply", "dimension": "factual_accuracy", "intensity": 3}
        ]
        assert planted[1] == []
        assert planted[2] == [{"phrase": "You fool", "dimension": "abusiveness", "intensity": 3}]

    def test_live_and_replay_runs(self, world):
        runs = kit.bundle(world.s)["runs"]
        l1 = world.s_runs.l1.pk
        assert [(r["kind"], r["status"], r["replicate"], r["replay_of"]) for r in runs] == [
            ("live", "done", 1, None), ("replay", "done", 1, l1), ("replay", "failed", 2, l1),
        ]
        assert [r[kit.KEYS.run_posted] for r in runs] == [None, None, None]
        assert [r[kit.KEYS.run_trigger] for r in runs] == [1, 1, 1]
        assert runs[2]["failure_reason"] == "structural"
        assert [len(r["llm_calls"]) for r in runs] == [1, 1, 2]

    def test_run_costs(self, world):
        b = kit.canon(kit.bundle(world.s))
        assert [r["cost_usd"] for r in b["runs"]] == [Decimal("0.001500"), Decimal("0.002000"), Decimal("0.002000")]
        assert b["cost_usd"] == kit.S_TOTAL_COST


class TestWaitingConversation:
    def test_a_conversation_with_one_participant_and_nothing_else(self, world):
        b = kit.bundle(world.e)
        assert (b["conversation"]["status"], b["conversation"]["experiment"], b["conversation"]["ended_by"]) == (
            "open", None, None,
        )
        assert b["messages"] == []
        assert b["runs"] == []
        assert [(p["label"], p["join_order"]) for p in b["participants"]] == [("A", 1)]


class TestUnknownConversation:
    def test_a_missing_id_raises_and_names_nothing_internal(self, world):
        from django.core.exceptions import ObjectDoesNotExist
        from moderation.queries import export_conversation, get_conversation_bundle

        with pytest.raises(ObjectDoesNotExist):
            get_conversation_bundle(987654)
        with pytest.raises(ObjectDoesNotExist):
            export_conversation(987654)
