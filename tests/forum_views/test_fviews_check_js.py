"""Step 19: compose.js checks the draft before a real post (docs/step19_composer_brief.md). Run in Node against the real rendered
conversation page with the DOM stand-in in fviews_jsrun.js; the check endpoint is answered by the test (queue_fetch)."""
import re
from pathlib import Path
from urllib.parse import parse_qsl

import pytest

import fviews_html as H
import fviews_js as J
import fviews_kit as K
import fviews_llm as L

pytestmark = pytest.mark.skipif(J.NODE is None, reason="Node is not installed")

DRAFT = "Rent control has failed everywhere it was tried, nobody disagrees on that."
NOTE = "Could a source be given for the claim in message 3?"
NOTE_2 = "The two messages disagree about whether landlords leave the market."
CHECKING = "Checking your message…"
HEADING = "The moderator would reply to this message"
INTRO = "If you post it as written, the AI moderator will reply:"
CSS = (Path(__file__).resolve().parents[2] / "forum" / "static" / "forum" / "site.css").read_text()


def reply(**json):
    return {"status": 200, "json": json}


def concern(notes=(NOTE,), check_id=41):
    return reply(status="concern", check_id=check_id, notes=list(notes))


def no_concern(check_id=42):
    return reply(status="no_concern", check_id=check_id, notes=[])


def unavailable(check_id=None):
    return reply(status="unavailable", check_id=check_id, notes=[])


def refused(message="Please wait 12 more seconds before posting again.", code="too_fast", retry_after=12):
    return reply(status="refused", code=code, message=message, retry_after=retry_after)


class Composer:
    """The conversation page of viewer A with compose.js loaded. Add operations, then run()."""

    def __init__(self, mutate=None, seed=True, without=()):
        self.duo = K.Duo(names=(K.uniq("cmpa"), K.uniq("cmpb")))
        if seed:
            self.duo.seed(self.duo.pa, "An earlier message.", 50)
        html = self.duo.ca.get(self.duo.url).content.decode()
        self.html = mutate(html) if mutate else html
        self.page = J.Page(self.html, pathname=self.duo.url)
        root = self.page.root
        self.form = H.form_with_action(root, self.duo.post_url)
        self.textarea = H.text_control(self.form)
        self.button = next(b for b in self.form.find_all("button") if b.get("type", "submit") == "submit")
        self.label = H.norm(self.button.text())
        self.ta = self.page.eid(self.textarea)
        self.btn = self.page.eid(self.button)
        self.frm = self.page.eid(self.form)
        for name in without:  # globals the browser might not have
            self.page.add(op="delete_global", name=name)
        self.page.add(op="load", script=J.script_path("compose.js"))
        self.check_path = f"/c/{self.duo.conv.pk}/check/"

    def type(self, text=DRAFT):
        self.page.add(op="set_value", eid=self.ta, value=text)
        return self.page.add(op="fire", eid=self.ta, type="input")

    def queue(self, *responses):
        return self.page.add(op="queue_fetch", responses=list(responses))

    def submit(self):
        return self.page.add(op="fire", eid=self.frm, type="submit")

    def click(self, text):
        return self.page.add(op="click_text", text=text, tag="button")

    def op(self, **kw):
        return self.page.add(**kw)

    def button_state(self):
        return (self.op(op="prop", eid=self.btn, name="disabled"), self.op(op="text", eid=self.btn))

    def run(self):
        return self.page.run()


def body_fields(entry):
    body = (entry["options"] or {}).get("body")
    if isinstance(body, dict) and "__form" in body:
        return {k: v for k, v in body["__form"]}
    if isinstance(body, dict) and "__urlencoded" in body:
        return dict(parse_qsl(body["__urlencoded"]))
    if isinstance(body, str):
        return dict(parse_qsl(body))
    return {}


def csrf_of(entry):
    headers = {k.lower(): v for k, v in ((entry["options"] or {}).get("headers") or {}).items()}
    return headers.get("x-csrftoken") or body_fields(entry).get("csrfmiddlewaretoken")


