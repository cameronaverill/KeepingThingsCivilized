"""Paired-set loader and replay runner (docs/plan.md sections 9 and 11, docs/step13_brief.md).

Scripted transcripts (`golden/transcripts/*.json`) are loaded into the database as synthetic conversations, and
`kind="replay"` moderation runs are created and executed on them, so the whole moderation pipeline is exercised on input
with known ground truth. Replays never post a moderator message (the model, a database constraint and the pipeline all
forbid it), so a scripted transcript is never contaminated by moderator output.

Public functions: `load_experiment`, `plan_runs`, `execute_runs`, `factors`, `dry_run_plan`, plus the data classes
`ExperimentPlan`, `RunSpec`, `RunResult` and `ReplayReport`.

Choices made where the brief is silent (all reported to the architect):

- **Idempotency key.** A synthetic conversation is identified by `(experiment, Conversation.transcript_id)`, the pair the
  model already makes unique. `transcript_id` is the golden transcript's id for the "as-is" assignment and
  `"<id>:swapped"` for the "swapped" one (`conversation_key`). The keys and each conversation's facts are recorded in
  `Experiment.config["conversations"]`. Loading again with the same arguments finds each conversation by that key, checks
  that its stored messages, topic, pair_id and variant still equal what the file gives (a changed transcript under an old
  experiment name is an error, never a silent reuse), and creates nothing. A run is identified by
  `(conversation, trigger message, replicate)` among `kind="replay"` runs.
- **Trigger.** The message whose `seq` equals the file's `trigger_seq` (what the old spike used). Messages after the
  trigger are loaded but no run sees them (`snapshot_seq = trigger.seq_no`).
- **Label seed.** `Conversation.label_seed` is a deterministic 62-bit integer from sha256 of
  `experiment name | transcript id | assignment`. It identifies the assignment; the assignment itself is explicit, not drawn.
- **Topic.** Found by (title, proposition); created visible (`hidden=False`, no creator) when there is none, so a later
  `seed_topics` fills in its leans and description. An existing topic with the same title but another proposition is an
  error (the title reaches the prompt, so it must not differ).
- **Timestamps.** Message i (0-based position) of a scripted conversation gets `created_at = REPLAY_BASE_TIME + i *
  REPLAY_MESSAGE_GAP_SECONDS`, the same for every transcript, variant and label assignment, so the "seconds between
  messages" fact cannot differ by side. It is set with a queryset update after the save (`created_at` is auto_now_add).
- **Ledger purpose.** `moderation.agents` uses `purpose="replay"` for a replay run, so replay spend follows the evaluation
  budget by itself. The only remaining wrapper around `llm.call` (`_Gateway`) exists for `model_overrides` and the
  per-call `SessionBudget(max_usd)`, which agents.py cannot take; it touches only the current replay run's calls.
- **Plan order.** Replicate-major, then pair (`pair_id`, else the transcript id), then variant, then assignment (as-is before
  swapped), so that *when a run ends for a reason unrelated to cost* (e.g. plain completion), complete pairs and both label
  assignments land together. **This is not a guarantee once real spend is involved** (owner decision, 2026-09-28, after a
  test audit found `test_replay_budget.py` contradicting the older wording here): `execute_runs` checks the *ledger's
  actual* spend before each run, not a look-ahead over the rest of a pair, so a budget stop can and does land mid-pair
  when real cost diverges from the worst-case estimate. A hard dollar cap wins over pair-completeness by design; the
  plan order still makes pairs land together in the common case (no early stop), it just isn't a hard promise.
- **Snapshot.** The pipeline overwrites `config_snapshot` when it claims a run, so `factors` and `replay` are written at
  creation and merged back after the run finishes.
"""
import contextlib
import copy
import datetime
import hashlib
import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import CommandError
from django.db import transaction
from django.db.models import Max

from forum.limits import count_message_chars
from forum.models import Conversation, Experiment, KIND_CHOICES, Message, Participant, Topic
from moderation import agents, budget, llm, pipeline, pricing, prompting, transcripts as transcript_files
from moderation.models import ModerationRun
from moderation.transcripts import compute_features

logger = logging.getLogger(__name__)

