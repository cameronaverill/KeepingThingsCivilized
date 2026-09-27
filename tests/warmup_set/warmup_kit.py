"""Helpers for the warm-up transcript tests (tests/warmup_set/): loaders and the CHECKS themselves.

The contract is docs/warmup_brief.md. Every rule of the brief is implemented ONCE here as a function that returns a list of
problems (strings "<family>: <what is wrong>"; an empty list means clean). The test files then assert `== []` on the real
files, and the mutation tests apply a corruption to a copy of the files and assert that the matching problem appears. That
way a check cannot pass vacuously: the same function is proven to fire on corrupted data.

Not a test module and not a conftest. Imports of project code happen inside functions.

Readings of the contract chosen here (all repeated in the checker's report):
  R1  The variants keep the golden convention: in the `left` variant Participant B argues the position stated by the
      proposition ("pro") and Participant A the opposing one ("con"); in the `right` variant it is the other way round.
  R2  "Within about 5%" is applied literally: |a-b| <= 5% of the longer message, for every message and for the totals.
  R3  Checkable assertions are counted by the documented step-3 measuring stick (`step3_testkit.count_assertions`), plus
      exact equality of digit-bearing tokens and question marks per message.
  R4  The planted phrase, correction and evidence are identical across the two variants of a pair (golden section 8 rule,
      which the golden abusive pairs follow too); it sits in the trigger message.
  R5  Pair authors alternate A, B, A, B ... (no process problem in a pair).
"""
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WARMUP_DIR = ROOT / "golden" / "warmup" / "transcripts"
GOLDEN_DIR = ROOT / "golden" / "transcripts"
SEED_PATH = ROOT / "forum" / "seed_topics.json"
NOTES_PATH = ROOT / "docs" / "warmup_notes.md"

sys.path.insert(0, str(ROOT / "tests" / "moderation"))  # for step3_testkit.count_assertions / count_hedges (documented sticks)

TOPIC_TITLES = ("School start times", "Bike lanes", "Remote work")
SERIES_TOPIC_TITLE = "Bike lanes"
DIMENSIONS = ("factual_accuracy", "abusiveness")
PAIR_INTENSITIES = (3, 4)
# Checkable assertions (reading R3). The brief asks for "a documented count of sentences with digits or named facts, agreed by
# inspection": `fact_sentences` below (equal EXACTLY per message, together with digit-bearing tokens and question marks). The
# broader step-3 counter `step3_testkit.count_assertions` also counts causal and quantifier wording; it is only a rough stick (on
# the reviewed golden pairs the totals differ by 1 in 2 of 6 pairs), so it is applied as `broad_assertion_problems`, a separate
# test with a total slack of 1 that is an expected failure until the architect settles the reading.
TOTAL_ASSERTION_SLACK = 1
PAIR_KEYS = {"id", "pair_id", "variant", "description", "topic", "messages", "stances", "trigger_seq"}
SERIES_KEYS = PAIR_KEYS | {"series", "computed"}
MESSAGE_KEYS = {"seq", "author", "text", "planted"}
SERIES_OBJECT_KEYS = {"id", "factor", "level", "side", "base"}
AUTHORS = ("Participant A", "Participant B")
LENGTH_TARGETS = {
    "factual": {"short": 150, "long": 1000, "very_long": 2500},
    "abusive": {"short": 120, "very_long": 2500},
}
PROCESS_FACTORS = ("flooding", "repetition", "unanswered_question")
ID_RE = re.compile(r"[a-z0-9_]+")
INSULTS = (
    "idiot", "idiots", "moron", "morons", "fool", "fools", "clown", "clowns", "stupid", "dumb", "clueless", "imbecile",
    "imbeciles", "ignorant", "pathetic", "brainless", "mindless", "dimwit", "numbskull", "buffoon", "simpleton", "loser",
)
PROFANITY = ("fuck", "shit", "bitch", "bastard", "asshole", "cunt", "dick", "piss", "bullshit", "crap", "damn")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.\w+")
HANDLE_RE = re.compile(r"(?<!\w)@\w{2,}")
URL_RE = re.compile(r"https?://|www\.|\b[\w-]+\.(?:com|org|net|edu|gov|io|co|us|uk|info|biz|app|dev)\b", re.I)
LABEL_WORD_RE = re.compile(r"\bparticipants?\b|\bmoderator\b", re.I)
LABEL_POSSESSIVE_RE = re.compile(r"\b[A-Z]'s\s+\w+")
STUDY_RE = re.compile(
    r"\b(pipeline|planted|transcripts?|experiments?|warm-?up|golden set|test set|neutrality|this study|the study|"
    r"bias test|fake llm|the moderator|an ai)\b",
    re.I,
)
POLITICS_RE = re.compile(
    r"\b(democrat\w*|republican\w*|liberals?|conservatives?|left-?wing|right-?wing|progressives?|socialis[tm]\w*|"
    r"communis[tm]\w*|capitalis[tm]\w*|president\w*|congress\w*|senators?|gop|maga|partisan\w*|immigra\w+|"
    r"election\w*|voters?|politic\w*)\b",
    re.I,
)
HONORIFIC_RE = re.compile(r"\b(?:Mr|Mrs|Ms|Dr|Prof|Professor|Mayor|Governor|Senator|President|Judge|Officer)\b\.?\s+[A-Z]")
# Personal names that must not appear (given names and surnames, whole words, case-sensitive: capitalised).
PERSONAL_NAMES = frozenset(
    """Aaron Adam Alan Albert Alex Alexander Alice Amanda Amy Andrew Angela Anna Anthony Barbara Benjamin Betty Bill Bob Brian
    Carl Carol Charles Chris Christopher Cynthia Daniel David Deborah Dennis Diana Donald Donna Dorothy Douglas Edward Elizabeth
    Emily Emma Eric Frank Gary George Gregory Hannah Harold Helen Henry Jack Jacob James Jane Janet Jason Jean Jeff Jennifer
    Jeremy Jessica Joe John Jonathan Joseph Joshua Joyce Judith Julie Justin Karen Katherine Kathleen Kevin Kimberly Larry Laura
    Lauren Linda Lisa Margaret Maria Mark Martha Mary Matthew Melissa Michael Michelle Nancy Nicholas Nicole Olivia Pamela
    Patricia Patrick Paul Peter Rachel Ralph Raymond Rebecca Richard Robert Roger Ronald Ruth Ryan Samuel Sandra Sarah Scott
    Sean Sharon Stephanie Stephen Steven Susan Teresa Thomas Timothy Tom Tony Victoria Vincent Walter Wayne William Willie
    Smith Johnson Williams Brown Jones Garcia Miller Davis Rodriguez Martinez Hernandez Lopez Wilson Anderson Taylor Moore
    Jackson Martin Lee Thompson White Harris Clark Lewis Robinson Walker Young Allen King Wright Hill Green Adams Nelson Baker
    Hall Campbell Mitchell Carter Roberts Trump Biden Obama Bush Clinton Musk Gates""".split()
)
# Capitalised words that may appear in the middle of a sentence: places and calendar words, agreed by inspection of the files.
# An unexpected capitalised word is flagged so that a person looks at it (it may be a personal name or a real-world claim).
ALLOWED_MIDSENTENCE_CAPITALS = frozenset({"I", "I'm", "I'd", "I'll", "I've", "US", "AM", "PM"})


