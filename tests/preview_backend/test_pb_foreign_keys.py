"""The preview tables' ids are real foreign keys (docs/fk_cleanup_brief.md): the database refuses a made-up id, PROTECT blocks
deleting the referenced row, the uniqueness of PreviewMode.conversation holds, and the ledger ids stay plain."""
import preview_kit as pk
import pipeline_run_kit as prk
import pytest
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.utils import timezone

MADE_UP = 987654


def check_values(w, **overrides):
    values = dict(
        conversation_id=w.conv.pk, participant_id=w.parts["A"].pk, draft_text="A draft.", char_count=8,
        draft_sha256=pk.sha("A draft."), snapshot_seq=0, mode="on", outcome="no_concern", unavailable_reason="",
        note_texts=[], master_output=None, intervenor_output=None, llm_call_ids=[], action="",
    )  # fmt: skip
    values.update(overrides)
    return values


def small_world():
    return pk.world([("A", "One thing."), ("B", "Another thing.")])


def make_check(w, **overrides):
    from moderation.models import PreviewCheck

    return PreviewCheck.objects.create(**check_values(w, **overrides))


@pytest.mark.django_db(transaction=True)
class TestTheDatabaseRefusesAMadeUpId:
    """A real commit is needed: SQLite checks foreign keys when the transaction commits."""

    def test_a_check_for_a_conversation_that_does_not_exist(self):
        w = small_world()
        with pytest.raises(IntegrityError):
            make_check(w, conversation_id=MADE_UP)

    def test_a_check_for_a_participant_that_does_not_exist(self):
        w = small_world()
        with pytest.raises(IntegrityError):
            make_check(w, participant_id=MADE_UP)

    def test_a_check_with_a_resulting_message_that_does_not_exist(self):
        w = small_world()
        with pytest.raises(IntegrityError):
            make_check(w, resulting_message_id=MADE_UP)

    def test_a_check_reused_by_a_run_that_does_not_exist(self):
        w = small_world()
        with pytest.raises(IntegrityError):
            make_check(w, reused_by_run_id=MADE_UP)

    def test_a_mode_for_a_conversation_that_does_not_exist(self):
        from moderation.models import PreviewMode

        with pytest.raises(IntegrityError):
            PreviewMode.objects.create(conversation_id=MADE_UP, mode="on", assigned_at=timezone.now())

    def test_real_ids_are_accepted_so_the_refusals_are_not_vacuous(self):
        w = small_world()
        run = prk.new_run(w.last)
        check = make_check(w, resulting_message_id=w.last.pk, reused_by_run_id=run.pk)
        assert (check.conversation_id, check.participant_id, check.resulting_message_id, check.reused_by_run_id) == (
            w.conv.pk, w.parts["A"].pk, w.last.pk, run.pk,
        )

    def test_the_two_optional_links_may_be_empty(self):
        check = make_check(small_world())
        assert (check.resulting_message_id, check.reused_by_run_id) == (None, None)

    def test_the_ledger_ids_take_any_number_and_the_ledger_is_never_blocked(self):
        from moderation.models import LLMCall

        w = small_world()
        make_check(w, llm_call_ids=[MADE_UP, MADE_UP + 1])
        row = LLMCall.objects.create(
            purpose="moderation", conversation_id=MADE_UP, run_id=MADE_UP, agent="master", model="m", max_tokens=10
        )
        assert (row.conversation_id, row.run_id) == (MADE_UP, MADE_UP)

    def test_a_raw_delete_of_a_participant_only_a_check_points_at_is_refused_until_the_check_is_gone(self):
        from django.db import connection

        from forum.models import Message

        conv, part, message = lone_rows()
        Message.objects.filter(pk=message.pk).delete()  # only the check may point at the participant
        check = PreviewCheckFor(conv, part)

        def raw_delete():
            with connection.cursor() as cursor:
                cursor.execute("delete from forum_participant where id = %s", [part.pk])

        with pytest.raises(IntegrityError):
            raw_delete()
        check.delete()
        raw_delete()


def lone_rows():
    """A conversation, one participant of it and one message of that participant, with nothing else pointing at them."""
    from forum.models import Conversation, Message, Participant, Topic

    topic = Topic.objects.create(title=f"fk topic {prk.n()}", description="d", proposition="Cities should plant more trees.")
    conv = Conversation.objects.create(topic=topic, source="synthetic")
    part = Participant.objects.create(conversation=conv, label="A", join_order=1)
    message = Message.objects.create(conversation=conv, author_type="user", participant=part, content="A first point.")
    return conv, part, message


def PreviewCheckFor(conv, part, **overrides):
    from moderation.models import PreviewCheck

    values = dict(
        conversation_id=conv.pk, participant_id=part.pk, draft_text="A draft.", char_count=8, draft_sha256=pk.sha("A draft."),
        snapshot_seq=0, mode="on", outcome="no_concern", unavailable_reason="", note_texts=[], master_output=None,
        intervenor_output=None, llm_call_ids=[], action="",
    )  # fmt: skip
    values.update(overrides)
    return PreviewCheck.objects.create(**values)


def blockers(error):
    return sorted({type(o).__name__ for o in error.value.protected_objects})


