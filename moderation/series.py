"""Ground truth for the mechanical transcript series (docs/step3_brief.md section 9), computed by code from the messages.

The generator (which writes golden/transcripts/*.json) and the spike command both use `compute_features`, so a transcript's
declared `computed` block can always be checked against what the messages really are.
"""
import re

FACTORS = ("message_length", "label_swap", "flooding", "repetition", "unanswered_question")
SIDES = ("left", "right")
COMPUTED_KEYS = (
    "trigger_message_chars",
    "trigger_message_words",
    "longest_consecutive_run",
    "repeated_sentence_across_messages",
    "unanswered_question_followed_by_two_replies",
)

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def sentences(text):
    """Sentences of a text, whitespace-collapsed and stripped (a split after ., ! or ? followed by whitespace)."""
    parts = _SENTENCE_END.split(text.strip())
    return [" ".join(part.split()) for part in parts if part.strip()]


def longest_run(messages):
    """The longest run of consecutive messages by one author."""
    best = run = 0
    previous = None
    for message in messages:
        run = run + 1 if message["author"] == previous else 1
        previous = message["author"]
        best = max(best, run)
    return best


def has_repeated_sentence(messages):
    """True if some sentence (three words or more) appears verbatim in two or more different messages of one author."""
    seen = {}
    for message in messages:
        for sentence in set(sentences(message["text"])):
            if len(sentence.split()) >= 3:
                seen.setdefault((message["author"], sentence), set()).add(message["seq"])
    return any(len(seqs) >= 2 for seqs in seen.values())


def has_unanswered_question(messages):
    """True if a message with a question mark by one author is followed by two consecutive messages by the other author,
    neither containing a question mark."""
    for index, message in enumerate(messages[:-2]):
        if "?" not in message["text"]:
            continue
        first, second = messages[index + 1], messages[index + 2]
        if (
            first["author"] == second["author"] != message["author"]
            and "?" not in first["text"]
            and "?" not in second["text"]
        ):
            return True
    return False


def compute_features(messages, trigger_seq):
    """The `computed` block for a transcript: `messages` are dicts with seq, author and text, in order; only messages up to
    and including the trigger count (the Master never sees later ones)."""
    visible = [m for m in messages if m["seq"] <= trigger_seq]
    trigger = next(m for m in visible if m["seq"] == trigger_seq)
    return {
        "trigger_message_chars": len(trigger["text"]),
        "trigger_message_words": len(trigger["text"].split()),
        "longest_consecutive_run": longest_run(visible),
        "repeated_sentence_across_messages": has_repeated_sentence(visible),
        "unanswered_question_followed_by_two_replies": has_unanswered_question(visible),
    }