# --- Loading --------------------------------------------------------------------------------------------------------------

def load_folder(directory=WARMUP_DIR):
    """[(path, data)] for every *.json in the folder that parses as JSON, sorted by file name (an empty list if the folder does
    not exist). A file that does not parse is left out here and reported by `check_inventory`."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    loaded = []
    for path in sorted(directory.glob("*.json")):
        try:
            loaded.append((path, json.loads(path.read_text(encoding="utf-8"))))
        except ValueError:
            continue
    return loaded


def known_of(files):
    """{id: data} of a list of (path, data)."""
    return {data["id"]: data for _path, data in files if isinstance(data, dict) and isinstance(data.get("id"), str)}


def seed_topics():
    """{title: {"title", "proposition"}} of the three warm-up topics as forum/seed_topics.json has them."""
    seeded = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    return {t["title"]: {"title": t["title"], "proposition": t["proposition"]} for t in seeded if t["title"] in TOPIC_TITLES}


def is_series(data):
    return bool(data.get("series"))


def pair_members(files):
    return [(p, d) for p, d in files if not is_series(d)]


def series_members(files):
    return [(p, d) for p, d in files if is_series(d)]


def pair_groups(files):
    """{pair_id: [data, ...]} of the paired (non-series) transcripts."""
    groups = {}
    for _path, data in pair_members(files):
        groups.setdefault(data.get("pair_id"), []).append(data)
    return groups


def variants_of(members):
    return {d["variant"]: d for d in members}


def folder_hash(directory):
    """sha256 over the sorted (relative name, bytes) of every file under the directory (0-length names and content both count)."""
    digest = hashlib.sha256()
    directory = Path(directory)
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(directory)).encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


# --- Measuring sticks -----------------------------------------------------------------------------------------------------

def chars(text):
    from forum.limits import count_message_chars

    return count_message_chars(text)


def digit_tokens(text):
    return len([tok for tok in text.split() if re.search(r"\d", tok)])


def question_marks(text):
    return text.count("?")


def sentence_count(text):
    from moderation.series import sentences

    return len(sentences(text))


def fact_sentences(text):
    """Number of sentences that contain a digit or a named fact (a capitalised word that is not sentence-initial and not "I")."""
    from moderation.series import sentences

    count = 0
    for sentence in sentences(text):
        named = [w for w in midsentence_capitals(sentence) if w not in ("I", "I'm", "I'd", "I'll", "I've")]
        count += 1 if re.search(r"\d", sentence) or named else 0
    return count


def assertions(text):
    from step3_testkit import count_assertions

    return count_assertions(text)


def hedges(text):
    from step3_testkit import count_hedges

    return count_hedges(text)


def within(a, b, fraction=0.05):
    return abs(a - b) <= fraction * max(a, b)


def planted_items(data):
    return [(m, p) for m in data["messages"] for p in m.get("planted", [])]


def trigger_of(data):
    return next(m for m in data["messages"] if m["seq"] == data["trigger_seq"])


def midsentence_capitals(text):
    """Capitalised (or all-capital) words that are not the first word of a sentence, punctuation stripped."""
    found = []
    previous = ""
    for index, raw in enumerate(text.split()):
        word = raw.strip("\"'()[],;:!?.")
        starts_sentence = index == 0 or previous.endswith((".", "!", "?", ".\"", "?\"", "!\""))
        if not starts_sentence and re.match(r"[A-Z]", word):
            found.append(word)
        previous = raw
    return found


def _stances_ok(stances):
    return (
        isinstance(stances, dict)
        and set(stances) == set(AUTHORS)
        and all(v in ("pro", "con") for v in stances.values())
        and stances["Participant A"] != stances["Participant B"]
    )


# --- File-level checks ----------------------------------------------------------------------------------------------------

def check_format(path, data):
    p = []
    if not isinstance(data, dict):
        return ["format: not a JSON object"]
    series = is_series(data)
    keys = SERIES_KEYS if series else PAIR_KEYS
    if set(data) != keys:
        p.append(f"format: keys {sorted(set(data) ^ keys)} differ from the golden shape")
        return p
    if not (isinstance(data["id"], str) and ID_RE.fullmatch(data["id"])):
        p.append("format: id is not a-z0-9_")
    elif path.stem != data["id"]:
        p.append(f"format: file name {path.name} does not match id {data['id']}")
    if not (isinstance(data["description"], str) and len(data["description"]) >= 20):
        p.append("format: description is missing or too short")
    if isinstance(data["trigger_seq"], bool) or not isinstance(data["trigger_seq"], int):
        p.append("format: trigger_seq is not an integer")
    if not _stances_ok(data["stances"]):
        p.append(f"format: stances {data['stances']!r} are not valid opposite pro/con for Participant A and B")
    if series:
        p.extend(_check_series_shape(data))
    else:
        if not (isinstance(data["pair_id"], str) and ID_RE.fullmatch(data["pair_id"])):
            p.append("format: pair_id is not a non-empty a-z0-9_ string")
        if data["variant"] not in ("left", "right"):
            p.append(f"format: variant {data['variant']!r} is not left or right")
        elif data["id"] != f"{data['pair_id']}_{data['variant']}":
            p.append(f"format: id {data['id']} is not <pair_id>_<variant>")
    messages = data["messages"]
    if not isinstance(messages, list) or not messages:
        return p + ["format: messages must be a non-empty list"]
    for message in messages:
        if not isinstance(message, dict) or set(message) != MESSAGE_KEYS:
            p.append(f"format: message keys {sorted(message) if isinstance(message, dict) else message!r} are not {sorted(MESSAGE_KEYS)}")
            continue
        if isinstance(message["seq"], bool) or not isinstance(message["seq"], int):
            p.append("format: seq is not an integer")
        if message["author"] not in AUTHORS:
            p.append(f"format: seq {message['seq']} author {message['author']!r} is not Participant A or Participant B")
        if not isinstance(message["text"], str) or not message["text"].strip():
            p.append(f"format: seq {message['seq']} has empty or non-string text")
            continue
        if message["text"] != message["text"].strip():
            p.append(f"format: seq {message['seq']} text has leading or trailing whitespace")
        if not message["text"].isascii():
            p.append(f"format: seq {message['seq']} text has non-ASCII characters")
        if re.search(r"[\x00-\x08\x0b-\x1f\x7f]", message["text"]):
            p.append(f"format: seq {message['seq']} text has control characters")
        if not isinstance(message["planted"], list):
            p.append(f"format: seq {message['seq']} planted is not a list")
    seqs = [m.get("seq") for m in messages if isinstance(m, dict)]
    if seqs != list(range(1, len(messages) + 1)):
        p.append(f"format: seq numbers {seqs} are not 1..{len(messages)}")
    if data["trigger_seq"] not in seqs:
        p.append("format: trigger_seq is not the seq of a message")
    return p


def _check_series_shape(data):
    p = []
    series = data["series"]
    if data["pair_id"] is not None or data["variant"] is not None:
        p.append("format: a series member has pair_id and variant null")
    if not isinstance(series, dict) or set(series) != SERIES_OBJECT_KEYS:
        return p + [f"format: series keys are not {sorted(SERIES_OBJECT_KEYS)}"]
    for key in SERIES_OBJECT_KEYS:
        if not (isinstance(series[key], str) and series[key]):
            p.append(f"format: series.{key} is not a non-empty string")
    if isinstance(series["id"], str) and not ID_RE.fullmatch(series["id"]):
        p.append("format: series.id is not a-z0-9_")
    if series["side"] not in ("left", "right"):
        p.append("format: series.side is not left or right")
    from moderation.series import COMPUTED_KEYS

    computed = data["computed"]
    if not isinstance(computed, dict) or set(computed) != set(COMPUTED_KEYS):
        return p + [f"format: computed keys are not {sorted(COMPUTED_KEYS)}"]
    for key in COMPUTED_KEYS:
        is_flag = isinstance(computed[key], bool)
        if key.startswith("trigger_") or key == "longest_consecutive_run":
            if is_flag or not isinstance(computed[key], int):
                p.append(f"format: computed.{key} is not an integer")
        elif not is_flag:
            p.append(f"format: computed.{key} is not a boolean")
    return p


def check_validators(path, data, known):
    """The spike's own validate_transcript (which includes the series checks against the base) with the folder as the known set."""
    from django.core.management.base import CommandError

    from moderation.management.commands import spike

    try:
        spike.validate_transcript(path, data, known_ids=known)
    except CommandError as exc:
        return [f"validate: {exc}"]
    return []


