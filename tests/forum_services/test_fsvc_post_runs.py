"""post_message and the moderation queue: exactly one pending live run per accepted user message, none for a refused post
or a moderator message, and the sync/worker modes (the pipeline is faked; step 5 builds the real one)."""
import ast
from pathlib import Path

import pytest
from fsvc_testkit import enter  # noqa: E402

from fsvc_testkit import (
    counts, make_active_via_service, orm_moderator_message, rejection, svc,
)  # fmt: skip

def runs():
    from moderation.models import ModerationRun

    return ModerationRun.objects.order_by("id")


@pytest.fixture
def world(clock):
    return make_active_via_service()


# --- exactly one pending live run ----------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_posting_creates_exactly_one_pending_live_run_for_the_message(world):
    message = svc().post_message(world.u1, world.conv, "hello there friend")
    (run,) = runs()
    assert run.trigger_message_id == message.pk
    assert run.conversation_id == world.conv.pk
    assert run.snapshot_seq == message.seq_no == 1
    assert run.kind == "live"
    assert run.status == "pending"
    assert run.replay_of_id is None
    assert run.posted_message_id is None
    assert run.attempts == 0
    assert run.is_stale is False


@pytest.mark.django_db
def test_each_accepted_message_gets_its_own_run_with_its_own_snapshot(world, clock):
    ids = []
    for i in range(3):
        clock.advance(31)
        ids.append(svc().post_message(world.u1 if i % 2 == 0 else world.u2, world.conv, f"message {i}"))
    rows = list(runs())
    assert [r.trigger_message_id for r in rows] == [m.pk for m in ids]
    assert [r.snapshot_seq for r in rows] == [1, 2, 3]
    assert all(r.kind == "live" and r.status == "pending" for r in rows)


@pytest.mark.django_db
def test_a_moderator_message_created_in_the_moderators_own_path_creates_no_run(world):
    """The moderator's posting path (step 5) is separate from post_message; a moderator Message never gets a run."""
    first = svc().post_message(world.u1, world.conv, "hello there friend")
    assert runs().count() == 1
    orm_moderator_message(world.conv, in_reply_to=first)
    assert runs().count() == 1
    assert not runs().filter(trigger_message__author_type="moderator").exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "text", ["", "   ", "a" * 3001]
)
def test_a_rejected_post_saves_nothing_and_creates_no_run(world, text):
    before = counts()
    rejection(svc().post_message, world.u1, world.conv, text)
    assert counts() == before
    assert runs().count() == 0


@pytest.mark.django_db
def test_a_too_fast_post_saves_nothing_and_creates_no_run(world):
    svc().post_message(world.u1, world.conv, "hello there friend")
    before = counts()
    rejection(svc().post_message, world.u1, world.conv, "again at once")
    assert counts() == before
    assert runs().count() == 1


@pytest.mark.django_db
def test_end_conversation_and_entering_create_no_runs(world):
    enter(world.u1, world.topic)
    svc().end_conversation(world.u1, world.conv)
    assert runs().count() == 0


# --- sync and worker mode ------------------------------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_sync_mode_calls_the_pipeline_once_with_the_run_after_the_commit(world, settings, fake_pipeline):
    settings.MODERATION_RUN_MODE = "sync"
    message = svc().post_message(world.u1, world.conv, "hello there friend")
    assert len(fake_pipeline.calls) == 1
    call = fake_pipeline.calls[0]
    assert call["trigger_message_id"] == message.pk
    assert call["in_atomic_block"] is False, "run_moderation must be called after the transaction has committed"
    assert call["message_row_exists"] is True
    assert call["run_row_status"] == "pending"
    assert call["run_pk"] == runs().get().pk


@pytest.mark.django_db(transaction=True)
def test_sync_mode_calls_the_pipeline_for_every_accepted_message(world, settings, fake_pipeline, clock):
    settings.MODERATION_RUN_MODE = "sync"
    for i in range(3):
        clock.advance(31)
        svc().post_message(world.u1, world.conv, f"message {i}")
    assert len(fake_pipeline.calls) == 3
    assert [c["run_pk"] for c in fake_pipeline.calls] == [r.pk for r in runs()]
    assert all(c["in_atomic_block"] is False for c in fake_pipeline.calls)


@pytest.mark.django_db(transaction=True)
def test_sync_mode_does_not_call_the_pipeline_for_a_rejected_post(world, settings, fake_pipeline):
    settings.MODERATION_RUN_MODE = "sync"
    rejection(svc().post_message, world.u1, world.conv, "")
    rejection(svc().post_message, world.u1, world.conv, "a" * 3001)
    assert fake_pipeline.calls == []


@pytest.mark.django_db(transaction=True)
def test_worker_mode_leaves_the_run_pending_and_does_not_call_the_pipeline(world, settings, fake_pipeline):
    settings.MODERATION_RUN_MODE = "worker"
    svc().post_message(world.u1, world.conv, "hello there friend")
    assert fake_pipeline.calls == []
    assert runs().get().status == "pending"


@pytest.mark.django_db(transaction=True)
def test_the_message_and_run_survive_when_the_pipeline_raises_in_sync_mode(world, settings, fake_pipeline):
    """The contract does not say whether post_message re-raises a pipeline error; either way the committed message and
    its pending run must remain (messages are always posted; a moderation problem never loses one)."""
    settings.MODERATION_RUN_MODE = "sync"

    def boom(run, *args, **kwargs):
        raise RuntimeError("the pipeline broke SECRET_INTERNAL_DETAIL")

    fake_pipeline.run_moderation = boom
    from forum.models import Message

    try:
        svc().post_message(world.u1, world.conv, "hello there friend")
    except Exception as exc:  # noqa: BLE001
        from forum.services import PostRejected

        assert not isinstance(exc, PostRejected) or "SECRET_INTERNAL_DETAIL" not in exc.message
    assert Message.objects.filter(conversation=world.conv, author_type="user").count() == 1
    assert runs().count() == 1


@pytest.mark.django_db(transaction=True)
def test_the_run_can_be_the_pipelines_input_a_real_moderation_run_instance(world, settings, fake_pipeline):
    from moderation.models import ModerationRun

    settings.MODERATION_RUN_MODE = "sync"
    seen = []
    fake_pipeline.run_moderation = lambda run, *a, **k: seen.append(run)
    svc().post_message(world.u1, world.conv, "hello there friend")
    assert len(seen) == 1 and isinstance(seen[0], ModerationRun) and seen[0].pk


# --- the pipeline is imported lazily -------------------------------------------------------------------------------------


def test_services_does_not_import_the_moderation_pipeline_at_module_level():
    import forum.services as services

    tree = ast.parse(Path(services.__file__).read_text())
    for node in tree.body:  # top level only
        if isinstance(node, ast.ImportFrom):
            assert node.module != "moderation.pipeline", "import moderation.pipeline lazily, inside the function"
            if node.module == "moderation":
                assert "pipeline" not in [alias.name for alias in node.names]
        if isinstance(node, ast.Import):
            assert "moderation.pipeline" not in [alias.name for alias in node.names]


def test_the_pipeline_import_is_inside_a_function():
    import forum.services as services

    tree = ast.parse(Path(services.__file__).read_text())
    found = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for inner in ast.walk(node):
                if isinstance(inner, ast.ImportFrom) and inner.module == "moderation.pipeline":
                    found = True
                if isinstance(inner, ast.ImportFrom) and inner.module == "moderation" and any(
                    alias.name == "pipeline" for alias in inner.names
                ):
                    found = True
    assert found, "no lazy import of moderation.pipeline inside a function was found"
