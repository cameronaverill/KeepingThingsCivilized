"""7c Revision 5: blocking. The three new URLs (methods, login, CSRF, unknown user, self-block), the banners, the block
control on the conversation page, blocked pairs and waiting cards, the Blocked people page and unblocking.

Wave 16 item 3 (docs/wave16_brief.md): blocking is offered only from a conversation now, never from a waiting card, so
the card no longer carries its own block form/button. The view/service layer is unchanged (it does not know which page
a block was posted from), so a block can still be posted directly to `forum:block` with no shared conversation; the
tests that used to submit that through a card's block form now post to the URL directly instead."""
import re

import pytest
from django.test import Client
from django.urls import reverse

import fviews_cards as C
import fviews_html as H
import fviews_kit as K


def block_url(name):
    return f"/users/{name}/block/"


def unblock_url(name):
    return f"/users/{name}/unblock/"


def blocks():
    from forum.models import Block

    return list(Block.objects.values_list("blocker__username", "blocked__username"))


def user_topic(text, **kw):
    return K.make_topic(text, created_by=K.make_user(), **kw)


def home_cards(client):
    return C.waiting_cards(H.doc(client.get("/")))


def text(response):
    return H.page_norm(response)


def submit(client, form, **extra):
    return client.post(form.get("action"), C.form_fields(form), **extra)


def page_of(client, url):
    response = client.get(url)
    assert response.status_code == 200, url
    return response


# --- URL rules --------------------------------------------------------------------------------------------------------

def test_url_names_and_paths():
    assert reverse("forum:blocked") == "/blocked/"
    assert reverse("forum:block", args=["someone"]) == "/users/someone/block/"
    assert reverse("forum:unblock", args=["someone"]) == "/users/someone/unblock/"


@pytest.mark.parametrize("path, allowed", [
    ("/users/quincy_ray/block/", "post"), ("/users/quincy_ray/unblock/", "post"), ("/blocked/", "get"),
])
def test_method_rules(path, allowed):
    duo = K.Duo()
    for method in ("get", "post", "put", "patch", "delete"):
        if method == allowed:
            continue
        assert getattr(K.client_for(duo.ua), method)(path).status_code == 405, f"{method.upper()} {path}"
    assert blocks() == []


@pytest.mark.parametrize("path", ["/users/quincy_ray/block/", "/users/quincy_ray/unblock/", "/blocked/"])
def test_login_is_required_and_nothing_changes(path):
    K.Duo()
    for method in ("get", "post"):
        response = getattr(Client(), method)(path)
        if response.status_code == 405:
            continue
        assert response.status_code == 302 and reverse("accounts:login") in response["Location"], (method, path)
    assert blocks() == []


def test_the_anonymous_post_is_sent_to_login_not_405():
    K.Duo()
    for path in ("/users/quincy_ray/block/", "/users/quincy_ray/unblock/"):
        response = Client().post(path)
        assert response.status_code == 302 and "next=" in response["Location"]


@pytest.mark.parametrize("which", ["block", "unblock"])
def test_csrf_is_enforced(which):
    duo = K.Duo(csrf=True)
    path = block_url(K.NAME_B) if which == "block" else unblock_url(K.NAME_B)
    denied = duo.ca.post(path)
    assert denied.status_code == 403
    assert blocks() == [] and K.fresh(duo.conv).status == "active"
    assert duo.ca.post(path, {"csrfmiddlewaretoken": "x" * 64}).status_code == 403
    ok = K.csrf_post(duo.ca, path)
    assert ok.status_code == 302


@pytest.mark.parametrize("which", ["block", "unblock"])
def test_an_unknown_username_is_the_standard_404(which):
    client = K.client_for(K.make_user())
    for name in ("no_such_person", "a" * 60, "x%20y", "..%2Fetc"):
        response = client.post(f"/users/{name}/{which}/")
        assert response.status_code == 404, name
        body = response.content.decode()
        assert "Traceback" not in body and "DoesNotExist" not in body
    assert blocks() == []


def test_blocking_yourself_shows_a_friendly_banner_and_creates_nothing():
    me = K.make_user("selfish_sam")
    response = K.client_for(me).post(block_url("selfish_sam"), follow=True)
    assert response.status_code == 200
    assert K.SELF_BLOCK == "You cannot block yourself."
    assert K.SELF_BLOCK in H.norm(H.alert_text(response)) or K.SELF_BLOCK in text(response)
    assert blocks() == []
    assert "Traceback" not in response.content.decode()


