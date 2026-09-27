"""Helpers for tests/rater_matching (step 14b: evaluation/matching.py). Built only from docs/step14_brief.md, docs/plan.md
section 9 and docs/step12_brief.md. Nothing here calls build_consensus, the LLM gateway or the code under test except
`match`, `unmatched_issues` and `unmatched_findings`, which are thin wrappers that import lazily (a missing module fails
inside the test that needs it, not at collection).

THE HAND-BUILT WORLD (`build_world`). Every expected link and report in the tests was worked out by hand from these rows.
All texts are 300 characters long, so any span up to 300 is valid. Panel P has span_match_min_iou 0.5.

Conversation 1 (synthetic; users A and B, plus a moderator message), message M1 (user), dimension FA = factual_accuracy,
AB = abusiveness. Consensus findings of panel P on M1 (span, intensity_mean):
    c1  FA [0,10)     2.0        c5  AB [80,90)    4.0
    c2  FA [20,30)    3.0        c6  FA [100,110)  2.0
    c3  FA [40,50)    1.0 (adjudicated_intensity 3, which must not be what is reported)
    c4  FA [60,70)    None (not scorable)            c7  FA [110,120)  2.0
Master issues on M1 (all valid unless said), with the IoU against each finding of the same dimension worked out:
    i1  FA [0,10)     c1: 10/10 = 1.0                                      -> link c1 (1.0)
    i4  FA [0,10)     same span as i1 (several issues, one finding)         -> link c1 (1.0)
    i2  FA [20,25)    c2: inter 5, union 10 = 0.5 (exactly the threshold)  -> link c2 (0.5)
    i9  FA [15,35)    c2: inter 10, union 20 = 0.5                          -> link c2 (0.5)
    i3  FA [40,44)    c3: inter 4, union 10 = 0.4 (below)                   -> no link, candidate false positive
    i5  AB [80,90)    c5: 1.0                                               -> link c5 (1.0)
    i6  FA [80,90)    only c5 is there and it is AB (different dimension)  -> no link, candidate false positive
    i7  FA [60,70)    REJECTED, exact overlap with c4                        -> never linked, c4 stays a candidate miss
    i8  FA no span    valid, quote_match not_found                           -> skipped and reported, not a false positive
    i10 FA [100,120)  c6: inter 10, union 20 = 0.5; c7: 0.5 (one issue, two findings) -> links c6 (0.5) and c7 (0.5)
Message M2 (user, same conversation) has NO consensus finding: valid issues i11 (FA) and i12 (AB) on it are `unrated_messages`.
Conversation 2, message M3 (user): consensus finding c8 AB [0,10) 2.0 and no issue at all (a candidate miss).
Moderator message MM in conversation 1 is not part of the fixture (tests add it where they need it).

Expected (architect rulings): linked 7, created 7 on the first run, 7 links
    (i1,c1,1.0) (i4,c1,1.0) (i2,c2,0.5) (i9,c2,0.5) (i5,c5,1.0) (i10,c6,0.5) (i10,c7,0.5)
issues_unmatched   = [(i3, M1, FA), (i6, M1, FA)]
findings_unmatched = [(c3, M1, FA, 1.0), (c4, M1, FA, None), (c8, M3, AB, 2.0)]
unrated_messages   = [M2]
not_found_issues = [(i8, M1, FA)]
"""
import itertools
from types import SimpleNamespace

from django.contrib.auth import get_user_model

FA = "factual_accuracy"
AB = "abusiveness"
TEXT = "abcdefghij" * 30  # 300 characters
ISSUE_TYPE_OF_DIMENSION = {FA: "possible_factual_error", AB: "abusive_language", "": "unsupported_claim"}

_counter = itertools.count(1)

REPORT_FIELDS = (
    "min_iou", "saved", "linked", "created", "issues_unmatched", "findings_unmatched", "unrated_messages",
    "not_found_issues",
)  # fmt: skip


def n():
    return next(_counter)