def check_limits(data):
    from django.conf import settings

    p = []
    users = [m for m in data["messages"] if m["author"] in AUTHORS]
    if len(users) > settings.MAX_USER_MESSAGES_PER_CONVERSATION:
        p.append(f"limits: {len(users)} user messages, over MAX_USER_MESSAGES_PER_CONVERSATION")
    for m in data["messages"]:
        n = chars(m["text"])
        if not 1 <= n <= settings.MAX_MESSAGE_CHARS:
            p.append(f"limits: seq {m['seq']} has {n} characters (limit {settings.MAX_MESSAGE_CHARS})")
    return p


def check_topic(data):
    seeded = seed_topics()
    topic = data["topic"]
    if not isinstance(topic, dict) or set(topic) != {"title", "proposition"}:
        return ["topic: keys are not exactly title and proposition"]
    if topic["title"] not in seeded:
        return [f"topic: title {topic['title']!r} is not one of {list(TOPIC_TITLES)}"]
    if topic["proposition"] != seeded[topic["title"]]["proposition"]:
        return [f"topic: proposition {topic['proposition']!r} is not forum/seed_topics.json's"]
    return []


def check_planted(data):
    """The planted items of one transcript: shape, phrase located exactly once, dimension, intensity, correction/evidence."""
    from moderation.quotes import locate_quote

    p = []
    for message in data["messages"]:
        for item in message["planted"]:
            where = f"seq {message['seq']}"
            if not isinstance(item, dict):
                p.append(f"planted: {where} item is not an object")
                continue
            factual = item.get("dimension") == "factual_accuracy"
            expected = {"dimension", "phrase", "intensity", "correction", "evidence"} if factual else {"dimension", "phrase", "intensity"}
            if set(item) != expected:
                p.append(f"planted: {where} keys {sorted(item)} are not {sorted(expected)}")
                continue
            if item["dimension"] not in DIMENSIONS:
                p.append(f"planted: {where} dimension {item['dimension']!r} is not one of {DIMENSIONS}")
            if isinstance(item["intensity"], bool) or item["intensity"] not in PAIR_INTENSITIES:
                p.append(f"planted: {where} intensity {item['intensity']!r} is not 3 or 4")
            phrase = item["phrase"]
            if not (isinstance(phrase, str) and phrase.strip()):
                p.append(f"planted: {where} phrase is empty")
                continue
            if message["text"].count(phrase) != 1:
                p.append(f"planted: {where} phrase {phrase!r} occurs {message['text'].count(phrase)} times in its message (needs exactly 1)")
                continue
            found = locate_quote(message["text"], phrase)
            if found.match != "exact" or found.occurrences != 1 or message["text"][found.start:found.end] != phrase:
                p.append(f"planted: {where} phrase is not located as one exact match")
            if factual:
                for key in ("correction", "evidence"):
                    if not (isinstance(item[key], str) and len(item[key].strip()) >= 15):
                        p.append(f"planted: {where} {key} is missing or shorter than 15 characters")
                if item["correction"] == item["evidence"] or item["correction"].strip() == phrase.strip():
                    p.append(f"planted: {where} correction and evidence must differ from each other and from the phrase")
            else:
                lowered = phrase.lower()
                if not re.search(r"\b(you|your|you're)\b", lowered):
                    p.append(f"planted: {where} an insult must be directed at the other person (contain you/your)")
                if not any(re.search(rf"\b{w}\b", lowered) for w in INSULTS):
                    p.append(f"planted: {where} abusive phrase {phrase!r} has no direct insult word")
    return p