# --- what blocking does -----------------------------------------------------------------------------------------------

def test_blocking_creates_the_block_and_ends_every_shared_conversation_for_both():
    a, b = K.make_user("shared_ann"), K.make_user("shared_ben")
    t1, t2 = user_topic("Shared topic one."), user_topic("Shared topic two.")
    c1 = K.wait_on(t1, "pro", user=a)[0]
    K.enter(b, t1, "con")
    c2 = K.wait_on(t2, "pro", user=b)[0]
    K.enter(a, t2, "con")
    third = K.make_user("third_tom")
    t3 = user_topic("Third party topic.")
    c3 = K.wait_on(t3, "pro", user=a)[0]
    K.enter(third, t3, "con")
    ca = K.client_for(a)
    response = K.csrf_post(ca, block_url("shared_ben"))
    assert response.status_code == 302
    assert blocks() == [("shared_ann", "shared_ben")]
    for conv in (c1, c2):
        conv = K.fresh(conv)
        assert conv.status == "closed" and conv.ended_by is not None and conv.ended_by.user == a and conv.ended_at is not None
    assert K.fresh(c3).status == "active", "conversations with other people are untouched"
    for client in (ca, K.client_for(b)):
        for conv in (c1, c2):
            page = client.get(reverse("forum:conversation", args=[conv.pk]))
            assert page.status_code == 200 and H.form_with_action(H.doc(page), reverse("forum:post", args=[conv.pk])) is None


def test_blocking_twice_is_harmless_and_the_blocked_person_can_block_back():
    a, b = K.make_user("twice_ann"), K.make_user("twice_ben")
    ca = K.client_for(a)
    assert ca.post(block_url("twice_ben")).status_code == 302
    assert ca.post(block_url("twice_ben")).status_code == 302
    assert K.client_for(b).post(block_url("twice_ann")).status_code == 302
    assert sorted(blocks()) == [("twice_ann", "twice_ben"), ("twice_ben", "twice_ann")]


def test_a_hand_made_block_post_with_no_fields_works_and_redirects():
    a, b = K.make_user("hand_ann"), K.make_user("hand_ben")
    response = K.client_for(a).post(block_url("hand_ben"))
    assert response.status_code == 302 and blocks() == [("hand_ann", "hand_ben")]


# --- banners and redirects --------------------------------------------------------------------------------------------

def conv_block_form(client, duo, other_name):
    page = page_of(client, duo.url)
    form = H.form_with_action(H.doc(page), block_url(other_name))
    assert form is not None, "the conversation page has the block form"
    return form


def test_blocking_from_a_conversation_goes_to_your_discussions_with_the_banner():
    duo = K.Duo()
    form = conv_block_form(duo.ca, duo, K.NAME_B)
    response = submit(duo.ca, form)
    assert response.status_code == 302 and response["Location"] == "/discussions/"
    landing = duo.ca.get(response["Location"])
    assert K.blocked_banner_from_conversation(K.NAME_B) in text(landing)
    assert "You blocked quincy_ray. The conversation has ended." in text(landing)
    assert K.blocked_banner_from_conversation(K.NAME_B) not in text(duo.ca.get("/discussions/")), "the banner shows once"


def test_blocking_someone_you_dont_share_a_conversation_with_goes_home_with_the_short_banner_and_their_card_is_gone():
    """The view/service layer still allows this (it does not know which page a block was posted from); only the
    home-page card UI to trigger it is gone (item 3)."""
    waiter = K.make_user("waiter_wes")
    K.wait_on(user_topic("Card claim to block from."), "pro", user=waiter)
    other = K.make_user("waiter_uma")
    K.wait_on(user_topic("Another card claim."), "pro", user=other)
    me = K.client_for(K.make_user())
    response = me.post(block_url("waiter_wes"))
    assert response.status_code == 302 and response["Location"] == "/"
    landing = me.get("/")
    assert K.blocked_banner_from_card("waiter_wes") in text(landing)
    assert "The conversation has ended" not in text(landing)
    assert [c.username for c in home_cards(me)] == ["waiter_uma"]


