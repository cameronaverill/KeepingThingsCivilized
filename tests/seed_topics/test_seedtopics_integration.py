"""10a: the seeded topics agree with the golden set, appear on the propose page (and on the home page once someone waits), can be entered, and the command touches
no secret file and no network (docs/step10a_brief.md, "Tests")."""
import builtins
import html
import json
import re
import socket
import sys

import pytest
from django.test import Client

import seedtopics_kit as K

MODEL_BACKEND = "django.contrib.auth.backends.ModelBackend"
TRANSCRIPTS = sorted(K.GOLDEN.glob("*.json"))


def transcript_topic(path):
    return json.loads(path.read_text(encoding="utf-8"))["topic"]


def logged_in(user):
    client = Client()
    client.force_login(user, backend=MODEL_BACKEND)
    return client


# --- agreement with the golden set -----------------------------------------------------------------------------------------

def test_there_are_golden_transcripts_to_compare_with():
    assert len(TRANSCRIPTS) == 42


@pytest.mark.parametrize("path", TRANSCRIPTS, ids=[p.stem for p in TRANSCRIPTS])
def test_every_golden_transcripts_topic_is_a_packaged_topic_word_for_word(path):
    topic = transcript_topic(path)
    packaged = K.by_title(K.packaged(), topic["title"])
    assert packaged["proposition"] == topic["proposition"]


def test_the_golden_set_uses_exactly_the_three_political_topics():
    pairs = {(transcript_topic(p)["title"], transcript_topic(p)["proposition"]) for p in TRANSCRIPTS}
    assert sorted(t for t, _ in pairs) == sorted(K.GOLDEN_TITLES)
    assert pairs == {(e["title"], e["proposition"]) for e in K.packaged() if not K.is_warmup(e)}


def test_the_seeded_rows_hold_the_golden_titles_and_propositions_exactly():
    K.run()
    for path in TRANSCRIPTS:
        topic = transcript_topic(path)
        assert K.topic_by_title(topic["title"]).proposition == topic["proposition"]


# --- the propose page (seeded topics with their two side buttons), the home page (waiting cards) and entering ----------

def lower_first(text):
    """The wording after "My position is that ": the first letter lower-cased (none of the six starts with a name)."""
    return text[0].lower() + text[1:]


def propose_page(user=None):
    return logged_in(user or K.make_user()).get("/propose/")


def propose_text(user=None):
    return html.unescape(propose_page(user).content.decode())


def home_forms(user):
    return K.enter_forms(logged_in(user).get("/").content.decode())


def test_the_propose_page_shows_the_section_for_seeded_topics_and_every_proposition_in_it():
    K.run()
    response = propose_page()
    page = html.unescape(response.content.decode())
    assert response.status_code == 200
    assert "Or start from one of these topics" in page
    assert [e["proposition"] for e in K.packaged() if e["proposition"] not in page] == []


def test_the_propose_page_has_no_seeded_section_when_nothing_is_seeded():
    assert "Or start from one of these topics" not in propose_text()


def test_every_seeded_topic_has_enter_forms_on_the_propose_page_and_nothing_else_does():
    from forum.models import Topic

    K.run()
    assert set(K.enter_forms(propose_page().content.decode())) == set(Topic.objects.values_list("id", flat=True))


def test_every_seeded_topic_has_exactly_one_button_for_each_position_with_the_side_posted():
    K.run()
    forms = K.enter_forms(propose_page().content.decode())
    assert [sorted(side or "" for side, _ in found) for found in forms.values()] == [["con", "pro"]] * 6


def test_every_seeded_topic_shows_both_positions_in_full_on_its_two_buttons():
    K.run()
    forms = K.enter_forms(propose_page().content.decode())
    shown = {title: dict(forms[K.topic_by_title(title).pk]) for title in (e["title"] for e in K.packaged())}
    assert shown == {
        e["title"]: {
            "pro": f"My position is that {lower_first(e['proposition'])}",
            "con": f"My position is that {lower_first(e['opposing_position'])}",
        }
        for e in K.packaged()
    }


def test_the_side_buttons_of_different_topics_never_swap_their_texts():
    K.run()
    forms = K.enter_forms(propose_page().content.decode())
    rent = dict(forms[K.topic_by_title("Rent control").pk])
    opposing = K.by_title(K.packaged(), "Rent control")["opposing_position"]
    assert rent["pro"] == f"My position is that {lower_first(K.RENT_PROPOSITION)}"
    assert rent["con"] == f"My position is that {lower_first(opposing)}"


