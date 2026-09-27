"""Architect rulings pinned after the first build: `internal_error` for unexpected failures, the reuse rules as built (retry
reclaims its own claim, newest check wins, a missing Intervenor output is fetched once, unusable JSON falls back), the exact
rate-cap window, reuse ignoring models and prompt fingerprints, what an export may hold, and that the draft normalisation
equals the forum's own."""
import logging
from datetime import datetime, timedelta, timezone

import pipeline_run_kit as prk
import preview_kit as pk
import pytest
from django.utils import timezone as dj_timezone

pytestmark = pytest.mark.django_db

T0 = datetime(2026, 6, 1, 8, 0, 0, tzinfo=timezone.utc)
LEAK_TEXT = "exception-detail-that-must-not-be-logged"


def reload_check(check):
    from moderation.models import PreviewCheck

    return PreviewCheck.objects.get(pk=check.pk)


def previewed(fake, factory=pk.concern, who="B", text=pk.DRAFT):
    w = pk.world()
    fake(*factory(w, pk.PLACEHOLDER))
    return w, pk.check(w, who, text)[1]


class TestInternalError:
    def boom(self, *args, **kwargs):
        raise RuntimeError(LEAK_TEXT + " " + pk.MARKER)

    def assert_internal(self, stored, before, returned=None):
        assert (stored.outcome, stored.unavailable_reason) == ("unavailable", "internal_error")
        assert (stored.note_texts, stored.master_output, stored.intervenor_output) == ([], None, None)
        assert pk.counts() == before

    def test_an_unexpected_exception_in_the_master_agent(self, fake, monkeypatch):
        from moderation import agents

        monkeypatch.setattr(agents, "call_master", self.boom)
        w = pk.world()
        client = fake()
        before = pk.counts()
        returned, stored = pk.check(w, "B")
        self.assert_internal(stored, before)
        assert returned.unavailable_reason == "internal_error"
        assert client.calls == []

    def test_an_unexpected_exception_in_the_intervenor_agent(self, fake, monkeypatch):
        from moderation import agents

        monkeypatch.setattr(agents, "call_intervenor", self.boom)
        w = pk.world()
        fake(pk.concern_script()[0])
        before = pk.counts()
        stored = pk.check(w, "B")[1]
        self.assert_internal(stored, before)
        assert stored.llm_call_ids == pk.ledger_ids(w.conv)

    def test_an_unexpected_exception_in_the_gateway(self, fake, monkeypatch):
        from moderation import llm

        monkeypatch.setattr(llm, "call", self.boom)
        w = pk.world()
        fake()
        before = pk.counts()
        self.assert_internal(pk.check(w, "B")[1], before)

    def test_an_unexpected_exception_in_the_issue_validation(self, fake, monkeypatch):
        from moderation import quotes

        monkeypatch.setattr(quotes, "locate_quote", self.boom)
        w = pk.world()
        fake(*pk.concern_script())
        before = pk.counts()
        self.assert_internal(pk.check(w, "B")[1], before)

    def test_an_unexpected_exception_in_the_act_validation(self, fake, monkeypatch):
        from moderation import label_check

        monkeypatch.setattr(label_check, "names_a_label", self.boom)
        w = pk.world()
        fake(*pk.concern_script())
        before = pk.counts()
        self.assert_internal(pk.check(w, "B")[1], before)

    def test_an_exhausted_test_model_is_an_internal_error_never_an_api_error(self, fake):
        w = pk.world()
        fake()
        stored = pk.check(w, "B")[1]
        assert stored.unavailable_reason == "internal_error"

    def test_the_log_carries_the_exception_class_and_the_check_id_but_not_the_exception_text_or_the_draft(self, fake, monkeypatch, caplog):
        from moderation import agents

        monkeypatch.setattr(agents, "call_master", self.boom)
        caplog.set_level(logging.DEBUG)
        w = pk.world()
        fake()
        stored = pk.check(w, "B")[1]
        logged = "\n".join(r.getMessage() for r in caplog.records if not r.name.startswith("django.db"))
        assert "RuntimeError" in logged
        assert str(stored.pk) in logged
        assert LEAK_TEXT not in logged
        assert pk.MARKER not in logged
        assert "Traceback" not in caplog.text

    def test_a_provider_error_is_still_api_error(self, fake):
        w = pk.world()
        fake(pk.provider_error())
        assert pk.check(w, "B")[1].unavailable_reason == "api_error"


