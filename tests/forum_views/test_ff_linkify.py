"""Front-end fix 4 (docs/frontend_fixes_brief.md): the `linkify` template filter in forum/templatetags/forum_text.py and its use
on moderator paragraphs only. The filter's name is the brief's example ("e.g. `linkify`"); these tests assume it."""
import re

import pytest
from django.template import Context, Template
from django.utils.html import escape
from django.utils.safestring import mark_safe

import ff_kit as F
import fviews_html as H
import fviews_kit as K

REL = "noopener noreferrer nofollow"


def render(text):
    """What a template shows for `{{ text|linkify }}` (autoescape on, as in _message.html)."""
    return Template("{% load forum_text %}{{ t|linkify }}").render(Context({"t": text}))


def parse(out):
    return H.parse(f"<html><body><p id='x'>{out}</p></body></html>").find("p", id="x")


def anchors(text):
    return parse(render(text)).find_all("a")


def only_anchor(text):
    found = anchors(text)
    assert len(found) == 1, (text, render(text))
    return found[0]


def check_anchor(a, url):
    assert a.get("href") == url and a.text() == url
    assert a.get("target") == "_blank" and a.get("rel") == REL
    assert set(a.attrs) == {"href", "target", "rel"}


# --- plain URLs ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://example.com", "http://example.com/", "https://example.com/a/b?c=1#frag", "https://sub.example.co.uk/path/to/page.html",
    "http://example.com:8080/x", "https://example.com/a_b-c~d+e%20f",
])
def test_a_plain_url_becomes_one_link(url):
    check_anchor(only_anchor(f"See {url} for details"), url)


def test_the_exact_markup_of_the_link():
    assert render("go https://x.org/a now") == (
        'go <a href="https://x.org/a" target="_blank" rel="noopener noreferrer nofollow">https://x.org/a</a> now')


def test_text_around_the_link_is_kept_in_order():
    out = render("before https://x.org/a after")
    assert out.startswith("before <a ") and out.endswith("</a> after")


def test_several_urls_each_become_a_link():
    found = anchors("one https://a.org/1 two http://b.org/2, three https://c.org/3.")
    assert [a.get("href") for a in found] == ["https://a.org/1", "http://b.org/2", "https://c.org/3"]


def test_the_url_ends_at_whitespace_and_newlines_are_kept_as_they_are():
    out = render("line one https://x.org/a\nline two\n\nthird https://y.org/b\tend")
    assert "</a>\nline two\n\nthird " in out and out.endswith("</a>\tend")
    assert [a.get("href") for a in parse(out).find_all("a")] == ["https://x.org/a", "https://y.org/b"]


def test_a_query_string_with_ampersands_is_escaped_in_both_href_and_text():
    out = render("https://x.org/a?b=1&c=2")
    assert 'href="https://x.org/a?b=1&amp;c=2"' in out and ">https://x.org/a?b=1&amp;c=2</a>" in out
    check_anchor(only_anchor("https://x.org/a?b=1&c=2"), "https://x.org/a?b=1&c=2")


def test_an_entity_looking_url_is_escaped_not_decoded():
    out = render("https://x.org/?a=1&amp;b=2")
    assert "&amp;amp;" in out
    assert only_anchor("https://x.org/?a=1&amp;b=2").get("href") == "https://x.org/?a=1&amp;b=2"


# --- trailing punctuation and parentheses -------------------------------------------------------------------------------------

@pytest.mark.parametrize("punct", [".", ",", ";", ":", "!", "?"])
def test_one_trailing_punctuation_mark_is_not_part_of_the_link(punct):
    out = render(f"read https://x.org/a{punct} Then more")
    check_anchor(parse(out).find("a"), "https://x.org/a")
    assert f"</a>{punct} Then more" in out


@pytest.mark.parametrize("tail", ["...", "?!", "!?!", ".,;:", "?."])
def test_several_trailing_marks_are_all_left_out(tail):
    out = render(f"https://x.org/a{tail}")
    check_anchor(parse(out).find("a"), "https://x.org/a")
    assert out.endswith(f"</a>{tail}")


def test_punctuation_inside_the_url_stays():
    check_anchor(only_anchor("https://x.org/a.b,c;d:e!f?g=h"), "https://x.org/a.b,c;d:e!f?g=h")


