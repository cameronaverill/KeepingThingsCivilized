"""Helpers for the mechanical-series tests. The ground truth is recomputed here, independently of the generator."""
import re
from collections import Counter

from step3_testkit import count_chars, is_series, load_transcripts

FACTORS = {"message_length", "label_swap", "flooding", "repetition", "unanswered_question"}
LENGTH_TARGETS = {
    "factual": {"short": 150, "long": 1000, "very_long": 2500},
    "abusive": {"short": 120, "very_long": 2500},
}
PROCESS_FACTORS = {"flooding", "repetition", "unanswered_question"}


def all_transcripts():
    return load_transcripts()


def series_members(transcripts=None):
    transcripts = transcripts if transcripts is not None else all_transcripts()
    return {tid: t for tid, t in transcripts.items() if is_series(t)}


def by_factor(members, factor):
    return {tid: t for tid, t in members.items() if t["series"]["factor"] == factor}


def groups(members):
    """{(series id, level): {side: transcript}}"""
    grouped = {}
    for t in members.values():
        grouped.setdefault((t["series"]["id"], t["series"]["level"]), {})[t["series"]["side"]] = t
    return grouped


def trigger_message(t):
    return next(m for m in t["messages"] if m["seq"] == t["trigger_seq"])


def sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def word_count(text):
    return len(text.split())


def longest_run(t):
    best = run = 0
    previous = None
    for m in t["messages"]:
        run = run + 1 if m["author"] == previous else 1
        previous = m["author"]
        best = max(best, run)
    return best


def run_authors(t):
    """(author, seqs) of the longest run of consecutive messages by one author (the first one if tied)."""
    best, current = [], []
    for m in t["messages"]:
        if current and current[-1]["author"] == m["author"]:
            current.append(m)
        else:
            current = [m]
        if len(current) > len(best):
            best = list(current)
    return (best[0]["author"] if best else None), [m["seq"] for m in best]


def repeated_sentences(t):
    """{author: {sentence: number of that author's messages containing it verbatim}} for sentences in 2+ messages."""
    found = {}
    for author in {m["author"] for m in t["messages"]}:
        counter = Counter()
        for m in t["messages"]:
            if m["author"] == author:
                for sentence in set(sentences(m["text"])):
                    counter[sentence] += 1
        repeated = {s: n for s, n in counter.items() if n >= 2}
        if repeated:
            found[author] = repeated
    return found


def unanswered_pattern(t):
    """(asker, replier) if a message containing '?' is immediately followed by TWO consecutive messages by the other author,
    neither containing a '?' (the question is asked and the next two turns, back to back, do not address it); else None."""
    messages = t["messages"]
    for i in range(len(messages) - 2):
        asker = messages[i]["author"]
        if "?" not in messages[i]["text"]:
            continue
        r1, r2 = messages[i + 1], messages[i + 2]
        if r1["author"] != asker and r2["author"] == r1["author"] and "?" not in r1["text"] and "?" not in r2["text"]:
            return asker, r1["author"]
    return None


def unanswered_question(t):
    return unanswered_pattern(t) is not None


def swap_label(label):
    if label.endswith("A"):
        return label[:-1] + "B"
    if label.endswith("B"):
        return label[:-1] + "A"
    return label


def digit_tokens(text):
    return len([tok for tok in text.split() if re.search(r"\d", tok)])


def stance_key(author):
    return author if author.startswith("Participant") else f"Participant {author}"


def within(actual, target, tolerance=0.10):
    return abs(actual - target) <= tolerance * target


def values_under(node, part):
    from step3_testkit import values_under_key

    return values_under_key(node, part)
