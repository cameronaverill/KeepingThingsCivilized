"""Front-end fix 5 (docs/frontend_fixes_brief.md): compose.js says "character" for exactly 1 and "characters" otherwise, in both places
of the over-limit advice ("Your message is N character(s)" and "shorten it by M character(s)"), with thousands separators kept.
The limit is read from the textarea's data-limit, so the tests lower it to reach every count. Skipped when Node is missing."""
import pytest

import fviews_html as H
import fviews_js as J
import fviews_kit as K

pytestmark = pytest.mark.skipif(J.NODE is None, reason="Node is not installed")


def advice_for(limit, text, noun_page="conversation"):
    """The advice text compose.js writes for `text` when the box's limit is `limit` (None if no advice is shown)."""
    duo = K.Duo(names=(K.uniq("ffcp_a"), K.uniq("ffcp_b")))
    html = duo.ca.get(duo.url).content.decode()
    assert 'data-limit="3000"' in html, "the composer renders its limit in data-limit"
    html = html.replace('data-limit="3000"', f'data-limit="{limit}"', 1)
    page = J.Page(html, pathname=duo.url)
    form = H.form_with_action(page.root, duo.post_url)
    box = H.text_control(form)
    advice = page.root.find(**{"id": box.get("data-advice")})
    assert advice is not None
    page.add(op="load", script=J.script_path("compose.js"))
    page.add(op="set_value", eid=page.eid(box), value=text)
    page.add(op="fire", eid=page.eid(box), type="input")
    shown = page.add(op="text", eid=page.eid(advice))
    hidden = page.add(op="prop", eid=page.eid(advice), name="hidden")
    errors = page.add(op="errors")
    res = page.run()
    assert res[errors] == []
    return None if res[hidden] else res[shown]


def test_by_one_character_is_singular():
    advice = advice_for(5, "abcdef")  # 6 characters, limit 5
    assert "shorten it by 1 character." in advice
    assert "1 characters" not in advice
    assert "Your message is 6 characters;" in advice


@pytest.mark.parametrize("extra,word", [(2, "characters"), (3, "characters"), (10, "characters"), (11, "characters")])
def test_other_counts_stay_plural(extra, word):
    advice = advice_for(5, "x" * (5 + extra))
    assert f"shorten it by {extra} {word}." in advice
    assert f"shorten it by {extra} character." not in advice


def test_a_count_of_exactly_one_uses_the_same_helper_for_the_message_length():
    advice = advice_for(-1, "a")  # 1 character against a limit of -1 (a degenerate box, only to reach n == 1)
    assert advice is not None
    assert "Your message is 1 character;" in advice and "1 characters" not in advice


def test_zero_is_not_singular_for_the_length_either():
    """The empty box is never over the limit, so no advice; one over the limit of a tiny box reads plural for n >= 2."""
    advice = advice_for(1, "ab")  # 2 characters, limit 1 -> shorten by 1
    assert "Your message is 2 characters;" in advice
    assert "the limit is 1." in advice
    assert "shorten it by 1 character." in advice


def test_thousands_separators_are_kept_in_every_number():
    advice = advice_for(5, "y" * 4000)
    assert "Your message is 4,000 characters;" in advice
    assert "the limit is 5." in advice
    assert "shorten it by 3,995 characters." in advice


def test_thousands_separators_with_a_singular_difference():
    advice = advice_for(2999, "z" * 3000)
    assert "Your message is 3,000 characters;" in advice
    assert "the limit is 2,999." in advice
    assert "shorten it by 1 character." in advice


def test_the_full_sentence_is_otherwise_unchanged():
    advice = advice_for(5, "abcdefgh")
    assert advice == ("Your message is 8 characters; the limit is 5. Please shorten it by 3 characters. "
                      "You can still press the button; the site will explain if it cannot send it.")


def test_no_advice_at_or_under_the_limit():
    assert advice_for(5, "abcde") is None
    assert advice_for(5, "") is None


def test_zero_characters_is_plural():
    advice = advice_for(-1, "")  # an empty box against a degenerate limit of -1, only to reach n == 0
    assert advice is not None
    assert "Your message is 0 characters;" in advice and "0 character;" not in advice
    assert "shorten it by 1 character." in advice
