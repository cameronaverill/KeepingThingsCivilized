"""Front-end fixes 2 and 3, the browser side (docs/frontend_fixes_brief.md): poll.js replaces a research slot when its state differs
and ignores everything malformed; research.js works on the slot (optimistic pending + busy, success keeps pending, failure restores
the previous markup and state). Run in Node with the step 7b DOM stand-in (fviews_jsrun.js). Skipped when Node is missing."""
import json

import pytest

import ff_kit as F
import fviews_html as H
import fviews_js as J
import fviews_kit as K

pytestmark = pytest.mark.skipif(J.NODE is None, reason="Node is not installed")

BASE_MS = 7000


class Env:
    """The real conversation page of viewer A with one researchable act in `state`, plus the scripts under test."""

    def __init__(self, settings, state="none", scripts=("research.js",), busy=False, extra_acts=0):
        settings.POLL_SECONDS = BASE_MS // 1000
        self.duo = K.Duo()
        self.trigger = self.duo.seed(self.duo.pa, "A claim worth checking.")
        self.mod, self.act = F.post_with_act(self.duo.conv, self.trigger, marker="ff-js")
        self.run_row = F.make_state(self.act, state, requested_by=self.duo.pa) if state != "none" else None
        html = self.duo.ca.get(self.duo.url).content.decode()
        self.page = J.Page(html, pathname=self.duo.url)
        self.slot = F.slot_for(self.page.root, self.act)
        assert self.slot is not None, "the page must render the research slot"
        if busy:
            self.slot.attrs["data-busy"] = "1"
            self.page.tree, self.page.ids = J.number_tree(self.page.root)
        self.slot_eid = self.page.eid(self.slot)
        for name in scripts:
            self.page.add(op="load", script=J.script_path(name))

    # -- reading --
    def attr(self, name, eid=None):
        return self.page.add(op="attr", eid=self.slot_eid if eid is None else eid, name=name)

    def inner(self, eid=None):
        return self.page.add(op="prop", eid=self.slot_eid if eid is None else eid, name="innerHTML")

    def forms(self):
        return self.page.add(op="find", tag="form")

    def errors(self):
        return self.page.add(op="errors")

    def fetches(self):
        return self.page.add(op="fetch_log")

    def html_log(self):
        return self.page.add(op="html_log")

    # -- doing --
    def queue(self, *responses):
        return self.page.add(op="queue_fetch", responses=list(responses))

    def submit(self, form_eid):
        return self.page.add(op="fire", eid=form_eid, type="submit")

    def tick(self):
        return self.page.add(op="run_timer")

    def real(self, after=0, client=None):
        response = (client or self.duo.ca).get(self.duo.poll_url, {"after": after})
        assert response.status_code == 200
        return json.loads(response.content)

    def run(self):
        return self.page.run()


def slot_form_eid(env, results, index):
    """eid of the research form found by find-op `index` (first one inside the slot)."""
    forms = [f for f in results[index] if "research-form" in (f["attrs"].get("class") or "")]
    assert forms, results[index]
    return forms[0]["eid"]


def first_form_eid(env):
    """The eid of the research form in the slot as rendered at load (taken from the parsed tree)."""
    form = F.descendants(env.slot, "form", "research-form")[0]
    return env.page.eid(form)


@pytest.fixture
def env_none(settings):
    return Env(settings, "none")


# --- research.js ----------------------------------------------------------------------------------------------------------------

def test_submit_is_intercepted_and_the_slot_goes_optimistically_pending_and_busy(settings):
    env = Env(settings, "none")
    form = first_form_eid(env)
    env.queue({"hang": True})
    fired = env.submit(form)
    state, busy, inner, log, errs = env.attr("data-state"), env.attr("data-busy"), env.inner(), env.fetches(), env.errors()
    res = env.run()
    assert res[fired] is False, "preventDefault must be called"
    assert res[state] == "pending" and res[busy] not in (None, "", "0"), "busy is set while the request is out"
    assert "research-pending" in res[inner] and F.PENDING_TEXT in res[inner] and "spinner" in res[inner]
    assert "research-form" not in res[inner] and "<button" not in res[inner]
    assert res[errs] == []
    (call,) = res[log]
    assert call["url"] == F.url_for(env.duo.conv, env.act)
    opts = call["options"]
    assert opts["method"] == "POST"
    token = H.hidden_csrf(F.descendants(env.slot, "form")[0])
    assert opts["headers"]["X-CSRFToken"] == token and ("csrfmiddlewaretoken=" + token) in opts["body"]


def test_success_removes_busy_and_keeps_the_state_pending(settings):
    env = Env(settings, "none")
    env.queue({"status": 200, "json": {"status": "pending", "act_id": env.act.pk, "run_id": 1}})
    env.submit(first_form_eid(env))
    state, busy, inner, errs = env.attr("data-state"), env.attr("data-busy"), env.inner(), env.errors()
    res = env.run()
    assert res[state] == "pending" and res[busy] is None
    assert F.PENDING_TEXT in res[inner]
    assert res[errs] == []


