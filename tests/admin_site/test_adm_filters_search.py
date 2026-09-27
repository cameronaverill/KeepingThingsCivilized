"""Filters and search on the changelists: runs (status, kind, decision; search by conversation id), calls (status), messages
(author_type), and search by the ids the contract names. Every filter is checked against the exact set of expected rows."""
import pytest

import adm_kit as K


def ids(client, app, model, **params):
    response = client.get(K.url(app, model, "changelist"), params)
    assert response.status_code == 200, params
    return sorted(K.listed_pks(response, app, model))


def pks(*rows):
    return sorted(r.pk for r in rows)


# --- runs -------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "params,attrs",
    [
        ({"status__exact": "done"}, ("run_done", "run_replay")),
        ({"status__exact": "failed"}, ("run_failed",)),
        ({"status__exact": "pending"}, ("run_other",)),
        ({"status__exact": "running"}, ()),
        ({"kind__exact": "live"}, ("run_done", "run_failed", "run_other")),
        ({"kind__exact": "replay"}, ("run_replay",)),
        ({"decision__exact": "intervene"}, ("run_done",)),
        ({"decision__exact": "no_intervention"}, ("run_replay",)),
        ({"status__exact": "done", "kind__exact": "live"}, ("run_done",)),
        ({"status__exact": "done", "kind__exact": "replay", "decision__exact": "intervene"}, ()),
    ],
    ids=str,
)
def test_run_filters_narrow_the_list_to_exactly_the_matching_runs(root, world, params, attrs):
    assert ids(root, "moderation", "moderationrun", **params) == pks(*[getattr(world, a) for a in attrs])


def test_the_run_list_has_filters_for_status_kind_and_decision(root, world):
    html = K.page(root.get(K.url("moderation", "moderationrun", "changelist")))
    for label in ("By status", "By kind", "By decision"):
        assert label in html, label


def test_searching_runs_by_conversation_id_finds_that_conversations_runs(root, world):
    assert ids(root, "moderation", "moderationrun", q="7001") == pks(world.run_done, world.run_failed, world.run_replay)
    assert ids(root, "moderation", "moderationrun", q="8002") == pks(world.run_other)


def test_searching_runs_by_an_unknown_conversation_id_finds_nothing(root, world):
    assert ids(root, "moderation", "moderationrun", q="9999") == []


@pytest.mark.parametrize("term", ["Cats make better pets", "Trains", "zelda_mox", "intervene", "done", "live"], ids=str)
def test_the_run_search_is_by_conversation_id_only_not_by_text(root, world, term):
    assert ids(root, "moderation", "moderationrun", q=term) == []


def test_a_run_search_and_a_filter_combine(root, world):
    assert ids(root, "moderation", "moderationrun", q="7001", status__exact="failed") == pks(world.run_failed)


def test_the_run_list_is_not_confused_by_an_unknown_filter_value(root, world):
    assert ids(root, "moderation", "moderationrun", status__exact="no-such-status") == []


# --- calls ------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value,attrs",
    [
        ("ok", ("call1", "call_other")),
        ("error", ("call2",)),
        ("refused_budget", ("call_norun",)),
        ("refused_breaker", ()),
    ],
    ids=str,
)
def test_the_call_list_can_be_filtered_by_status(root, world, value, attrs):
    assert ids(root, "moderation", "llmcall", status__exact=value) == pks(*[getattr(world, a) for a in attrs])


def test_the_call_list_has_a_status_filter(root, world):
    assert "By status" in K.page(root.get(K.url("moderation", "llmcall", "changelist")))


def test_calls_can_be_found_by_run_id(root, world):
    assert ids(root, "moderation", "llmcall", q=str(world.run_done.pk)) == pks(world.call1, world.call2)


# --- messages, participants, conversations ---------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value,attrs",
    [("user", ("m1", "m3", "n1")), ("moderator", ("mod",)), ("nobody", ())],
    ids=str,
)
def test_messages_can_be_filtered_by_author_type(root, world, value, attrs):
    assert ids(root, "forum", "message", author_type__exact=value) == pks(*[getattr(world, a) for a in attrs])


def test_the_message_list_has_an_author_type_filter(root, world):
    assert "By author type" in K.page(root.get(K.url("forum", "message", "changelist")))


def test_the_message_list_shows_every_message_unfiltered(root, world):
    assert ids(root, "forum", "message") == pks(world.m1, world.mod, world.m3, world.n1)


def test_the_participant_list_shows_every_participant(root, world):
    assert ids(root, "forum", "participant") == pks(world.pa, world.pb, world.qa, world.qb)


# --- lists that do not filter still keep working with a query string --------------------------------------------------------

@pytest.mark.parametrize(
    "app,model",
    [("moderation", "issue"), ("moderation", "issuedisposition"), ("moderation", "interventionact"),
     ("forum", "conversation"), ("forum", "message"), ("forum", "participant")],
    ids=lambda v: v,
)
def test_a_search_that_matches_nothing_renders_an_empty_list(root, world, app, model):
    assert ids(root, app, model, q="zzzz-no-such-thing") == []


# --- id search is an exact match and never crashes -------------------------------------------------------------------------

ID_SEARCH_LISTS = [
    ("moderation", "moderationrun"), ("moderation", "llmcall"), ("moderation", "issue"), ("moderation", "issuedisposition"),
    ("moderation", "interventionact"), ("forum", "conversation"), ("forum", "message"), ("forum", "participant"),
]
ODD_TERMS = ["abc", "7001abc", "-1", "1.5", "99999999999999999999999999", "0x10", "%", "_", "'", '"; DROP TABLE x;--', "\u00e9\u00e8", "   ", "\u0000"]


@pytest.mark.parametrize("app,model", ID_SEARCH_LISTS, ids=lambda v: v)
@pytest.mark.parametrize("term", ODD_TERMS, ids=repr)
def test_a_non_numeric_or_odd_search_term_never_crashes_a_list(root, world, app, model, term):
    assert root.get(K.url(app, model, "changelist"), {"q": term}).status_code == 200


@pytest.mark.parametrize("term", ["abc", "7001abc", "1.5", "99999999999999999999999999"], ids=repr)
def test_an_odd_search_term_finds_no_runs(root, world, term):
    response = root.get(K.url("moderation", "moderationrun", "changelist"), {"q": term}, follow=True)
    assert response.status_code == 200
    assert K.listed_pks(response, "moderation", "moderationrun") == []


def test_a_conversation_id_search_matches_the_whole_number_only(root, world):
    for part in ("700", "001", "7", "1"):
        assert ids(root, "moderation", "moderationrun", q=part) == [], part


def test_a_run_id_search_on_calls_matches_the_whole_number_only(root, world):
    from moderation.models import LLMCall

    LLMCall.objects.create(purpose="moderation", run_id=12345, conversation_id=7001, agent="a", model="m", max_tokens=10)
    for part in ("1234", "2345", "123456", "234"):
        assert ids(root, "moderation", "llmcall", q=part) == [], part
    assert len(ids(root, "moderation", "llmcall", q="12345")) == 1
