"""Match the Master's issues to the ground truth (plan section 9: detection is matching by span overlap).

An `Issue` (the Master's detection) and a `ConsensusFinding` (the panel's ground truth) are linked by an
`IssueFindingLink` when they are on the same message, have the same dimension, and their spans overlap with IoU at
least `min_iou` (default: the panel's `span_match_min_iou`). IoU is `evaluation.consensus.iou`.

What counts:

* Issues: `validity="valid"`, a non-empty dimension, on a **user** message (moderator messages are never matched; the
  model already forces issues on them to be rejected). Rejected issues are never linked and appear nowhere.
* An issue with `quote_match="not_found"` (no span) cannot be matched: it is skipped and listed in `not_found_issues`
  (whether or not its message is rated); it is never a false-positive candidate.
* Consensus findings: those of this panel on user messages (target type `message`). A finding of another panel, a
  finding on an intervention act or on a moderator message is ignored.
* A message that has no consensus findings of this panel at all is "unrated": its valid issues are listed once in
  `unrated_messages` and are not false-positive candidates. (A message the raters looked at and found clean also has no
  consensus findings, so it cannot be told from an unrated one here.)
* `conversation_ids=None` means every conversation; an empty list means none.

The report is computed from the spans, not from the links already in the database, so `save=False` and `save=True`
report the same. With `save=True` the missing links are created (existing links are left as they are, so re-running
adds nothing). `linked` counts every matching pair; `created` counts the ones that were new.

The querysets `unmatched_issues` and `unmatched_findings` are the database view of the same idea (they look at the
saved links, so they agree with the report after a `save=True` run at the same threshold).
"""

from dataclasses import asdict, dataclass, field

from django.db import transaction
from django.db.models import Exists, OuterRef

from forum.models import Message
from moderation.models import Issue

from .consensus import iou
from .models import ConsensusFinding, IssueFindingLink
from .targets import TARGET_MESSAGE


@dataclass
class MatchReport:
    """Plain, JSON-friendly result of `match_issues` (see `as_dict`)."""

    min_iou: float = 0.0
    saved: bool = False
    linked: int = 0
    created: int = 0
    # Valid issues on rated messages with no link: candidate false positives, as (issue_id, message_id, dimension).
    issues_unmatched: list = field(default_factory=list)
    # Consensus findings with no linked valid issue: candidate misses, as
    # (consensus_finding_id, message_id, dimension, intensity_mean).
    findings_unmatched: list = field(default_factory=list)
    # Message ids that have valid issues but no consensus findings of the panel.
    unrated_messages: list = field(default_factory=list)
    # Valid issues without a span, skipped (rated message or not): (issue_id, message_id, dimension).
    not_found_issues: list = field(default_factory=list)

    def as_dict(self):
        return asdict(self)


def _conversation_filter(conversation_ids):
    return {} if conversation_ids is None else {"message__conversation_id__in": list(conversation_ids)}


def _user_message_ids(conversation_ids):
    """A queryset of the ids of the user messages of the chosen conversations."""
    messages = Message.objects.filter(author_type="user")
    if conversation_ids is not None:
        messages = messages.filter(conversation_id__in=list(conversation_ids))
    return messages.values("pk")


def _candidate_issues(conversation_ids):
    """Valid issues with a dimension on user messages of the chosen conversations."""
    return (
        Issue.objects.filter(validity="valid", message__author_type="user", **_conversation_filter(conversation_ids))
        .exclude(dimension="")
    )


def _panel_findings(panel, conversation_ids):
    """The panel's consensus findings on user messages of the chosen conversations."""
    return ConsensusFinding.objects.filter(
        panel=panel, target_type=TARGET_MESSAGE, target_id__in=_user_message_ids(conversation_ids)
    )


def _resolve_min_iou(panel, min_iou):
    value = panel.span_match_min_iou if min_iou is None else min_iou
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 1:
        raise ValueError("min_iou must be above 0 and at most 1.")
    return value


def match_issues(panel, conversation_ids=None, *, min_iou=None, save=True):
    """Link the Master's valid issues to the panel's consensus findings; return a `MatchReport`."""
    threshold = _resolve_min_iou(panel, min_iou)
    report = MatchReport(min_iou=threshold, saved=bool(save))

    findings = list(_panel_findings(panel, conversation_ids).order_by("pk"))
    findings_by_message = {}
    for finding in findings:
        findings_by_message.setdefault(finding.target_id, []).append(finding)

    issues = list(_candidate_issues(conversation_ids).order_by("pk"))

    pairs = []  # (issue, finding, overlap)
    linked_issue_ids, linked_finding_ids = set(), set()
    unrated = set()
    for issue in issues:
        rated = findings_by_message.get(issue.message_id)
        if not rated:
            unrated.add(issue.message_id)
        if issue.quote_match == "not_found" or issue.quote_start is None or issue.quote_end is None:
            report.not_found_issues.append((issue.pk, issue.message_id, issue.dimension))
            continue
        if not rated:
            continue
        span = (issue.quote_start, issue.quote_end)
        matched = False
        for finding in rated:
            if finding.dimension != issue.dimension:
                continue
            overlap = iou(span, (finding.start, finding.end))
            if overlap > 0 and overlap >= threshold:
                pairs.append((issue, finding, overlap))
                linked_finding_ids.add(finding.pk)
                matched = True
        if matched:
            linked_issue_ids.add(issue.pk)
        else:
            report.issues_unmatched.append((issue.pk, issue.message_id, issue.dimension))

    report.unrated_messages = sorted(unrated)
    report.linked = len(pairs)
    report.findings_unmatched = [
        (f.pk, f.target_id, f.dimension, f.intensity_mean) for f in findings if f.pk not in linked_finding_ids
    ]

    if save and pairs:
        with transaction.atomic():
            existing = set(
                IssueFindingLink.objects.filter(
                    issue_id__in={i.pk for i, _, _ in pairs}, consensus_finding_id__in={f.pk for _, f, _ in pairs}
                ).values_list("issue_id", "consensus_finding_id")
            )
            new = [
                IssueFindingLink(issue=issue, consensus_finding=finding, overlap=overlap)
                for issue, finding, overlap in pairs
                if (issue.pk, finding.pk) not in existing
            ]
            IssueFindingLink.objects.bulk_create(new)
            report.created = len(new)
    return report


def unmatched_issues(panel, conversation_ids=None):
    """Valid issues (with a span and a dimension) on user messages the panel rated, with no link to a consensus finding
    of this panel: candidate false positives. Ordered by id."""
    rated_messages = _panel_findings(panel, conversation_ids).values("target_id")
    linked = IssueFindingLink.objects.filter(issue=OuterRef("pk"), consensus_finding__panel=panel)
    return (
        _candidate_issues(conversation_ids)
        .exclude(quote_match="not_found")
        .filter(message_id__in=rated_messages)
        .filter(~Exists(linked))
        .order_by("pk")
    )


def unmatched_findings(panel, conversation_ids=None):
    """The panel's consensus findings on user messages with no linked valid issue: candidate misses. Ordered by id."""
    linked = IssueFindingLink.objects.filter(consensus_finding=OuterRef("pk"), issue__validity="valid")
    return _panel_findings(panel, conversation_ids).filter(~Exists(linked)).order_by("pk")
