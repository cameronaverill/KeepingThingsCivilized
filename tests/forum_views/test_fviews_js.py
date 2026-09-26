"""7b: compose.js and poll.js behave as the brief says, run in Node against the real rendered pages with a small DOM
stand-in (tests/forum_views/fviews_jsrun.js). Skipped when Node is not installed; the text patterns in
test_fviews_static.py still apply then."""
import json
import re
from urllib.parse import parse_qs, urlparse

import pytest

import fviews_html as H
import fviews_js as J
import fviews_kit as K

pytestmark = pytest.mark.skipif(J.NODE is None, reason="Node is not installed")

FIXTURES = [
    "", " ", "\r\n", " ", "　 ", "a", "abc", "hello world", "  abc  ", "\n\nabc\n\n", "a   b", "a\tb",
    "a\r\nb", "a\rb", "a\nb", "a\r\n\r\nb", "a\r\rb", "a\r\r\nb", "a\n\rb", "line1\r\nline2\rline3\nline4",
    "é", "é", "café", "café", "한", "Å", "Å", "x̣", "à́",
    "\U0001f600", "\U0001f600" * 10, "hi \U0001f44d", "\U0001f44d\U0001f3fd", "\U0001f468‍\U0001f469‍\U0001f467",
    "ﬁ", "…", "ＡＢ", "日本語" * 100,
    "é" * 50, "  Café\r\nnaïve\rcafé  \U0001f600 ", "a" * 200, "a" * 201, "a" * 2999, "a" * 3000, "a" * 3001,
    "  " + "b" * 100 + "\r\n", "\U0001f600" * 3001,
    # whitespace edge cases where JavaScript's own trim() and Python's strip() disagree
    "\x85abc\x85", "\x1cabc\x1f", "\ufeffabc\ufeff", "\u200babc\u200b", "\u180eabc\u180e", "abc\u2028", "\u2029abc", "\u0085",
]


def python_count(text):
    from forum.limits import count_message_chars

    return count_message_chars(text)


def counter_of(root):
    nodes = H.counter_nodes(root)
    assert nodes, "the page has no counter markup"
    inner = [n for n in nodes if not any(d in nodes for d in n.walk())]
    return (inner or nodes)[0]


def parse_counter(text):
    found = re.search(r"([\d,]+)\s*(?:/|of)\s*([\d,]+)", text)
    assert found, f"counter text should read 'N / LIMIT' (the plan's '2,950 / 3,000'), got {text!r}"
    return int(found.group(1).replace(",", "")), int(found.group(2).replace(",", ""))


def run_counting(html, form_action=None, script="compose.js"):
    page = J.Page(html)
    root = page.root
    if form_action:
        form = H.form_with_action(root, form_action)
    else:
        form = next(f for f in H.forms(root) if H.text_control(f) is not None)
    textarea = H.text_control(form)
    counter = counter_of(root)
    button = next(n for n in form.walk() if n.tag == "button")
    page.add(op="load", script=J.script_path(script))
    indices = []
    for text in FIXTURES:
        page.add(op="set_value", eid=page.eid(textarea), value=text)
        for event in ("input", "keyup", "change"):
            page.add(op="fire", eid=page.eid(textarea), type=event)
        indices.append(page.add(op="text", eid=page.eid(counter)))
    tail = {
        "value": page.add(op="value", eid=page.eid(textarea)),
        "disabled": page.add(op="prop", eid=page.eid(button), name="disabled"),
        "maxlength": page.add(op="attr", eid=page.eid(textarea), name="maxlength"),
        "errors": page.add(op="errors"),
    }
    results = page.run()
    return [results[i] for i in indices], {k: results[v] for k, v in tail.items()}


@pytest.fixture
def duo_html():
    duo = K.Duo()
    return duo, duo.ca.get(duo.url).content.decode()


# --- compose.js: the counting rule ------------------------------------------------------------------------------------

def test_the_message_counter_agrees_with_count_message_chars_on_every_fixture(duo_html):
    duo, html = duo_html
    texts, tail = run_counting(html, duo.post_url)
    assert tail["errors"] == [], tail["errors"]
    for text, shown in zip(FIXTURES, texts):
        count, limit = parse_counter(shown)
        assert limit == 3000, (text[:20], shown)
        assert count == python_count(text), f"{text[:30]!r}: page says {count}, server counts {python_count(text)}"


def test_the_proposition_counter_agrees_too_and_shows_the_200_limit():
    client = K.client_for(K.make_user())
    html = client.get("/propose/").content.decode()
    texts, tail = run_counting(html)
    assert tail["errors"] == [], tail["errors"]
    for text, shown in zip(FIXTURES, texts):
        count, limit = parse_counter(shown)
        assert limit == 200, shown
        assert count == python_count(text), f"{text[:30]!r}: page says {count}, server counts {python_count(text)}"


