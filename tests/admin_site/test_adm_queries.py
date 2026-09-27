"""The changelists do not run more queries as rows are added (no N+1 on the foreign keys they display)."""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

import adm_kit as K

LISTS = [
    ("moderation", "moderationrun"),
    ("moderation", "issue"),
    ("moderation", "issuedisposition"),
    ("moderation", "interventionact"),
    ("moderation", "llmcall"),
    ("forum", "conversation"),
    ("forum", "message"),
    ("forum", "participant"),
    ("forum", "topic"),
    ("accounts", "user"),
]


def add_rows(world, n, start):
    """n more user messages in conversation 7001, each with a live run, an issue, a disposition, an act and a call."""
    from forum.models import Conversation, Message, Participant, Topic
    from moderation.models import InterventionAct, Issue, IssueDisposition, LLMCall, ModerationRun

    for i in range(start, start + n):
        user = K.make_user(f"bulkuser{i}")
        topic = Topic.objects.create(proposition=f"Bulk proposition {i}.", created_by=user)
        conv = Conversation.objects.create(topic=topic, status="active")
        part = Participant.objects.create(conversation=conv, user=user, label="A", join_order=1)
        msg = Message.objects.create(conversation=conv, author_type="user", participant=part, content=f"Bulk message {i} here.")
        run = ModerationRun.objects.create(conversation=conv, trigger_message=msg, snapshot_seq=msg.seq_no)
        issue = Issue.objects.create(run=run, local_id=f"B{i}", message=msg, issue_type="unsupported_claim", confidence=0.5)
        IssueDisposition.objects.create(issue=issue, disposition="declined", reason="r")
        InterventionAct.objects.create(run=run, order=1, act_type="request_information", tone="neutral", text="Why?", addressee="A", subject="none")
        LLMCall.objects.create(purpose="moderation", run_id=run.pk, agent="a", model="m", max_tokens=10)


def query_count(client, app, model):
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(K.url(app, model, "changelist"))
    assert response.status_code == 200
    return len(ctx)


@pytest.mark.parametrize("app,model", LISTS, ids=lambda v: v)
def test_a_changelist_runs_the_same_number_of_queries_for_3_rows_and_for_20_more(root, world, app, model):
    add_rows(world, 3, 0)
    query_count(root, app, model)  # warm-up
    small = query_count(root, app, model)
    add_rows(world, 20, 100)
    large = query_count(root, app, model)
    assert large == small


@pytest.mark.parametrize("app,model", [("moderation", "moderationrun"), ("moderation", "issue")], ids=lambda v: v)
def test_a_change_page_runs_the_same_number_of_queries_however_many_rows_the_run_has(root, world, app, model):
    add_rows(world, 3, 0)
    row = world.rows(app, model)[0]
    query_count_page(root, app, model, row.pk)  # warm-up: the first request of a session loads the user and content types
    first = query_count_page(root, app, model, row.pk)
    add_rows(world, 20, 100)
    second = query_count_page(root, app, model, row.pk)
    assert second == first


def query_count_page(client, app, model, pk):
    with CaptureQueriesContext(connection) as ctx:
        assert client.get(K.url(app, model, "change", pk)).status_code == 200
    return len(ctx)
