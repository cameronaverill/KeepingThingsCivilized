"""Helpers for the step 19 back-end tests (tests/preview_backend/): worlds, scripted answers for a draft check, id-free
fingerprints of a live run (so a reused run can be compared with a fresh one), and small readers.

Not a test module and not a conftest. Model and preview imports happen inside functions, so a missing module fails the
test that needs it, not collection. Conversations, scripted Master and Intervenor answers and the recorded-call readers come
from pipeline_run_kit (imported here as `prk`).
"""
import hashlib
import re
import unicodedata

import pipeline_run_kit as prk

PLACEHOLDER = -1  # the message id the preview gives the draft (docs/step19_backend_brief.md)

# A draft that contains QUOTE (so an issue on it can be located exactly). MARKER never appears anywhere else, so a test can
# search a log, a prompt or an export for it.
MARKER = "zqxjv4417"
QUOTE = prk.QUOTE_1  # "nobody disagrees on that"
DRAFT = f"Rent control has failed everywhere it was tried, {QUOTE}. {MARKER}"
NOTE = prk.CLEAN_TEXT
NOTE_2 = prk.CLEAN_TEXT_2

SPECS = [
    ("A", "I think rent control reduces the supply of housing, which is a well known fact."),
    ("B", "The supply of homes barely changes when rents are capped, and tenants gain a lot from that."),
    ("A", "Landlords leave the market when rent is capped, and that is simply how it works."),
]


def world(specs=None, **kwargs):
    return prk.build(SPECS if specs is None else specs, **kwargs)


def with_draft(specs, who, text):
    """The same conversation with the draft already posted as a real message (the message a check's live run is for)."""
    return prk.build(list(specs) + [(who, text)])


# --- Scripted answers --------------------------------------------------------------------------------------------------

def concern_script(msg_id=PLACEHOLDER, texts=(NOTE,), quote=QUOTE, issue_id="i1", act_messages=None, agreements=()):
    """[Master answer, Intervenor answer]: one valid issue on the draft (message `msg_id`) and one act per text."""
    master = prk.master_d(prk.issue_d(issue_id, msg_id, "unsupported_claim", quote), agreements=list(agreements))
    messages = [msg_id] if act_messages is None else list(act_messages)
    acts = [
        prk.act_d(t, issues=[issue_id], messages=messages, addressee="all", subject="none") for t in texts
    ]
    return [master, prk.interv_d(dispositions=[prk.disp_d(issue_id)], acts=acts)]


def quiet_master():
    """A Master answer with no issue at all (the Intervenor is then never called, as in a live run)."""
    return prk.master_d()


def declined_script(msg_id=PLACEHOLDER, quote=QUOTE):
    """A valid issue that the Intervenor declines to act on."""
    return [
        prk.master_d(prk.issue_d("i1", msg_id, "unsupported_claim", quote)),
        prk.interv_d("no_intervention", "Not worth a post.", [prk.disp_d("i1", "declined")], []),
    ]


# --- Scripts as functions of (world, id of the draft message), so one answer can drive a check and a live run ------------

ISSUE_QUOTE_1 = "rent control reduces the supply of housing"  # a phrase of the first message of SPECS


def concern(w, draft_id):
    return concern_script(draft_id)


def two_notes(w, draft_id):
    return concern_script(draft_id, texts=(NOTE, NOTE_2))


def one_bad_one_good(w, draft_id):
    return concern_script(draft_id, texts=("Participant B, please cite a source.", NOTE_2))


def all_acts_invalid(w, draft_id):
    return concern_script(draft_id, texts=("Participant B, please cite a source.", "The other participant is wrong."))


def old_repetition(w, draft_id):
    master = prk.master_d(prk.issue_d("r1", w[1], "repetition", ISSUE_QUOTE_1))
    act = prk.act_d(NOTE, issues=["r1"], messages=[w[1], draft_id], addressee="all", subject="none")
    return [master, prk.interv_d(dispositions=[prk.disp_d("r1")], acts=[act])]


def acts_capped(w, draft_id):
    return concern_script(draft_id, texts=(NOTE, NOTE_2, prk.CLEAN_TEXT_3))


def declined(w, draft_id):
    return declined_script(draft_id)


def quiet(w, draft_id):
    return [quiet_master()]


def run_live(fake, factory, who="B", text=DRAFT, specs=None):
    """A fresh live run on a message with this text, driven by the same scripted answers. Returns (world, client, run)."""
    w = with_draft(SPECS if specs is None else specs, who, text)
    client = fake(*factory(w, w.last.pk))
    _, stored = prk.go(prk.new_run(w.last))
    return w, client, stored


def provider_error(status=500, kind="api_error", message="upstream exploded"):
    from moderation.fake_llm import FakeProviderError

    return FakeProviderError(status, kind, message)


# --- Calling the code under test ---------------------------------------------------------------------------------------

def check(w, who, text=DRAFT, **kwargs):
    """`preview.check_draft` for participant `who`; returns (the returned check, the same row reloaded from the database)."""
    from moderation import preview
    from moderation.models import PreviewCheck

    returned = preview.check_draft(w.conv, w.parts[who], text, **kwargs)
    return returned, PreviewCheck.objects.get(pk=returned.pk)