def fields_of(submit):
    out = {}
    for k, v in submit["fields"]:
        out[k] = v
    return out


# --- off, or no attribute: no interception at all -----------------------------------------------------------------------------

def strip_preview(html):
    html = re.sub(r'\sdata-preview="[^"]*"', "", html)
    html = re.sub(r'\sdata-check-url="[^"]*"', "", html)
    return re.sub(r'\sdata-check-timeout="[^"]*"', "", html)


@pytest.mark.parametrize("how", ["off", "absent"])
def test_no_interception_when_the_preview_is_off_or_the_attribute_is_missing(how, tune):
    if how == "off":
        tune(PREVIEW_SHARE=0.0)
    c = Composer(mutate=strip_preview if how == "absent" else None)
    c.type()
    submitted = c.submit()
    disabled, label = c.button_state()
    fetches = c.op(op="fetch_log")
    errors = c.op(op="errors")
    r = c.run()
    assert r[submitted] is True, "the submit goes through untouched (the browser posts the form itself)"
    assert r[fetches] == [] and r[disabled] is False and H.norm(r[label]) == c.label and r[errors] == []


def test_off_pages_carry_no_check_machinery_at_all(tune):
    tune(PREVIEW_SHARE=0.0)
    c = Composer()
    assert c.form.get("data-preview") == "off"
    assert "data-check-url" not in c.form.attrs
    assert not [n for n in c.page.root.walk() if n.get("id") in ("concern-panel", "check-status", "check-refusal")]


# --- the checking state -----------------------------------------------------------------------------------------------------

def test_on_intercepts_the_submit_and_shows_the_checking_state_while_waiting():
    c = Composer()
    c.type()
    c.queue({"hang": True})
    submitted = c.submit()
    disabled, label = c.button_state()
    fetches = c.op(op="fetch_log")
    live = c.op(op="live_regions")
    timers = c.op(op="timers")
    subs = c.op(op="submits")
    r = c.run()
    assert r[submitted] is False, "the native submit is prevented"
    assert r[disabled] is True and H.norm(r[label]) == CHECKING
    assert any(CHECKING == x["text"] and x["attrs"].get("aria-live") == "polite" for x in r[live]), r[live]
    assert r[subs] == [], "nothing is posted while the check runs"
    assert len(r[fetches]) == 1
    entry = r[fetches][0]
    assert entry["url"] == c.check_path and entry["options"]["method"].upper() == "POST"
    fields = body_fields(entry)
    assert fields.get("text") == DRAFT and set(fields) <= {"text", "csrfmiddlewaretoken"}, fields
    token = next(n.get("value") for n in c.form.walk() if n.tag == "input" and n.get("name") == "csrfmiddlewaretoken")
    assert csrf_of(entry) == token, "the CSRF token travels with the check"
    assert 25000 in [t["delay"] for t in r[timers]], "the client timeout is the tunable, 25 seconds by default"


def test_the_checking_timeout_follows_the_tunable(tune):
    tune(PREVIEW_CLIENT_TIMEOUT_SECONDS=7)
    c = Composer()
    c.type()
    c.queue({"hang": True})
    c.submit()
    timers = c.op(op="timers")
    r = c.run()
    assert 7000 in [t["delay"] for t in r[timers]]


def test_a_second_press_while_checking_does_nothing_more():
    c = Composer()
    c.type()
    c.queue({"hang": True})
    first, second = c.submit(), c.submit()
    fetches = c.op(op="fetch_log")
    r = c.run()
    assert r[first] is False and r[second] is False
    assert len(r[fetches]) == 1


def test_the_check_sends_the_draft_exactly_as_typed_including_line_breaks_and_spaces():
    c = Composer()
    text = "  Line one\r\nLine two é \U0001f600  "
    c.type(text)
    c.queue({"hang": True})
    c.submit()
    fetches = c.op(op="fetch_log")
    r = c.run()
    assert body_fields(r[fetches][0])["text"] == text