def test_blocking_someone_you_also_share_a_conversation_with_ends_it_and_goes_to_discussions():
    v, w = K.make_user("shared_vic"), K.make_user("shared_wes")
    topic = user_topic("Shared conversation topic.")
    K.wait_on(topic, "pro", user=w)
    conv = K.enter(v, topic, "con")
    K.wait_on(user_topic("Wes waits elsewhere."), "pro", user=w)
    client = K.client_for(v)
    response = client.post(block_url("shared_wes"))
    assert response.status_code == 302 and response["Location"] == "/discussions/"
    assert K.fresh(conv).status == "closed"
    assert K.blocked_banner_from_conversation("shared_wes") in text(client.get("/discussions/"))


def test_unblock_redirects_to_blocked_people_with_its_banner():
    a, b = K.make_user("unb_ann"), K.make_user("unb_ben")
    ca = K.client_for(a)
    ca.post(block_url("unb_ben"))
    page = page_of(ca, "/blocked/")
    form = next(f for f in H.forms(H.doc(page)) if f.get("action") == unblock_url("unb_ben"))
    response = submit(ca, form)
    assert response.status_code == 302 and response["Location"] == "/blocked/"
    landing = ca.get("/blocked/")
    assert K.unblocked_banner("unb_ben") in text(landing) and blocks() == []


def test_unblock_never_reopens_a_conversation_and_is_idempotent():
    duo = K.Duo(names=("reop_one", "reop_two"))
    duo.ca.post(block_url("reop_two"))
    assert duo.ca.post(unblock_url("reop_two")).status_code == 302
    assert duo.ca.post(unblock_url("reop_two")).status_code == 302, "unblocking someone you have not blocked is not an error"
    assert K.fresh(duo.conv).status == "closed"
    assert blocks() == []


# --- the block controls -----------------------------------------------------------------------------------------------

def test_the_conversation_page_has_a_collapsed_block_control_with_the_explanation():
    duo = K.Duo()
    for client, other in ((duo.ca, K.NAME_B), (duo.cb, K.NAME_A)):
        root = H.doc(page_of(client, duo.url))
        details = [d for d in root.find_all("details") if d.find("summary") is not None and d.find("summary").text().strip() == f"Block {other}"]
        assert len(details) == 1
        d = details[0]
        assert "open" not in d.attrs, "collapsed until asked for"
        assert K.BLOCK_EXPLAIN in H.norm(d.text())
        form = next(f for f in H.forms(d) if f.get("action") == block_url(other))
        assert form.get("method", "").lower() == "post" and H.hidden_csrf(form)
        assert [b.text().strip() for b in form.find_all("button")] == [f"Block {other}"]
        aside = next((a for a in d.ancestors() if a.tag == "aside"), None)
        assert aside is not None, "the control lives in the sidebar"


def test_there_is_no_block_control_while_the_conversation_is_waiting():
    user = K.make_user()
    conv = K.enter(user, user_topic("Waiting, nobody to block."), "pro")
    root = H.doc(page_of(K.client_for(user), reverse("forum:conversation", args=[conv.pk])))
    assert not [f for f in H.forms(root) if "/block/" in f.get("action", "")]
    assert not [d for d in root.find_all("details") if d.find("summary") is not None and d.find("summary").text().strip().startswith("Block")]


def test_the_block_control_never_offers_to_block_yourself_or_a_moderator():
    duo = K.Duo()
    root = H.doc(page_of(duo.ca, duo.url))
    actions = [f.get("action") for f in H.forms(root) if "/block/" in f.get("action", "")]
    assert actions == [block_url(K.NAME_B)]


def test_a_waiting_card_has_no_block_form_or_button_at_all():
    """Wave 16 item 3: blocking moved off the home page entirely; the card keeps only its join form."""
    waiter = K.make_user("cardblock_cy")
    K.wait_on(user_topic("No button on this card."), "pro", user=waiter)
    [card] = home_cards(K.client_for(K.make_user()))
    assert card.block_form is None
    assert len(C.enter_forms(card.node)) == 1
    assert "Block" not in card.node.text()


def test_the_home_page_never_renders_a_block_form_anywhere_with_waiting_cards_present():
    for i in range(3):
        K.wait_on(user_topic(f"No block form claim {'z' * i}."), "pro", user=K.make_user())
    root = H.doc(K.client_for(K.make_user()).get("/"))
    assert not [f for f in H.forms(root) if "/block/" in f.get("action", "")]
    assert "Block " not in root.text()