ASSIGNMENTS = ("as-is", "swapped")
ASSIGNMENT_CHOICES = ASSIGNMENTS + ("both",)
EXPERIMENT_KINDS = tuple(value for value, _label in KIND_CHOICES)
REPLAY_PURPOSE = "replay"
_ZERO = Decimal("0")
REPLAY_BASE_TIME = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
_AUTHOR_RE = re.compile(r"Participant ([A-Z])")
_SWAP = {"A": "B", "B": "A"}
_STOP_REASONS = ("budget_exceeded", "budget_unavailable", "breaker_open", "refused", "model_not_allowed")


class ReplayError(CommandError, ValueError):
    """A transcript, an argument or a stored experiment that cannot be used. Both a CommandError (the command shows it
    cleanly) and a ValueError (library callers)."""


# --- Data classes ---------------------------------------------------------------------------------------------------

@dataclass
class PlannedConversation:
    conversation: object  # forum.Conversation (None in a dry-run plan)
    transcript_id: str  # the idempotency key stored in Conversation.transcript_id
    source_id: str  # the golden transcript's id
    assignment: str
    label_seed: int
    trigger_seq: int
    created: bool
    pair_id: str = ""
    variant: str = ""


@dataclass
class ExperimentPlan:
    experiment: object  # forum.Experiment
    conversations: list
    assignments: tuple
    replicates: int

    @property
    def created_conversations(self):
        return [c for c in self.conversations if c.created]


@dataclass(frozen=True)
class RunSpec:
    conversation: object
    trigger_message: object
    snapshot_seq: int
    replicate: int
    transcript_id: str  # the conversation's key (see conversation_key)
    assignment: str
    experiment_name: str = ""

    @property
    def trigger(self):
        """Alias of `trigger_message`."""
        return self.trigger_message


@dataclass
class RunResult:
    spec: RunSpec
    run: object
    status: str
    failure_reason: str
    cost: Decimal


@dataclass
class ReplayReport:
    max_usd: Decimal
    results: list = field(default_factory=list)
    already_done: list = field(default_factory=list)  # specs skipped because a done run exists
    not_run: list = field(default_factory=list)  # specs left because the next run would pass max_usd (or a guard stopped)
    stopped_reason: str = ""
    total_cost: Decimal = _ZERO
    cost_by_transcript: dict = field(default_factory=dict)  # conversation key -> Decimal

    @property
    def counts_by_status(self):
        counts = {}
        for result in self.results:
            counts[result.status] = counts.get(result.status, 0) + 1
        return counts

    @property
    def counts(self):
        """Alias of `counts_by_status`."""
        return self.counts_by_status

    @property
    def counts_by_failure_reason(self):
        counts = {}
        for result in self.results:
            if result.failure_reason:
                counts[result.failure_reason] = counts.get(result.failure_reason, 0) + 1
        return counts

    def summary(self):
        lines = ["Replay report", "-------------"]
        counts = self.counts_by_status
        detail = ", ".join(f"{status}: {n}" for status, n in sorted(counts.items())) or "none"
        lines.append(f"Runs executed: {len(self.results)} ({detail})")
        reasons = self.counts_by_failure_reason
        if reasons:
            lines.append("Failure reasons: " + ", ".join(f"{reason}: {n}" for reason, n in sorted(reasons.items())))
        lines.append(f"Runs already done before this call (skipped, no cost): {len(self.already_done)}")
        lines.append(f"Runs not run: {len(self.not_run)}" + (f" ({self.stopped_reason})" if self.stopped_reason else ""))
        lines.append(f"Total cost (ledger, purpose {REPLAY_PURPOSE}): ${self.total_cost:.6f} of a ${Decimal(self.max_usd):.4f} limit")
        if self.cost_by_transcript:
            lines.append("Cost per transcript:")
            for key in sorted(self.cost_by_transcript):
                lines.append(f"  {key}: ${self.cost_by_transcript[key]:.6f}")
        return "\n".join(lines)

    def __str__(self):
        return self.summary()


# --- Small pure helpers -----------------------------------------------------------------------------------------------

def expand_assignments(assignments):
    """("as-is",), ("swapped",) or both, in that order, from "as-is" | "swapped" | "both" (or an iterable of the first two)."""
    if isinstance(assignments, str):
        if assignments == "both":
            return ASSIGNMENTS
        assignments = (assignments,)
    chosen = tuple(assignments)
    if not chosen or any(a not in ASSIGNMENTS for a in chosen):
        raise ReplayError(f"assignments must be one of {', '.join(ASSIGNMENT_CHOICES)}, not {assignments!r}")
    return tuple(a for a in ASSIGNMENTS if a in chosen)


