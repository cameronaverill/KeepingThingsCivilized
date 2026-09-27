"""`preview.resolve_check`: what the author did with a check. Also the database rules of PreviewCheck (choices as CHECK
constraints, a resolved check has `resolved_at`) and the shape of the two new models."""
from datetime import datetime, timezone

import preview_kit as pk
import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone as dj_timezone

pytestmark = pytest.mark.django_db


def row_values(**overrides):
    w = pk.world([("A", "One thing."), ("B", "Another thing.")])
    values = dict(
        conversation_id=w.conv.pk, participant_id=w.parts["A"].pk, draft_text="A draft.", char_count=8, draft_sha256=pk.sha("A draft."), snapshot_seq=0,
        mode="on", outcome="no_concern", unavailable_reason="", note_texts=[], master_output=None, intervenor_output=None,
        llm_call_ids=[], action="",
    )  # fmt: skip
    values.update(overrides)
    return values


def make_row(**overrides):
    from moderation.models import PreviewCheck

    return PreviewCheck.objects.create(**row_values(**overrides))


def real_check(fake):
    w = pk.world()
    fake(*pk.concern_script())
    return w, pk.check(w, "B")[1]


def reload(check):
    from moderation.models import PreviewCheck

    return PreviewCheck.objects.get(pk=check.pk)


class TestResolve:
    @pytest.mark.parametrize("action", ["posted_as_written", "edited", "abandoned"])
    def test_an_action_is_recorded_with_the_time(self, fake, action):
        from moderation import preview

        w, check = real_check(fake)
        before = dj_timezone.now()
        preview.resolve_check(check.pk, action)
        after = dj_timezone.now()
        stored = reload(check)
        assert (stored.action, stored.resulting_message_id) == (action, None)
        assert before <= stored.resolved_at <= after

    def test_the_resulting_message_is_recorded(self, fake):
        from moderation import preview

        w, check = real_check(fake)
        message = w.add_user("B", pk.DRAFT)
        preview.resolve_check(check.pk, "posted_as_written", message_id=message.pk)
        stored = reload(check)
        assert (stored.action, stored.resulting_message_id) == ("posted_as_written", message.pk)

    def test_resolving_changes_nothing_else_of_the_check(self, fake):
        from moderation import preview

        w, check = real_check(fake)
        preview.resolve_check(check.pk, "edited")
        stored = reload(check)
        for name in ("outcome", "unavailable_reason", "note_texts", "master_output", "intervenor_output", "llm_call_ids",
                     "draft_text", "draft_sha256", "char_count", "snapshot_seq", "mode", "created_at", "reused_by_run_id"):
            assert getattr(stored, name) == getattr(check, name), name

    def test_the_same_action_again_is_harmless_and_keeps_the_first_time_and_message(self, fake):
        from moderation import preview

        w, check = real_check(fake)
        message = w.add_user("B", pk.DRAFT)
        preview.resolve_check(check.pk, "posted_as_written", message_id=message.pk)
        first = reload(check)
        preview.resolve_check(check.pk, "posted_as_written", message_id=message.pk)
        preview.resolve_check(check.pk, "posted_as_written")
        again = reload(check)
        assert (again.action, again.resolved_at, again.resulting_message_id) == (
            "posted_as_written", first.resolved_at, message.pk,
        )

    @pytest.mark.parametrize("first,second", [("edited", "posted_as_written"), ("posted_as_written", "abandoned"), ("abandoned", "edited")])
    def test_a_different_second_action_is_refused_and_the_first_stands(self, fake, first, second):
        from moderation import preview

        w, check = real_check(fake)
        preview.resolve_check(check.pk, first)
        with pytest.raises(ValueError):
            preview.resolve_check(check.pk, second)
        assert reload(check).action == first

    def test_an_unknown_check_is_refused(self, fake):
        from moderation import preview

        with pytest.raises(ValueError):
            preview.resolve_check(987654321, "edited")

    @pytest.mark.parametrize("bad", ["", "posted", "EDITED", "resolved", None])
    def test_an_unknown_action_is_refused_and_nothing_is_written(self, fake, bad):
        from moderation import preview

        w, check = real_check(fake)
        with pytest.raises(ValueError):
            preview.resolve_check(check.pk, bad)
        stored = reload(check)
        assert (stored.action, stored.resolved_at, stored.resulting_message_id) == ("", None, None)

    def test_an_unavailable_check_can_be_resolved_too(self, fake, tune):
        from moderation import preview

        tune(PREVIEW_SHARE=0.0)
        w = pk.world()
        fake()
        check = pk.check(w, "B")[1]
        preview.resolve_check(check.pk, "abandoned")
        assert reload(check).action == "abandoned"