class TestReuseRulesAsBuilt:
    def test_a_retried_run_reclaims_its_own_claim_whatever_the_age_of_the_check(self, fake):
        from moderation.models import PreviewCheck

        w, check = previewed(fake)
        message = w.add_user("B", pk.DRAFT)
        run = prk.new_run(message)
        PreviewCheck.objects.filter(pk=check.pk).update(
            reused_by_run_id=run.pk, created_at=dj_timezone.now() - timedelta(hours=5)
        )  # the first attempt claimed the check and died before finishing
        client = fake()
        _, stored = prk.go(run)
        assert client.calls == []
        assert stored.config_snapshot["preview_check_id"] == check.pk
        assert (stored.status, stored.decision, stored.posted_message.content) == ("done", "intervene", pk.NOTE)
        assert reload_check(check).reused_by_run_id == run.pk

    def test_an_old_check_claimed_by_another_run_is_not_reclaimed(self, fake):
        from moderation.models import PreviewCheck

        w, check = previewed(fake)
        message = w.add_user("B", pk.DRAFT)
        other = prk.new_run(w[3], kind="replay")
        PreviewCheck.objects.filter(pk=check.pk).update(reused_by_run_id=other.pk)
        run = prk.new_run(message)
        client = fake(*pk.concern(w, message.pk))
        _, stored = prk.go(run)
        assert len(client.calls) == 2
        assert "preview_check_id" not in stored.config_snapshot

    def test_the_newest_matching_check_is_the_one_used(self, fake):
        w = pk.world()
        fake(*pk.concern_script(texts=(pk.NOTE,)))
        older = pk.check(w, "B")[1]
        fake(*pk.concern_script(texts=(pk.NOTE_2,)))
        newer = pk.check(w, "B")[1]
        client = fake()
        message = w.add_user("B", pk.DRAFT)
        _, run = prk.go(prk.new_run(message))
        assert client.calls == []
        assert run.config_snapshot["preview_check_id"] == newer.pk
        assert run.posted_message.content == pk.NOTE_2
        assert reload_check(older).reused_by_run_id is None
        assert reload_check(newer).reused_by_run_id == run.pk

    def test_a_stored_intervenor_output_that_is_missing_costs_exactly_one_fresh_intervenor_call(self, fake):
        from moderation.models import PreviewCheck

        w, check = previewed(fake)
        PreviewCheck.objects.filter(pk=check.pk).update(intervenor_output=None)
        message = w.add_user("B", pk.DRAFT)
        script = pk.concern(w, message.pk)
        client = fake(script[1])
        _, run = prk.go(prk.new_run(message))
        assert [c["output_format"].__name__ for c in client.calls] == ["IntervenorOutput"]
        assert [(r.agent, r.status) for r in prk.ledger(run)] == [("intervenor", "ok")]
        assert (run.status, run.decision, run.posted_message.content) == ("done", "intervene", pk.NOTE)
        assert prk.issue_summary(run) == [("i1", "valid", "")]

    @pytest.mark.parametrize("bad", [{"issues": "not a list"}, {"unexpected": True}, None], ids=["wrong_type", "wrong_shape", "missing"])
    def test_unusable_stored_master_json_falls_back_to_a_fresh_run(self, fake, bad):
        from moderation.models import PreviewCheck

        w, check = previewed(fake)
        PreviewCheck.objects.filter(pk=check.pk).update(master_output=bad)
        message = w.add_user("B", pk.DRAFT)
        client = fake(*pk.concern(w, message.pk))
        _, run = prk.go(prk.new_run(message))
        assert len(client.calls) == 2
        assert "preview_check_id" not in run.config_snapshot
        assert (run.status, run.decision, run.posted_message.content) == ("done", "intervene", pk.NOTE)
        assert reload_check(check).reused_by_run_id is None

    def test_reuse_ignores_the_models_and_prompt_fingerprint_of_the_check_time(self, fake, tune, monkeypatch):
        from moderation import agents

        w, check = previewed(fake)
        tune(MASTER_MODEL="claude-haiku-4-5", INTERVENOR_MODEL="claude-haiku-4-5", MASTER_MAX_TOKENS=1111)
        monkeypatch.setattr(
            agents, "prompt_fingerprint",
            lambda: {"master": {"name": "master_v99", "sha256": "0" * 64}, "intervenor": {"name": "intervenor_v99", "sha256": "1" * 64}},
        )
        client = fake()
        message = w.add_user("B", pk.DRAFT)
        _, run = prk.go(prk.new_run(message))
        assert client.calls == []
        assert run.config_snapshot["preview_check_id"] == check.pk
        assert run.config_snapshot["models"] == {"master": "claude-haiku-4-5", "intervenor": "claude-haiku-4-5"}
        assert run.config_snapshot["max_tokens"]["master"] == 1111
        assert run.config_snapshot["prompts"]["master"]["name"] == "master_v99"