def conversation_key(transcript_id, assignment):
    """The value of Conversation.transcript_id (the idempotency key within an experiment)."""
    return transcript_id if assignment == "as-is" else f"{transcript_id}:{assignment}"


def make_label_seed(experiment_name, transcript_id, assignment):
    digest = hashlib.sha256(f"{experiment_name}|{transcript_id}|{assignment}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") >> 2  # 62 bits: fits BigIntegerField, never negative


def normalize_transcripts(transcripts):
    """A list of (path, data) sorted by transcript id, from a dict {id: data}, an iterable of data dicts, or an iterable of
    (path, data) pairs (what `transcripts.load_transcript_files` returns). A missing path becomes `<id>.json` (only used in errors)."""
    if isinstance(transcripts, dict):
        transcripts = list(transcripts.values())
    pairs = []
    for item in transcripts:
        if isinstance(item, (tuple, list)) and len(item) == 2:
            path, data = item
        else:
            path, data = None, item
        if not isinstance(data, dict) or not isinstance(data.get("id"), str) or not data["id"]:
            raise ReplayError("a transcript must be an object with a non-empty string 'id'")
        pairs.append((Path(path) if path is not None else Path(f"{data['id']}.json"), data))
    seen = {}
    for path, data in pairs:
        if data["id"] in seen:
            raise ReplayError(f"duplicate transcript id {data['id']!r} in {seen[data['id']].name} and {path.name}")
        seen[data["id"]] = path
    return sorted(pairs, key=lambda pair: pair[1]["id"])


def _rows(path, data, assignment):
    """The messages to store, as plain dicts, for one transcript and one label assignment. Raises ReplayError."""
    rows = []
    for message in data["messages"]:
        author = message["author"]
        row = {"seq": message["seq"], "text": message["text"], "planted": copy.deepcopy(message.get("planted") or [])}
        if transcript_files.is_moderator(author):
            row.update(author_type="moderator", label=None)
        else:
            match = _AUTHOR_RE.fullmatch(author)
            if match is None:
                raise ReplayError(
                    f"{path.name}: message seq {message['seq']} has author {author!r}; expected 'Participant <letter>' or a Moderator"
                )
            label = match.group(1)
            if assignment == "swapped":
                label = _SWAP.get(label, label)
            row.update(author_type="user", label=label)
        rows.append(row)
    return rows


def _stances(data, assignment):
    stances = data.get("stances")
    if not isinstance(stances, dict) or assignment != "swapped":
        return stances
    swapped = {}
    for author, stance in stances.items():
        match = _AUTHOR_RE.fullmatch(author) if isinstance(author, str) else None
        swapped[f"Participant {_SWAP.get(match.group(1), match.group(1))}" if match else author] = stance
    return swapped


def validate_transcripts(pairs, *, known=None):
    """Check every transcript against the transcript rules and the real users' limits, writing nothing. `known` is the
    {id: data} of the whole folder (so a series member's base can be checked even when it is not selected)."""
    for path, data in pairs:
        transcript_files.validate_transcript(path, data, known_ids=known)
        rows = _rows(path, data, "as-is")
        user_messages = [row for row in rows if row["author_type"] == "user"]
        limit = int(settings.MAX_USER_MESSAGES_PER_CONVERSATION)
        if len(user_messages) > limit:
            raise ReplayError(
                f"{path.name}: {len(user_messages)} user messages, over MAX_USER_MESSAGES_PER_CONVERSATION ({limit}); "
                "synthetic transcripts obey the same limit as real users"
            )
        for row in user_messages:
            counted = count_message_chars(row["text"])
            if counted < 1:
                raise ReplayError(f"{path.name}: message seq {row['seq']} is empty")
            if counted > int(settings.MAX_MESSAGE_CHARS):
                raise ReplayError(
                    f"{path.name}: message seq {row['seq']} is {counted} characters, over MAX_MESSAGE_CHARS "
                    f"({settings.MAX_MESSAGE_CHARS}); synthetic transcripts obey the same limit as real users"
                )


def _check_replicates(replicates):
    if isinstance(replicates, bool) or not isinstance(replicates, int) or replicates < 1:
        raise ReplayError(f"replicates must be a whole number of at least 1, not {replicates!r}")