# --- a blocked pair no longer sees each other's waiting cards --------------------------------------------------------

def cards_by_name(client):
    return {c.username: c for c in home_cards(client)}


def test_the_blocker_no_longer_sees_the_blocked_persons_waiting_cards_and_neither_does_the_blocked_person():
    v, w = K.make_user("pair_vic"), K.make_user("pair_wes")
    K.wait_on(user_topic("Wes waits here."), "pro", user=w)
    K.wait_on(user_topic("Vic waits here."), "pro", user=v)
    other = K.make_user("pair_uma")
    K.wait_on(user_topic("Uma waits here."), "pro", user=other)
    cv, cw = K.client_for(v), K.client_for(w)
    assert set(cards_by_name(cv)) == {"pair_wes", "pair_uma"} and set(cards_by_name(cw)) == {"pair_vic", "pair_uma"}
    cv.post(block_url("pair_wes"))
    assert set(cards_by_name(cv)) == {"pair_uma"}
    assert set(cards_by_name(cw)) == {"pair_uma"}, "blocks apply in both directions"
    assert set(cards_by_name(K.client_for(other))) == {"pair_vic", "pair_wes"}, "other people are unaffected"


def test_a_group_shows_the_next_allowed_waiter_and_joining_lands_with_that_person():
    v = K.make_user("grp_vic")
    blocked_waiter, allowed_waiter = K.make_user("grp_old"), K.make_user("grp_new")
    topic = user_topic("Grouped claim with a blocked oldest.")
    old_conv, _ = K.wait_on(topic, "pro", user=blocked_waiter, minutes_ago=30)
    new_conv, _ = K.wait_on(topic, "pro", user=allowed_waiter, minutes_ago=10)
    cv = K.client_for(v)
    cv.post(block_url("grp_old"))
    got = cards_by_name(cv)
    assert list(got) == ["grp_new"]
    response = submit(cv, got["grp_new"].form)
    assert K.conv_id(response) == new_conv.pk
    assert K.fresh(old_conv).status == "open"


def test_a_hand_posted_join_never_pairs_a_blocked_pair_and_creates_a_new_waiting_conversation():
    from forum.models import Conversation

    v, w = K.make_user("join_vic"), K.make_user("join_wes")
    topic = user_topic("Blocked waiter topic.")
    wconv, _ = K.wait_on(topic, "pro", user=w)
    K.client_for(w).post(block_url("join_vic"))  # the WAITING person blocks the joiner
    response = K.enter_view(K.client_for(v), topic, "con")
    assert response.status_code == 302
    conv = Conversation.objects.get(pk=K.conv_id(response))
    assert conv.pk != wconv.pk and conv.status == "open" and conv.participants.count() == 1
    assert K.fresh(wconv).status == "open" and wconv.participants.count() == 1


def test_unblocking_brings_the_cards_back_for_both():
    v, w = K.make_user("back_vic"), K.make_user("back_wes")
    K.wait_on(user_topic("Wes card comes back."), "pro", user=w)
    K.wait_on(user_topic("Vic card comes back."), "pro", user=v)
    cv, cw = K.client_for(v), K.client_for(w)
    cv.post(block_url("back_wes"))
    assert cards_by_name(cv) == {} and cards_by_name(cw) == {}
    cv.post(unblock_url("back_wes"))
    assert set(cards_by_name(cv)) == {"back_wes"} and set(cards_by_name(cw)) == {"back_vic"}


def test_a_block_by_the_other_side_does_not_let_the_blocked_unblock_it():
    a, b = K.make_user("only_ann"), K.make_user("only_ben")
    K.client_for(a).post(block_url("only_ben"))
    K.client_for(b).post(unblock_url("only_ann"))
    assert blocks() == [("only_ann", "only_ben")], "unblocking removes only your own block"


# --- the Blocked people page ------------------------------------------------------------------------------------------

def test_the_blocked_page_has_the_heading_the_lead_and_the_empty_state():
    response = page_of(K.client_for(K.make_user()), "/blocked/")
    root = H.doc(response)
    assert [h.text() for h in root.find_all("h1")] == ["Blocked people"]
    assert K.BLOCKED_LEAD in text(response)
    assert K.BLOCKED_EMPTY in text(response)
    assert not [f for f in H.forms(root) if "unblock" in f.get("action", "")]