def test_the_counter_only_advises_the_box_and_button_are_left_alone(duo_html):
    duo, html = duo_html
    _, tail = run_counting(html, duo.post_url)
    assert tail["value"] == FIXTURES[-1], "the typed text is never changed or cut"
    assert tail["disabled"] is False, "the button stays enabled; the server explains"
    assert tail["maxlength"] is None


def test_the_counter_reads_its_limit_from_the_page_settings(settings):
    settings.MAX_MESSAGE_CHARS = 1234
    duo = K.Duo()
    texts, _ = run_counting(duo.ca.get(duo.url).content.decode(), duo.post_url)
    assert parse_counter(texts[FIXTURES.index("abc")]) == (3, 1234)


# --- poll.js ----------------------------------------------------------------------------------------------------------

BASE_MS = 7000


class PollHarness:
    """Loads poll.js against the real conversation page of viewer A. Answers to its polls are the real answers of the
    real endpoint, captured while the scenario is set up, so the test does not guess the JSON shape."""

    def __init__(self, settings, seeded=3):
        settings.POLL_SECONDS = BASE_MS // 1000
        self.duo = K.Duo()
        self.tokens = []
        self.seeded = []
        for i in range(seeded):
            self.tokens.append(f"Seeded-token-{i + 1}")
            self.seeded.append(self.duo.seed(self.duo.pa if i % 2 == 0 else self.duo.pb, self.tokens[-1], 60 - i))
        html = self.duo.ca.get(self.duo.url).content.decode()
        self.page = J.Page(html, pathname=self.duo.url)
        self.textarea = H.text_control(H.form_with_action(self.page.root, self.duo.post_url))
        self.ta = self.page.eid(self.textarea)
        self.page.add(op="load", script=J.script_path("poll.js"))

    # -- changing the conversation after the page was rendered --
    def other_says(self, text):
        return self.duo.seed(self.duo.pb, text)

    def you_say(self, text):
        return self.duo.seed(self.duo.pa, text)

    def moderator_answers(self, trigger, text, heading_subject="A"):
        label = self.duo.pa.label if heading_subject == "A" else self.duo.pb.label
        return K.add_moderator_post(self.duo.conv, trigger, text, [(label, label, [trigger])])

    def end(self):
        self.duo.cb.post(self.duo.end_url)

    def real(self, after):
        """The endpoint's real answer for viewer A, as a queued response."""
        response = self.duo.ca.get(self.duo.poll_url, {"after": after})
        assert response.status_code == 200
        return {"status": 200, "json": json.loads(response.content)}

    # -- script operations --
    def queue(self, *responses):
        return self.page.add(op="queue_fetch", responses=list(responses))

    def tick(self):
        return self.page.add(op="run_timer")

    def timers(self):
        return self.page.add(op="timers")

    def body_text(self):
        return self.page.add(op="dom_text", eid=self.page.eid(self.page.root.find("body")))

    def fetches(self):
        return self.page.add(op="fetch_log")

    def run(self):
        return self.page.run()


@pytest.fixture
def poll(settings):
    return PollHarness(settings)


def count(text, token):
    return text.count(token)


def test_polling_is_scheduled_at_the_interval_from_the_page_and_loads_without_errors(poll):
    t = poll.timers()
    e = poll.page.add(op="errors")
    results = poll.run()
    assert results[e] == []
    delays = [x["delay"] for x in results[t]]
    assert delays and min(delays) == BASE_MS, f"the next poll must be scheduled {BASE_MS} ms out, timers: {results[t]}"


def test_new_messages_are_appended_using_the_last_seen_sequence_number(poll):
    poll.other_says("Polled-token-4")
    poll.you_say("Polled-token-5")
    poll.queue(poll.real(after=3))
    poll.tick()
    f = poll.fetches()
    b = poll.body_text()
    reloads = poll.page.add(op="reloads")
    results = poll.run()
    last = results[f][-1]
    parsed = urlparse(last["url"])
    assert parsed.path == poll.duo.poll_url
    assert parse_qs(parsed.query).get("after") == ["3"]
    assert (last["options"] or {}).get("method", "GET").upper() == "GET"
    text = results[b]
    assert count(text, "Polled-token-4") == 1 and count(text, "Polled-token-5") == 1
    assert results[reloads] == 0, "messages are appended without reloading the page"


