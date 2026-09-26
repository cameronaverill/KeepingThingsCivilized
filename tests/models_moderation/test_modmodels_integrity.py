"""Whole-system properties of the moderation models: nobody ever holds a moderator-triggered run and no user message has two
live runs (property style); the LLMCall ledger stays free of foreign keys; deletion is deliberate (PROTECT); migrations are clean."""
import io
import random

import modmodels_testkit as kit
import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, models, transaction

pytestmark = pytest.mark.django_db


def test_property_no_run_has_a_moderator_trigger_and_at_most_one_live_run_per_user_message():
    """Create many user messages, runs (some duplicated, some replays), moderator posts and hostile attempts through every
    path; afterwards the database must satisfy both invariants, checked with plain SQL."""
    from forum.models import Message
    from moderation.models import ModerationRun

    rng = random.Random(4242)
    all_user, all_mod, refused = [], [], 0
    for _ in range(6):
        conv, parts = kit.make_conversation()
        previous = None
        for _ in range(rng.randint(4, 8)):
            user = kit.user_msg(conv, parts, rng.choice("AB"), in_reply_to=previous)
            all_user.append(user)
            run = kit.make_run(user)
            if rng.random() < 0.7:  # the moderator answers
                mod = kit.mod_msg(conv, in_reply_to=user)
                all_mod.append(mod)
                run.posted_message = mod
                run.status = "done"
                run.save()
                previous = mod
            else:
                previous = user
            for _ in range(rng.randint(0, 2)):  # replays repeat freely
                kit.make_run(user, kind="replay", replay_of=run, replicate=rng.randint(1, 4))
            # hostile attempts: a second live run, moderator triggers by every path
            some_mod = rng.choice([m for m in all_mod if m.conversation_id == conv.pk] or all_mod or [None])
            attempts = [lambda u=user: kit.make_run(u)]  # a second live run for the same user message
            if some_mod is not None:
                attempts += [
                    lambda m=some_mod: kit.make_run(m),
                    lambda m=some_mod: kit.raw_insert(kit.unsaved_run(m)),
                    lambda m=some_mod: ModerationRun.objects.bulk_create([kit.unsaved_run(m)]),
                    lambda r=run, m=some_mod: ModerationRun.objects.filter(pk=r.pk).update(trigger_message=m),
                ]
            for attempt in attempts:
                try:
                    with transaction.atomic():
                        attempt()
                except (ValidationError, IntegrityError):
                    refused += 1
    assert len(all_user) >= 20 and all_mod
    assert refused >= len(all_user)  # the attacks really ran and really failed

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM moderation_moderationrun r JOIN forum_message m ON m.id = r.trigger_message_id "
            "WHERE m.author_type <> 'user'"
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT trigger_message_id, COUNT(*) FROM moderation_moderationrun WHERE kind = 'live' GROUP BY trigger_message_id HAVING COUNT(*) > 1"
        )
        assert cursor.fetchall() == []
        cursor.execute(
            "SELECT COUNT(*) FROM moderation_moderationrun r JOIN forum_message m ON m.id = r.posted_message_id WHERE m.author_type <> 'moderator'"
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute("SELECT COUNT(*) FROM moderation_moderationrun WHERE kind = 'replay' AND posted_message_id IS NOT NULL")
        assert cursor.fetchone()[0] == 0
        cursor.execute("SELECT COUNT(*) FROM moderation_moderationrun r JOIN forum_message m ON m.id = r.trigger_message_id WHERE r.snapshot_seq <> m.seq_no")
        assert cursor.fetchone()[0] == 0
    # every user message got its live run, no moderator message got one, and the ORM agrees with SQL
    assert ModerationRun.objects.filter(kind="live").count() == len(all_user)
    assert not ModerationRun.objects.filter(trigger_message__author_type="moderator").exists()
    assert Message.objects.filter(author_type="moderator").count() == len(all_mod)


def test_a_moderator_post_never_creates_a_run_by_itself():
    from moderation.models import ModerationRun

    conv, parts = kit.make_conversation()
    user = kit.user_msg(conv, parts)
    kit.make_run(user)
    kit.mod_msg(conv, in_reply_to=user)
    assert ModerationRun.objects.count() == 1  # posting a moderator message enqueues nothing


# --- the ledger has no foreign keys ------------------------------------------------------------------------------------------------
def test_llmcall_has_no_relations_at_all():
    from moderation.models import LLMCall

    assert [f.name for f in LLMCall._meta.get_fields() if f.is_relation] == []


@pytest.mark.parametrize("name", ["run_id", "conversation_id"])
def test_llmcall_context_ids_stay_plain_integers(name):
    from moderation.models import LLMCall

    field = LLMCall._meta.get_field(name)
    assert isinstance(field, models.IntegerField) and not field.is_relation and field.null is True


def test_llmcall_table_has_no_foreign_keys_in_the_database():
    with connection.cursor() as cursor:
        assert connection.introspection.get_relations(cursor, "moderation_llmcall") == {}


def test_llmcall_accepts_ids_of_rows_that_do_not_exist():
    from moderation.models import LLMCall

    call = LLMCall.objects.create(purpose="moderation", model="m", max_tokens=1, run_id=987654, conversation_id=123456)
    call.refresh_from_db()
    assert (call.run_id, call.conversation_id) == (987654, 123456)


def test_llmcall_rows_survive_and_stay_linked_by_id_across_run_and_conversation_lifecycle():
    from moderation.models import LLMCall

    conv, parts = kit.make_conversation()
    user = kit.user_msg(conv, parts)
    run = kit.make_run(user)
    call = LLMCall.objects.create(purpose="moderation", model="m", max_tokens=1, run_id=run.pk, conversation_id=conv.pk)
    assert list(run.llm_calls()) == [call]
    # the ledger imposes no constraint on the run: PROTECT comes from the run's own dependants, never from LLMCall
    from moderation.models import ModerationRun

    ModerationRun.objects.filter(pk=run.pk).delete()
    call.refresh_from_db()
    assert call.run_id == run.pk  # the ledger row keeps its plain integer


def test_llm_calls_uses_the_run_id_filter():
    from moderation.models import LLMCall

    conv, parts = kit.make_conversation()
    run = kit.make_run(kit.user_msg(conv, parts))
    LLMCall.objects.create(purpose="moderation", model="m", max_tokens=1, run_id=run.pk)
    LLMCall.objects.create(purpose="judge", model="m", max_tokens=1, run_id=run.pk + 1000)
    assert run.llm_calls().count() == 1
    assert run.llm_calls().get().run_id == run.pk


# --- PROTECT: nothing cascades silently --------------------------------------------------------------------------------------------------
def test_deleting_what_a_run_depends_on_is_blocked():
    from django.db.models import ProtectedError

    conv, parts = kit.make_conversation()
    user = kit.user_msg(conv, parts)
    mod = kit.mod_msg(conv, in_reply_to=user)
    run = kit.make_run(user, posted_message=mod)
    for victim in (user, mod, conv):
        with pytest.raises(ProtectedError), transaction.atomic():
            victim.delete()
    live = run
    replay = kit.make_run(user, kind="replay", replay_of=live)
    with pytest.raises(ProtectedError), transaction.atomic():
        live.delete()  # a replay points at it
    replay.delete()
    live_pk = live.pk
    live.delete()  # nothing else refers to it now
    from moderation.models import ModerationRun

    assert not ModerationRun.objects.filter(pk=live_pk).exists()


# --- migrations ---------------------------------------------------------------------------------------------------------------------------
def test_makemigrations_check_is_clean_for_every_app():
    call_command("makemigrations", "--check", "--dry-run", stdout=io.StringIO(), stderr=io.StringIO())  # SystemExit(1) on drift


def test_the_moderation_and_forum_migrations_form_one_chain():
    from django.db.migrations.executor import MigrationExecutor

    graph = MigrationExecutor(connection).loader.graph
    leaves = {app: name for app, name in graph.leaf_nodes() if app in ("moderation", "forum")}
    assert leaves["moderation"].startswith("0004")
    assert "forum" in leaves
    assert graph.forwards_plan(("moderation", leaves["moderation"])).count(("forum", "0001_initial")) == 1