def post(w, who, text):
    """The draft becomes a real message and a pending live run is made for it. Returns (message, run)."""
    message = w.add_user(who, text)
    return message, prk.new_run(message)


# --- Reading the database ----------------------------------------------------------------------------------------------

def counts():
    """Row counts of every table a check must leave alone."""
    from forum.models import Message
    from moderation.models import InterventionAct, Issue, IssueDisposition, ModerationRun

    return {
        "message": Message.objects.count(),
        "run": ModerationRun.objects.count(),
        "issue": Issue.objects.count(),
        "disposition": IssueDisposition.objects.count(),
        "act": InterventionAct.objects.count(),
    }


def ledger_of(conv):
    from moderation.models import LLMCall

    return list(LLMCall.objects.filter(conversation_id=conv.pk).order_by("pk"))


def ledger_ids(conv):
    return [row.pk for row in ledger_of(conv)]


def check_rows(conv=None):
    from moderation.models import PreviewCheck

    rows = PreviewCheck.objects.all() if conv is None else PreviewCheck.objects.filter(conversation=conv.pk)
    return list(rows.order_by("pk"))


def total_calls():
    from moderation.models import LLMCall

    return LLMCall.objects.count()


# --- Normalisation the contract names (CRLF to LF, NFC, strip) ---------------------------------------------------------

def normalise(text):
    return unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n")).strip()


def sha(text):
    return hashlib.sha256(normalise(text).encode("utf-8")).hexdigest()


# --- Fingerprints -------------------------------------------------------------------------------------------------------

def fingerprint(run):
    """Everything a run stores, with message ids replaced by seq_no (the ids differ between two worlds), so a reused run
    can be compared with a fresh run of the same outputs. `preview_check_id` is taken out of the config snapshot."""
    from forum.models import Message

    seq = {m.pk: m.seq_no for m in Message.objects.filter(conversation_id=run.conversation_id)}
    snapshot = {k: v for k, v in run.config_snapshot.items() if k != "preview_check_id"}
    posted = run.posted_message
    return {
        "status": run.status,
        "decision": run.decision,
        "rationale": run.rationale,
        "failure_reason": run.failure_reason,
        "error": run.error,
        "is_stale": run.is_stale,
        "attempts": run.attempts,
        "discussion_map": run.discussion_map,
        "snapshot": snapshot,
        "posted": None if posted is None else (posted.content, posted.seq_no, seq.get(posted.in_reply_to_id)),
        "issues": [
            (
                i.local_id, i.issue_type, i.dimension, i.quote, i.quote_start, i.quote_end, i.quote_match, i.explanation,
                i.confidence, i.intensity, i.validity, i.rejection_reason, seq[i.message_id],
                None if not hasattr(i, "disposition") else (i.disposition.disposition, i.disposition.reason),
            )
            for i in prk.issues_of(run)
        ],
        "acts": [
            (
                a.order, a.act_type, a.tone, a.text, a.addressee, a.subject, a.validity, a.rejection_reason, a.char_len,
                a.word_count, a.is_question, a.quotes_participant,
                sorted(i.local_id for i in a.source_issues.all()),
                sorted(seq[m.pk] for m in a.source_messages.all()),
            )
            for a in prk.acts_of(run)
        ],
    }


# --- Reading what the model was sent -----------------------------------------------------------------------------------

_SECONDS_FACT_RE = re.compile(r'<fact name="seconds_between_last_two_messages">[^<]*</fact>')


def canonical_input(call, w, *, draft_id=None, swap_labels=False):
    """A call's user input made comparable across worlds: message ids become m<seq_no> (the draft's placeholder, or its
    real id when it is a real message, becomes m<draft seq>), the topic title becomes T, the seconds fact is dropped and,
    with `swap_labels`, Participant A and Participant B trade places."""
    text = prk.user_input(call)
    for message in w.msgs:
        text = text.replace(f'id="{message.pk}"', f'id="m{message.seq_no}"')
        text = text.replace(f"<message_id>{message.pk}</message_id>", f"<message_id>m{message.seq_no}</message_id>")
    if draft_id is not None:
        text = text.replace(f'id="{PLACEHOLDER}"', f'id="m{len(w.msgs) + 1}"')
        text = text.replace(f"<message_id>{PLACEHOLDER}</message_id>", f"<message_id>m{len(w.msgs) + 1}</message_id>")
    text = text.replace(w.topic.title, "T")
    text = _SECONDS_FACT_RE.sub("", text)
    if swap_labels:
        text = text.replace("Participant A", "@@").replace("Participant B", "Participant A").replace("@@", "Participant B")
    return text


_MESSAGE_RE = re.compile(r'<message id="(-?\d+)" participant="([^"]*)">')


def rendered_ids(call):
    """[(message id, participant label)] of the transcript block, oldest first; unlike the pipeline kit's reader it accepts the
    placeholder id -1."""
    return [(int(i), label) for i, label in _MESSAGE_RE.findall(prk.user_input(call))]


def request_messages_text(fake_client):
    """Every user turn sent to the model (no system prompt), as one string."""
    return " ".join(prk.user_input(c) for c in fake_client.calls)