def _tunables_in_force():
    names = (
        "MAX_MESSAGE_CHARS", "MAX_USER_MESSAGES_PER_CONVERSATION", "TRANSCRIPT_MAX_MESSAGES", "MAX_ACTS_PER_INTERVENTION",
        "MASTER_MODEL", "INTERVENOR_MODEL", "MASTER_MAX_TOKENS", "INTERVENOR_MAX_TOKENS",
    )
    return {name: getattr(settings, name) for name in names}


# --- Loading ---------------------------------------------------------------------------------------------------------

def _topic_for(path, data):
    title, proposition = data["topic"]["title"], data["topic"]["proposition"]
    topic = Topic.objects.filter(title=title, proposition=proposition).first()
    if topic is not None:
        return topic
    if title and Topic.objects.filter(title=title).exists():
        raise ReplayError(
            f"{path.name}: a topic titled {title!r} already exists with a different proposition; the title reaches the "
            "prompt, so a replay cannot reuse it with another proposition"
        )
    return Topic.objects.create(title=title, proposition=proposition, hidden=False, created_by=None)


def _stored_rows(conversation):
    return [
        (m.seq_no, m.author_type, m.participant.label if m.participant_id else None, m.content, m.planted)
        for m in conversation.messages.select_related("participant").order_by("seq_no")
    ]


def _expected_rows(rows):
    return [(r["seq"], r["author_type"], r["label"], r["text"], r["planted"]) for r in rows]


def _create_conversation(experiment, key, seed, topic, data, rows):
    conversation = Conversation.objects.create(
        topic=topic,
        status="closed",
        source="synthetic",
        experiment=experiment,
        pair_id=data.get("pair_id") or "",
        variant=data.get("variant") or "",
        transcript_id=key,
        label_seed=seed,
    )
    labels = sorted({"A", "B"} | {row["label"] for row in rows if row["label"]})
    participants = {}
    for order, label in enumerate(labels, start=1):
        participant = Participant(conversation=conversation, user=None, label=label, join_order=order)
        participant.save()
        participants[label] = participant
    gap = datetime.timedelta(seconds=int(settings.REPLAY_MESSAGE_GAP_SECONDS))
    for position, row in enumerate(rows):
        message = Message(
            conversation=conversation,
            seq_no=row["seq"],
            author_type=row["author_type"],
            participant=participants.get(row["label"]) if row["label"] else None,
            content=row["text"],
            planted=row["planted"],
        )
        message.save()
        # created_at is auto_now_add, so the fixed time is written with an update after the save.
        Message.objects.filter(pk=message.pk).update(created_at=REPLAY_BASE_TIME + position * gap)
    return conversation


def _experiment(name, kind, description):
    experiment = Experiment.objects.filter(name=name).first()
    if experiment is None:
        return Experiment.objects.create(name=name, kind=kind, description=description, config={}), True
    if experiment.kind != kind:
        raise ReplayError(f"experiment {name!r} already exists with kind {experiment.kind!r}, not {kind!r}")
    return experiment, False