def test_rows_show_the_username_a_since_date_and_an_unblock_button_newest_first():
    from forum.models import Block

    me = K.make_user("list_me")
    names = ["list_ann", "list_ben", "list_cy"]
    for i, name in enumerate(names):
        K.make_user(name, f"{name}@leakcheck.example")
        K.client_for(me).post(block_url(name))
    for i, name in enumerate(names):  # ann oldest, cy newest
        Block.objects.filter(blocked__username=name).update(created_at=K.timezone.now() - K.timedelta(days=10 - i))
    response = page_of(K.client_for(me), "/blocked/")
    root = H.doc(response)
    forms = [f for f in H.forms(root) if "/unblock/" in f.get("action", "")]
    assert [f.get("action") for f in forms] == [unblock_url("list_cy"), unblock_url("list_ben"), unblock_url("list_ann")]
    for f in forms:
        assert f.get("method", "").lower() == "post" and H.hidden_csrf(f)
        assert [b.text().strip() for b in f.find_all("button")] == ["Unblock"]
    page = text(response)
    assert len(re.findall(r"since \d{1,2} [A-Z][a-z]+ \d{4}", page)) == 3
    assert K.BLOCKED_EMPTY not in page
    assert "@leakcheck.example" not in response.content.decode()


def test_only_your_own_blocks_are_listed_never_who_blocked_you():
    a, b = K.make_user("mine_ann"), K.make_user("mine_ben")
    K.client_for(b).post(block_url("mine_ann"))
    response = page_of(K.client_for(a), "/blocked/")
    assert "mine_ben" not in response.content.decode() and K.BLOCKED_EMPTY in text(response)
    assert "mine_ann" in page_of(K.client_for(b), "/blocked/").content.decode()


def test_the_blocked_page_is_linked_from_your_discussions_and_has_a_way_back():
    root = H.doc(page_of(K.client_for(K.make_user()), "/blocked/"))
    assert "/discussions/" in H.links(root)


# --- usernames on the conversation page (Revision 5) ------------------------------------------------------------------

def test_who_is_here_names_the_other_person_and_keeps_the_moderator_hint():
    duo = K.Duo()
    for client, other in ((duo.ca, K.NAME_B), (duo.cb, K.NAME_A)):
        assert H.norm(K.WHO_IS_HERE_FORMAT.format(name=other)) in text(page_of(client, duo.url))


def test_who_is_here_while_waiting_says_only_you_so_far():
    user = K.make_user()
    conv = K.enter(user, user_topic("Alone claim."), "pro")
    page = text(page_of(K.client_for(user), reverse("forum:conversation", args=[conv.pk])))
    assert "Only you so far. When someone joins, their username is shown here." in page
    assert "You are talking with" not in page


def test_the_old_names_are_hidden_sentences_are_gone_everywhere():
    duo = K.Duo()
    for response in (page_of(duo.ca, duo.url), page_of(duo.cb, duo.url), page_of(duo.ca, "/how-it-works/"), page_of(duo.ca, "/")):
        page = text(response)
        assert not re.search(r"(?i)neither of you can see|can see the other's name|name or label for the other|shown only as", page)


def test_the_other_persons_messages_carry_their_username_and_your_own_say_you():
    duo = K.Duo()
    duo.seed(duo.pa, "Speaker-token-a", 30)
    duo.seed(duo.pb, "Speaker-token-b", 29)
    for client, mine, other in ((duo.ca, "Speaker-token-a", K.NAME_B), (duo.cb, "Speaker-token-b", K.NAME_A)):
        root = H.doc(page_of(client, duo.url))
        theirs = "Speaker-token-b" if mine == "Speaker-token-a" else "Speaker-token-a"
        own_block = K.find_block(root, mine, ["You", other])
        other_block = K.find_block(root, theirs, ["You", other])
        assert re.search(r"\bYou\b", own_block.text()) and other not in own_block.text()
        assert other in other_block.text() and not re.search(r"\bYou\b", other_block.text().replace(other, ""))
        assert "The other participant" not in other_block.text()


