"""7c Revision 4/5: the "Your discussions" page (discussions/, forum:mine): heading, search, rows with the other person's
username, statuses, order, the ended limit, empty and no-match states, login, privacy, and the header link on every page."""
import re

import pytest
from django.test import Client
from django.urls import reverse

import fviews_html as H
import fviews_kit as K

WORDS = (K.STATUS_WAITING, K.STATUS_ACTIVE, K.STATUS_ENDED)
URL = "/discussions/"


def page(client, q=None):
    return client.get(URL, {"q": q} if q is not None else {})


def flat(text):
    return " ".join(H.unescape(text).split())


def rows(response):
    """[{href, text, status, position, with_name, node}] in page order, one per 'Open' link in the main area."""
    root = H.doc(response)
    main = root.find("main") or root
    out = []
    for link in main.find_all("a"):
        if link.text().strip() != "Open":
            continue
        node = link.parent
        while node is not None and node is not main and not any(w in node.text() for w in WORDS):
            node = node.parent
        text = flat(node.text())
        assert sum(1 for l in node.find_all("a") if l.text().strip() == "Open") == 1, "one Open link per row"
        found = [w for w in WORDS if w in text]
        assert len(found) == 1, f"one status word per row: {found} in {text!r}"
        rest = text.replace(found[0], " ").replace("Open", " ")
        rest = " ".join(rest.split())
        named = re.search(r"\bwith (\S+)$", rest)
        out.append({"href": link.get("href"), "text": text, "status": found[0], "node": node,
                    "with_name": named.group(1) if named else None,
                    "position": rest[:named.start()].strip() if named else rest})
    return out


def conv_url(conv):
    return reverse("forum:conversation", args=[conv.pk])


def user_topic(text, **kw):
    return K.make_topic(text, created_by=K.make_user(), **kw)


def seeded_topic(text, opposing="", **kw):
    return K.make_topic(text, created_by=None, opposing=opposing, **kw)


# --- the page ---------------------------------------------------------------------------------------------------------

def test_the_url_name_and_login_rule():
    assert reverse("forum:mine") == URL
    for method in ("get",):
        response = getattr(Client(), method)(URL)
        assert response.status_code == 302 and reverse("accounts:login") in response["Location"]
        assert "next=" in response["Location"]
    client = K.client_for(K.make_user())
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)(URL).status_code == 405, method


def test_heading_search_form_and_blocked_link():
    conv, me = K.wait_on(user_topic("Some claim."), "pro")
    response = page(K.client_for(me))
    root = H.doc(response)
    assert [h.text() for h in root.find_all("h1")] == [K.YOUR_DISCUSSIONS]
    search = [f for f in H.forms(root) if f.get("role") == "search"]
    assert len(search) == 1 and search[0].get("method", "get").lower() == "get"
    box = next(n for n in search[0].walk() if n.tag == "input" and n.get("name") == "q")
    assert not H.unlabelled_controls(search[0])
    assert any(l.text().strip() == K.SEARCH_LABEL == "Search your discussions" for l in search[0].find_all("label"))
    assert [b.text().strip() for b in search[0].find_all("button")] == ["Search"]
    assert search[0].get("action", "") in ("", URL)
    assert reverse("forum:blocked") in H.links(root) and any(a.text().strip() == "Blocked people" for a in root.find_all("a"))
    assert K.OLD_HOME_SECTION not in root.text()


def test_the_search_form_has_no_newest_first_hint_with_or_without_a_search():
    """The owner removed the "Newest first." hint under the search box (commit 5484f1c); the order itself is unchanged."""
    conv, me = K.wait_on(user_topic("Some claim."), "pro")
    client = K.client_for(me)
    for label, response in (("no search", page(client)), ("search", page(client, "claim")), ("no match", page(client, "zzz"))):
        root = H.doc(response)
        search = [f for f in H.forms(root) if f.get("role") == "search"]
        assert len(search) == 1, label
        assert "Newest first" not in root.text(), label
        assert [n for n in search[0].walk() if "hint" in n.get("class", "").split()] == [], label
        assert flat(search[0].text()) == K.SEARCH_LABEL + " Search", label


def test_the_search_button_keeps_its_label_on_one_line_like_the_old_home_search():
    from pathlib import Path

    css = (Path(__file__).resolve().parents[2] / "forum" / "static" / "forum" / "site.css").read_text()
    body = re.search(r"\.search-row \.btn\s*\{([^}]*)\}", css)
    assert body and "white-space: nowrap" in body.group(1) and "flex: 0 0 auto" in body.group(1)


