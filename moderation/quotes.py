"""Find the phrase the model quoted inside a message and return where it is (docs/plan.md section 6).

The model returns only quote text; this module computes the character offsets. The offsets always index the ORIGINAL
message text (Python string indices, that is Unicode code points), so `text[start:end]` is the located phrase.

Matching, in order:
1. exact: the quote occurs in the text as given (first occurrence wins; `occurrences` counts all of them);
2. normalized: ignoring Unicode form (NFC against NFD), case, treating any run of whitespace as one space, treating curly and straight quotes and
   apostrophes as equal, and treating the different dashes as equal (also "…" as "...");
3. the same two attempts with the quote HTML-unescaped (`&amp;` to `&`), for a quote copied in its escaped form;
4. not_found (`start` and `end` are None, `occurrences` is 0), including for an empty or whitespace-only quote.
"""
import html
import unicodedata
from dataclasses import dataclass

EXACT = "exact"
NORMALIZED = "normalized"
NOT_FOUND = "not_found"

_SINGLE_QUOTES = "‘’‚‛′ʼ`´"
_DOUBLE_QUOTES = "“”„‟″«»"
_DASHES = "‐‑‒–—―−﹘﹣－"

_CHAR_MAP = {}
_CHAR_MAP.update({c: "'" for c in _SINGLE_QUOTES})
_CHAR_MAP.update({c: '"' for c in _DOUBLE_QUOTES})
_CHAR_MAP.update({c: "-" for c in _DASHES})
_CHAR_MAP["…"] = "..."


@dataclass(frozen=True)
class QuoteMatch:
    start: int | None  # index of the first character of the quote in the original text
    end: int | None  # index just after the last character (exclusive), like a slice end
    match: str  # "exact", "normalized" or "not_found"
    occurrences: int  # how many times the quote occurs (by the rule that matched); 0 if not found


def _normalize(text, *, strip, nfc):
    """Return (normalized, starts, ends): starts[i] and ends[i] are the start and end (exclusive) indices in `text` of the
    original characters that normalized[i] came from. With `nfc`, Unicode form is also normalized to NFC one cluster
    (a character plus its combining marks) at a time, so a decomposed "e" + accent matches a composed "é" and both map
    back to the original characters. Without it, every character stands alone, so a quote may start or end inside a
    cluster (for example the "e" of "e" + accent)."""
    chars = []
    starts = []
    ends = []
    previous_was_space = False
    size = len(text)
    index = 0
    while index < size:
        char = text[index]
        if char.isspace():
            if not previous_was_space:
                chars.append(" ")
                starts.append(index)
                ends.append(index + 1)
            previous_was_space = True
            index += 1
            continue
        previous_was_space = False
        stop = index + 1
        while nfc and stop < size and unicodedata.combining(text[stop]):
            stop += 1
        cluster = text[index:stop]
        if stop - index > 1 or not char.isascii():
            cluster = unicodedata.normalize("NFC", cluster)
        for piece_char in cluster:
            piece = _CHAR_MAP.get(piece_char)
            if piece is None:
                piece = piece_char.casefold()
            for out_char in piece:
                chars.append(out_char)
                starts.append(index)
                ends.append(stop)
        index = stop
    normalized = "".join(chars)
    if strip:
        left = len(normalized) - len(normalized.lstrip(" "))
        right = len(normalized.rstrip(" "))
        normalized = normalized[left:right]
        starts = starts[left:right]
        ends = ends[left:right]
    return normalized, starts, ends


def _count(haystack, needle):
    return haystack.count(needle)


def locate_quote(text, quote):
    """Locate `quote` in `text`. See the module docstring for the rules. Never raises for str input."""
    if not isinstance(text, str) or not isinstance(quote, str) or not quote.strip():
        return QuoteMatch(None, None, NOT_FOUND, 0)

    start = text.find(quote)
    if start != -1:
        return QuoteMatch(start, start + len(quote), EXACT, _count(text, quote))

    # The renderer escapes & < > " in messages, so a model may copy the escaped form: try the unescaped quote last.
    candidates = [quote]
    unescaped = html.unescape(quote)
    if unescaped != quote:
        candidates.append(unescaped)
    texts = {}  # nfc flag -> the normalized text, built only when needed
    for candidate in candidates:
        if candidate is not quote and candidate.strip():
            start = text.find(candidate)
            if start != -1:
                return QuoteMatch(start, start + len(candidate), NORMALIZED, _count(text, candidate))
        for nfc in (False, True):
            needle, _, _ = _normalize(candidate, strip=True, nfc=nfc)
            if not needle:
                continue
            if nfc not in texts:
                texts[nfc] = _normalize(text, strip=False, nfc=nfc)
            haystack, starts, ends = texts[nfc]
            position = haystack.find(needle)
            if position == -1:
                continue
            # The slice starts at the first original character that contributed and ends after the last one.
            return QuoteMatch(starts[position], ends[position + len(needle) - 1], NORMALIZED, _count(haystack, needle))
    return QuoteMatch(None, None, NOT_FOUND, 0)