def test_a_server_error_restores_the_previous_markup_and_state(settings):
    env = Env(settings, "none")
    before = env.inner()
    env.queue({"status": 500, "json": {}})
    env.submit(first_form_eid(env))
    state, busy, after, errs = env.attr("data-state"), env.attr("data-busy"), env.inner(), env.errors()
    res = env.run()
    assert res[state] == "none" and res[busy] is None
    assert res[after] == res[before], "the slot's previous inner HTML comes back"
    assert F.OFFER_BUTTON in res[after]
    assert res[errs] == []


@pytest.mark.parametrize("status", [404, 403, 400, 502])
def test_any_non_2xx_restores(settings, status):
    env = Env(settings, "none")
    before = env.inner()
    env.queue({"status": status, "json": {}})
    env.submit(first_form_eid(env))
    state, after = env.attr("data-state"), env.inner()
    res = env.run()
    assert res[state] == "none" and res[after] == res[before]


def test_a_network_error_restores_too(settings):
    env = Env(settings, "none")
    before = env.inner()
    env.queue({"reject": True})
    env.submit(first_form_eid(env))
    state, busy, after = env.attr("data-state"), env.attr("data-busy"), env.inner()
    res = env.run()
    assert res[state] == "none" and res[busy] is None and res[after] == res[before]


def test_the_failed_state_retry_goes_optimistically_pending_and_busy(settings):
    env = Env(settings, "failed")
    before = env.inner()
    env.queue({"hang": True})
    env.submit(first_form_eid(env))
    mid_state, mid_busy, mid = env.attr("data-state"), env.attr("data-busy"), env.inner()
    res = env.run()
    assert res[before].count(F.RETRY_BUTTON) == 1 and F.FAILED_TEXT in res[before]
    assert res[mid_state] == "pending" and res[mid_busy] not in (None, "", "0")
    assert F.FAILED_TEXT not in res[mid] and F.PENDING_TEXT in res[mid]


def test_the_failed_state_retry_error_restores_the_failed_markup_and_state(settings):
    env = Env(settings, "failed")
    before = env.inner()
    env.queue({"status": 500, "json": {}})
    env.submit(first_form_eid(env))
    state, busy, after = env.attr("data-state"), env.attr("data-busy"), env.inner()
    res = env.run()
    assert res[state] == "failed" and res[busy] is None
    assert res[after] == res[before] and F.RETRY_BUTTON in res[after], "so the person can retry"


def test_the_failed_state_retry_success_stays_pending(settings):
    env = Env(settings, "failed")
    env.queue({"status": 200, "json": {"status": "pending", "act_id": env.act.pk, "run_id": env.run_row.pk}})
    env.submit(first_form_eid(env))
    state, busy, inner = env.attr("data-state"), env.attr("data-busy"), env.inner()
    res = env.run()
    assert res[state] == "pending" and res[busy] is None and F.PENDING_TEXT in res[inner]


def test_after_a_failed_request_the_person_can_submit_again(settings):
    env = Env(settings, "none")
    env.queue({"status": 500, "json": {}})
    env.submit(first_form_eid(env))
    env.queue({"status": 200, "json": {"status": "pending"}})
    env.page.add(op="fire_sel", selector=".research-slot .research-form", index=0, type="submit")
    log, state, busy = env.fetches(), env.attr("data-state"), env.attr("data-busy")
    res = env.run()
    assert len(res[log]) == 2 and res[state] == "pending" and res[busy] is None


def test_a_non_research_form_submit_is_left_alone(settings):
    env = Env(settings, "none")
    composer = H.form_with_action(env.page.root, env.duo.post_url)
    fired = env.submit(env.page.eid(composer))
    log = env.fetches()
    res = env.run()
    assert res[fired] is True and res[log] == []


def test_the_listener_is_delegated_so_a_polled_in_slot_works_without_setup(settings):
    env = Env(settings, "none", scripts=("research.js", "poll.js"))
    new_trigger = env.duo.seed(env.duo.pb, "Another claim.")
    mod2, act2 = F.post_with_act(env.duo.conv, new_trigger, marker="ff-js-new")
    env.queue({"status": 200, "json": env.real(after=env.trigger.seq_no + 1)})
    env.tick()
    sel = f'.research-slot[data-act-id="{act2.pk}"]'
    before = env.page.add(op="query", selector=sel)
    env.queue({"hang": True})
    env.page.add(op="fire_sel", selector=f"{sel} .research-form", type="submit")
    after = env.page.add(op="query", selector=sel)
    log = env.fetches()
    res = env.run()
    assert len(res[before]) == 1 and res[before][0]["attrs"]["data-state"] == "none", "the polled-in message brings its own slot"
    assert res[after][0]["attrs"]["data-state"] == "pending" and F.PENDING_TEXT in res[after][0]["text"]
    assert res[log][-1]["url"] == F.url_for(env.duo.conv, act2)