# --- empty and no-match -------------------------------------------------------------------------------------------------

def test_the_empty_state_and_its_two_links():
    response = page(K.client_for(K.make_user()))
    root = H.doc(response)
    assert K.EMPTY_DISCUSSIONS in flat(root.text())
    para = next(p for p in root.find_all("p") if "You have no discussions yet" in p.text())
    links = {a.text().strip(): a.get("href") for a in para.find_all("a")}
    # the brief names the links "the home page" and "start one of your own"; the built page links just "one of your own"
    # (architect accepted): the sentence is the same, so either extent of the second link is fine.
    assert links.pop("the home page") == reverse("forum:home")
    assert list(links.values()) == [reverse("forum:propose")] and list(links)[0] in ("start one of your own", "one of your own")
    assert not rows(response)


def test_a_search_with_no_match_says_so_and_keeps_the_search_text():
    conv, me = K.wait_on(user_topic("Vegetables are tastier than sweets."), "pro")
    response = page(K.client_for(me), "zzzz-no-such-thing")
    assert not rows(response) and K.NO_MATCH == "No discussion matches your search."
    assert K.NO_MATCH in flat(H.doc(response).text())
    box = next(n for n in H.doc(response).walk() if n.tag == "input" and n.get("name") == "q")
    assert box.get("value") == "zzzz-no-such-thing"
    assert K.EMPTY_DISCUSSIONS not in flat(H.doc(response).text())


def test_the_no_match_sentence_is_not_shown_when_there_is_a_match_or_no_search():
    conv, me = K.wait_on(user_topic("Vegetables are tastier than sweets."), "pro")
    assert K.NO_MATCH not in flat(H.doc(page(K.client_for(me))).text())
    assert K.NO_MATCH not in flat(H.doc(page(K.client_for(me), "vegetables")).text())


# --- search -----------------------------------------------------------------------------------------------------------

def searchable():
    me = K.make_user()
    topics = {
        "veg": user_topic("Vegetables are tastier than sweets."),
        "cars": seeded_topic("Cities should ban cars.", "Cars should stay in cities."),
        "remote": user_topic("Remote work beats office work."),
    }
    convs = {}
    for i, (key, topic) in enumerate(topics.items()):
        convs[key], _ = K.wait_on(topic, "pro", user=me, minutes_ago=30 - i)
    return me, convs


def hrefs(response):
    return [r["href"] for r in rows(response)]


def test_search_finds_only_matching_discussions():
    me, convs = searchable()
    client = K.client_for(me)
    assert hrefs(page(client, "remote")) == [conv_url(convs["remote"])]
    assert hrefs(page(client, "vegetables")) == [conv_url(convs["veg"])]


def test_search_matches_the_opposing_position_of_a_seeded_topic_and_ignores_case_and_spaces():
    me, convs = searchable()
    client = K.client_for(me)
    assert hrefs(page(client, "stay in cities")) == [conv_url(convs["cars"])]
    for q in ("VEGETABLES", "VeGeTaBlEs", "  vegetables  ", "tier than sw", "  ban    cars "):
        assert len(rows(page(client, q))) == 1, q


def test_search_is_unicode_safe_and_literal():
    me = K.make_user()
    a, _ = K.wait_on(user_topic("\u00c9coles publiques should be free."), "pro", user=me, minutes_ago=10)
    b, _ = K.wait_on(user_topic("Taxes above 100% are unfair."), "pro", user=me, minutes_ago=9)
    c, _ = K.wait_on(user_topic("Snake_case is better than camelCase."), "pro", user=me, minutes_ago=8)
    d, _ = K.wait_on(seeded_topic("Stra\u00dfe names should stay.", "\u00dcbung names should change."), "pro", user=me, minutes_ago=7)
    client = K.client_for(me)
    for q in ("\u00e9coles", "\u00c9COLES"):
        assert hrefs(page(client, q)) == [conv_url(a)], q
    assert hrefs(page(client, "\u00fcbung")) == [conv_url(d)]
    assert hrefs(page(client, "%")) == [conv_url(b)]
    assert hrefs(page(client, "_")) == [conv_url(c)]
    assert hrefs(page(client, "\\")) == []


def test_search_is_safe_with_nul_injection_and_huge_input():
    me, convs = searchable()
    client = K.client_for(me)
    assert len(rows(page(client, "vegetables"))) == 1
    for q in ("\u0000", "veg\u0000etables", "' OR 1=1 --", '"; DROP TABLE forum_topic; --', "<script>alert(1)</script>", "x" * 20000):
        response = page(client, q)
        assert response.status_code == 200, q[:20]
        assert not rows(response), q[:20]
    assert "<script>alert(1)</script>" not in page(client, "<script>alert(1)</script>").content.decode()
    assert len(rows(page(client, "vegetables"))) == 1, "the data is intact"