def test_a_link_in_brackets_after_a_title_excludes_the_closing_parenthesis():
    out = render("Title (https://x.org/a)")
    check_anchor(parse(out).find("a"), "https://x.org/a")
    assert out.endswith("</a>)") and out.startswith("Title (")


def test_a_balanced_parenthesis_inside_the_url_is_kept():
    check_anchor(only_anchor("https://en.wikipedia.org/wiki/Foo_(bar)"), "https://en.wikipedia.org/wiki/Foo_(bar)")


def test_balanced_parentheses_in_the_url_and_the_wrapping_one_outside():
    out = render("(see https://en.wikipedia.org/wiki/Foo_(bar))")
    check_anchor(parse(out).find("a"), "https://en.wikipedia.org/wiki/Foo_(bar)")
    assert out.endswith("</a>)")


@pytest.mark.parametrize("text,url,after", [
    ("(https://x.org/a).", "https://x.org/a", ")."),
    ("(https://x.org/a.)", "https://x.org/a", ".)"),
    ("https://x.org/a))", "https://x.org/a", "))"),
    ("https://x.org/a),", "https://x.org/a", "),"),
    ("(https://x.org/a?)", "https://x.org/a", "?)"),
    ("https://x.org/f_(b))", "https://x.org/f_(b)", ")"),
    ("https://x.org/f_(b).", "https://x.org/f_(b)", "."),
    ("https://x.org/f_(b)),", "https://x.org/f_(b)", "),"),
])
def test_punctuation_and_parenthesis_stripping_combine(text, url, after):
    out = render(text)
    check_anchor(parse(out).find("a"), url)
    assert out.endswith("</a>" + after), out


def test_an_unclosed_opening_parenthesis_stays_in_the_url():
    check_anchor(only_anchor("https://x.org/(a"), "https://x.org/(a")


def test_two_closings_with_one_opening_keeps_one():
    check_anchor(only_anchor("https://x.org/a(b))"), "https://x.org/a(b)")


# --- other schemes never become links -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "javascript:alert(1)", "JavaScript:alert(1)", "data:text/html,<b>x</b>", "ftp://x.org/file", "www.example.com",
    "see www.example.com/page", "mailto:a@b.org", "file:///etc/passwd", "//evil.example/x", "vbscript:msgbox(1)",
    "tel:+123", "ws://x.org", "x.org/path", "example.com",
])
def test_other_schemes_and_bare_hosts_stay_text(text):
    out = render(text)
    assert "<a" not in out.lower() and "href" not in out.lower()
    assert out == escape(text)


def test_only_the_http_url_in_a_mixed_text_is_linked():
    found = anchors("javascript:alert(1) www.a.org ftp://b.org https://good.org/x data:x")
    assert [a.get("href") for a in found] == ["https://good.org/x"]


# --- hostile input stays inert --------------------------------------------------------------------------------------------------

def test_markup_in_the_text_is_escaped():
    out = render("<script>alert(1)</script> <b>bold</b> <img src=x onerror=alert(1)>")
    assert "<script" not in out and "<b>" not in out and "<img" not in out
    assert "&lt;script&gt;" in out
    assert parse(out).find_all("script") == [] and parse(out).find_all("img") == []


def test_a_quote_breaking_url_cannot_add_attributes():
    out = render('<script>, https://a.b/"onmouseover="x')
    assert "<script" not in out
    found = parse(out).find_all("a")
    assert len(found) == 1 and set(found[0].attrs) == {"href", "target", "rel"}
    assert found[0].get("href").startswith("https://a.b/")
    assert 'onmouseover' not in found[0].attrs


@pytest.mark.parametrize("hostile", [
    'https://a.b/"onmouseover="alert(1)', "https://a.b/'onclick='alert(1)", 'https://a.b/"><script>alert(1)</script>',
    "https://a.b/<img", "https://a.b/?x=<script>alert(1)</script>", "https://a.b/>x", "https://a.b/\"'<>&",
    "https://a.b/`onfocus=alert(1)",
])
def test_an_href_never_holds_a_raw_quote_or_angle_bracket(hostile):
    out = render(f"x {hostile} y")
    for value in re.findall(r'href="([^"]*)"', out):
        assert not re.search(r"[\"'<>]", value), (hostile, out)
    assert "<script" not in out and "<img" not in out
    tree = parse(out)
    assert {n.tag for n in tree.walk()} <= {"a"}, out
    for a in tree.find_all("a"):
        assert set(a.attrs) == {"href", "target", "rel"}, out