def test_research_js_reads_the_slot_even_when_the_form_is_nested_deeper(settings):
    """The slot, not the form's parent, is what changes (the offer div sits between them)."""
    env = Env(settings, "none")
    form = first_form_eid(env)
    assert F.descendants(env.slot, "div", "research-offer"), "the form lives inside div.research-offer inside the slot"
    env.queue({"hang": True})
    env.submit(form)
    inner, state = env.inner(), env.attr("data-state")
    res = env.run()
    assert res[state] == "pending"
    assert "research-offer" not in res[inner], "the whole slot content is replaced, not just the form"


# --- poll.js ----------------------------------------------------------------------------------------------------------------------

def poll_with(env, research, **overrides):
    """Queue the real poll answer (so status flags match the page) with `research` replaced and tick once."""
    data = env.real(after=env.mod.seq_no)
    if research is not ...:
        data["research"] = research
    data.update(overrides)
    env.queue({"status": 200, "json": data})
    return env.tick()


def item(env, state, html, **kw):
    return {"act_id": env.act.pk, "state": state, "html": html, **kw}


def test_a_slot_is_replaced_when_the_state_differs(settings):
    env = Env(settings, "none", scripts=("poll.js",))
    html = '<p class="msg-meta research-pending"><span class="spinner"></span><span>Checking — this may take a moment</span></p>'
    poll_with(env, [item(env, "pending", html)])
    state, inner, errs = env.attr("data-state"), env.inner(), env.errors()
    res = env.run()
    assert res[state] == "pending"
    assert "research-pending" in res[inner] and "research-offer" not in res[inner]
    assert res[errs] == []


@pytest.mark.parametrize("old,new", [("pending", "failed"), ("pending", "done"), ("failed", "pending"), ("none", "failed"),
                                     ("failed", "done"), ("none", "done")])
def test_the_real_payload_html_replaces_the_slot_across_state_changes(settings, old, new):
    env = Env(settings, old, scripts=("poll.js",))
    # the server moves on: the research run changes state after the page was rendered
    from moderation.models import ModerationRun
    if old == "none":
        F.make_state(env.act, new, requested_by=env.duo.pa)
    else:
        run = env.run_row
        if new == "pending":
            ModerationRun.objects.filter(pk=run.pk).update(status="pending", failure_reason="", error="")
        elif new == "failed":
            ModerationRun.objects.filter(pk=run.pk).update(status="failed")
        else:
            ModerationRun.objects.filter(pk=run.pk).update(status="done", posted_message=F.note_message(env.duo.conv, env.trigger).pk)
    data = env.real(after=env.mod.seq_no + 5)
    expected = next(i for i in data["research"] if i["act_id"] == env.act.pk)
    assert expected["state"] == new
    env.queue({"status": 200, "json": data})
    env.tick()
    state, inner, errs = env.attr("data-state"), env.inner(), env.errors()
    res = env.run()
    assert res[state] == new
    got = F.fragment(res[inner])
    want = F.fragment(expected["html"])
    assert got.text() == want.text()
    assert [(n.tag, n.get("class")) for n in got.walk()] == [(n.tag, n.get("class")) for n in want.walk()]
    assert res[errs] == []


def test_a_slot_with_the_same_state_is_not_touched(settings):
    env = Env(settings, "pending", scripts=("poll.js",))
    before = env.inner()
    poll_with(env, [item(env, "pending", "<p>SHOULD-NOT-APPEAR</p>")])
    after, logs = env.inner(), env.html_log()
    res = env.run()
    assert res[after] == res[before]
    assert not any("SHOULD-NOT-APPEAR" in chunk for chunk in res[logs])


def test_a_busy_slot_is_never_touched_by_a_poll(settings):
    env = Env(settings, "none", scripts=("poll.js",), busy=True)
    before = env.inner()
    poll_with(env, [item(env, "failed", "<p>BUSY-OVERWRITE</p>")])
    after, state, busy, logs = env.inner(), env.attr("data-state"), env.attr("data-busy"), env.html_log()
    res = env.run()
    assert res[after] == res[before] and res[state] == "none" and res[busy] == "1"
    assert not any("BUSY-OVERWRITE" in chunk for chunk in res[logs])


def test_a_poll_arriving_during_a_research_request_does_not_overwrite_the_optimistic_slot(settings):
    env = Env(settings, "none", scripts=("research.js", "poll.js"))
    env.queue({"hang": True})
    env.submit(first_form_eid(env))
    poll_with(env, [item(env, "failed", "<p>STALE-FAILED</p>")])
    inner, state = env.inner(), env.attr("data-state")
    res = env.run()
    assert res[state] == "pending" and "STALE-FAILED" not in res[inner] and F.PENDING_TEXT in res[inner]


