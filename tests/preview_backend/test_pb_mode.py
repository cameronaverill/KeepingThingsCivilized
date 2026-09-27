"""PreviewMode and `preview.preview_mode`: created lazily once per conversation, stable, drawn with `secrets`, shared by both
participants; PREVIEW_SHARE 1.0 is always on, 0.0 always off, 0.5 gives both. Mode off means no model call."""
import random

import preview_kit as pk
import pipeline_run_kit as prk
import pytest

pytestmark = pytest.mark.django_db


def bare_conversation():
    from forum.models import Conversation, Topic

    topic = Topic.objects.create(title=f"mode topic {prk.n()}", description="d", proposition="Cities should plant more trees.")
    return Conversation.objects.create(topic=topic, source="synthetic")


def mode_rows():
    from moderation.models import PreviewMode

    return list(PreviewMode.objects.order_by("pk"))


class TestLazyCreationAndStability:
    def test_no_row_exists_until_the_first_call_and_the_first_call_creates_exactly_one(self):
        from moderation import preview

        conv = bare_conversation()
        assert mode_rows() == []
        value = preview.preview_mode(conv.pk)
        (row,) = mode_rows()
        assert (row.conversation_id, row.mode) == (conv.pk, value)
        assert row.assigned_at is not None

    def test_the_value_is_on_or_off_only(self):
        from moderation import preview

        assert preview.preview_mode(bare_conversation().pk) in ("on", "off")

    def test_repeated_calls_return_the_same_value_and_create_no_second_row(self, tune):
        from moderation import preview

        tune(PREVIEW_SHARE=0.5)
        conv = bare_conversation()
        first = preview.preview_mode(conv.pk)
        again = [preview.preview_mode(conv.pk) for _ in range(20)]
        assert again == [first] * 20
        assert len(mode_rows()) == 1

    @pytest.mark.parametrize("first,later,expected", [(1.0, 0.0, "on"), (0.0, 1.0, "off")])
    def test_changing_the_tunable_later_never_changes_an_existing_conversation(self, tune, first, later, expected):
        from moderation import preview

        conv = bare_conversation()
        tune(PREVIEW_SHARE=first)
        assert preview.preview_mode(conv.pk) == expected
        tune(PREVIEW_SHARE=later)
        assert preview.preview_mode(conv.pk) == expected
        assert [r.mode for r in mode_rows()] == [expected]

    def test_a_stored_row_wins_over_the_tunable(self, tune):
        from django.utils import timezone

        from moderation import preview
        from moderation.models import PreviewMode

        conv = bare_conversation()
        PreviewMode.objects.create(conversation_id=conv.pk, mode="off", assigned_at=timezone.now())
        tune(PREVIEW_SHARE=1.0)
        assert preview.preview_mode(conv.pk) == "off"

    def test_each_conversation_gets_its_own_row(self, tune):
        from moderation import preview

        tune(PREVIEW_SHARE=1.0)
        ids = [bare_conversation().pk for _ in range(3)]
        for conv_id in ids:
            preview.preview_mode(conv_id)
        assert sorted(r.conversation_id for r in mode_rows()) == sorted(ids)

    def test_a_second_row_for_one_conversation_is_refused_by_the_database(self):
        from django.db import IntegrityError, transaction
        from django.utils import timezone

        from moderation.models import PreviewMode

        conv = bare_conversation()
        PreviewMode.objects.create(conversation_id=conv.pk, mode="on", assigned_at=timezone.now())
        with pytest.raises(IntegrityError), transaction.atomic():
            PreviewMode.objects.create(conversation_id=conv.pk, mode="off", assigned_at=timezone.now())