class TestDatabaseRules:
    @pytest.mark.parametrize("outcome", ["no_concern", "concern", "unavailable"])
    def test_the_three_outcomes_are_accepted(self, outcome):
        assert make_row(outcome=outcome).outcome == outcome

    @pytest.mark.parametrize("outcome", ["", "maybe", "CONCERN"])
    def test_any_other_outcome_is_refused_by_the_database(self, outcome):
        with pytest.raises(IntegrityError), transaction.atomic():
            make_row(outcome=outcome)

    @pytest.mark.parametrize("action", ["posted_as_written", "edited", "abandoned"])
    def test_a_resolved_check_with_resolved_at_is_accepted(self, action):
        assert make_row(action=action, resolved_at=dj_timezone.now()).action == action

    def test_an_unresolved_check_needs_no_resolved_at(self):
        assert make_row(action="").resolved_at is None

    @pytest.mark.parametrize("action", ["posted", "EDITED", "resolved"])
    def test_any_other_action_is_refused_by_the_database(self, action):
        with pytest.raises(IntegrityError), transaction.atomic():
            make_row(action=action, resolved_at=dj_timezone.now())

    @pytest.mark.parametrize("action", ["posted_as_written", "edited", "abandoned"])
    def test_a_resolved_check_without_resolved_at_is_refused_by_the_database(self, action):
        with pytest.raises(IntegrityError), transaction.atomic():
            make_row(action=action, resolved_at=None)

    def test_a_check_is_created_with_a_time_and_no_resolution(self):
        row = make_row()
        assert row.created_at is not None
        assert (row.resolved_at, row.resulting_message_id, row.reused_by_run_id) == (None, None, None)


class TestModelShape:
    @pytest.mark.parametrize(
        "model,field,target,nullable",
        [
            ("PreviewMode", "conversation", "forum.Conversation", False),
            ("PreviewCheck", "conversation", "forum.Conversation", False),
            ("PreviewCheck", "participant", "forum.Participant", False),
            ("PreviewCheck", "resulting_message", "forum.Message", True),
            ("PreviewCheck", "reused_by_run", "moderation.ModerationRun", True),
        ],
    )
    def test_the_ids_are_real_protecting_foreign_keys_with_the_old_column_names(self, model, field, target, nullable):
        from django.apps import apps
        from django.db import models

        f = apps.get_model("moderation", model)._meta.get_field(field)
        assert f.is_relation is True
        assert f.related_model._meta.label == target
        assert f.remote_field.on_delete is models.PROTECT
        assert f.null is nullable
        assert f.column == f"{field}_id"

    def test_the_mode_of_a_conversation_is_unique(self):
        from moderation.models import PreviewMode

        assert PreviewMode._meta.get_field("conversation").unique is True

    def test_the_ledger_ids_stay_plain(self):
        from moderation.models import LLMCall, PreviewCheck

        for cls, name in ((LLMCall, "run_id"), (LLMCall, "conversation_id")):
            f = cls._meta.get_field(name)
            assert (f.get_internal_type(), f.is_relation) == ("IntegerField", False)
        f = PreviewCheck._meta.get_field("llm_call_ids")
        assert (f.get_internal_type(), f.is_relation) == ("JSONField", False)

    def test_the_indexes_of_the_check_are_kept(self):
        from moderation.models import PreviewCheck

        names = {i.name for i in PreviewCheck._meta.indexes}
        assert {"previewcheck_participant_time", "previewcheck_reuse_lookup"} <= names
        assert PreviewCheck._meta.get_field("conversation").db_index is True

    def test_the_stored_json_fields_take_lists_and_objects(self):
        row = make_row(
            note_texts=["one", "two"], master_output={"issues": []}, intervenor_output={"decision": "intervene"}, llm_call_ids=[3, 4]
        )
        stored = reload(row)
        assert (stored.note_texts, stored.master_output, stored.intervenor_output, stored.llm_call_ids) == (
            ["one", "two"], {"issues": []}, {"decision": "intervene"}, [3, 4],
        )

    def test_the_time_a_check_was_made_can_be_set(self):
        moment = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        assert reload(make_row(created_at=moment)).created_at == moment