class TestTheRateWindowExactly:
    def six_at(self, fake, w, times):
        fake(*[pk.quiet_master() for _ in times])
        return [pk.check(w, "B", f"Draft {i}.", now=t)[1] for i, t in enumerate(times)]

    def test_a_check_exactly_sixty_seconds_old_no_longer_counts(self, fake):
        w = pk.world()
        self.six_at(fake, w, [T0] * 6)
        fake(pk.quiet_master())
        assert pk.check(w, "B", "On the dot.", now=T0 + timedelta(seconds=60))[1].outcome == "no_concern"

    def test_a_check_just_under_sixty_seconds_old_counts(self, fake):
        w = pk.world()
        self.six_at(fake, w, [T0] * 6)
        fake()
        refused = pk.check(w, "B", "A moment early.", now=T0 + timedelta(seconds=59, microseconds=999000))[1]
        assert refused.unavailable_reason == "rate_limited"

    def test_earlier_refusals_do_not_use_up_slots(self, fake):
        w = pk.world()
        self.six_at(fake, w, [T0 + timedelta(seconds=10 * i) for i in range(6)])
        fake()
        refused = pk.check(w, "B", "Too many.", now=T0 + timedelta(seconds=55))[1]
        assert refused.unavailable_reason == "rate_limited"
        fake(pk.quiet_master())
        allowed = pk.check(w, "B", "The first one has left.", now=T0 + timedelta(seconds=65))[1]
        assert allowed.outcome == "no_concern"

    def test_the_mode_off_answer_comes_before_the_cap(self, fake, tune):
        tune(PREVIEW_SHARE=0.0, PREVIEW_MAX_CHECKS_PER_MINUTE=1)
        w = pk.world()
        fake()
        reasons = [pk.check(w, "B", f"Draft {i}.", now=T0)[1].unavailable_reason for i in range(4)]
        assert reasons == ["off"] * 4