# --- no concern, unavailable: a normal post -----------------------------------------------------------------------------------

def test_no_concern_submits_the_original_form_with_the_check_id():
    c = Composer()
    c.type()
    c.queue(no_concern(check_id=42))
    c.submit()
    subs = c.op(op="submits")
    alerts = c.op(op="alerts")
    r = c.run()
    assert len(r[subs]) == 1
    fields = fields_of(r[subs][0])
    assert r[subs][0]["eid"] == c.frm and fields["text"] == DRAFT and fields["check_id"] == "42"
    assert "csrfmiddlewaretoken" in fields
    assert r[alerts] == [], "the person sees nothing extra"


def test_unavailable_submits_a_normal_post_and_adds_the_check_id_only_when_there_is_one():
    for reply_, expected in ((unavailable(None), None), (unavailable(7), "7")):
        c = Composer()
        c.type()
        c.queue(reply_)
        c.submit()
        subs = c.op(op="submits")
        alerts = c.op(op="alerts")
        r = c.run()
        assert len(r[subs]) == 1 and fields_of(r[subs][0])["text"] == DRAFT
        assert fields_of(r[subs][0]).get("check_id") in ((None, "") if expected is None else (expected,))
        assert r[alerts] == []


@pytest.mark.parametrize("answer", [
    {"reject": True},
    {"status": 500, "json": {}},
    {"status": 403, "json": {"detail": "csrf"}},
    {"status": 200, "body": "<html>not json</html>"},
    reply(status="weird"),
    reply(),
    reply(status="concern"),
    reply(status="refused"),
], ids=["network error", "http 500", "http 403", "unreadable body", "unknown status", "empty object", "concern without notes", "refused without message"])
def test_any_failure_or_unexpected_reply_falls_back_to_a_normal_post_and_shows_nothing(answer):
    c = Composer()
    c.type()
    c.queue(answer)
    c.submit()
    subs = c.op(op="submits")
    alerts = c.op(op="alerts")
    errors = c.op(op="errors")
    r = c.run()
    assert len(r[subs]) == 1, "posting is never blocked because the check failed"
    fields = fields_of(r[subs][0])
    assert fields["text"] == DRAFT and fields.get("check_id") in (None, "")
    assert r[alerts] == [] and r[errors] == []


def test_a_check_that_exceeds_the_timeout_falls_back_to_a_normal_post_with_no_error_shown():
    c = Composer()
    c.type()
    c.queue({"hang": True})
    c.submit()
    before = c.op(op="submits")
    timers = c.op(op="timers")
    ran = c.op(op="run_timer")
    subs = c.op(op="submits")
    alerts = c.op(op="alerts")
    errors = c.op(op="errors")
    fetches = c.op(op="fetch_log")
    r = c.run()
    assert r[before] == []
    assert 25000 in [t["delay"] for t in r[timers]]
    assert r[ran]["ran"] is True and r[ran]["delay"] == 25000
    assert len(r[subs]) == 1, "the original form is submitted for real"
    fields = fields_of(r[subs][0])
    assert fields["text"] == DRAFT and fields.get("check_id") in (None, "")
    assert r[alerts] == [] and r[errors] == []
    assert len(r[fetches]) == 1, "the slow request was given up, not repeated"


def test_a_late_answer_after_the_timeout_does_not_post_twice():
    c = Composer()
    c.type()
    c.queue({"hang": True})
    c.submit()
    c.op(op="run_timer")
    c.op(op="fire_window", type="pageshow", persisted=False)
    subs = c.op(op="submits")
    r = c.run()
    assert len(r[subs]) == 1


# --- the refusal ------------------------------------------------------------------------------------------------------------

