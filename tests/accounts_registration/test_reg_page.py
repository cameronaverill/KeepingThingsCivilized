"""Step 6c: the register page says exactly what the brief says (the owner reviews these sentences), links to login, and
mentions no email confirmation, no resend, no reset link, and nothing about costs."""
import re

import pytest

import reg_testkit as kit
from reg_testkit import BUTTON, INTRO, LOGIN_LINK_SENTENCE, PASSWORD_NOTE, TITLE

# Wording of the removed flows and anything about money. The brief's own sentences mention "email" (there is none), so
# the word alone is not banned; the phrases of the old flow are.
EMAIL_FLOW_WORDS = re.compile(
    r"confirm(ation)? (link|your (email|e-mail|address))|we (will )?(email|sent|send) you|check your (email|inbox|spam)|"
    r"spam|resend|new link|verify|verification|forgot your password|reset your password|email address is required",
    re.I,
)
MONEY_WORDS = re.compile(r"\bcosts?\b|\bbudgets?\b|\bspend|\bspending\b|\bpric(e|es|ing)\b|\bdollars?\b|\busd\b|\$", re.I)


@pytest.fixture
def response(client):
    return client.get(kit.url("register"))


def test_the_page_loads(response):
    assert response.status_code == 200
    assert "text/html" in response["Content-Type"]


def test_the_title_and_heading_are_create_an_account(response):
    page = kit.parse(response)
    assert TITLE in page.title
    assert page.headings == [TITLE]


def test_the_intro_sentence_is_exact(response):
    assert INTRO in kit.page_text(response)


def test_the_password_note_is_exact(response):
    assert PASSWORD_NOTE in kit.page_text(response)


def test_the_password_note_sits_under_the_password_fields(response):
    html = response.content.decode()
    last_password_input = max(m.start() for m in re.finditer(r'type="password"', html))
    assert html.index("There is no password reset by email.") > last_password_input


def test_the_submit_button_says_create_account(response):
    assert kit.parse(response).buttons == [BUTTON]


def test_the_login_sentence_and_link(response):
    assert LOGIN_LINK_SENTENCE in kit.page_text(response)
    # The site header already has a "Log in" link; the sentence adds a second one, whose text is the sentence or "Log in".
    login_links = [text for href, text in kit.parse(response).links if href == kit.url("login")]
    assert len(login_links) >= 2
    assert login_links[-1] in ("Log in", LOGIN_LINK_SENTENCE)


def test_the_login_link_goes_to_the_login_page(response, client):
    assert kit.url("login") in kit.link_targets(response)
    assert client.get(kit.url("login")).status_code == 200


def test_the_password_boxes_are_masked_and_named_for_password_managers(response):
    html = response.content.decode()
    assert len(re.findall(r'type="password"', html)) == 2
    assert len(re.findall(r'autocomplete="new-password"', html)) == 2
    assert re.search(r'autocomplete="username"', html)


def test_the_username_rules_are_stated_on_the_page(response, settings):
    text = kit.page_text(response)
    assert str(settings.USERNAME_MIN_LENGTH) in text and str(settings.USERNAME_MAX_LENGTH) in text


def test_the_password_rules_are_listed_on_the_page(response, settings):
    from django.contrib.auth.password_validation import password_validators_help_texts

    text = kit.page_text(response)
    texts = password_validators_help_texts()
    assert len(texts) == 4
    for help_text in texts:
        assert help_text in text
    assert f"at least {settings.PASSWORD_MIN_LENGTH} characters" in text


def test_there_is_no_email_field_or_label(response):
    page = kit.parse(response)
    inputs = [i for form in page.forms for i in form["inputs"]]
    assert [i for i in inputs if i["type"] == "email" or "email" in (i["name"] or "")] == []
    assert not re.search(r"<label[^>]*>[^<]*e-?mail", response.content.decode(), re.I)


def test_no_word_of_the_removed_email_flow_is_on_the_page(response):
    assert EMAIL_FLOW_WORDS.findall(kit.page_text(response)) == []
    assert not EMAIL_FLOW_WORDS.search(response.content.decode())


def test_the_page_links_to_none_of_the_removed_pages(response):
    targets = kit.link_targets(response)
    assert not [t for t in targets if "resend" in t or "confirm" in t or "check-email" in t or "password-reset" in t]


def test_the_page_mentions_no_costs_budgets_or_spending(response):
    assert MONEY_WORDS.findall(kit.page_text(response)) == []


@pytest.mark.parametrize(
    "typed, password",
    [("alice", "short"), ("bad name", kit.STRONG_A), ("", kit.STRONG_A)],
)
def test_the_page_after_a_refusal_keeps_the_same_texts_and_still_mentions_no_email_flow(client, typed, password):
    refused = kit.register(client, typed, password)
    text = kit.page_text(refused)
    assert refused.status_code == 200
    assert TITLE in kit.parse(refused).title and INTRO in text and PASSWORD_NOTE in text
    assert kit.parse(refused).buttons == [BUTTON]
    assert EMAIL_FLOW_WORDS.findall(text) == []
    assert MONEY_WORDS.findall(text) == []


def test_the_taken_username_refusal_page_names_no_email_flow_and_offers_login(client):
    from django.contrib.auth import get_user_model

    get_user_model().objects.create_user(username="alice", password=kit.STRONG_B)
    refused = kit.register(client, "alice", kit.STRONG_A)
    assert kit.TAKEN in kit.page_text(refused)
    assert EMAIL_FLOW_WORDS.findall(kit.page_text(refused)) == []
    assert kit.url("login") in kit.link_targets(refused)


def test_a_fresh_page_shows_no_errors_before_anything_is_submitted(response):
    html = response.content.decode()
    assert 'role="alert"' not in html
    assert "required" not in kit.page_text(response).lower()
    assert "errorlist" not in html