class TestExports:
    def export_world(self, fake):
        w = pk.world()
        fake(*pk.concern_script())
        pk.check(w, "B")
        return w

    def test_the_check_calls_appear_as_unattached_ledger_rows_with_metadata_and_cost_only(self, fake):
        from moderation.queries import get_conversation_bundle

        w = self.export_world(fake)
        bundle = get_conversation_bundle(w.conv.pk)
        rows = bundle["unattached_llm_calls"]
        assert [(r["agent"], r["purpose"]) for r in rows] == [("master", "moderation"), ("intervenor", "moderation")]
        for row in rows:
            assert not {"request", "raw_response", "parsed"} & set(row)
            assert row["cost_usd"] is not None
        assert bundle["runs"] == []
        assert bundle["cost_usd"] > 0

    def test_the_draft_is_in_no_export_when_a_different_text_is_posted_instead(self, fake):
        from moderation.queries import export_conversation

        w = self.export_world(fake)
        message = w.add_user("B", "I will say something else entirely.")
        fake(*pk.concern(w, message.pk))
        prk.go(prk.new_run(message))
        assert pk.MARKER not in export_conversation(w.conv.pk)

    def test_nothing_about_previews_is_in_an_export(self, fake):
        from moderation.queries import export_conversation

        w = self.export_world(fake)
        text = export_conversation(w.conv.pk).lower()
        for word in ("preview", "draft"):
            assert word not in text

    def test_the_raw_export_of_a_conversation_does_not_hold_the_draft_either(self, fake):
        from moderation.queries import export_conversation

        w = self.export_world(fake)
        assert pk.MARKER not in export_conversation(w.conv.pk, include_raw=True)


# About forty spellings of the same kinds of text: line endings, Unicode forms, emoji and every sort of whitespace.
NORMALISATION_TABLE = [
    "plain", "  padded  ", "\tTabbed\t", "\n\nleading newlines", "trailing newlines\n\n", "crlf\r\nline", "cr\rline",
    "mixed\r\n\r\nlines\r", "\r\n", "\r", "  \r\n  spaced \r\n ", "Café", "Café", "  Café  ", "é́",
    "Å ngstrom", "Å", "ẛ̣", "ẹ́", "emoji \U0001F600", "family \U0001F468‍\U0001F469‍\U0001F467",
    "flag \U0001F1E9\U0001F1EA", "\U0001F600\r\n\U0001F600", "nbsp inside", " leading nbsp", "trailing nbsp ",
    "zero​width", "​leading zero width", "em space", "line separator", "para separator",
    "form\x0cfeed", "vertical\x0btab", "\x0bleading vtab", "tab\tinside", "double  space", "unicode 　 ideographic space",
    "hangul 한", "ﬁligature", "quote “x”", "   ",
]


class TestNormalisationEqualsTheForums:
    def test_the_table_has_about_forty_strings(self):
        assert len(NORMALISATION_TABLE) >= 40

    @pytest.mark.parametrize("text", NORMALISATION_TABLE, ids=[f"s{i}" for i in range(len(NORMALISATION_TABLE))])
    def test_normalise_draft_equals_the_forums_normalisation(self, text):
        from forum.services import _normalise_message
        from moderation import preview

        assert preview.normalise_draft(text) == _normalise_message(text)

    def test_the_stored_hash_and_char_count_equal_the_forums_for_every_string(self, tune):
        import hashlib

        from forum.limits import count_message_chars
        from forum.services import _normalise_message

        tune(PREVIEW_SHARE=0.0)  # mode off answers before any call and before the cap, so forty checks are cheap
        w = pk.world()
        stored = [(text, pk.check(w, "B", text)[1]) for text in NORMALISATION_TABLE]
        wrong = [
            text
            for text, row in stored
            if (row.draft_sha256, row.char_count)
            != (hashlib.sha256(_normalise_message(text).encode("utf-8")).hexdigest(), count_message_chars(text))
        ]
        assert wrong == []
        assert len(stored) == len(NORMALISATION_TABLE)

    def test_the_table_really_contains_strings_that_change_when_normalised(self):
        from forum.services import _normalise_message

        changed = [t for t in NORMALISATION_TABLE if _normalise_message(t) != t]
        assert len(changed) >= 15


def master_with(**changes):
    """The scripted concern check's Master output with some top-level fields replaced."""
    return {"issues": [], "discussion_map": {"agreements": [], "disagreements": []}, **changes}