def test_a_blank_search_lists_everything_newest_activity_first():
    me, convs = searchable()
    client = K.client_for(me)
    order = [conv_url(convs["remote"]), conv_url(convs["cars"]), conv_url(convs["veg"])]
    assert hrefs(page(client)) == order
    for q in ("", "   "):
        assert hrefs(page(client, q)) == order
    assert hrefs(page(client, "work OR cars")) == []


def test_search_only_looks_at_your_own_discussions():
    me, convs = searchable()
    K.wait_on(user_topic("Vegetables belong to somebody else."), "pro")
    assert hrefs(page(K.client_for(me), "somebody else")) == []


def test_the_search_form_keeps_the_typed_text():
    me, convs = searchable()
    box = next(n for n in H.doc(page(K.client_for(me), "vegetables")).walk() if n.tag == "input" and n.get("name") == "q")
    assert box.get("value") == "vegetables"


def test_a_search_shows_all_matching_ended_rows_while_no_search_is_limited(settings):
    settings.MY_ENDED_CONVERSATIONS_SHOWN = 2
    me = K.make_user()
    ended = []
    for i in range(4):
        conv, _ = K.wait_on(user_topic(f"Ended cat claim {i}."), "pro", user=me, minutes_ago=100 - i * 10)
        K.client_for(me).post(reverse("forum:end", args=[conv.pk]))
        ended.append(conv)
    client = K.client_for(me)
    assert len(rows(page(client))) == 2, "without a search only the most recent ended ones show"
    assert len(rows(page(client, "cat claim"))) == 4, "with a search every matching ended row is shown"
    assert len(rows(page(client, "claim 0"))) == 1


# --- rows: position, with <username>, status, Open ---------------------------------------------------------------------

def test_a_pro_holder_sees_your_position_with_the_proposition_and_a_waiting_row_has_no_partner():
    conv, me = K.wait_on(user_topic("Cats make better pets than dogs."), "pro")
    [row] = rows(page(K.client_for(me)))
    assert row["position"] == "Your position: Cats make better pets than dogs."
    assert row["status"] == K.STATUS_WAITING and row["href"] == conv_url(conv)
    assert row["with_name"] is None, "nobody else is in a waiting conversation"


def test_a_con_holder_on_a_seeded_topic_sees_the_opposing_wording_as_their_position():
    conv, me = K.wait_on(seeded_topic("Cities should cap rents.", "Cities should not cap rents."), "con")
    [row] = rows(page(K.client_for(me)))
    assert row["position"] == "Your position: Cities should not cap rents."


def test_a_con_holder_on_a_user_created_topic_sees_they_disagree_with_the_proposition():
    conv, me = K.wait_on(user_topic("Cats make better pets than dogs."), "con")
    [row] = rows(page(K.client_for(me)))
    assert row["position"] == "You disagree with this position: Cats make better pets than dogs."


def test_a_blank_side_row_shows_your_position_with_the_proposition():
    from forum.models import Conversation, Participant

    me = K.make_user()
    conv = Conversation.objects.create(topic=user_topic("Old row claim."), status="open")
    Participant.objects.create(conversation=conv, user=me, label="A", join_order=1)
    [row] = rows(page(K.client_for(me)))
    assert row["position"] == "Your position: Old row claim."


def test_the_three_status_words_the_partner_name_and_open_links_work():
    waiting_conv, me = K.wait_on(user_topic("Waiting one."), "pro")
    other1, other2 = K.make_user("partner_pia", "partner_pia@leakcheck.example"), K.make_user("partner_ted", "partner_ted@leakcheck.example")
    a_topic, b_topic = user_topic("Active for me."), user_topic("Ended for me.")
    K.wait_on(a_topic, "pro", user=other1)
    joined = K.enter(me, a_topic, "con")
    K.wait_on(b_topic, "pro", user=other2)
    j2 = K.enter(me, b_topic, "con")
    K.client_for(other2).post(reverse("forum:end", args=[j2.pk]))
    client = K.client_for(me)
    response = page(client)
    by_href = {r["href"]: r for r in rows(response)}
    assert by_href[conv_url(waiting_conv)]["status"] == K.STATUS_WAITING and by_href[conv_url(waiting_conv)]["with_name"] is None
    assert (by_href[conv_url(joined)]["status"], by_href[conv_url(joined)]["with_name"]) == (K.STATUS_ACTIVE, "partner_pia")
    assert (by_href[conv_url(j2)]["status"], by_href[conv_url(j2)]["with_name"]) == (K.STATUS_ENDED, "partner_ted")
    for href in by_href:
        assert client.get(href).status_code == 200
    page_text = response.content.decode()
    assert "partner_pia@" not in page_text and "partner_ted@" not in page_text