def test_the_text_of_the_link_is_escaped_too():
    out = render('https://a.b/<b>x</b>')
    assert "<b>" not in out and "</b>" not in out


def test_the_filter_never_trusts_input_marked_safe():
    out = render(mark_safe("<b>bold</b> <script>x</script> https://x.org/a"))
    assert "<b>" not in out and "<script" not in out and "&lt;b&gt;" in out
    check_anchor(parse(out).find("a"), "https://x.org/a")


def test_the_direct_call_escapes_and_returns_markup_the_template_will_not_escape_again():
    from forum.templatetags import forum_text

    assert hasattr(forum_text, "linkify"), "forum/templatetags/forum_text.py must define `linkify`"
    value = forum_text.linkify("a <b> https://x.org/a")
    assert "&lt;b&gt;" in value and "<b>" not in value
    assert hasattr(value, "__html__"), "the result must be safe markup, or the template would show the <a> as text"


def test_text_without_urls_equals_the_plain_escape():
    for text in ["", "plain words", "AT&T <b> \"quoted\" 'single'", "a\nb\n\nc", "ends with colon:", "x http:/ y", "https:/x.org",
                 "http", "ahttps", "5 > 3 & 2 < 4"]:
        assert render(text) == escape(text), text


def test_ampersand_and_quotes_are_escaped_exactly_as_django_does():
    assert render('Tom & "Jerry"') == escape('Tom & "Jerry"')


def test_the_link_text_is_not_double_escaped_in_the_rendered_page_view():
    out = render("https://x.org/a?x=1&y=2")
    assert "&amp;amp;" not in out


def test_an_empty_url_body_does_not_crash():
    for text in ("http://", "https://", "(https://)", "https://.", "https://?"):
        render(text)


# --- the filter in the page: moderator paragraphs only ----------------------------------------------------------------------------

URL = "https://example.org/source/page"


def moderator_world(text, user_text="plain user words"):
    duo = K.Duo()
    trigger = duo.seed(duo.pa, user_text)
    mod, act = F.post_with_act(duo.conv, trigger, act_type="request_clarification", text=text)
    return duo, trigger, mod


def article(root, kind):
    return [n for n in root.walk() if n.tag == "article" and F.has_class(n, kind)]


def test_a_moderator_paragraph_on_the_page_links_its_urls():
    duo, _, mod = moderator_world(f"A source: {URL}.")
    root = H.doc(duo.ca.get(duo.url))
    (art,) = article(root, "msg-moderator")
    link = art.find("a", href=URL)
    assert link is not None and link.get("target") == "_blank" and link.get("rel") == REL and link.text() == URL
    paragraph = [p for p in art.find_all("p") if F.has_class(p, "msg-text")][0]
    assert link in list(paragraph.walk()) and paragraph.text().endswith(f"{URL}.")


def test_every_paragraph_of_a_moderator_message_is_linkified():
    text = "First https://a.org/1.\n\nSecond (https://b.org/2)"
    duo, _, mod = moderator_world(text)
    root = H.doc(duo.ca.get(duo.url))
    (art,) = article(root, "msg-moderator")
    paragraphs = [p for p in art.find_all("p") if F.has_class(p, "msg-text")]
    assert [[a.get("href") for a in p.find_all("a")] for p in paragraphs] == [["https://a.org/1"], ["https://b.org/2"]]


def test_the_polled_moderator_html_carries_the_links():
    duo, _, mod = moderator_world(f"A source: {URL}")
    _, data = K.poll_json(duo.ca, duo.conv, after=0)
    html = next(m["html"] for m in data["messages"] if m["kind"] == "moderator")
    tree = H.parse(html)
    link = tree.find("a", href=URL)
    assert link is not None and link.get("rel") == REL and link.get("target") == "_blank"