@pytest.mark.django_db
class TestProtect:
    def test_a_conversation_with_a_mode_cannot_be_deleted_until_the_mode_is_gone(self):
        from forum.models import Conversation, Topic
        from moderation import preview
        from moderation.models import PreviewMode

        topic = Topic.objects.create(title=f"fk topic {prk.n()}", description="d", proposition="Cities should plant more trees.")
        conv = Conversation.objects.create(topic=topic, source="synthetic")
        preview.preview_mode(conv.pk)
        with pytest.raises(ProtectedError) as error:
            conv.delete()
        assert blockers(error) == ["PreviewMode"]
        assert Conversation.objects.filter(pk=conv.pk).exists()
        PreviewMode.objects.filter(conversation_id=conv.pk).delete()
        conv.delete()
        assert not Conversation.objects.filter(pk=conv.pk).exists()

    def test_a_conversation_with_a_check_is_blocked_by_the_check(self):
        conv, part, message = lone_rows()
        PreviewCheckFor(conv, part)
        with pytest.raises(ProtectedError) as error:
            conv.delete()
        assert "PreviewCheck" in blockers(error)

    def test_a_participant_a_check_points_at_cannot_be_deleted_until_the_check_is_gone(self):
        from forum.models import Message, Participant

        conv, part, message = lone_rows()
        Message.objects.filter(pk=message.pk).delete()  # only the check may now point at the participant
        check = PreviewCheckFor(conv, part)
        with pytest.raises(ProtectedError) as error:
            part.delete()
        assert blockers(error) == ["PreviewCheck"]
        assert list(error.value.protected_objects) == [check]
        check.delete()
        part.delete()
        assert not Participant.objects.filter(pk=part.pk).exists()

    def test_a_message_that_became_a_checks_result_cannot_be_deleted_until_the_check_is_gone(self):
        from forum.models import Message

        conv, part, message = lone_rows()
        check = PreviewCheckFor(conv, part, resulting_message_id=message.pk)
        with pytest.raises(ProtectedError) as error:
            message.delete()
        assert list(error.value.protected_objects) == [check]
        check.delete()
        message.delete()
        assert not Message.objects.filter(pk=message.pk).exists()

    def test_a_run_that_reused_a_check_cannot_be_deleted_until_the_check_is_gone(self):
        from moderation.models import ModerationRun

        conv, part, message = lone_rows()
        run = ModerationRun.objects.create(conversation=conv, trigger_message=message, snapshot_seq=message.seq_no, kind="live")
        check = PreviewCheckFor(conv, part, reused_by_run_id=run.pk)
        with pytest.raises(ProtectedError) as error:
            run.delete()
        assert list(error.value.protected_objects) == [check]
        check.delete()
        run.delete()
        assert not ModerationRun.objects.filter(pk=run.pk).exists()

    def test_deleting_a_check_does_not_touch_what_it_pointed_at(self):
        from forum.models import Conversation, Message, Participant

        conv, part, message = lone_rows()
        PreviewCheckFor(conv, part, resulting_message_id=message.pk).delete()
        assert (Conversation.objects.filter(pk=conv.pk).exists(), Participant.objects.filter(pk=part.pk).exists(),
                Message.objects.filter(pk=message.pk).exists()) == (True, True, True)

    def test_the_ledger_never_blocks_a_delete_and_keeps_its_rows(self):
        from forum.models import Conversation, Topic
        from moderation.models import LLMCall

        topic = Topic.objects.create(title=f"fk topic {prk.n()}", description="d", proposition="Cities should plant more trees.")
        conv = Conversation.objects.create(topic=topic, source="synthetic")
        row = LLMCall.objects.create(purpose="moderation", conversation_id=conv.pk, agent="master", model="m", max_tokens=10)
        conv.delete()
        row.refresh_from_db()
        assert row.conversation_id is not None


@pytest.mark.django_db
class TestUniquenessAndInterface:
    def test_a_second_mode_row_for_a_conversation_is_refused(self):
        from django.db import transaction

        from moderation.models import PreviewMode

        conv, _, _ = lone_rows()
        PreviewMode.objects.create(conversation=conv, mode="on", assigned_at=timezone.now())
        with pytest.raises(IntegrityError), transaction.atomic():
            PreviewMode.objects.create(conversation=conv, mode="off", assigned_at=timezone.now())

    def test_preview_mode_takes_an_id_or_a_conversation_and_creates_one_row(self, tune):
        from moderation import preview
        from moderation.models import PreviewMode

        tune(PREVIEW_SHARE=0.5)
        conv, _, _ = lone_rows()
        by_id = preview.preview_mode(conv.pk)
        assert preview.preview_mode(conv) == by_id
        assert PreviewMode.objects.filter(conversation=conv).count() == 1

    def test_a_check_links_to_the_real_rows(self, fake):
        w = pk.world()
        fake(*pk.concern_script())
        stored = pk.check(w, "B")[1]
        assert (stored.conversation, stored.participant, stored.resulting_message, stored.reused_by_run) == (
            w.conv, w.parts["B"], None, None,
        )

    def test_resolving_links_the_message(self, fake):
        from moderation import preview

        w = pk.world()
        fake(*pk.concern_script())
        stored = pk.check(w, "B")[1]
        message = w.add_user("B", pk.DRAFT)
        preview.resolve_check(stored.pk, "posted_as_written", message_id=message.pk)
        stored.refresh_from_db()
        assert stored.resulting_message == message

    def test_a_reusing_run_is_linked(self, fake):
        w = pk.world()
        fake(*pk.concern_script())
        stored = pk.check(w, "B")[1]
        fake()
        message = w.add_user("B", pk.DRAFT)
        _, run = prk.go(prk.new_run(message))
        stored.refresh_from_db()
        assert stored.reused_by_run == run
