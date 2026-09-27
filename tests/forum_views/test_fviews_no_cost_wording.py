"""7b: the pages never tell participants about API costs or spending, and the composer has no helper paragraph under the button."""
import pytest

import fviews_kit as K

BANNED = ("running cost", "api cost", "spending", "budget")


@pytest.mark.parametrize("path", ["/how-it-works/", "/propose/", "/", "/discussions/", "/blocked/"])
def test_pages_do_not_mention_costs(path):
    duo = K.Duo()
    text = duo.ca.get(path).content.decode().lower()
    for word in BANNED:
        assert word not in text, f"{path} mentions {word!r}"


def test_the_conversation_page_does_not_mention_costs_and_has_no_composer_hint():
    duo = K.Duo()
    text = duo.ca.get(duo.url).content.decode()
    lowered = text.lower()
    for word in BANNED:
        assert word not in lowered, f"the conversation page mentions {word!r}"
    assert "only the server decides" not in text
    assert "the wait only spaces them out" not in text