def load_experiment(name, transcripts, *, assignments="as-is", replicates=1, kind="replay", known=None):
    """Load scripted transcripts as synthetic conversations of an experiment and return an `ExperimentPlan`.

    `transcripts`: a dict {id: data}, data dicts, or (path, data) pairs (see `normalize_transcripts`). `assignments`:
    "as-is", "swapped" or "both". `known`: the {id: data} of the whole folder, for the series checks (default: none).
    Everything is validated before anything is written, and the writes are one transaction, so a bad transcript leaves
    nothing behind. Loading again with the same arguments creates nothing (see the module docstring for the key)."""
    if not isinstance(name, str) or not name.strip():
        raise ReplayError("an experiment needs a name")
    if kind not in EXPERIMENT_KINDS:
        raise ReplayError(f"kind must be one of {', '.join(EXPERIMENT_KINDS)}, not {kind!r}")
    _check_replicates(replicates)
    chosen = expand_assignments(assignments)
    requested = assignments if isinstance(assignments, str) else "+".join(chosen)
    pairs = normalize_transcripts(transcripts)
    if not pairs:
        raise ReplayError("no transcripts to load")
    validate_transcripts(pairs, known=known)

    planned = []
    with transaction.atomic():
        experiment, _created = _experiment(name, kind, f"{kind} of {len(pairs)} scripted transcript(s), assignments {', '.join(chosen)}")
        config = dict(experiment.config or {})
        records = dict(config.get("conversations") or {})
        for path, data in pairs:
            topic = None
            for assignment in chosen:
                key = conversation_key(data["id"], assignment)
                seed = make_label_seed(name, data["id"], assignment)
                rows = _rows(path, data, assignment)
                existing = Conversation.objects.filter(experiment=experiment, transcript_id=key).first()
                if existing is None:
                    topic = topic or _topic_for(path, data)
                    conversation = _create_conversation(experiment, key, seed, topic, data, rows)
                    created = True
                else:
                    conversation, created = existing, False
                    same = (
                        _stored_rows(conversation) == _expected_rows(rows)
                        and conversation.topic.title == data["topic"]["title"]
                        and conversation.topic.proposition == data["topic"]["proposition"]
                        and conversation.pair_id == (data.get("pair_id") or "")
                        and conversation.variant == (data.get("variant") or "")
                    )
                    if not same:
                        raise ReplayError(
                            f"{path.name}: experiment {name!r} already holds {key!r} with different content; use a new "
                            "experiment name for a changed transcript"
                        )
                records[key] = {
                    "transcript_id": data["id"],
                    "assignment": assignment,
                    "label_seed": conversation.label_seed,
                    "pair_id": data.get("pair_id") or "",
                    "variant": data.get("variant") or "",
                    "trigger_seq": data["trigger_seq"],
                    "series": data.get("series"),
                    "stances": _stances(data, assignment),
                }
                planned.append(
                    PlannedConversation(
                        conversation=conversation, transcript_id=key, source_id=data["id"], assignment=assignment,
                        label_seed=conversation.label_seed, trigger_seq=data["trigger_seq"], created=created,
                        pair_id=conversation.pair_id, variant=conversation.variant,
                    )
                )
        fingerprint = agents.prompt_fingerprint()
        seen_prompts = config.get("prompts_seen") or []
        if fingerprint not in seen_prompts:
            seen_prompts = seen_prompts + [fingerprint]
        config.update(
            transcript_ids=sorted(set(config.get("transcript_ids") or []) | {d["id"] for _p, d in pairs}),
            assignments=[a for a in ASSIGNMENTS if a in set(config.get("assignments") or []) | set(chosen)],
            assignments_requested=list(dict.fromkeys([*(config.get("assignments_requested") or []), requested])),
            replicates=max(int(config.get("replicates") or 1), replicates),
            idempotency_key="Conversation(experiment, transcript_id): id for as-is, '<id>:swapped' for swapped",
            tunables=_tunables_in_force(),
            prompts=fingerprint,
            prompts_seen=seen_prompts,
            conversations=records,
        )
        experiment.config = config
        experiment.save(update_fields=["config"])
    plan = ExperimentPlan(experiment=experiment, conversations=planned, assignments=chosen, replicates=replicates)
    logger.info(
        "replay: experiment %s loaded, %d conversation(s) (%d new)",
        experiment.pk, len(planned), len(plan.created_conversations),
    )
    return plan


# --- Planning runs -----------------------------------------------------------------------------------------------------

def plan_runs(experiment_plan, *, replicates=None):
    """One RunSpec per conversation and replicate 1..N: the trigger is the message whose seq is the transcript's
    `trigger_seq`, `snapshot_seq` its seq_no. Order: replicate, then pair (`pair_id`, else the transcript id), then variant,
    then assignment (as-is before swapped), then transcript id. Deterministic: the same plan gives the same list."""
    replicates = experiment_plan.replicates if replicates is None else replicates
    _check_replicates(replicates)
    ordered = sorted(
        experiment_plan.conversations,
        key=lambda p: (p.pair_id or p.source_id, p.variant, ASSIGNMENTS.index(p.assignment), p.source_id),
    )
    triggers = {
        p.transcript_id: Message.objects.get(conversation=p.conversation, seq_no=p.trigger_seq) for p in ordered
    }
    specs = []
    for replicate in range(1, replicates + 1):
        for planned in ordered:
            trigger = triggers[planned.transcript_id]
            specs.append(
                RunSpec(
                    conversation=planned.conversation,
                    trigger_message=trigger,
                    snapshot_seq=trigger.seq_no,
                    replicate=replicate,
                    transcript_id=planned.transcript_id,
                    assignment=planned.assignment,
                    experiment_name=experiment_plan.experiment.name,
                )
            )
    return specs