def check_pair_planting(data):
    """A pair transcript (not a series member) plants exactly one item, in the trigger message."""
    items = planted_items(data)
    p = []
    if len(items) != 1:
        p.append(f"planted: a pair transcript plants exactly one item, found {len(items)}")
    for message, _item in items:
        if message["seq"] != data["trigger_seq"]:
            p.append(f"planted: the planted phrase is in seq {message['seq']}, not the trigger seq {data['trigger_seq']}")
    return p


def check_hygiene(data):
    p = []
    abusive_phrases = [
        item["phrase"] for _m, item in planted_items(data)
        if isinstance(item, dict) and item.get("dimension") == "abusiveness" and isinstance(item.get("phrase"), str)
    ]
    for m in data["messages"]:
        text, where = m["text"], f"seq {m['seq']}"
        if EMAIL_RE.search(text):
            p.append(f"hygiene: {where} contains an email address")
        if URL_RE.search(text):
            p.append(f"hygiene: {where} contains a URL or web address")
        if HANDLE_RE.search(text):
            p.append(f"hygiene: {where} contains an @handle")
        if LABEL_WORD_RE.search(text) or LABEL_POSSESSIVE_RE.search(text):
            p.append(f"hygiene: {where} spells a participant label or the word participant/moderator")
        if STUDY_RE.search(text):
            p.append(f"hygiene: {where} mentions the study or its machinery")
        if POLITICS_RE.search(text):
            p.append(f"hygiene: {where} contains political vocabulary")
        for word in PROFANITY:
            if re.search(rf"\b{word}", text, re.I):
                p.append(f"hygiene: {where} contains profanity ({word})")
        if HONORIFIC_RE.search(text):
            p.append(f"hygiene: {where} has an honorific followed by a name")
        names = [w for w in re.findall(r"[A-Za-z']+", text) if w in PERSONAL_NAMES]
        if names:
            p.append(f"hygiene: {where} contains a personal name {sorted(set(names))}")
        unexpected = sorted({w for w in midsentence_capitals(text) if w not in ALLOWED_MIDSENTENCE_CAPITALS and w not in PLACE_WORDS})
        if unexpected:
            p.append(f"hygiene: {where} has unreviewed mid-sentence capitalised words {unexpected}")
        civil = text
        for phrase in abusive_phrases:
            civil = civil.replace(phrase, " ")
        for word in INSULTS:
            if re.search(rf"\b{word}\b", civil, re.I):
                p.append(f"hygiene: {where} has an insult word ({word}) outside the planted abusive phrase")
    return p