# --- forum and moderation rows -----------------------------------------------------------------------------------------
def make_conversation(labels="AB", **extra):
    from forum.models import Conversation, Participant, Topic

    topic = extra.pop("topic", None) or Topic.objects.create(title=f"matchtopic {n()}", description="d", proposition="p")
    conv = Conversation.objects.create(topic=topic, source="synthetic", **extra)
    parts = {
        label: Participant.objects.create(conversation=conv, label=label, join_order=order)
        for order, label in enumerate(labels, start=1)
    }
    return conv, parts


def user_msg(conv, parts, label="A", content=TEXT, **extra):
    from forum.models import Message

    return Message.objects.create(
        conversation=conv, author_type="user", participant=parts[label], content=content, **extra
    )


def mod_msg(conv, content=TEXT):
    from forum.models import Message

    return Message.objects.create(conversation=conv, author_type="moderator", participant=None, content=content)


def make_run(trigger, **extra):
    from moderation.models import ModerationRun

    values = dict(conversation=trigger.conversation, trigger_message=trigger, snapshot_seq=trigger.seq_no, kind="live")
    values.update(extra)
    return ModerationRun.objects.create(**values)


def make_issue(run, message, dimension, start=None, end=None, validity="valid", quote_match="exact", **extra):
    """A Master issue on `message`. With start/end the quote is the message text at the span; without, the issue is a
    not_found issue (no offsets). A rejected issue gets a reason."""
    from moderation.models import Issue

    # Issue.dimension is derived from issue_type when the row is saved, so the issue type is chosen from the dimension;
    # "" (no dimension) uses unsupported_claim, an issue type that has none.
    values = dict(
        run=run, local_id=f"i{n()}", message=message, issue_type=ISSUE_TYPE_OF_DIMENSION[dimension],
        explanation="because", confidence=0.5, validity=validity,
    )  # fmt: skip
    if start is None:
        values.update(quote="not in the text", quote_match="not_found", quote_start=None, quote_end=None)
    else:
        values.update(quote=message.content[start:end], quote_match=quote_match, quote_start=start, quote_end=end)
    if validity == "rejected":
        values["rejection_reason"] = "quote_not_found"
    values.update(extra)
    return Issue.objects.create(**values)


def make_panel(name=None, version="1", **extra):
    from evaluation.models import Panel

    return Panel.objects.create(name=name or f"panel{n()}", version=version, **extra)


def make_cf(panel, message, dimension, start, end, mean=None, **extra):
    """A ConsensusFinding on a message, written directly (never built with build_consensus)."""
    from evaluation.models import ConsensusFinding

    values = dict(
        panel=panel, target_type="message", target_id=message.pk, dimension=dimension, start=start, end=end,
        n_raters=2, intensity_mean=mean,
    )  # fmt: skip
    values.update(extra)
    return ConsensusFinding.objects.create(**values)


def make_link(issue, consensus_finding, overlap=1.0):
    from evaluation.models import IssueFindingLink

    return IssueFindingLink.objects.create(issue=issue, consensus_finding=consensus_finding, overlap=overlap)


def simple_world(threshold=0.5, text=TEXT):
    """One conversation, one user message, one panel; a helper for the small focused tests. Returns a namespace with
    conv, parts, message, run, panel. The run is triggered by the message, so issues on it are valid."""
    conv, parts = make_conversation()
    message = user_msg(conv, parts, "A", text)
    panel = make_panel(span_match_min_iou=threshold)
    return SimpleNamespace(conv=conv, parts=parts, message=message, run=make_run(message), panel=panel)


# --- the code under test -----------------------------------------------------------------------------------------------
def match(panel, *args, **kwargs):
    from evaluation import matching

    return matching.match_issues(panel, *args, **kwargs)


def unmatched_issues(panel, *args, **kwargs):
    from evaluation import matching

    return matching.unmatched_issues(panel, *args, **kwargs)


def unmatched_findings(panel, *args, **kwargs):
    from evaluation import matching

    return matching.unmatched_findings(panel, *args, **kwargs)


# --- reading the results -----------------------------------------------------------------------------------------------
def links(panel=None):
    """The stored links as a set of (issue_id, consensus_finding_id, overlap), optionally only those of one panel."""
    from evaluation.models import IssueFindingLink

    rows = IssueFindingLink.objects.all()
    if panel is not None:
        rows = rows.filter(consensus_finding__panel=panel)
    return set(rows.values_list("issue_id", "consensus_finding_id", "overlap"))


