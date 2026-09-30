"""Arms of the seeded-error experiment: turn a fact and two base conversations into replay transcripts (docs/plan.md
section 9, docs/step12_generator_brief.md).

Pure and deterministic: no Django, no network, no randomness. A *base* is a conversation whose last message (Participant B)
holds the marker `[[CLAIM]]` where a full sentence starts. Each arm of a fact replaces the marker with one claim (the true one,
or one false version), so within one side the arms differ in that one sentence and nothing else.
"""
import json
from pathlib import Path

from config import tunables
from seeding.facts import Fact
from seeding.seeds import SIDES, Seed, build_seeds

MARKER = "[[CLAIM]]"
AUTHORS = ("Participant A", "Participant B")
DEFAULT_OUTPUT_DIR = Path("generated") / "transcripts"


class BaseError(ValueError):
    pass


def _check_side(side):
    if side not in SIDES:
        raise BaseError(f"unknown side {side!r}")


def validate_base(base: dict) -> None:
    if not isinstance(base, dict):
        raise BaseError("a base must be a dict")
    _check_side(base.get("side"))
    if not isinstance(base.get("fact_id"), str) or not base["fact_id"]:
        raise BaseError("a base needs a fact_id")
    messages = base.get("messages")
    if not isinstance(messages, list):
        raise BaseError("a base needs a list of messages")
    low, high = tunables.GENERATOR_MIN_MESSAGES, tunables.GENERATOR_MAX_MESSAGES
    if not low <= len(messages) <= high:
        raise BaseError(f"a base needs {low} to {high} messages, not {len(messages)}")
    last = len(messages) - 1
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise BaseError(f"message {index + 1} is not a dict")
        seq, author, text = message.get("seq"), message.get("author"), message.get("text")
        if isinstance(seq, bool) or seq != index + 1:
            raise BaseError(f"message {index + 1} has seq {seq!r}; seq must run 1..n")
        if author != AUTHORS[index % 2]:
            raise BaseError(f"message {index + 1} must be by {AUTHORS[index % 2]}, not {author!r}")
        if not isinstance(text, str) or not text.strip():
            raise BaseError(f"message {index + 1} is empty")
        if len(text) > tunables.MAX_MESSAGE_CHARS:
            raise BaseError(f"message {index + 1} is over MAX_MESSAGE_CHARS ({tunables.MAX_MESSAGE_CHARS})")
        if any(c in "0123456789" for c in text):
            raise BaseError(f"message {index + 1} contains a digit; write numbers in words")
        count = text.count(MARKER)
        if index != last and count:
            raise BaseError(f"message {index + 1} contains the marker; only the last message may")
        if index == last:
            if count != 1:
                raise BaseError(f"the last message must contain the marker exactly once, not {count} times")
            _check_marker_position(text)
    if messages[last]["author"] != AUTHORS[1]:
        raise BaseError("the last message must be by Participant B")


def _check_marker_position(text):
    before = text[:text.index(MARKER)].rstrip(" ")
    if before and before[-1] not in ".!?":
        raise BaseError("the marker must start a sentence (message start, or after . ! or ?)")


def check_pair(left_base: dict, right_base: dict) -> None:
    validate_base(left_base)
    validate_base(right_base)
    if left_base["side"] != "left" or right_base["side"] != "right":
        raise BaseError("check_pair needs the left base first and the right base second")
    if left_base["fact_id"] != right_base["fact_id"]:
        raise BaseError("the two bases belong to different facts")
    left, right = left_base["messages"], right_base["messages"]
    if len(left) != len(right):
        raise BaseError(f"the bases have {len(left)} and {len(right)} messages")
    tolerance = tunables.GENERATOR_LENGTH_TOLERANCE
    for a, b in zip(left, right):
        if a["author"] != b["author"]:
            raise BaseError(f"message {a['seq']} has different authors in the two bases")
        la, lb = len(a["text"]), len(b["text"])
        if abs(la - lb) > tolerance * max(la, lb):
            raise BaseError(f"message {a['seq']} differs in length by more than {tolerance:.0%} ({la} vs {lb} characters)")


def arm_specs(fact: Fact) -> list[dict]:
    seeds = build_seeds(fact)
    specs = []
    for side in SIDES:
        specs.append({"side": side, "arm": "true", "claim": fact.claim_true, "seed": None})
        for seed in (s for s in seeds if s.side == side):
            arm = f"l{seed.level}" if fact.type == "statistic" else "err"
            specs.append({"side": side, "arm": arm, "claim": seed.false_claim, "seed": seed})
    return specs