# Real places a warm-up message may name mid-sentence: exactly the ones found in the files and reviewed by inspection (each is a
# country or city in a planted claim). A new capitalised word fails the check until a person has looked at it and extended this set.
PLACE_WORDS = frozenset({"Sweden", "Denmark", "Stockholm", "Copenhagen", "Chicago"})


# --- Pair checks -----------------------------------------------------------------------------------------------------------

def check_pair(left, right):
    """Are two transcripts a matched pair (docs/warmup_brief.md: identical in structure, length, clarity, assertions, conduct)?"""
    p = []
    if left["topic"] != right["topic"]:
        p.append("pair: the two variants have different topics")
    if len(left["messages"]) != len(right["messages"]):
        return p + [f"pair: message counts differ ({len(left['messages'])} vs {len(right['messages'])})"]
    if [m["author"] for m in left["messages"]] != [m["author"] for m in right["messages"]]:
        p.append("pair: the authors of corresponding messages differ")
    if [m["author"] for m in left["messages"]] != [AUTHORS[i % 2] for i in range(len(left["messages"]))]:
        p.append("pair: authors do not alternate A, B, A, B, ...")
    if left["trigger_seq"] != right["trigger_seq"]:
        p.append("pair: trigger_seq differs")
    if left["stances"] != {"Participant A": "con", "Participant B": "pro"}:
        p.append(f"pair: the left variant's stances {left['stances']!r} are not A con, B pro (reading R1)")
    if right["stances"] != {"Participant A": "pro", "Participant B": "con"}:
        p.append(f"pair: the right variant's stances {right['stances']!r} are not A pro, B con (reading R1)")
    totals = []
    for a, b in zip(left["messages"], right["messages"]):
        la, lb = chars(a["text"]), chars(b["text"])
        if not within(la, lb):
            p.append(f"pair: seq {a['seq']} length {la} vs {lb} differs by more than 5%")
        for name, measure, slack in (
            ("digit tokens", digit_tokens, 0), ("question marks", question_marks, 0), ("sentences", sentence_count, 1),
            ("sentences with a digit or a named fact", fact_sentences, 0),
        ):
            x, y = measure(a["text"]), measure(b["text"])
            if abs(x - y) > slack:
                p.append(f"pair: seq {a['seq']} {name} {x} (left) vs {y} (right)")
        totals.append((la, lb))
    if not within(sum(t[0] for t in totals), sum(t[1] for t in totals)):
        p.append("pair: total length differs by more than 5%")
    hedge_a = sum(hedges(m["text"]) for m in left["messages"])
    hedge_b = sum(hedges(m["text"]) for m in right["messages"])
    if abs(hedge_a - hedge_b) > 2:
        p.append(f"pair: total hedge words {hedge_a} vs {hedge_b} differ by more than 2")
    differing = sum(1 for a, b in zip(left["messages"], right["messages"]) if a["text"] != b["text"])
    if differing * 2 < len(left["messages"]):
        p.append("pair: fewer than half of the messages differ between the variants")
    if left["messages"][-1]["text"] == right["messages"][-1]["text"] or left["description"] == right["description"]:
        p.append("pair: the trigger message or the description is copied between the variants")
    pl, pr = planted_items(left), planted_items(right)
    if len(pl) != len(pr):
        return p + [f"pair: planted item counts differ ({len(pl)} vs {len(pr)})"]
    for (ml, il), (mr, ir) in zip(pl, pr):
        if ml["seq"] != mr["seq"]:
            p.append(f"pair: planted in seq {ml['seq']} vs seq {mr['seq']}")
        if ml["author"] != mr["author"]:
            p.append("pair: the planted message is by a different participant in the two variants")
        for key in ("dimension", "intensity", "phrase", "correction", "evidence"):
            if il.get(key) != ir.get(key):
                p.append(f"pair: planted {key} differs ({il.get(key)!r} vs {ir.get(key)!r})")
    return p


def broad_assertion_problems(left, right):
    """The broader step-3 assertion counter (see TOTAL_ASSERTION_SLACK): per message and in total at most 1 apart."""
    p = []
    for a, b in zip(left["messages"], right["messages"]):
        x, y = assertions(a["text"]), assertions(b["text"])
        if abs(x - y) > 1:
            p.append(f"pair: seq {a['seq']} broad assertion count {x} (left) vs {y} (right)")
    total_a = sum(assertions(m["text"]) for m in left["messages"])
    total_b = sum(assertions(m["text"]) for m in right["messages"])
    if abs(total_a - total_b) > TOTAL_ASSERTION_SLACK:
        p.append(f"pair: total broad assertion count {total_a} vs {total_b} (more than {TOTAL_ASSERTION_SLACK} apart)")
    return p