def test_a_refusal_is_shown_in_the_alert_area_the_text_is_kept_and_nothing_is_posted():
    c = Composer()
    message = "Please wait 12 more seconds before posting again. <b>Messages</b> are limited."
    c.type()
    c.queue(refused(message))
    c.submit()
    alerts = c.op(op="alerts")
    value = c.op(op="value", eid=c.ta)
    disabled, label = c.button_state()
    subs = c.op(op="submits")
    html = c.op(op="html_log")
    r = c.run()
    assert r[alerts] == [message], "the server's words, as plain text, in an alert area"
    assert r[value] == DRAFT
    assert r[disabled] is False and H.norm(r[label]) == c.label
    assert r[subs] == []
    assert not [h for h in r[html] if "<b>Messages" in h]


def test_after_a_refusal_the_person_can_try_again():
    c = Composer()
    c.type()
    c.queue(refused(), no_concern(43))
    c.submit()
    c.submit()
    subs = c.op(op="submits")
    fetches = c.op(op="fetch_log")
    r = c.run()
    assert len(r[fetches]) == 2 and len(r[subs]) == 1 and fields_of(r[subs][0])["check_id"] == "43"


# --- a concern --------------------------------------------------------------------------------------------------------------

def test_the_panel_markup_is_in_the_page_and_hidden_until_a_concern():
    c = Composer()
    root = c.page.root
    panel = next(n for n in root.walk() if n.get("role") == "region" and HEADING in n.text())
    assert "hidden" in panel.attrs, "hidden until there is something to show"
    heading = next(h for h in panel.walk() if h.text().strip() == HEADING)
    assert heading.tag in ("h2", "h3", "h4") and heading.get("tabindex") == "-1"
    assert panel.get("aria-labelledby") == heading.get("id")
    buttons = {H.norm(b.text()): b.get("type") for b in panel.find_all("button")}
    assert buttons == {"Edit my message": "button", "Post as written": "button"}
    assert INTRO in H.norm(panel.text())
    assert not [n for n in root.walk() if n.get("class", "").find("quote") >= 0 and NOTE in n.text()]


def concern_scenario(notes=(NOTE,)):
    c = Composer()
    c.type()
    c.queue(concern(notes))
    c.submit()
    return c


def test_a_concern_shows_the_panel_with_the_notes_and_moves_focus_to_its_heading():
    c = concern_scenario((NOTE, NOTE_2))
    heading = c.op(op="find", text=HEADING)
    intro = c.op(op="find", text=INTRO)
    notes = [c.op(op="find", text=t) for t in (NOTE, NOTE_2)]
    edit = c.op(op="find", text="Edit my message", tag="button")
    post = c.op(op="find", text="Post as written", tag="button")
    active = c.op(op="active_desc")
    live = c.op(op="live_regions")
    subs = c.op(op="submits")
    value = c.op(op="value", eid=c.ta)
    r = c.run()
    assert any(h["visible"] for h in r[heading]), "the heading is visible"
    assert any(i["visible"] for i in r[intro])
    for i in notes:
        assert any(n["visible"] for n in r[i]), "each note is shown"
    quote_blocks = [[n for n in r[i] if n["tag"] == "blockquote" or "quote" in n["attrs"].get("class", "")] for i in notes]
    assert all(len(q) == 1 for q in quote_blocks), "one quote block per note"
    assert r[edit][-1]["attrs"].get("type") == "button" and r[post][-1]["attrs"].get("type") == "button"
    assert r[edit][-1]["visible"] and r[post][-1]["visible"]
    assert r[active] and r[active]["text"] == HEADING and r[active]["attrs"].get("tabindex") == "-1", "focus is on the heading"
    assert any(x["text"] == "The moderator would reply to this message." for x in r[live]), r[live]
    assert r[subs] == [], "nothing is posted until the author chooses"
    assert r[value] == DRAFT


def test_the_note_order_is_kept():
    c = concern_scenario((NOTE_2, NOTE))
    text = c.op(op="dom_text", eid=c.page.eid(c.page.root.find("body")))
    r = c.run()
    assert r[text].index(NOTE_2) < r[text].index(NOTE)