# --- Factors --------------------------------------------------------------------------------------------------------

def _stored_transcript(conversation):
    """The stored messages as {"seq", "author", "text"} dicts (the golden format), oldest first."""
    return [
        {
            "seq": m.seq_no,
            "author": "Moderator" if m.author_type == "moderator" else "Participant " + m.participant.label,
            "text": m.content,
        }
        for m in conversation.messages.select_related("participant").order_by("seq_no")
    ]


def _trigger_seq_of(conversation, trigger_seq=None):
    if trigger_seq is not None:
        return trigger_seq
    experiment = conversation.experiment
    record = ((experiment.config or {}).get("conversations") or {}).get(conversation.transcript_id) if experiment else None
    if record and record.get("trigger_seq") is not None:
        return record["trigger_seq"]
    last = conversation.messages.filter(author_type="user").aggregate(m=Max("seq_no"))["m"]
    if last is None:
        raise ReplayError(f"conversation {conversation.pk} has no user message to use as a trigger")
    return last


def factors(conversation, trigger_seq=None):
    """The series factor values (`transcripts.compute_features`) computed from the STORED transcript, never hand-labeled.
    The trigger is `trigger_seq` if given, else the one recorded in the experiment config, else the last user message."""
    seq = _trigger_seq_of(conversation, trigger_seq)
    return compute_features(_stored_transcript(conversation), seq)


# --- Cost estimates ----------------------------------------------------------------------------------------------------

def _worst_case_for(transcript, models):
    """(master, intervenor) worst-case reservation-basis cost for a transcript-format, with the given models."""
    master_prompt = prompting.load_prompt(agents.MASTER_PROMPT)
    intervenor_prompt = prompting.load_prompt(agents.INTERVENOR_PROMPT)
    limit = int(settings.TRANSCRIPT_MAX_MESSAGES)
    visible = [m for m in transcript["messages"] if m["seq"] <= transcript["trigger_seq"]][-limit:]
    trimmed = {**transcript, "messages": visible}
    master, _ = transcript_files.estimate_worst_case(trimmed, models["master"], master_prompt, intervenor_prompt)
    _, intervenor = transcript_files.estimate_worst_case(trimmed, models["intervenor"], master_prompt, intervenor_prompt)
    return master, intervenor


def _models(overrides=None):
    overrides = overrides or {}
    return {
        "master": overrides.get("master") or settings.MASTER_MODEL,
        "intervenor": overrides.get("intervenor") or settings.INTERVENOR_MODEL,
    }


def _check_models(models):
    for model in models.values():
        pricing.get_price(model)  # raises a ModelNotAllowed (an LLMRefused) for an unknown model


def worst_case_run_usd(transcript, model_overrides=None):
    """Worst-case cost of one moderation run (Master + Intervenor) on a transcript-format dict."""
    return sum(_worst_case_for(transcript, _models(model_overrides)), _ZERO)


@dataclass
class DryRunPlan:
    experiment_name: str
    kind: str
    assignments: tuple
    replicates: int
    transcript_ids: list
    conversations: list  # dicts: key, source_id, assignment, exists, runs, runs_done, worst_case_usd
    conversations_to_create: int
    runs_to_do: int
    worst_case_usd: Decimal