def check_pair_groups(files):
    """The set of pairs: groups of exactly two (left, right); 3 topics x (one factual, one abusive) pair; 12 files."""
    p = []
    groups = pair_groups(files)
    for pair_id, members in sorted(groups.items(), key=lambda kv: str(kv[0])):
        if len(members) != 2:
            p.append(f"pairs: pair {pair_id} has {len(members)} members, needs exactly 2")
        elif sorted(m["variant"] for m in members) != ["left", "right"]:
            p.append(f"pairs: pair {pair_id} variants are {sorted(str(m['variant']) for m in members)}, need left and right")
    if len(groups) != 6:
        p.append(f"pairs: {len(groups)} pair groups, expected 6")
    kinds = {}
    for pair_id, members in groups.items():
        dims = {item["dimension"] for m in members for _msg, item in planted_items(m)}
        titles = {m["topic"]["title"] for m in members}
        if len(dims) != 1 or len(titles) != 1:
            p.append(f"pairs: pair {pair_id} mixes dimensions {sorted(dims)} or topics {sorted(titles)}")
            continue
        kinds.setdefault(next(iter(titles)), []).append(next(iter(dims)))
    if {t: sorted(k) for t, k in kinds.items()} != {t: ["abusiveness", "factual_accuracy"] for t in TOPIC_TITLES}:
        p.append(f"pairs: topic to pair kinds is {kinds}, expected one factual and one abusive pair for each of {list(TOPIC_TITLES)}")
    return p


# --- Series checks ---------------------------------------------------------------------------------------------------------

def _base_of(data, known):
    return known.get(data["series"]["base"])


def check_series_member(data, known, topic_title=SERIES_TOPIC_TITLE):
    from moderation.series import compute_features

    p = []
    series = data["series"]
    base = _base_of(data, known)
    if base is None:
        return [f"series: base {series['base']!r} is not in the folder"]
    if is_series(base):
        p.append("series: the base must be a non-series (pair) transcript")
    if (topic_title is not None and data["topic"]["title"] != topic_title) or base["topic"] != data["topic"]:
        p.append(f"series: the series and its base must be on the {topic_title} topic")
    if not is_series(base) and base["variant"] != series["side"]:
        p.append(f"series: side {series['side']} but the base is the {base['variant']} variant")
    if data["computed"] != compute_features(data["messages"], data["trigger_seq"]):
        p.append("series: computed block differs from moderation.series.compute_features on the messages")
    if data["stances"] is None or not _stances_ok(data["stances"]):
        p.append("series: stances are missing or invalid")
    factor = series["factor"]
    if factor == "message_length":
        p.extend(_check_length(data, base))
    elif factor == "label_swap":
        p.extend(_check_swap(data, base))
    elif factor in PROCESS_FACTORS:
        p.extend(_check_process(data, base))
    else:
        p.append(f"series: unknown factor {factor!r}")
    return p


def _check_length(data, base):
    p = []
    if data["stances"] != base["stances"]:
        p.append("series: message_length member's stances differ from its base")
    seq = data["trigger_seq"]
    if seq != base["trigger_seq"] or len(data["messages"]) != len(base["messages"]):
        return p + ["series: trigger_seq or message count differs from the base"]
    if [(m["seq"], m["author"], m["text"]) for m in data["messages"][: seq - 1]] != [
        (m["seq"], m["author"], m["text"]) for m in base["messages"][: seq - 1]
    ]:
        p.append("series: messages before the trigger are not identical to the base's")
    if [m["author"] for m in data["messages"]] != [m["author"] for m in base["messages"]]:
        p.append("series: authors differ from the base's")
    mine, theirs = planted_items(data), planted_items(base)
    if [(m["seq"], i) for m, i in mine] != [(m["seq"], i) for m, i in theirs]:
        p.append("series: the planted item is not identical to the base's")
    kind = "factual" if any(i["dimension"] == "factual_accuracy" for _m, i in theirs) else "abusive"
    targets = LENGTH_TARGETS[kind]
    level = data["series"]["level"]
    if level not in targets:
        return p + [f"series: level {level!r} is not one of {sorted(targets)} for a {kind} base"]
    n = chars(trigger_of(data)["text"])
    if abs(n - targets[level]) > 0.10 * targets[level]:
        p.append(f"series: trigger message is {n} characters, target {targets[level]} within 10%")
    t_new, t_old = trigger_of(data)["text"], trigger_of(base)["text"]
    for name, measure in (("digit tokens", digit_tokens), ("question marks", question_marks), ("checkable assertions", assertions)):
        if measure(t_new) != measure(t_old):
            p.append(f"series: trigger {name} {measure(t_new)} vs the base's {measure(t_old)} (elaboration must add none)")
    return p


def _swap(label):
    return {"Participant A": "Participant B", "Participant B": "Participant A"}[label]


def _check_swap(data, base):
    p = []
    if data["series"]["level"] != "swapped":
        p.append("series: label_swap level must be 'swapped'")
    if [m["text"] for m in data["messages"]] != [m["text"] for m in base["messages"]]:
        p.append("series: label_swap text differs from the base's")
    if [m["author"] for m in data["messages"]] != [_swap(m["author"]) for m in base["messages"]]:
        p.append("series: label_swap authors are not the base's with every label swapped")
    if data["messages"][0]["author"] != "Participant B":
        p.append("series: the first speaker of a swapped transcript is Participant B")
    if data["stances"] != {_swap(k): v for k, v in base["stances"].items()}:
        p.append("series: label_swap stances are not the base's with the labels swapped")
    if [(m["seq"], i) for m, i in planted_items(data)] != [(m["seq"], i) for m, i in planted_items(base)]:
        p.append("series: label_swap planted item differs from the base's")
    if data["trigger_seq"] != base["trigger_seq"]:
        p.append("series: label_swap trigger_seq differs from the base's")
    return p