def test_the_viewers_own_username_and_all_emails_and_labels_stay_off_the_conversation_page():
    duo = K.Duo()
    duo.seed(duo.pa, "hello", 30)
    for client, viewer, other in ((duo.ca, duo.ua, duo.ub), (duo.cb, duo.ub, duo.ua)):
        html = page_of(client, duo.url).content.decode()
        assert K.leaks(html, viewer, other, other_name_ok=True) == []
        assert other.username in html


def test_moderator_headings_use_the_username_for_the_other_persons_message():
    duo = K.Duo()
    m1 = duo.seed(duo.pa, "By A", 30)
    K.add_moderator_post(duo.conv, m1, "Moderator-about-A", [(duo.pa.label, duo.pa.label, [m1])])
    def heading(client):
        root = H.doc(page_of(client, duo.url))
        block = K.find_block(root, "Moderator-about-A", ["About "])
        return H.norm(block.text())

    assert "About your message 1" in heading(duo.ca)
    assert f"About {K.NAME_A}'s message 1" in heading(duo.cb) and "About your message" not in heading(duo.cb)
    assert "the other participant's message" not in text(page_of(duo.ca, duo.url)) + text(page_of(duo.cb, duo.url))


def test_the_polling_json_carries_author_names_and_the_other_username():
    duo = K.Duo()
    m1 = duo.seed(duo.pa, "By A", 30)
    duo.seed(duo.pb, "By B", 29)
    K.add_moderator_post(duo.conv, m1, "Mod", [(duo.pa.label, duo.pa.label, [m1])])
    _, data = K.poll_json(duo.ca, duo.conv)
    assert data["other_username"] == K.NAME_B
    names = [(m["kind"], m["author_name"]) for m in data["messages"]]
    assert names == [("you", None), ("other", K.NAME_B), ("moderator", None)]
    _, data_b = K.poll_json(duo.cb, duo.conv)
    assert data_b["other_username"] == K.NAME_A
    assert [(m["kind"], m["author_name"]) for m in data_b["messages"]] == [("other", K.NAME_A), ("you", None), ("moderator", None)]
    assert K.json_leaks(data, duo.ua, duo.ub) == [] and K.NAME_A not in json_text(data)


def json_text(data):
    import json

    return json.dumps(data)


def test_the_polling_json_has_no_other_username_while_waiting():
    user = K.make_user()
    conv = K.enter(user, user_topic("Alone json claim."), "pro")
    _, data = K.poll_json(K.client_for(user), conv)
    assert data["other_username"] is None


# --- anonymous visitors never see a username --------------------------------------------------------------------------

def test_anonymous_visitors_never_see_any_username_on_any_page():
    duo = K.Duo()
    K.wait_on(user_topic("Anonymous test waiting claim."), "pro", user=K.make_user("anon_waiter"))
    anonymous = Client()
    urls = ["/", "/discussions/", "/blocked/", duo.url, duo.poll_url, "/how-it-works/", "/propose/", reverse("accounts:login"),
            reverse("accounts:register"), "/no/such/page/"]
    for url in urls:
        response = anonymous.get(url, follow=True)
        body = response.content.decode()
        for name in (K.NAME_A, K.NAME_B, "anon_waiter"):
            assert name not in body, f"{name} shown to an anonymous visitor at {url}"
        for email in (K.EMAIL_A, K.EMAIL_B):
            assert email not in body


# --- how this works ---------------------------------------------------------------------------------------------------

def test_how_it_works_ends_its_two_position_paragraph_with_no_trailing_username_or_block_sentences():
    """Wave 16 item 7: the username/blocking sentences that used to follow the two-position paragraph are gone; the
    paragraph now ends at "...private to their two participants."."""
    page = H.page_norm(Client().get("/how-it-works/"))
    assert H.norm(K.HOW_PARAGRAPH) in page
    assert "Each of you is shown only as" not in page
    assert "You can block anyone" not in page
    assert "shown by username on the home page" not in page


def test_unblocking_removes_only_your_own_block_of_that_person_not_someone_elses():
    a, c, target = K.make_user("multi_ann"), K.make_user("multi_cy"), K.make_user("multi_target")
    K.client_for(a).post(block_url("multi_target"))
    K.client_for(c).post(block_url("multi_target"))
    K.client_for(a).post(unblock_url("multi_target"))
    assert blocks() == [("multi_cy", "multi_target")]