def link_count():
    from evaluation.models import IssueFindingLink

    return IssueFindingLink.objects.count()


def rows(items):
    """A report list as a list of tuples sorted by its first element (ids), so element order and list-vs-tuple do not matter."""
    return sorted((tuple(item) for item in items), key=lambda item: item[0])


def pks(queryset):
    return sorted(queryset.values_list("pk", flat=True))


# --- the hand-built world ----------------------------------------------------------------------------------------------
def build_world():
    conv1, parts1 = make_conversation("AB")
    m1 = user_msg(conv1, parts1, "A")
    m2 = user_msg(conv1, parts1, "B")
    last = user_msg(conv1, parts1, "A")  # the run is triggered by the last message so that every issue is on or before it
    conv2, parts2 = make_conversation("AB")
    m3 = user_msg(conv2, parts2, "A")
    panel = make_panel(span_match_min_iou=0.5)
    run1, run2 = make_run(last), make_run(m3)

    c1 = make_cf(panel, m1, FA, 0, 10, 2.0)
    c2 = make_cf(panel, m1, FA, 20, 30, 3.0)
    c3 = make_cf(panel, m1, FA, 40, 50, 1.0, adjudicated_intensity=3, needs_adjudication=True)
    c4 = make_cf(panel, m1, FA, 60, 70, None)
    c5 = make_cf(panel, m1, AB, 80, 90, 4.0)
    c6 = make_cf(panel, m1, FA, 100, 110, 2.0)
    c7 = make_cf(panel, m1, FA, 110, 120, 2.0)
    c8 = make_cf(panel, m3, AB, 0, 10, 2.0)

    i1 = make_issue(run1, m1, FA, 0, 10)
    i2 = make_issue(run1, m1, FA, 20, 25)
    i3 = make_issue(run1, m1, FA, 40, 44)
    i4 = make_issue(run1, m1, FA, 0, 10)
    i5 = make_issue(run1, m1, AB, 80, 90)
    i6 = make_issue(run1, m1, FA, 80, 90)
    i7 = make_issue(run1, m1, FA, 60, 70, validity="rejected")
    i8 = make_issue(run1, m1, FA)
    i9 = make_issue(run1, m1, FA, 15, 35)
    i10 = make_issue(run1, m1, FA, 100, 120)
    i11 = make_issue(run1, m2, FA, 0, 10)
    i12 = make_issue(run1, m2, AB, 0, 10)
    return SimpleNamespace(
        conv1=conv1, conv2=conv2, m1=m1, m2=m2, m3=m3, panel=panel, run1=run1, run2=run2,
        c1=c1, c2=c2, c3=c3, c4=c4, c5=c5, c6=c6, c7=c7, c8=c8,
        i1=i1, i2=i2, i3=i3, i4=i4, i5=i5, i6=i6, i7=i7, i8=i8, i9=i9, i10=i10, i11=i11, i12=i12,
    )  # fmt: skip


def world_links(w):
    """The seven links worked out by hand in the module docstring."""
    return {
        (w.i1.pk, w.c1.pk, 1.0),
        (w.i4.pk, w.c1.pk, 1.0),
        (w.i2.pk, w.c2.pk, 0.5),
        (w.i9.pk, w.c2.pk, 0.5),
        (w.i5.pk, w.c5.pk, 1.0),
        (w.i10.pk, w.c6.pk, 0.5),
        (w.i10.pk, w.c7.pk, 0.5),
    }


def world_issues_unmatched(w):
    return [(w.i3.pk, w.m1.pk, FA), (w.i6.pk, w.m1.pk, FA)]


def world_findings_unmatched(w):
    return [(w.c3.pk, w.m1.pk, FA, 1.0), (w.c4.pk, w.m1.pk, FA, None), (w.c8.pk, w.m3.pk, AB, 2.0)]


def world_not_found(w):
    return [(w.i8.pk, w.m1.pk, FA)]