def test_a_message_is_never_shown_twice_even_if_the_server_repeats_it(poll):
    poll.other_says("Polled-token-4")
    poll.queue(poll.real(after=3))
    poll.tick()
    poll.queue(poll.real(after=3))  # the same answer again, as from a server that ignores `after`
    poll.tick()
    poll.queue(poll.real(after=0))  # everything, including what the page showed from the start
    poll.tick()
    f = poll.fetches()
    b = poll.body_text()
    results = poll.run()
    text = results[b]
    assert count(text, "Polled-token-4") == 1
    for token in poll.tokens:
        assert count(text, token) == 1, f"{token} was drawn again"
    assert parse_qs(urlparse(results[f][-1]["url"]).query).get("after") == ["4"], "after= follows the newest message seen"


def test_appended_messages_carry_the_speaker_label_and_the_moderator_card_its_heading(poll):
    before = poll.body_text()
    poll.other_says("Polled-token-4")
    mine = poll.you_say("Polled-token-5")
    poll.moderator_answers(mine, "Moderator-words")
    poll.queue(poll.real(after=3))
    poll.tick()
    after = poll.body_text()
    results = poll.run()
    b, a = results[before], results[after]
    assert a.count("The other participant") > b.count("The other participant")
    assert len(re.findall(r"\bYou\b", a)) > len(re.findall(r"\bYou\b", b))
    assert "About your message 5" in a and "Moderator-words" in a
    assert "moderator" in a.lower()


def test_polled_text_is_never_run_as_markup(poll):
    nasty = "<img src=x onerror=alert(1)>Token-xss <b>bold</b>"
    poll.other_says(nasty)
    poll.queue(poll.real(after=3))
    poll.tick()
    b = poll.body_text()
    h = poll.page.add(op="html_log")
    results = poll.run()
    assert nasty in results[b], "the text is shown as text"
    for chunk in results[h]:
        assert "<img" not in chunk and "<b>bold" not in chunk, f"unescaped message markup handed to innerHTML: {chunk[:80]!r}"


def test_polling_does_not_disturb_what_the_user_is_typing(poll):
    poll.page.add(op="focus", eid=poll.ta)
    poll.page.add(op="set_value", eid=poll.ta, value="my half typed reply")
    poll.other_says("Polled-token-4")
    poll.queue(poll.real(after=3))
    poll.tick()
    mine = poll.you_say("Polled-token-5")
    poll.moderator_answers(mine, "Mod text")
    poll.queue(poll.real(after=4))
    poll.tick()
    v = poll.page.add(op="value", eid=poll.ta)
    c = poll.page.add(op="connected", eid=poll.ta)
    active = poll.page.add(op="active")
    focus = poll.page.add(op="focus_log")
    errors = poll.page.add(op="errors")
    results = poll.run()
    assert results[v] == "my half typed reply"
    assert results[c] is True, "the composer must not be replaced or removed"
    assert results[active] == poll.ta and results[focus] == [], "focus is not taken away or moved"
    assert results[errors] == []


def test_polling_backs_off_after_errors(poll):
    poll.queue({"reject": True})
    poll.tick()
    first = poll.timers()
    poll.queue({"status": 500, "json": {}})
    poll.tick()
    second = poll.timers()
    poll.queue({"status": 200, "body": "<html>not json</html>"})
    poll.tick()
    third = poll.timers()
    errors = poll.page.add(op="errors")
    results = poll.run()
    d1, d2, d3 = (min(x["delay"] for x in results[i]) for i in (first, second, third))
    assert d1 > BASE_MS, f"after a network error the next poll must wait longer than {BASE_MS} ms, got {d1}"
    assert d2 >= d1 and d2 > BASE_MS, f"and keep backing off after a server error: {d1} then {d2}"
    assert d3 > BASE_MS, "an unreadable answer is an error too"
    assert results[errors] == [], "a failed poll must be handled, not thrown"


def test_polling_keeps_going_after_a_short_outage(poll):
    poll.queue({"reject": True})
    poll.tick()
    poll.other_says("Polled-token-4")
    poll.queue(poll.real(after=3))
    poll.tick()
    b = poll.body_text()
    t = poll.timers()
    results = poll.run()
    assert "Polled-token-4" in results[b]
    assert results[t], "still polling"


def test_polling_stops_when_the_conversation_is_closed(poll):
    poll.other_says("Last words")
    poll.end()
    poll.queue(poll.real(after=3))
    poll.tick()
    b = poll.body_text()
    t = poll.timers()
    f_before = poll.fetches()
    again = poll.tick()
    f_after = poll.fetches()
    results = poll.run()
    assert "Last words" in results[b], "the final messages are still shown"
    assert results[t] == [], "no timer is left running"
    assert results[again] == {"ran": False}
    assert len(results[f_after]) == len(results[f_before])
