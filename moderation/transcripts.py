"""Transcript files (the replay and seeded-error input format): loading, validation, the series factor values and the
worst-case cost estimate.

Moved here unchanged from the retired `spike` command and `moderation.series` (cleanup 2, 2026-10-01) so that
`moderation.replay`, the replay command and the seeded-error tests keep one validator. Pure: no database, no API call.
"""
import json
import re
from pathlib import Path

from django.conf import settings
from django.core.management.base import CommandError

from forum.limits import count_message_chars
from moderation import budget, prompting
from moderation.schemas import IntervenorOutput, MasterOutput

TRANSCRIPTS_DIR = Path(settings.BASE_DIR) / "golden" / "transcripts"

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


# --- Loading and validation ---------------------------------------------------------------------------------------

def load_transcript_files(directory=None):
    """Read every *.json file in the folder: a list of (path, data). A file that is not valid JSON, is not an object, has
    no `id`, or repeats another file's id is a CommandError naming the file(s), whatever --only selects."""
    directory = Path(directory) if directory else TRANSCRIPTS_DIR
    files = []
    seen = {}
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:  # JSONDecodeError and UnicodeDecodeError are ValueErrors
            raise CommandError(f"{path.name}: cannot read this transcript file ({type(exc).__name__}: {exc})") from exc
        if not isinstance(data, dict):
            raise CommandError(f"{path.name}: a transcript file must contain a JSON object")
        if not isinstance(data.get("id"), str) or not data["id"]:
            raise CommandError(f"{path.name}: missing required key 'id' (a non-empty string)")
        if not re.fullmatch(r"[a-z0-9_]+", data["id"]):
            raise CommandError(
                f"{path.name}: transcript id {data['id']!r} is not allowed; an id may contain only a-z, 0-9 and _"
            )
        if data["id"] in seen:
            raise CommandError(f"duplicate transcript id {data['id']!r} in {seen[data['id']].name} and {path.name}")
        seen[data["id"]] = path
        files.append((path, data))
    return files


def load_transcripts(directory=None):
    """All transcripts as {id: dict}, sorted by file name (see load_transcript_files for the errors)."""
    return {data["id"]: data for _path, data in load_transcript_files(directory)}



def is_moderator(author):
    return "moderator" in str(author).lower()


def validate_transcript(path, data, known_ids=None):
    """Raise CommandError (naming the file and the key or message) unless the transcript is usable and within limits."""
    name = path.name

    def need(container, key, where=""):
        if not isinstance(container, dict) or key not in container:
            raise CommandError(f"{name}: missing required key '{where}{key}'")
        return container[key]

    topic = need(data, "topic")
    need(topic, "title", "topic.")
    need(topic, "proposition", "topic.")
    messages = need(data, "messages")
    trigger = need(data, "trigger_seq")
    if not isinstance(messages, list) or not messages:
        raise CommandError(f"{name}: 'messages' must be a non-empty list")
    seqs = []
    for position, message in enumerate(messages, start=1):
        label = f"messages[{position}]."
        seq = need(message, "seq", label)
        author = need(message, "author", label)
        text = need(message, "text", label)
        if isinstance(seq, bool) or not isinstance(seq, int):
            raise CommandError(f"{name}: message {position} has a seq that is not an integer")
        if not isinstance(author, str) or not isinstance(text, str):
            raise CommandError(f"{name}: message seq {seq} needs a string 'author' and a string 'text'")
        counted = count_message_chars(text)  # the rule real users are held to (CRLF as one, NFC, stripped, code points)
        if counted > settings.MAX_MESSAGE_CHARS:
            raise CommandError(
                f"{name}: message seq {seq} is {counted} characters, over MAX_MESSAGE_CHARS "
                f"({settings.MAX_MESSAGE_CHARS}); synthetic transcripts obey the same limit as real users"
            )
        planted = message.get("planted", [])
        if not isinstance(planted, list):
            raise CommandError(f"{name}: message seq {seq}: 'planted' must be a list")
        for item in planted:
            for key in ("dimension", "phrase", "intensity"):
                need(item, key, f"messages[{position}].planted[].")
        seqs.append(seq)
    if len(set(seqs)) != len(seqs):
        raise CommandError(f"{name}: message seq numbers are not unique")
    if seqs != sorted(seqs):
        raise CommandError(f"{name}: message seq numbers must be in ascending order")
    if isinstance(trigger, bool) or trigger not in seqs:
        raise CommandError(f"{name}: trigger_seq {trigger!r} is not the seq of any message")
    for message in messages:
        if message["seq"] == trigger and is_moderator(message["author"]):
            raise CommandError(
                f"{name}: trigger_seq {trigger} is a Moderator message; the newest message the Master sees must be "
                "a participant message (the moderator never replies to itself)"
            )
    if data.get("series") is not None:
        _validate_series(name, data, messages, trigger, known_ids)