def test_the_propose_page_shows_the_proposition_as_a_neutral_heading_and_never_the_draft_description():
    K.run()
    page = propose_text()
    assert K.packaged()[0]["description"] not in page
    assert K.RENT_PROPOSITION in page
    assert [e["title"] for e in K.packaged() if e["title"] in page and e["title"] not in e["proposition"]] == []


def test_a_hidden_seeded_topic_leaves_the_propose_page_with_both_of_its_wordings():
    from forum.models import Topic

    K.run()
    Topic.objects.filter(title="Rent control").update(hidden=True)
    forms = K.enter_forms(propose_page().content.decode())
    page = propose_text()
    assert K.topic_by_title("Rent control").pk not in forms
    assert len(forms) == 5
    assert K.RENT_PROPOSITION not in page
    assert K.by_title(K.packaged(), "Rent control")["opposing_position"] not in page


def test_a_user_created_topic_is_not_offered_in_the_seeded_section():
    K.run()
    K.make_user_topic(K.make_user(), "Coffee is better than tea.")
    forms = K.enter_forms(propose_page().content.decode())
    assert len(forms) == 6
    assert "Coffee is better than tea." not in propose_text().split("Or start from one of these topics")[1]


def test_a_user_with_a_conversation_on_a_seeded_topic_gets_the_open_variant_there_only():
    from forum.services import enter_proposition

    K.run()
    user = K.make_user()
    topic = K.topic_by_title("Rent control")
    enter_proposition(user, topic, "con")
    forms = K.enter_forms(propose_page(user).content.decode())
    assert forms[topic.pk] == [("con", "Open your conversation")]
    assert sorted(len(found) for found in forms.values()) == [1, 2, 2, 2, 2, 2]
    assert "You already have a conversation here." in propose_text(user)


def test_the_propose_page_for_seeded_topics_says_no_debate_and_names_no_cost():
    K.run()
    page = propose_page().content.decode().casefold()
    assert "debate" not in page
    assert [w for w in ("$", "dollar", "cost", " usd") if w in page] == []


def test_a_logged_out_visitor_is_not_shown_the_seeded_topics_on_the_propose_page():
    K.run()
    response = Client().get("/propose/")
    assert response.status_code == 302
    assert "Cities should cap" not in response.content.decode()


def test_the_home_page_lists_no_seeded_topic_until_someone_is_waiting_on_it():
    K.run()
    response = logged_in(K.make_user()).get("/")
    assert response.status_code == 200
    assert home_forms(K.make_user()) == {}
    assert "Cities should cap" not in html.unescape(response.content.decode())


@pytest.mark.parametrize(
    "waiter_side, quote_key, join_side, join_key",
    [("pro", "proposition", "con", "opposing_position"), ("con", "opposing_position", "pro", "proposition")],
    ids=["a_pro_waiter", "a_con_waiter"],
)
def test_a_seeded_topic_with_someone_waiting_is_a_waiting_card_with_the_join_button_for_the_opposite_side(
    waiter_side, quote_key, join_side, join_key
):
    from forum.services import enter_proposition

    K.run()
    entry = K.by_title(K.packaged(), "Rent control")
    topic = K.topic_by_title("Rent control")
    enter_proposition(K.make_user(), topic, waiter_side)
    response = logged_in(K.make_user()).get("/")
    page = html.unescape(response.content.decode())
    assert K.enter_forms(response.content.decode()) == {
        topic.pk: [(join_side, f"My position is that {lower_first(entry[join_key])}")]
    }
    assert entry[quote_key] in page


def test_a_waiting_person_does_not_see_their_own_seeded_topic_as_a_waiting_card():
    from forum.services import enter_proposition

    K.run()
    waiter = K.make_user()
    enter_proposition(waiter, K.topic_by_title("Rent control"), "pro")
    assert home_forms(waiter) == {}


def test_the_join_button_of_a_waiting_card_puts_the_joiner_on_the_opposite_side_in_an_active_conversation():
    from forum.models import Participant
    from forum.services import enter_proposition

    K.run()
    topic = K.topic_by_title("Rent control")
    enter_proposition(K.make_user(), topic, "pro")
    joiner = K.make_user()
    (join_side, _), = home_forms(joiner)[topic.pk]
    response = logged_in(joiner).post(f"/p/{topic.pk}/enter/", {"side": join_side})
    assert response.status_code == 302
    assert Participant.objects.get(user=joiner, conversation__topic=topic).side == "con"
    assert sorted(Participant.objects.filter(conversation__topic=topic).values_list("side", flat=True)) == ["con", "pro"]