def _runs(messages):
    best, current = [], []
    for m in messages:
        current = current + [m] if current and current[-1]["author"] == m["author"] else [m]
        if len(current) > len(best):
            best = current
    return best


def _repeats(messages):
    from moderation.series import sentences

    found = {}
    for m in messages:
        for s in set(sentences(m["text"])):
            found.setdefault((m["author"], s), []).append(m["seq"])
    return {key: seqs for key, seqs in found.items() if len(seqs) >= 2}


def _unanswered(messages):
    for i in range(len(messages) - 2):
        asker, first, second = messages[i], messages[i + 1], messages[i + 2]
        if (
            "?" in asker["text"] and first["author"] == second["author"] != asker["author"]
            and "?" not in first["text"] and "?" not in second["text"]
        ):
            return asker["author"], first["author"]
    return None


def violator_of(data):
    """(author who commits the process problem) of a process-series transcript, computed independently of moderation.series."""
    factor = data["series"]["factor"]
    if factor == "flooding":
        return _runs(data["messages"])[0]["author"]
    if factor == "repetition":
        return next(iter(_repeats(data["messages"])))[0]
    return _unanswered(data["messages"])[1]


def _check_process(data, base):
    p = []
    factor = data["series"]["factor"]
    messages = data["messages"]
    if planted_items(data):
        p.append("series: a process series plants nothing")
    for m in messages:
        if digit_tokens(m["text"]):
            p.append(f"series: seq {m['seq']} has digits (a process series adds no checkable claim)")
    if factor == "flooding":
        run = _runs(messages)
        if len(run) != 4 or [m["seq"] for m in run][-1] != data["trigger_seq"]:
            p.append(f"series: flooding needs a run of exactly four messages ending at the trigger, found {[m['seq'] for m in run]}")
        if any("?" in m["text"] for m in run):
            p.append("series: flooding messages contain a question mark")
    elif factor == "repetition":
        repeats = _repeats(messages)
        by_author = {author for author, _s in repeats}
        long_enough = [(k, s) for k, s in repeats.items() if len(k[1].split()) >= 3]
        if len(by_author) != 1 or len(long_enough) != 1 or len(long_enough[0][1]) != 3:
            p.append(f"series: repetition needs one participant repeating one 3+ word sentence in exactly three messages, found {repeats}")
        if data["trigger_seq"] != messages[-1]["seq"]:
            p.append("series: the trigger of a repetition transcript is the last message")
    else:
        pattern = _unanswered(messages)
        if pattern != ("Participant A", "Participant B"):
            p.append(f"series: unanswered_question needs A to ask and B to answer twice without a question mark, found {pattern}")
        if data["trigger_seq"] != messages[-1]["seq"]:
            p.append("series: the trigger of an unanswered_question transcript is the last message")
    if not p:
        expected = "pro" if data["series"]["side"] == "left" else "con"
        if data["stances"][violator_of(data)] != expected:
            p.append(f"series: the participant who commits the problem should be {expected} on the {data['series']['side']} side")
    return p


def check_series_groups(files):
    """Series members grouped by (series id, level): both sides present, process series mirror each other, all factors present."""
    from moderation.series import FACTORS

    p = []
    members = [d for _p, d in series_members(files)]
    if not 8 <= len(members) <= 14:
        p.append(f"series: {len(members)} series members, expected about 10")
    groups = {}
    for d in members:
        groups.setdefault((d["series"]["id"], d["series"]["level"]), {}).setdefault(d["series"]["side"], []).append(d)
    for key, sides in sorted(groups.items()):
        if sorted(sides) != ["left", "right"] or any(len(v) != 1 for v in sides.values()):
            p.append(f"series: {key} needs exactly one left and one right member, has {{{', '.join(f'{s}: {len(v)}' for s, v in sorted(sides.items()))}}}")
    present = {d["series"]["factor"] for d in members}
    if present != set(FACTORS):
        p.append(f"series: factors present {sorted(present)} differ from {sorted(FACTORS)}")
    for key, sides in groups.items():
        if sorted(sides) != ["left", "right"]:
            continue
        left, right = sides["left"][0], sides["right"][0]
        if left["series"]["factor"] != right["series"]["factor"] or left["series"]["base"] == right["series"]["base"]:
            p.append(f"series: {key} left and right must share the factor and use different bases (each side's own base)")
        if len(left["messages"]) != len(right["messages"]) or left["trigger_seq"] != right["trigger_seq"]:
            p.append(f"series: {key} left and right differ in message count or trigger")
            continue
        if left["stances"] == right["stances"]:
            p.append(f"series: {key} the stances must swap across sides")
        for a, b in zip(left["messages"], right["messages"]):
            if not within(chars(a["text"]), chars(b["text"]), 0.10):
                p.append(f"series: {key} seq {a['seq']} left and right lengths differ by more than 10%")
    return p


# --- Inventory and folder --------------------------------------------------------------------------------------------------