def test_the_position_line_comes_before_the_name_and_the_status_in_each_row():
    duo = K.Duo("Order in row claim.", names=("row_one", "row_two"))
    [row] = rows(page(duo.ca))
    text = row["text"]
    assert text.index("Your position:") < text.index("with row_two") < text.index(K.STATUS_ACTIVE) < text.index("Open")


def test_a_conversation_ended_by_the_other_person_is_ended():
    duo = K.Duo("Ended by other.", names=("ended_by_a", "ended_by_b"))
    duo.cb.post(duo.end_url)
    [row] = rows(page(duo.ca))
    assert row["status"] == K.STATUS_ENDED and row["href"] == duo.url and row["with_name"] == "ended_by_b"


def test_a_row_shows_only_the_viewers_position_never_the_other_persons():
    duo = K.Duo("Pair proposition.", opposing="Pair opposite.", names=("pos_one", "pos_two"))
    for client, mine, theirs in ((duo.ca, "Pair proposition.", "Pair opposite."), (duo.cb, "Pair opposite.", "Pair proposition.")):
        [row] = rows(page(client))
        assert row["position"] == "Your position: " + mine and theirs not in row["position"]


def test_row_text_is_escaped():
    duo = K.Duo("<b>Bold</b> & <script>alert(1)</script>", names=("esc_one", "esc_two"))
    text = page(duo.ca).content.decode()
    assert "<b>Bold</b>" not in text and "<script>alert(1)</script>" not in text and "&lt;b&gt;Bold&lt;/b&gt;" in text


# --- order and limit -------------------------------------------------------------------------------------------------

def test_rows_are_newest_activity_first_with_ended_ones_last():
    me = K.make_user()
    t = [user_topic(f"Order claim {c}.") for c in "abcd"]
    a, _ = K.wait_on(t[0], "pro", user=me, minutes_ago=100)
    b, _ = K.wait_on(t[1], "pro", user=me, minutes_ago=50)
    c, _ = K.wait_on(t[2], "pro", user=me, minutes_ago=20)
    d, _ = K.wait_on(t[3], "pro", user=me, minutes_ago=90)
    K.seed_message(a, K.participant_of(a, me), "old conversation, fresh message", minutes_ago=10)
    K.seed_message(d, K.participant_of(d, me), "last words", minutes_ago=1)
    K.client_for(me).post(reverse("forum:end", args=[d.pk]))
    assert hrefs(page(K.client_for(me))) == [conv_url(a), conv_url(c), conv_url(b), conv_url(d)]


def test_only_the_most_recent_ended_conversations_are_shown_without_a_search(settings):
    settings.MY_ENDED_CONVERSATIONS_SHOWN = 2
    me = K.make_user()
    ended = []
    for i in range(4):
        conv, _ = K.wait_on(user_topic(f"Ended claim {i}."), "pro", user=me, minutes_ago=100 - i * 10)
        K.client_for(me).post(reverse("forum:end", args=[conv.pk]))
        ended.append(conv)
    live, _ = K.wait_on(user_topic("Still waiting claim."), "pro", user=me, minutes_ago=200)
    got = hrefs(page(K.client_for(me)))
    assert got[0] == conv_url(live)
    assert set(got[1:]) == {conv_url(ended[3]), conv_url(ended[2])}


def test_the_ended_limit_defaults_to_ten():
    from django.conf import settings as live_settings

    assert live_settings.MY_ENDED_CONVERSATIONS_SHOWN == 10
    me = K.make_user()
    for i in range(12):
        conv, _ = K.wait_on(user_topic(f"Old ended claim {i}."), "pro", user=me, minutes_ago=300 - i)
        K.client_for(me).post(reverse("forum:end", args=[conv.pk]))
    assert len(rows(page(K.client_for(me)))) == 10


def test_ended_rows_are_marked_quieter():
    me = K.make_user()
    live, _ = K.wait_on(user_topic("Live row."), "pro", user=me)
    gone, _ = K.wait_on(user_topic("Gone row."), "pro", user=me)
    K.client_for(me).post(reverse("forum:end", args=[gone.pk]))
    by = {r["href"]: r for r in rows(page(K.client_for(me)))}
    marks = lambda n: " ".join([n.get("class", "")] + [c.get("class", "") for c in n.walk()])
    assert re.search(r"ended|quiet|muted|closed", marks(by[conv_url(gone)]["node"]))
    assert not re.search(r"\bis-ended\b|\bended\b", by[conv_url(live)]["node"].get("class", ""))