def bad_issue(**changes):
    issue = {
        "id": "i1", "message_id": 1, "issue_type": "unsupported_claim", "quote": pk.QUOTE, "explanation": "e",
        "confidence": 0.5, "intensity": None, "needs_verification": False,
    }  # fmt: skip
    return master_with(issues=[{**issue, **changes}])


MALFORMED_MASTER = {
    "list": [],
    "string": "issues",
    "number": 7,
    "empty_object": {},
    "issues_string": {"issues": "not a list", "discussion_map": {"agreements": [], "disagreements": []}},
    "issues_number": master_with(issues=3),
    "issues_not_dicts": master_with(issues=[1, "x"]),
    "issue_id_number": bad_issue(id=5),
    "issue_message_id_string": bad_issue(message_id="12"),
    "issue_confidence_string": bad_issue(confidence="high"),
    "issue_confidence_out_of_range": bad_issue(confidence=7.5),
    "issue_type_unknown": bad_issue(issue_type="rudeness"),
    "issue_intensity_string": bad_issue(intensity="high"),
    "issue_extra_field": bad_issue(extra=1),
    "issue_missing_field": master_with(issues=[{"id": "i1"}]),
    "map_missing": {"issues": []},
    "map_string": master_with(discussion_map="none"),
    "map_agreements_number": master_with(discussion_map={"agreements": 4, "disagreements": []}),
}

MALFORMED_INTERVENOR = {
    "list": [],
    "string": "intervene",
    "number": 3,
    "empty_object": {},
    "decision_unknown": {"decision": "maybe", "rationale": "r", "issue_dispositions": [], "acts": []},
    "acts_string": {"decision": "intervene", "rationale": "r", "issue_dispositions": [], "acts": "none"},
    "acts_not_dicts": {"decision": "intervene", "rationale": "r", "issue_dispositions": [], "acts": [1, "x"]},
    "act_ids_strings": {
        "decision": "intervene", "rationale": "r", "issue_dispositions": [],
        "acts": [{"type": "request_information", "addressee": "all", "subject": "none", "source_issue_ids": [],
                  "source_message_ids": ["1"], "tone": "neutral", "text": "t"}],
    },  # fmt: skip
    "act_type_unknown": {
        "decision": "intervene", "rationale": "r", "issue_dispositions": [],
        "acts": [{"type": "shout", "addressee": "all", "subject": "none", "source_issue_ids": [],
                  "source_message_ids": [], "tone": "neutral", "text": "t"}],
    },  # fmt: skip
    "dispositions_number": {"decision": "intervene", "rationale": "r", "issue_dispositions": 2, "acts": []},
    "rationale_number": {"decision": "intervene", "rationale": 5, "issue_dispositions": [], "acts": []},
}


class TestMalformedStoredOutputsFallBack:
    def assert_fell_back(self, fake, w, check):
        message = w.add_user("B", pk.DRAFT)
        client = fake(*pk.concern(w, message.pk))
        _, run = prk.go(prk.new_run(message))  # must not raise
        assert len(client.calls) == 2
        assert [(r.agent, r.status) for r in prk.ledger(run)] == [("master", "ok"), ("intervenor", "ok")]
        assert "preview_check_id" not in run.config_snapshot
        assert (run.status, run.decision, run.posted_message.content) == ("done", "intervene", pk.NOTE)
        assert reload_check(check).reused_by_run_id is None

    @pytest.mark.parametrize("bad", list(MALFORMED_MASTER.values()), ids=list(MALFORMED_MASTER))
    def test_a_malformed_master_output_falls_back_to_a_fresh_call_and_is_not_claimed(self, fake, bad):
        from moderation.models import PreviewCheck

        w, check = previewed(fake)
        PreviewCheck.objects.filter(pk=check.pk).update(master_output=bad)
        self.assert_fell_back(fake, w, check)

    @pytest.mark.parametrize("bad", list(MALFORMED_INTERVENOR.values()), ids=list(MALFORMED_INTERVENOR))
    def test_a_malformed_intervenor_output_falls_back_to_a_fresh_call_and_is_not_claimed(self, fake, bad):
        from moderation.models import PreviewCheck

        w, check = previewed(fake)
        PreviewCheck.objects.filter(pk=check.pk).update(intervenor_output=bad)
        self.assert_fell_back(fake, w, check)

    def test_the_untouched_check_is_still_reused_so_the_fallbacks_above_are_not_vacuous(self, fake):
        w, check = previewed(fake)
        message = w.add_user("B", pk.DRAFT)
        client = fake()
        _, run = prk.go(prk.new_run(message))
        assert client.calls == []
        assert run.config_snapshot["preview_check_id"] == check.pk