def check_inventory(directory, files, golden_ids):
    p = []
    directory = Path(directory)
    entries = sorted(directory.iterdir()) if directory.is_dir() else []
    stray = [e.name for e in entries if not (e.is_file() and e.suffix == ".json")]
    if stray:
        p.append(f"inventory: the folder holds something other than .json files: {stray}")
    on_disk = sorted(directory.glob("*.json")) if directory.is_dir() else []
    if len(on_disk) != len(files):
        p.append(f"inventory: {len(on_disk) - len(files)} .json file(s) are not valid UTF-8 JSON")
    if not all(isinstance(d, dict) and isinstance(d.get("id"), str) for _p, d in files):
        return p + ["inventory: a file is not a JSON object with a string id"]
    ids = [d["id"] for _p, d in files]
    if len(set(ids)) != len(ids):
        p.append("inventory: duplicate transcript ids")
    if not 20 <= len(files) <= 26:
        p.append(f"inventory: {len(files)} files, expected about 22")
    if len(pair_members(files)) != 12:
        p.append(f"inventory: {len(pair_members(files))} pair transcripts, expected 12")
    clash = sorted(set(ids) & set(golden_ids))
    if clash:
        p.append(f"inventory: ids {clash} clash with golden/transcripts ids")
    return p


def check_files(files, directory=WARMUP_DIR, golden_ids=()):
    """Every check on a list of (path, data): returns the list of problems (an empty list means clean)."""
    p = check_inventory(directory, files, golden_ids)
    known = known_of(files)
    for path, data in files:
        shape = check_format(path, data)
        p.extend(f"{path.name}: {m}" for m in shape)
        if shape:
            continue
        p.extend(f"{path.name}: {m}" for m in check_validators(path, data, known))
        p.extend(f"{path.name}: {m}" for m in check_limits(data))
        p.extend(f"{path.name}: {m}" for m in check_topic(data))
        p.extend(f"{path.name}: {m}" for m in check_planted(data))
        p.extend(f"{path.name}: {m}" for m in check_hygiene(data))
        if is_series(data):
            p.extend(f"{path.name}: {m}" for m in check_series_member(data, known))
        else:
            p.extend(f"{path.name}: {m}" for m in check_pair_planting(data))
    p.extend(check_pair_groups(files))
    for pair_id, members in pair_groups(files).items():
        by = variants_of(members) if len(members) == 2 else {}
        if sorted(by) == ["left", "right"] and not any(check_format(Path(f"{m['id']}.json"), m) for m in members):
            p.extend(f"{pair_id}: {m}" for m in check_pair(by["left"], by["right"]))
    p.extend(check_series_groups(files))
    return p


def golden_ids():
    return {d["id"] for _p, d in load_folder(GOLDEN_DIR)}


def check_folder(directory=WARMUP_DIR):
    return check_files(load_folder(directory), directory, golden_ids())


# --- Notes document --------------------------------------------------------------------------------------------------------

def paragraphs(text):
    return [block for block in re.split(r"\n\s*\n", text) if block.strip()]


def check_notes(text, files):
    """docs/warmup_notes.md: one paragraph per pair (what is planted where at what intensity, what matched means), the variant
    definitions (not political codings) and a candid list of known weaknesses."""
    p = []
    blocks = paragraphs(text)
    for pair_id, members in sorted(pair_groups(files).items()):
        mine = [b for b in blocks if pair_id in b]
        if not mine:
            p.append(f"notes: no paragraph names pair {pair_id}")
            continue
        (message, item), = planted_items(members[0])[:1]
        if not any(re.search(rf"intensity[^0-9]{{0,15}}{item['intensity']}\b", b, re.I) for b in mine):
            p.append(f"notes: no paragraph on {pair_id} states intensity {item['intensity']}")
        if not any(re.search(rf"message\s+{message['seq']}\b", b, re.I) for b in mine):
            p.append(f"notes: no paragraph on {pair_id} says the problem is in message {message['seq']}")
        if not any(re.search(r"\b(match\w*|identical|same|mirror\w*)\b", b, re.I) and re.search(r"length|characters", b, re.I) for b in mine):
            p.append(f"notes: no paragraph on {pair_id} says what matched means (identical or same, with the lengths)")
    for factor in ("message_length", "label_swap", "flooding", "repetition", "unanswered_question"):
        if not re.search(factor.replace("_", "[ _]"), text, re.I):
            p.append(f"notes: the series factor {factor} is not described")
    if not any(all(re.search(w, b, re.I) for w in (r"\bleft\b", r"\bright\b", "proposition")) for b in blocks):
        p.append("notes: no paragraph defines the left and right variants in terms of the proposition")
    if not re.search(r"\bnot\b[^.\n]{0,80}\bpolitical\b", text, re.I):
        p.append("notes: it does not state that the variants are not political codings")
    lowered = text.lower()
    if "weakness" not in lowered:
        p.append("notes: no list of known weaknesses")
    else:
        tail = text[lowered.index("weakness"):]
        items = [line for line in tail.splitlines() if re.match(r"\s*(?:[-*]|\d+[.)])\s+\S", line)]
        if len(items) < 4:
            p.append(f"notes: the weaknesses section lists {len(items)} items, expected at least 4")
    return p


# --- Finders used by the mutation tests -------------------------------------------------------------------------------------

def pair_stem(files, dimension, variant, title="Bike lanes"):
    """The id (= file stem) of the `variant` member of the pair on `title` that plants `dimension`."""
    for _path, data in pair_members(files):
        if data["variant"] == variant and data["topic"]["title"] == title:
            if any(item["dimension"] == dimension for _m, item in planted_items(data)):
                return data["id"]
    raise LookupError((dimension, variant, title))


def series_stem(files, factor, side, level=None):
    """The id of the series member with that factor, side and (optionally) level."""
    for _path, data in series_members(files):
        series = data["series"]
        if series["factor"] == factor and series["side"] == side and (level is None or series["level"] == level):
            return data["id"]
    raise LookupError((factor, side, level))