@pytest.mark.parametrize("title", [e["title"] for e in K.packaged()] if K.PACKAGED.exists() else [])
def test_every_seeded_topic_can_be_entered_and_starts_a_waiting_conversation(title):
    from forum.services import enter_proposition

    K.run()
    topic = K.topic_by_title(title)
    user = K.make_user()
    conversation = enter_proposition(user, topic, "pro")
    assert (conversation.topic_id, conversation.status) == (topic.pk, "open")
    assert [(p.user_id, p.side) for p in conversation.participants.all()] == [(user.pk, "pro")]


@pytest.mark.parametrize("title", [e["title"] for e in K.packaged()] if K.PACKAGED.exists() else [])
def test_every_seeded_topic_can_be_entered_on_the_opposing_side_too(title):
    from forum.services import enter_proposition

    K.run()
    user = K.make_user()
    conversation = enter_proposition(user, K.topic_by_title(title), "con")
    assert [(p.user_id, p.side) for p in conversation.participants.all()] == [(user.pk, "con")]


def test_two_users_entering_a_seeded_topic_are_paired():
    from forum.services import enter_proposition

    K.run()
    title = next(e["title"] for e in K.packaged() if e["title"].casefold() == "bike lanes")
    topic = K.topic_by_title(title)
    first, second = K.make_user(), K.make_user()
    one = enter_proposition(first, topic, "pro")
    two = enter_proposition(second, topic, "con")
    assert one.pk == two.pk
    assert two.status == "active"
    assert sorted(p.side for p in two.participants.all()) == ["con", "pro"]


def test_two_users_choosing_the_same_side_of_a_seeded_topic_are_not_paired():
    from forum.services import enter_proposition

    K.run()
    topic = K.topic_by_title("Rent control")
    one = enter_proposition(K.make_user(), topic, "con")
    two = enter_proposition(K.make_user(), topic, "con")
    assert one.pk != two.pk
    assert (one.status, two.status) == ("open", "open")


def test_entering_through_the_page_form_puts_the_user_in_a_conversation():
    from forum.models import Conversation

    K.run()
    topic = K.topic_by_title("Rent control")
    response = logged_in(K.make_user()).post(f"/p/{topic.pk}/enter/", {"side": "pro"})
    assert response.status_code == 302
    assert re.fullmatch(r"/c/\d+/", response["Location"])
    assert Conversation.objects.filter(topic=topic).count() == 1


@pytest.mark.parametrize("side", ["pro", "con"])
def test_the_side_posted_by_a_propose_page_button_is_the_side_the_user_gets(side):
    from forum.models import Participant

    K.run()
    topic = K.topic_by_title("Rent control")
    user = K.make_user()
    forms = K.enter_forms(propose_page(user).content.decode())
    assert side in [posted for posted, _ in forms[topic.pk]]
    assert logged_in(user).post(f"/p/{topic.pk}/enter/", {"side": side}).status_code == 302
    assert Participant.objects.get(user=user, conversation__topic=topic).side == side


def test_seeded_rows_pass_the_models_own_validation():
    from forum.models import Topic

    K.run()
    for topic in Topic.objects.all():
        topic.full_clean()


# --- no secrets, no network, no API ----------------------------------------------------------------------------------------

def test_the_command_source_never_mentions_env_files_keys_or_the_api():
    source = K.COMMAND_SOURCE.read_text(encoding="utf-8")
    assert re.findall(r"\.env|dotenv|api[_-]?key|anthropic|environ|getenv|urllib|requests|http|socket", source, re.I) == []


def test_running_the_command_opens_no_env_file_and_only_the_seed_file(monkeypatch, tmp_path):
    opened = []
    real_open = builtins.open

    def spying_open(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    path = K.write_file(tmp_path, K.make_entries(2))
    monkeypatch.setattr(builtins, "open", spying_open)
    K.run_file(path)
    K.run()
    K.run("--dry-run")
    assert [name for name in opened if name.rstrip("/").endswith(".env") or "/.env" in name] == []


def test_running_the_command_makes_no_network_connection(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the seed command tried to use the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    K.run()
    K.run("--dry-run")
    assert K.topic_count() == 6


def test_running_the_command_does_not_load_the_anthropic_sdk_or_the_llm_module(monkeypatch):
    # monkeypatch.delitem puts every removed module back after the test. Deleting them for good would leave a second copy of
    # moderation.llm in memory, and later tests would then patch a different module object than the code under test uses.
    for name in [n for n in sys.modules if n == "anthropic" or n.startswith("anthropic.") or n == "moderation.llm"]:
        monkeypatch.delitem(sys.modules, name)
    K.run()
    assert [n for n in sys.modules if n == "anthropic" or n == "moderation.llm"] == []


def test_running_the_command_writes_no_llm_ledger_row():
    from moderation.models import LLMCall

    K.run()
    assert LLMCall.objects.count() == 0