def test_notes_are_shown_as_plain_text_never_run_as_markup_and_never_bold():
    nasty = "<img src=x onerror=alert(1)>Note <b>bold</b> & more"
    c = concern_scenario((nasty,))
    text = c.op(op="dom_text", eid=c.page.eid(c.page.root.find("body")))
    html = c.op(op="html_log")
    found = c.op(op="find", text=nasty)
    r = c.run()
    assert nasty in r[text], "the note appears as literal text"
    assert not [h for h in r[html] if "<img" in h or "<b>bold" in h], "no note markup is ever handed to innerHTML"
    holders = [n for n in r[found] if n["tag"] == "blockquote" or "quote" in n["attrs"].get("class", "")]
    assert holders and not [a for h in holders for a in h["ancestors"] if a in ("b", "strong")]
    assert not [h for h in holders if h["tag"] in ("b", "strong")]


def test_the_note_block_keeps_line_breaks_and_is_not_styled_bold():
    c = concern_scenario(("Line one\nLine two",))
    found = c.op(op="find", text="Line one\nLine two")
    r = c.run()
    holder = next(n for n in r[found] if n["tag"] == "blockquote" or "quote" in n["attrs"].get("class", ""))
    keys = {holder["tag"], *holder["attrs"].get("class", "").split()}
    css = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    rules = re.findall(r"([^{}]+)\{([^{}]*)\}", css)

    def reaches(selector):
        last = selector.strip().split()[-1] if selector.strip() else ""
        return any(k and (last == k or last == "." + k or last.endswith("." + k) or last.startswith(k + ".")) for k in keys)

    relevant = [body for sel, body in rules if any(reaches(part) for part in sel.split(","))]
    assert any("white-space" in b and "pre-wrap" in b for b in relevant), f"no pre-wrap rule reaches the note block {keys}"
    assert not [b for b in relevant if re.search(r"font-weight\s*:\s*(bold|[6-9]00)", b) and "pre-wrap" in b]


def test_post_as_written_submits_the_original_form_with_the_check_id():
    c = concern_scenario()
    clicked = c.click("Post as written")
    subs = c.op(op="submits")
    r = c.run()
    assert r[clicked] is True and len(r[subs]) == 1
    fields = fields_of(r[subs][0])
    assert r[subs][0]["eid"] == c.frm and fields["text"] == DRAFT and fields["check_id"] == "41" and "csrfmiddlewaretoken" in fields


def test_edit_my_message_hides_the_panel_returns_focus_keeps_the_text_and_tells_the_server():
    c = concern_scenario()
    c.queue({"status": 200, "json": {"ok": True}})
    clicked = c.click("Edit my message")
    heading = c.op(op="find", text=HEADING)
    active = c.op(op="active_desc")
    value = c.op(op="value", eid=c.ta)
    disabled, label = c.button_state()
    fetches = c.op(op="fetch_log")
    subs = c.op(op="submits")
    errors = c.op(op="errors")
    r = c.run()
    assert r[clicked] is True
    assert not any(h["visible"] for h in r[heading]), "the panel is hidden again"
    assert r[active]["eid"] == c.ta, "focus is back in the text box"
    assert r[value] == DRAFT, "the text is kept"
    assert r[disabled] is False and H.norm(r[label]) == c.label, "the Post button works again"
    assert r[subs] == [] and r[errors] == []
    assert len(r[fetches]) == 2
    edit = r[fetches][1]
    assert edit["url"] == f"{c.check_path}41/edit/" and edit["options"]["method"].upper() == "POST"
    assert csrf_of(edit), "the edit notice carries the CSRF token too"


def test_a_failing_edit_notice_is_ignored():
    for answer in ({"reject": True}, {"status": 500, "json": {}}, {"status": 404, "json": {}}):
        c = concern_scenario()
        c.queue(answer)
        c.click("Edit my message")
        alerts = c.op(op="alerts")
        errors = c.op(op="errors")
        active = c.op(op="active_desc")
        disabled, label = c.button_state()
        r = c.run()
        assert r[alerts] == [] and r[errors] == [] and r[active]["eid"] == c.ta
        assert r[disabled] is False