def test_after_a_request_finishes_the_slot_is_no_longer_busy_and_polls_apply_again(settings):
    env = Env(settings, "none", scripts=("research.js", "poll.js"))
    env.queue({"status": 200, "json": {"status": "pending"}})
    env.submit(first_form_eid(env))
    poll_with(env, [item(env, "failed", "<p class='msg-meta research-failed'>NOW-FAILED</p>")])
    inner, state = env.inner(), env.attr("data-state")
    res = env.run()
    assert res[state] == "failed" and "NOW-FAILED" in res[inner]


MALFORMED = [
    ("absent", ...),
    ("null", None),
    ("string", "pending"),
    ("number", 7),
    ("object", {"act_id": 1}),
    ("true", True),
    ("junk items", [None, 1, "x", [], {}, {"act_id": 1}, {"html": "x"}, {"state": "failed"}, {"act_id": "no", "html": 5}]),
    ("unknown act", [{"act_id": 99999999, "state": "failed", "html": "<p>NOSLOT</p>"}]),
]


@pytest.mark.parametrize("label,value", MALFORMED, ids=[m[0] for m in MALFORMED])
def test_malformed_research_values_are_ignored_without_error(settings, label, value):
    env = Env(settings, "pending", scripts=("poll.js",))
    before = env.inner()
    poll_with(env, value)
    after, state, errs, logs, timers, console = (env.inner(), env.attr("data-state"), env.errors(), env.html_log(),
                                                 env.page.add(op="timers"), env.page.add(op="console"))
    res = env.run()
    assert res[errs] == [], res[errs]
    assert res[after] == res[before] and res[state] == "pending"
    assert not any("NOSLOT" in chunk for chunk in res[logs])
    delays = [t["delay"] for t in res[timers]]
    assert delays and min(delays) == BASE_MS, "an odd research value is not a poll failure: no backoff"


def test_an_item_for_one_act_does_not_touch_another_acts_slot(settings):
    env = Env(settings, "none", scripts=("poll.js",))
    t2 = env.duo.seed(env.duo.pb, "Second claim.")
    mod2, act2 = F.post_with_act(env.duo.conv, t2, marker="ff-js-two")
    html = env.duo.ca.get(env.duo.url).content.decode()
    env.page = J.Page(html, pathname=env.duo.url)
    slot2 = F.slot_for(env.page.root, act2)
    slot1 = F.slot_for(env.page.root, env.act)
    env.page.add(op="load", script=J.script_path("poll.js"))
    env.slot_eid = env.page.eid(slot1)
    eid2 = env.page.eid(slot2)
    before1, before2 = env.inner(), env.inner(eid2)
    data = env.real(after=99)
    data["research"] = [{"act_id": act2.pk, "state": "failed", "html": "<p>ONLY-TWO</p>"}]
    env.queue({"status": 200, "json": data})
    env.tick()
    after1, after2, st2 = env.inner(), env.inner(eid2), env.attr("data-state", eid2)
    res = env.run()
    assert res[after1] == res[before1]
    assert "ONLY-TWO" in res[after2] and res[st2] == "failed"


def test_messages_are_appended_before_slots_are_updated(settings):
    env = Env(settings, "none", scripts=("poll.js",))
    t2 = env.duo.seed(env.duo.pb, "Second claim.")
    mod2, act2 = F.post_with_act(env.duo.conv, t2, marker="ff-js-order")
    data = env.real(after=env.mod.seq_no)  # carries the new moderator message, whose slot reads `none`
    assert any(m["seq_no"] == mod2.seq_no for m in data["messages"])
    data["research"] = [{"act_id": act2.pk, "state": "failed", "html": "<p class='research-failed'>ORDERED</p>"}]
    env.queue({"status": 200, "json": data})
    env.tick()
    found = env.page.add(op="find", tag="div")
    errs = env.errors()
    res = env.run()
    slots = [d for d in res[found] if "research-slot" in (d["attrs"].get("class") or "")]
    mine = [d for d in slots if d["attrs"].get("data-act-id") == str(act2.pk)]
    assert len(mine) == 1, "the new message's slot exists after the poll"
    assert mine[0]["attrs"].get("data-state") == "failed" and "ORDERED" in mine[0]["text"]
    assert res[errs] == []


def test_the_polling_cadence_is_unchanged_by_research_items(settings):
    env = Env(settings, "none", scripts=("poll.js",))
    poll_with(env, [item(env, "failed", "<p>x</p>")])
    timers = env.page.add(op="timers")
    res = env.run()
    assert min(t["delay"] for t in res[timers]) == BASE_MS
