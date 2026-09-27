"""7b: owner rule, the site never says "debate" (it uses gentler words such as "discuss")."""
import pytest

import fviews_kit as K


@pytest.mark.parametrize("path", ["/how-it-works/", "/propose/", "/", "/discussions/", "/blocked/"])
def test_pages_do_not_use_the_word_debate(path):
    duo = K.Duo()
    text = duo.ca.get(path).content.decode().lower()
    assert "debat" not in text


def test_the_conversation_page_does_not_use_the_word_debate():
    duo = K.Duo()
    assert "debat" not in duo.ca.get(duo.url).content.decode().lower()


def test_the_propose_page_calls_itself_a_discussion():
    duo = K.Duo()
    text = duo.ca.get("/propose/").content.decode()
    assert "Start a new discussion" in text