# --- only your own data ------------------------------------------------------------------------------------------------

def test_other_peoples_conversations_and_data_never_appear():
    duo = K.Duo("Pair claim.", names=("owner_one", "owner_two"))
    duo.seed(duo.pa, "Secret-message-words.", 20)
    stranger = K.make_user("stranger_sam", "stranger_sam@leakcheck.example")
    response = page(K.client_for(stranger))
    text = response.content.decode()
    assert not rows(response) and K.EMPTY_DISCUSSIONS in flat(H.doc(response).text())
    for secret in ("owner_one", "owner_two", "Secret-message-words.", "Pair claim.", duo.url):
        assert secret not in text
    _, other_wait = K.wait_on(user_topic("Waiting elsewhere."), "pro")
    assert "Waiting elsewhere." not in page(K.client_for(stranger)).content.decode()


def test_privacy_scan_of_the_page_partner_name_ok_email_label_and_participant_never():
    duo = K.Duo("Privacy claim.", names=("priv_one", "priv_two"))
    duo.seed(duo.pa, "hello", 20)
    for client, me, other in ((duo.ca, duo.ua, duo.ub), (duo.cb, duo.ub, duo.ua)):
        html = page(client).content.decode()
        assert K.leaks(html, me, other, own_name_in_header_ok=True, other_name_ok=True) == []
        assert other.username in html


def test_a_blocked_partner_stays_in_the_list_as_an_ended_discussion():
    duo = K.Duo("Blocked pair claim.", names=("blk_one", "blk_two"))
    K.csrf_post(duo.ca, "/users/blk_two/block/")
    for client, name in ((duo.ca, "blk_two"), (duo.cb, "blk_one")):
        [row] = rows(page(client))
        assert row["status"] == K.STATUS_ENDED and row["href"] == duo.url and row["with_name"] == name


# --- the header link on every page -------------------------------------------------------------------------------------

def header_links(response):
    root = H.doc(response)
    header = root.find("header")
    return {a.text().strip(): a.get("href") for a in (header.find_all("a") if header is not None else [])}


def logged_in_pages():
    duo = K.Duo("Header pages claim.", names=("hdr_one", "hdr_two"))
    wconv, wuser = K.wait_on(user_topic("Header waiting claim."), "pro")
    duo.ca.post(reverse("forum:end", args=[K.enter(duo.ua, K.make_topic("Other topic for ending.", created_by=duo.ub), "pro").pk]))
    return {
        "home": duo.ca.get("/"), "propose": duo.ca.get("/propose/"), "how": duo.ca.get("/how-it-works/"),
        "discussions": duo.ca.get(URL), "blocked": duo.ca.get("/blocked/"), "conversation": duo.ca.get(duo.url),
        "waiting conversation": K.client_for(wuser).get(reverse("forum:conversation", args=[wconv.pk])),
        "password change": duo.ca.get(reverse("accounts:password_change")), "404": duo.ca.get("/no/such/page/"),
        "missing conversation": duo.ca.get(reverse("forum:conversation", args=[987654])),
    }


def test_the_header_has_the_your_discussions_link_on_every_page_for_logged_in_people():
    for label, response in logged_in_pages().items():
        links = header_links(response)
        assert links.get("Your discussions") == URL, f"{label}: {links}"
        assert "How this works" in links, label
        assert not re.search(r"\d", "Your discussions"), "no count in the link"


def test_the_header_links_are_contiguous_how_this_works_then_waiting_then_yours():
    """Wave 16 item 2 inserted "Waiting to discuss" between "How this works" and "Your discussions", so the three
    are contiguous in that order (no longer "Your discussions" directly next to "How this works")."""
    response = logged_in_pages()["home"]
    header = H.doc(response).find("header")
    texts = [a.text().strip() for a in header.find_all("a")]
    i_how, i_wait, i_yours = texts.index("How this works"), texts.index("Waiting to discuss"), texts.index("Your discussions")
    assert i_how < i_wait < i_yours
    assert i_wait - i_how == 1 and i_yours - i_wait == 1


def test_the_header_link_is_absent_for_anonymous_visitors():
    for url in ("/how-it-works/", reverse("accounts:login"), reverse("accounts:register"), "/no/such/page/"):
        response = Client().get(url)
        assert "Your discussions" not in H.doc(response).text(), url