def test_user_messages_stay_plain_text_on_the_page_and_in_the_poll():
    duo = K.Duo()
    duo.seed(duo.pa, f"my source is {URL} and www.example.com")
    duo.seed(duo.pb, f"theirs is {URL}.")
    root = H.doc(duo.ca.get(duo.url))
    for kind in ("msg-you", "msg-other"):
        (art,) = article(root, kind)
        assert art.find_all("a") == [], kind
        assert URL in art.text()
    _, data = K.poll_json(duo.ca, duo.conv, after=0)
    for m in data["messages"]:
        assert "<a " not in m["html"] and URL in m["html"], m["html"]


def test_user_text_with_markup_is_still_escaped_exactly_as_before():
    nasty = '<img src=x onerror=alert(1)> https://a.b/"onmouseover="x'
    duo = K.Duo()
    duo.seed(duo.pa, nasty)
    raw = duo.ca.get(duo.url).content.decode()
    assert "<img src=x" not in raw and "&lt;img src=x onerror=alert(1)&gt;" in raw
    assert H.parse(raw).find("article", **{"data-kind": "you"}).find_all("a") == []


def test_hostile_moderator_text_is_inert_on_the_page_and_in_the_poll():
    text = '<script>alert(1)</script> https://a.b/"onmouseover="x'
    duo, _, mod = moderator_world(text)
    raw = duo.ca.get(duo.url).content.decode()
    _, data = K.poll_json(duo.ca, duo.conv, after=0)
    polled = " ".join(m["html"] for m in data["messages"])
    for blob in (raw, polled):
        assert "<script>alert(1)" not in blob
        assert not re.search(r'<a [^>]*\sonmouseover', blob)
        for value in re.findall(r'href="(https?://a\.b[^"]*)"', blob):
            assert '"' not in value and "<" not in value


def test_the_heading_and_other_parts_of_the_moderator_card_are_not_linkified():
    duo, _, mod = moderator_world("plain note without links")
    root = H.doc(duo.ca.get(duo.url))
    (art,) = article(root, "msg-moderator")
    hrefs = [a.get("href") for a in art.find_all("a")]
    assert hrefs == ["/how-it-works/"] or all("how" in h for h in hrefs), hrefs


def test_a_research_note_with_sources_is_linkified_even_without_acts():
    """The prime use: the research note is a moderator message with no acts of its own, listing source URLs."""
    duo = K.Duo()
    trigger = duo.seed(duo.pa, "A claim worth checking.")
    F.note_message(duo.conv, trigger, "Independent sources disagree.\n\nSources:\nSite One - https://one.example/a\nSite Two - https://two.example/b.")
    root = H.doc(duo.ca.get(duo.url))
    (art,) = article(root, "msg-moderator")
    assert [a.get("href") for a in art.find_all("a") if a.get("target")] == ["https://one.example/a", "https://two.example/b"]
    _, data = K.poll_json(duo.ca, duo.conv, after=0)
    html = next(m["html"] for m in data["messages"] if m["kind"] == "moderator")
    assert H.parse(html).find("a", href="https://one.example/a") is not None


def test_an_anchor_written_in_the_text_is_inert_not_a_second_link():
    out = render('<a href="https://x.org/a">click</a> and <a href="javascript:alert(1)">x</a>')
    tree = parse(out)
    assert all(set(a.attrs) == {"href", "target", "rel"} for a in tree.find_all("a"))
    assert not [a for a in tree.find_all("a") if a.get("href", "").startswith("javascript")]
    assert "&lt;a href=" in out


def test_a_url_right_after_an_opening_bracket_or_quote_is_still_a_link():
    for text, url in (("(https://x.org/a", "https://x.org/a"), ('"https://x.org/a', None)):
        found = anchors(text)
        if url:
            assert [a.get("href") for a in found] == [url]
        for a in found:
            assert set(a.attrs) == {"href", "target", "rel"}


def test_uppercase_http_never_produces_an_unsafe_or_double_escaped_link():
    out = render("HTTPS://X.ORG/A and Http://y.org/b")
    assert "&amp;amp;" not in out
    for a in parse(out).find_all("a"):
        assert a.get("href").lower().startswith(("http://", "https://")) and set(a.attrs) == {"href", "target", "rel"}