class TestShare:
    def test_share_one_is_always_on(self, tune):
        from moderation import preview

        tune(PREVIEW_SHARE=1.0)
        assert {preview.preview_mode(bare_conversation().pk) for _ in range(40)} == {"on"}

    def test_share_zero_is_always_off(self, tune):
        from moderation import preview

        tune(PREVIEW_SHARE=0.0)
        assert {preview.preview_mode(bare_conversation().pk) for _ in range(40)} == {"off"}

    def test_the_default_share_is_one(self):
        from config import tunables

        assert tunables.PREVIEW_SHARE == 1.0

    def test_share_one_half_gives_both_modes_in_roughly_equal_numbers(self, tune):
        from moderation import preview

        tune(PREVIEW_SHARE=0.5)
        modes = [preview.preview_mode(bare_conversation().pk) for _ in range(200)]
        assert 50 <= modes.count("on") <= 150
        assert modes.count("on") + modes.count("off") == 200

    def test_a_quarter_share_is_mostly_off(self, tune):
        from moderation import preview

        tune(PREVIEW_SHARE=0.25)
        modes = [preview.preview_mode(bare_conversation().pk) for _ in range(300)]
        assert 30 <= modes.count("on") <= 130

    def test_the_draw_uses_secrets_not_the_random_module(self, tune, monkeypatch):
        from moderation import preview

        def forbidden(*args, **kwargs):
            raise AssertionError("the random module was used to draw the preview mode")

        tune(PREVIEW_SHARE=0.5)
        conv = bare_conversation()
        for name in ("random", "randint", "randrange", "choice", "choices", "uniform", "sample", "getrandbits", "shuffle"):
            monkeypatch.setattr(random, name, forbidden)
        assert preview.preview_mode(conv.pk) in ("on", "off")

    def test_the_draw_does_not_follow_the_seeded_random_module(self, tune):
        from moderation import preview

        tune(PREVIEW_SHARE=0.5)
        outcomes = []
        for _ in range(60):
            random.seed(12345)
            outcomes.append(preview.preview_mode(bare_conversation().pk))
        assert set(outcomes) == {"on", "off"}


class TestBothParticipantsShareTheMode:
    def test_a_check_by_either_participant_records_the_conversations_mode(self, tune, settings):
        """LLM off makes every check `unavailable` without a call, so many conversations can be checked cheaply."""
        tune(PREVIEW_SHARE=0.5)
        settings.LLM_ENABLED = False
        pairs = []
        for _ in range(12):
            w = prk.build([("A", "One thing."), ("B", "Another thing.")])
            a = pk.check(w, "A")[1]
            b = pk.check(w, "B")[1]
            pairs.append((a.mode, b.mode))
        assert all(a == b for a, b in pairs)
        assert len(mode_rows()) == 12

    def test_the_check_records_the_stored_mode(self, tune):
        from moderation import preview

        tune(PREVIEW_SHARE=0.0)
        w = prk.build([("A", "One thing."), ("B", "Another thing.")])
        stored = pk.check(w, "A")[1]
        assert stored.mode == "off" == preview.preview_mode(w.conv.pk)

    def test_checking_creates_the_mode_row_of_the_conversation(self, tune, fake):
        tune(PREVIEW_SHARE=1.0)
        w = pk.world()
        fake(pk.quiet_master())
        pk.check(w, "B")
        assert [(r.conversation_id, r.mode) for r in mode_rows()] == [(w.conv.pk, "on")]

    def test_both_participants_of_one_conversation_use_one_row(self, tune, fake):
        tune(PREVIEW_SHARE=1.0)
        w = pk.world()
        fake(pk.quiet_master(), pk.quiet_master())
        pk.check(w, "A")
        pk.check(w, "B")
        assert len(mode_rows()) == 1


class TestOffMeansNoModelCall:
    def make_off(self, tune):
        from moderation import preview

        tune(PREVIEW_SHARE=0.0)
        w = pk.world()
        assert preview.preview_mode(w.conv.pk) == "off"
        return w

    def test_a_check_in_an_off_conversation_is_unavailable_with_reason_off(self, tune, fake):
        w = self.make_off(tune)
        client = fake()
        returned, stored = pk.check(w, "B")
        for check in (returned, stored):
            assert (check.outcome, check.unavailable_reason, check.mode) == ("unavailable", "off", "off")
            assert check.note_texts == []
            assert check.master_output is None and check.intervenor_output is None
            assert check.llm_call_ids == []
        assert client.calls == []

    def test_an_off_check_writes_no_ledger_row_and_nothing_else_of_the_pipeline(self, tune, fake):
        w = self.make_off(tune)
        fake()
        before, calls = pk.counts(), pk.total_calls()
        pk.check(w, "B")
        assert pk.counts() == before
        assert pk.total_calls() == calls

    def test_an_off_check_still_records_the_draft_and_its_facts(self, tune, fake):
        w = self.make_off(tune)
        fake()
        stored = pk.check(w, "B")[1]
        assert (stored.conversation_id, stored.participant_id) == (w.conv.pk, w.parts["B"].pk)
        assert stored.draft_text == pk.DRAFT
        assert stored.draft_sha256 == pk.sha(pk.DRAFT)
        assert stored.snapshot_seq == 3
        assert (stored.action, stored.resulting_message_id, stored.reused_by_run_id, stored.resolved_at) == ("", None, None, None)

    def test_an_off_conversation_stays_off_even_when_the_share_is_raised_later(self, tune, fake):
        w = self.make_off(tune)
        tune(PREVIEW_SHARE=1.0)
        client = fake()
        stored = pk.check(w, "B")[1]
        assert stored.unavailable_reason == "off"
        assert client.calls == []