class TestExportsInEveryForm:
    def marked_world(self, fake):
        w = pk.world()
        fake(*pk.concern_script())
        pk.check(w, "B")
        return w

    @pytest.mark.parametrize("raw", [False, True], ids=["plain", "raw"])
    def test_the_draft_is_in_no_conversation_export(self, fake, raw):
        from moderation.queries import export_conversation

        w = self.marked_world(fake)
        assert pk.MARKER not in export_conversation(w.conv.pk, include_raw=raw)

    @pytest.mark.parametrize("raw", [False, True], ids=["plain", "raw"])
    def test_the_draft_is_in_no_export_all_file(self, fake, tmp_path, raw):
        from django.core.management import call_command

        w = self.marked_world(fake)
        call_command("export_all", output_dir=str(tmp_path), include_raw=raw, stdout=__import__("io").StringIO())
        texts = [f.read_text(encoding="utf-8") for f in sorted(tmp_path.iterdir())]
        assert len(texts) >= 2
        assert [t for t in texts if pk.MARKER in t] == []
        assert f"conversation_{w.conv.pk}.json" in [f.name for f in tmp_path.iterdir()]

    def test_the_preview_calls_keep_their_metadata_and_cost_with_include_raw(self, fake):
        from moderation.queries import get_conversation_bundle

        w = self.marked_world(fake)
        plain = get_conversation_bundle(w.conv.pk)
        raw = get_conversation_bundle(w.conv.pk, include_raw=True)
        for bundle in (plain, raw):
            rows = bundle["unattached_llm_calls"]
            assert [(r["agent"], r["purpose"]) for r in rows] == [("master", "moderation"), ("intervenor", "moderation")]
            assert not {"request", "raw_response", "parsed"} & set(rows[0])
            assert all(r["cost_usd"] is not None for r in rows)
        assert raw["cost_usd"] == plain["cost_usd"] > 0

    def test_the_cost_total_and_the_listing_are_unchanged_by_the_omission(self, fake):
        from moderation import budget
        from moderation.queries import get_conversation_bundle, list_conversations

        w = self.marked_world(fake)
        spent = float(budget.spend(purposes=("moderation",), conversation_id=w.conv.pk))
        bundle = get_conversation_bundle(w.conv.pk, include_raw=True)
        (listed,) = [row for row in list_conversations() if row["id"] == w.conv.pk]
        assert bundle["cost_usd"] == pytest.approx(spent)
        assert listed["cost_usd"] == pytest.approx(spent)

    def test_other_calls_of_the_same_conversation_keep_their_raw_fields_with_include_raw(self, fake):
        """Non-vacuity: only the preview calls lose request, response and parsed."""
        from moderation.queries import get_conversation_bundle

        w = self.marked_world(fake)
        message = w.add_user("B", f"A different message about tenants, {pk.QUOTE}.")
        fake(*pk.concern(w, message.pk))
        prk.go(prk.new_run(message))
        bundle = get_conversation_bundle(w.conv.pk, include_raw=True)
        (run,) = bundle["runs"]
        assert len(run["llm_calls"]) == 2
        assert all({"request", "raw_response", "parsed"} <= set(c) for c in run["llm_calls"])
        assert all(not {"request", "raw_response", "parsed"} & set(c) for c in bundle["unattached_llm_calls"])