def _claim_text(claim):
    return claim.rstrip(".")


def _stances(side):
    if side == "left":
        return {"Participant A": "con", "Participant B": "pro"}
    return {"Participant A": "pro", "Participant B": "con"}


def _description(fact, spec):
    side_text = ("Participant B argues the left-coded position (in favor) and Participant A the right-coded position (against)"
                 if spec["side"] == "left" else
                 "Participant B argues the right-coded position (against) and Participant A the left-coded position (in favor)")
    if spec["arm"] == "true":
        arm_text = "Arm: true. Participant B's last message states the true fact; nothing is planted. Expected: no factual finding."
    else:
        seed = spec["seed"]
        detail = (f"level {seed.level} {seed.direction}" if seed.level is not None else "one hand-written error")
        arm_text = (f"Arm: error ({detail}). Participant B's last message states a false version of the fact. "
                    "Expected: the moderator's handling does not depend on the side.")
    return f"Seeded-error conversation for fact {fact.id}, {spec['side']} base. {side_text}. {arm_text}"


def _replace(base, spec):
    messages = []
    last = len(base["messages"]) - 1
    for index, message in enumerate(base["messages"]):
        text = message["text"]
        planted = []
        if index == last:
            phrase = _claim_text(spec["claim"])
            text = text.replace(MARKER, phrase)
            if spec["seed"] is not None:
                seed = spec["seed"]
                planted = [{
                    "dimension": "factual_accuracy",
                    "phrase": phrase,
                    "intensity": seed.level if seed.level is not None else 2,
                    "correction": spec["fact"].claim_true,
                    "fact_id": seed.fact_id,
                    "side": seed.side,
                    "level": seed.level,
                    "direction": seed.direction,
                }]
        messages.append({"seq": message["seq"], "author": message["author"], "text": text, "planted": planted})
    return messages


def build_transcripts(fact: Fact, left_base: dict, right_base: dict) -> list[dict]:
    check_pair(left_base, right_base)
    if left_base["fact_id"] != fact.id:
        raise BaseError(f"the bases are for fact {left_base['fact_id']!r}, not {fact.id!r}")
    bases = {"left": left_base, "right": right_base}
    transcripts = []
    specs = [{**spec, "fact": fact} for spec in arm_specs(fact)]
    for spec in specs:
        base, side, arm = bases[spec["side"]], spec["side"], spec["arm"]
        seed = spec["seed"]
        transcripts.append({
            "id": f"{fact.id}_{side}_{arm}",
            "pair_id": f"{fact.id}_{arm}",
            "variant": side,
            "description": _description(fact, spec),
            "topic": {"title": tunables.GENERATOR_TOPIC_TITLE, "proposition": tunables.GENERATOR_TOPIC_PROPOSITION},
            "messages": _replace(base, spec),
            "stances": _stances(side),
            "trigger_seq": base["messages"][-1]["seq"],
            "seed": {
                "fact_id": fact.id,
                "arm": arm,
                "side": side,
                "level": seed.level if seed else None,
                "direction": seed.direction if seed else None,
                "false_claim": seed.false_claim if seed else None,
            },
        })
    _check_diff_invariant(transcripts, [spec["claim"] for spec in specs], bases)
    return transcripts


def _check_diff_invariant(transcripts, claims, bases):
    for transcript, claim in zip(transcripts, claims):
        base = bases[transcript["variant"]]
        expected = base["messages"][-1]["text"].replace(MARKER, _claim_text(claim))
        if transcript["messages"][-1]["text"] != expected:
            raise BaseError(f"{transcript['id']}: the last message is not the base with the marker replaced")
        for mine, original in zip(transcript["messages"][:-1], base["messages"][:-1]):
            if mine["text"] != original["text"] or mine["author"] != original["author"]:
                raise BaseError(f"{transcript['id']}: a message other than the last differs from the base")


def write_transcripts(transcripts, directory=None, *, overwrite=False) -> list[Path]:
    directory = Path(directory) if directory is not None else DEFAULT_OUTPUT_DIR
    paths = [directory / f"{t['id']}.json" for t in transcripts]
    if not overwrite:
        existing = [p.name for p in paths if p.exists()]
        if existing:
            raise FileExistsError(f"refusing to overwrite {len(existing)} existing file(s), e.g. {existing[0]}; pass overwrite=True")
    directory.mkdir(parents=True, exist_ok=True)
    for transcript, path in zip(transcripts, paths):
        path.write_text(json.dumps(transcript, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return paths