def _validate_series(name, data, messages, trigger, known_ids):
    """Rules for a member of a mechanical series (docs/step3_brief.md section 9): a well-formed `series` object, a base
    transcript that exists, and a `computed` block equal to what the messages really are."""
    series = data["series"]
    if not isinstance(series, dict):
        raise CommandError(f"{name}: 'series' must be an object")
    for key in ("id", "factor", "level", "side", "base"):
        if not isinstance(series.get(key), str) or not series[key]:
            raise CommandError(f"{name}: missing required key 'series.{key}' (a non-empty string)")
    if series["factor"] not in FACTORS:
        raise CommandError(f"{name}: series.factor {series['factor']!r} is not one of {', '.join(FACTORS)}")
    if series["side"] not in SIDES:
        raise CommandError(f"{name}: series.side {series['side']!r} is not 'left' or 'right'")
    if known_ids is not None and series["base"] not in known_ids:
        raise CommandError(f"{name}: series.base {series['base']!r} is not a transcript in the folder")
    computed = data.get("computed")
    if not isinstance(computed, dict):
        raise CommandError(f"{name}: missing required key 'computed' (an object) for a series member")
    for key in COMPUTED_KEYS:
        if key not in computed:
            raise CommandError(f"{name}: missing required key 'computed.{key}'")
    actual = compute_features(messages, trigger)
    for key in COMPUTED_KEYS:
        if computed[key] != actual[key]:
            raise CommandError(
                f"{name}: computed.{key} is {computed[key]!r} but the messages give {actual[key]!r}; regenerate the transcript"
            )
    base = known_ids.get(series["base"]) if isinstance(known_ids, dict) else None
    if base is not None and series["factor"] in ("message_length", "label_swap"):
        _check_against_base(name, data, base, series)


def _planted_phrases(transcript):
    return sorted(
        (m["seq"], p.get("phrase")) for m in transcript["messages"] for p in (m.get("planted") or []) if isinstance(p, dict)
    )


def _check_against_base(name, data, base, series):
    """A message-length member equals its base except message 4 (messages 1 to 3 identical, same planted phrase); a
    label-swap member has identical text with every label swapped."""
    swap = {"Participant A": "Participant B", "Participant B": "Participant A"}
    mine, theirs = data["messages"], base["messages"]
    if len(mine) != len(theirs):
        raise CommandError(f"{name}: has {len(mine)} messages but its base {series['base']} has {len(theirs)}")
    if data["trigger_seq"] != base["trigger_seq"]:
        raise CommandError(f"{name}: trigger_seq differs from the base {series['base']}")
    if series["factor"] == "message_length":
        for own, ref in zip(mine[: data["trigger_seq"] - 1], theirs[: data["trigger_seq"] - 1]):
            if (own["seq"], own["author"], own["text"]) != (ref["seq"], ref["author"], ref["text"]):
                raise CommandError(f"{name}: message seq {own['seq']} must be identical to the base {series['base']}")
        if _planted_phrases(data) != _planted_phrases(base):
            raise CommandError(f"{name}: the planted phrase must be identical to the base {series['base']}'s")
    else:
        for own, ref in zip(mine, theirs):
            if own["text"] != ref["text"] or own["author"] != swap.get(ref["author"], ref["author"]):
                raise CommandError(
                    f"{name}: message seq {own['seq']} must be the base {series['base']}'s text with the label swapped"
                )


def visible_messages(transcript):
    """(message_id, label, text) for every message up to and including the trigger message; message_id is the seq."""
    trigger = transcript["trigger_seq"]
    return [(m["seq"], m["author"], m["text"]) for m in transcript["messages"] if m["seq"] <= trigger]


# --- Input rendering and cost estimates --------------------------------------------------------------------------------

def master_request(transcript, master_prompt):
    user = prompting.render_master_input(
        visible_messages(transcript),
        topic_title=transcript["topic"]["title"],
        proposition=transcript["topic"]["proposition"],
    )
    return {"system": master_prompt.text, "messages": [{"role": "user", "content": user}], "user_text": user}


def intervenor_request(transcript, intervenor_prompt, valid_issues, discussion_map):
    user = prompting.render_intervenor_input(
        visible_messages(transcript),
        valid_issues,
        topic_title=transcript["topic"]["title"],
        proposition=transcript["topic"]["proposition"],
        discussion_map=discussion_map,
    )
    return {"system": intervenor_prompt.text, "messages": [{"role": "user", "content": user}], "user_text": user}


def estimate_worst_case(transcript, model, master_prompt, intervenor_prompt):
    """Reservation-basis worst-case cost of the two calls for one transcript (what the guard would reserve)."""
    master = master_request(transcript, master_prompt)
    master_tokens = budget.estimate_input_tokens(
        system=master["system"], messages=master["messages"], schema=MasterOutput
    )
    master_cost = budget.reservation_usd(model, estimated_input_tokens=master_tokens, max_tokens=settings.MASTER_MAX_TOKENS)
    # The Intervenor also receives the Master's issues; assume they fill the whole Master output allowance.
    intervenor = intervenor_request(transcript, intervenor_prompt, [], None)
    extra_chars = int(settings.MASTER_MAX_TOKENS) * 4
    intervenor_tokens = budget.estimate_input_tokens(
        system=intervenor["system"],
        messages=intervenor["messages"] + [{"role": "user", "content": "x" * extra_chars}],
        schema=IntervenorOutput,
    )
    intervenor_cost = budget.reservation_usd(
        model, estimated_input_tokens=intervenor_tokens, max_tokens=settings.INTERVENOR_MAX_TOKENS
    )
    return master_cost, intervenor_cost


