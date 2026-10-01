"""Small text helpers for templates (step 7c, revision 2)."""

import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()

# Proper nouns that commonly start a political claim and keep their capital after "My position is that ".
PROPER_NOUNS = frozenset(
    """America American Americans Canada Canadian China Chinese Europe European Germany Israel Israeli Mexico Mexican
    Russia Russian Ukraine Ukrainian Palestinian Palestine Iran India Britain British France French Congress Democrats
    Republicans Trump Biden Muslims Christians Jews Black White Asian Hispanic Latino""".split()
)

_FIRST_WORD = re.compile(r"\S+")
_LETTERS = re.compile(r"[^\W\d_]+")


def position_phrase(text):
    """``text`` ready to follow "My position is that ": its first letter lower-cased, and nothing else changed.

    The first letter stays as it is when the first word is an acronym or number-like (two or more capital letters, or a
    digit), is "I" or begins "I'" (I'm, I'll), or is one of ``PROPER_NOUNS``. Proper nouns outside that list are
    lower-cased; the owner accepts this for the MVP. The stored proposition is never changed.
    """
    if not isinstance(text, str) or not text:
        return text
    start = len(text) - len(text.lstrip())
    match = _FIRST_WORD.match(text, start)
    if match is None:
        return text
    word = match.group(0)
    first = text[start]
    if not first.isupper():
        return text
    if sum(1 for ch in word if ch.isupper()) >= 2 or any(ch.isdigit() for ch in word):
        return text
    if word == "I" or word.startswith(("I'", "I’")):
        return text
    letters = _LETTERS.match(word)
    if letters is not None and letters.group(0) in PROPER_NOUNS:
        return text
    return text[:start] + first.lower() + text[start + 1:]


register.filter("position_phrase", position_phrase)


_URL = re.compile(r"(?<![A-Za-z0-9+.-])https?://[^\s]+", re.IGNORECASE)
_TRAILING_PUNCTUATION = ".,;:!?"


def _split_url(raw):
    """(url, trailing) for a run of non-space characters that starts with a scheme: trailing punctuation and an unmatched
    closing parenthesis are not part of the link."""
    url = raw
    while url:
        last = url[-1]
        if last in _TRAILING_PUNCTUATION or (last == ")" and url.count(")") > url.count("(")):
            url = url[:-1]
        else:
            break
    return url, raw[len(url):]


def linkify(text):
    """Plain ``text`` as safe HTML in which each http(s) URL is a link that opens in a new tab. ``text`` is never trusted:
    every piece, link text and href included, is escaped, so the only markup in the result is the anchors built here."""
    if text is None:
        return ""
    text = str(text)
    out = []
    position = 0
    for match in _URL.finditer(text):
        url, trailing = _split_url(match.group(0))
        if "//" not in url or url.endswith("//"):
            continue  # a scheme and nothing after it
        out.append(escape(text[position:match.start()]))
        shown = escape(url)
        out.append(f'<a href="{shown}" target="_blank" rel="noopener noreferrer nofollow">{shown}</a>')
        out.append(escape(trailing))
        position = match.end()
    out.append(escape(text[position:]))
    return mark_safe("".join(out))


register.filter("linkify", linkify)