def test_pressing_post_again_on_the_unchanged_text_checks_again():
    c = concern_scenario()
    c.queue({"status": 200, "json": {"ok": True}}, concern(check_id=99))
    c.click("Edit my message")
    again = c.submit()
    fetches = c.op(op="fetch_log")
    heading = c.op(op="find", text=HEADING)
    subs = c.op(op="submits")
    click2 = c.click("Post as written")
    subs2 = c.op(op="submits")
    r = c.run()
    assert r[again] is False
    urls = [f["url"] for f in r[fetches]]
    assert urls == [c.check_path, f"{c.check_path}41/edit/", c.check_path], "a new check, not a cached answer"
    assert body_fields(r[fetches][2])["text"] == DRAFT
    assert any(h["visible"] for h in r[heading]) and r[subs] == []
    assert fields_of(r[subs2][0])["check_id"] == "99", "the new check's id, not the old one"


def test_changing_the_text_after_a_concern_and_pressing_post_checks_the_new_text():
    c = concern_scenario()
    c.queue({"status": 200, "json": {"ok": True}}, no_concern(50))
    c.click("Edit my message")
    c.type(DRAFT + " And here is my source.")
    c.submit()
    subs = c.op(op="submits")
    fetches = c.op(op="fetch_log")
    r = c.run()
    assert body_fields(r[fetches][2])["text"].endswith("And here is my source.")
    assert fields_of(r[subs][0])["check_id"] == "50" and fields_of(r[subs][0])["text"].endswith("And here is my source.")


def test_coming_back_through_history_does_not_leave_the_button_stuck_on_checking():
    c = Composer()
    c.type()
    c.queue({"hang": True})
    c.submit()
    stuck = c.button_state()
    c.op(op="fire_window", type="pageshow", persisted=True)
    disabled, label = c.button_state()
    r = c.run()
    assert r[stuck[0]] is True
    assert r[disabled] is False and H.norm(r[label]) == c.label


# --- the counter keeps working -------------------------------------------------------------------------------------------------

def test_the_character_counter_still_agrees_with_the_server_when_the_preview_is_on():
    c = Composer()
    counter = next(n for n in H.counter_nodes(c.page.root) if n.tag not in ("textarea", "input"))
    c.type("  abc  ")
    shown = c.op(op="text", eid=c.page.eid(counter))
    r = c.run()
    assert re.search(r"\b3\b\s*/\s*3,000", r[shown])


def turn_off_but_keep_the_url(html):
    return html.replace('data-preview="on"', 'data-preview="off"')


def test_the_off_word_alone_switches_interception_off_even_if_the_check_url_is_still_there():
    c = Composer(mutate=turn_off_but_keep_the_url)
    assert c.form.get("data-preview") == "off" and c.form.get("data-check-url")
    c.type()
    submitted = c.submit()
    fetches = c.op(op="fetch_log")
    r = c.run()
    assert r[submitted] is True and r[fetches] == []


def test_the_timeout_still_falls_back_when_the_browser_cannot_abort_a_request():
    c = Composer(without=("AbortController",))
    c.type()
    c.queue({"hang": True})
    c.submit()
    c.op(op="run_timer")
    subs = c.op(op="submits")
    alerts = c.op(op="alerts")
    r = c.run()
    assert len(r[subs]) == 1 and fields_of(r[subs][0]).get("check_id") in (None, "") and r[alerts] == []


def test_an_old_refusal_disappears_when_the_next_check_starts():
    c = Composer()
    c.type()
    c.queue(refused("Please wait 12 more seconds."), {"hang": True})
    c.submit()
    first = c.op(op="alerts")
    c.submit()
    second = c.op(op="alerts")
    r = c.run()
    assert r[first] == ["Please wait 12 more seconds."] and r[second] == []