def dry_run_plan(name, transcripts, *, assignments="both", replicates=1, kind="replay", known=None):
    """What `load_experiment` + `execute_runs` would do, computed without writing anything (the database is only read)
    and without any API call. The cost is `transcript_files.estimate_worst_case` for each run still to do."""
    if not isinstance(name, str) or not name.strip():
        raise ReplayError("an experiment needs a name")
    if kind not in EXPERIMENT_KINDS:
        raise ReplayError(f"kind must be one of {', '.join(EXPERIMENT_KINDS)}, not {kind!r}")
    _check_replicates(replicates)
    chosen = expand_assignments(assignments)
    pairs = normalize_transcripts(transcripts)
    validate_transcripts(pairs, known=known)
    _check_models(_models())
    experiment = Experiment.objects.filter(name=name).first()
    if experiment is not None and experiment.kind != kind:
        raise ReplayError(f"experiment {name!r} already exists with kind {experiment.kind!r}, not {kind!r}")
    entries = []
    total = _ZERO
    for path, data in pairs:
        master, intervenor = _worst_case_for(data, _models())
        per_run = master + intervenor
        for assignment in chosen:
            key = conversation_key(data["id"], assignment)
            existing = (
                Conversation.objects.filter(experiment=experiment, transcript_id=key).first() if experiment else None
            )
            done = set(
                ModerationRun.objects.filter(conversation=existing, kind="replay", status="done").values_list("replicate", flat=True)
            ) if existing else set()
            runs_done = len([r for r in range(1, replicates + 1) if r in done])
            to_do = replicates - runs_done
            total += per_run * to_do
            entries.append(
                {
                    "key": key, "source_id": data["id"], "assignment": assignment, "exists": existing is not None,
                    "runs": replicates, "runs_done": runs_done, "worst_case_usd": per_run * to_do,
                }
            )
    return DryRunPlan(
        experiment_name=name, kind=kind, assignments=chosen, replicates=replicates,
        transcript_ids=[d["id"] for _p, d in pairs], conversations=entries,
        conversations_to_create=sum(1 for e in entries if not e["exists"]),
        runs_to_do=sum(e["runs"] - e["runs_done"] for e in entries), worst_case_usd=total,
    )


# --- Executing runs -----------------------------------------------------------------------------------------------------

class _Gateway:
    """Wraps `llm.call` while a replay run executes, for `model_overrides` and the session limit only (see the module docstring)."""

    def __init__(self, session, overrides):
        self.session = session
        self.overrides = overrides
        self.run_id = None
        self._original = None

    def __enter__(self):
        self._original = llm.call
        original, gateway = self._original, self

        def call(**kwargs):
            if gateway.run_id is not None and kwargs.get("run_id") == gateway.run_id:
                model = gateway.overrides.get(kwargs.get("agent"))
                if model:
                    kwargs["model"] = model
                if kwargs.get("session") is None:
                    kwargs["session"] = gateway.session
            return original(**kwargs)

        self._wrapper = call
        llm.call = call
        return self

    def __exit__(self, *exc_info):
        if llm.call is self._wrapper:
            llm.call = self._original
        return False


def _is_done(spec):
    return ModerationRun.objects.filter(
        conversation=spec.conversation, trigger_message=spec.trigger_message, replicate=spec.replicate,
        kind="replay", status="done",
    ).exists()


def _series_info(spec):
    experiment = spec.conversation.experiment
    record = ((experiment.config or {}).get("conversations") or {}).get(spec.transcript_id) if experiment else None
    return (record or {}).get("series")


def _replay_facts(spec):
    return {
        "experiment": spec.experiment_name or (spec.conversation.experiment.name if spec.conversation.experiment_id else ""),
        "transcript": spec.transcript_id,
        "assignment": spec.assignment,
        "label_seed": spec.conversation.label_seed,
        "series": _series_info(spec),
    }


def _new_run(spec):
    """An existing pending replay run for the spec (a leftover of an interrupted call), else a new one."""
    pending = ModerationRun.objects.filter(
        conversation=spec.conversation, trigger_message=spec.trigger_message, replicate=spec.replicate,
        kind="replay", status="pending",
    ).order_by("pk").first()
    if pending is not None:
        return pending
    run = ModerationRun(
        conversation=spec.conversation,
        trigger_message=spec.trigger_message,
        snapshot_seq=spec.snapshot_seq,
        kind="replay",
        replicate=spec.replicate,
        status="pending",
        config_snapshot={"factors": factors(spec.conversation, spec.snapshot_seq), "replay": _replay_facts(spec)},
    )
    run.save()
    return run


def _merge_snapshot(run, spec, overrides):
    """The pipeline replaces config_snapshot when it claims a run: put the replay facts back next to its keys."""
    stored = ModerationRun.objects.filter(pk=run.pk).values_list("config_snapshot", flat=True).first() or {}
    snapshot = dict(stored)
    snapshot["factors"] = factors(spec.conversation, spec.snapshot_seq)
    snapshot["replay"] = _replay_facts(spec)
    if overrides:
        snapshot["model_overrides"] = dict(overrides)
        snapshot["models"] = {**(snapshot.get("models") or {}), **overrides}
    ModerationRun.objects.filter(pk=run.pk).update(config_snapshot=snapshot)
    run.config_snapshot = snapshot


def _estimate_for(spec, models, cache):
    if spec.conversation.pk not in cache:
        transcript = {
            "id": spec.transcript_id,
            "topic": {"title": spec.conversation.topic.title, "proposition": spec.conversation.topic.proposition},
            "messages": _stored_transcript(spec.conversation),
            "trigger_seq": spec.snapshot_seq,
        }
        cache[spec.conversation.pk] = sum(_worst_case_for(transcript, models), _ZERO)
    return cache[spec.conversation.pk]


def execute_runs(specs, *, max_usd, model_overrides=None, on_result=None):
    """Create and execute `kind="replay"` runs one at a time, in order, and return a `ReplayReport`.

    - A spec with a `done` replay run (same conversation, trigger, replicate) is skipped at no cost (`already_done`).
    - Before each run (when LLM calls are enabled) the run's worst-case cost is added to the ledger spend of this call
      (`purpose="replay"`); if that would pass `max_usd` the call stops cleanly and every remaining spec is `not_run`. Run rows
      are created just before their turn, so `not_run` specs leave no rows. The guard's own session limit (`max_usd`) and the
      evaluation budget cap are a second and third line of defense.
    - A guard refusal that would repeat for every later run (budget, breaker, model) also stops the call.
    - With LLM_ENABLED False nothing is called: runs end `skipped_disabled` and the report counts them.
    - `on_result(RunResult)` is called after each executed run. Nothing here posts, and no live run is touched."""
    max_usd = Decimal(str(max_usd))
    if not max_usd.is_finite() or max_usd < 0:
        raise ReplayError("max_usd must be a non-negative number of dollars")
    overrides = dict(model_overrides or {})
    unknown = sorted(set(overrides) - {"master", "intervenor"})
    if unknown:
        raise ReplayError(f"unknown model_overrides key(s): {', '.join(unknown)}; use 'master' and 'intervenor'")
    models = _models(overrides)
    _check_models(models)
    specs = list(specs)
    for spec in specs:
        if spec.conversation.source != "synthetic":
            raise ReplayError(f"conversation {spec.conversation.pk} is not synthetic; a replay never touches real conversations")

    report = ReplayReport(max_usd=max_usd)
    session = budget.SessionBudget(max_usd)
    baseline = budget.spend(purposes=(REPLAY_PURPOSE,))
    estimates = {}
    with _Gateway(session, overrides) as gateway:
        for index, spec in enumerate(specs):
            if _is_done(spec):
                report.already_done.append(spec)
                continue
            spent = budget.spend(purposes=(REPLAY_PURPOSE,)) - baseline
            if settings.LLM_ENABLED and spent + _estimate_for(spec, models, estimates) > max_usd:
                report.not_run = [s for s in specs[index:] if not _is_done(s)]
                report.stopped_reason = f"the next run's worst case would pass --max-usd (${max_usd:.4f}); spent ${spent:.6f}"
                break
            run = _new_run(spec)
            gateway.run_id = run.pk
            before = budget.spend(purposes=(REPLAY_PURPOSE,))
            try:
                run = pipeline.run_moderation(run)
            finally:
                gateway.run_id = None
                run.refresh_from_db()
                _merge_snapshot(run, spec, overrides)
            cost = budget.spend(purposes=(REPLAY_PURPOSE,)) - before
            result = RunResult(spec=spec, run=run, status=run.status, failure_reason=run.failure_reason, cost=cost)
            report.results.append(result)
            report.total_cost += cost
            report.cost_by_transcript[spec.transcript_id] = report.cost_by_transcript.get(spec.transcript_id, _ZERO) + cost
            if on_result is not None:
                on_result(result)
            if run.status == "skipped_budget" or run.failure_reason in _STOP_REASONS:
                report.not_run = [s for s in specs[index + 1:] if not _is_done(s)]
                if report.not_run:
                    report.stopped_reason = f"a run ended {run.status} ({run.failure_reason or 'no reason'}); later runs would be refused too"
                break
    logger.info(
        "replay: executed %d run(s), %d already done, %d not run, cost=%s",
        len(report.results), len(report.already_done), len(report.not_run), report.total_cost,
    )
    return report


@contextlib.contextmanager
def llm_switched_off():
    """LLM calls are off for the duration (process-local; the tunables file is untouched)."""
    from django.test.utils import override_settings

    with override_settings(LLM_ENABLED=False):
        yield
