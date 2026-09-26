"""Consensus over the raters' findings (plan section 9).

Within one target and one dimension, findings whose spans overlap enough are merged into one `ConsensusFinding`.
The result is deterministic and does not depend on the order in which raters or findings are given.

Rules (all thresholds come from the panel, whose defaults are the tunables `SPAN_MATCH_MIN_IOU` and
`INTENSITY_DISAGREEMENT_THRESHOLD`):

* Findings are sorted by (start, end, rater name, local id, intensity, reason) and merged greedily in that order. A
  finding joins an existing cluster when its IoU with the cluster's first (anchor) span is at least the threshold and
  the cluster has no finding by the same rater yet (one rater's separate phrases never merge with each other); if
  several clusters qualify, the one with the highest IoU wins and the earlier one breaks ties.
* The consensus span is the union of the merged spans (smallest start, largest end).
* `n_raters` counts the merged findings (one per rater). `intensity_mean` and `intensity_range` (largest minus
  smallest) are over the scorable findings only and are None when none is scorable.
* `needs_adjudication` is set when: the panel has more than one rater and only one found the phrase; the scorable
  intensities differ by `intensity_disagreement_threshold` or more; or some raters gave an intensity and others said
  "not scorable" for the phrase.
* `snap_to_sentences=True` first widens every span to the whole sentences it touches (a robustness check, plan section
  9). Sentences end at `.`, `!`, `?` or `…` (with any closing quotes or brackets) followed by whitespace or the end of
  the text, and at line breaks; abbreviations are not recognised.
"""

import re
from itertools import groupby

from django.db import transaction

from .models import ConsensusFinding, ConsensusFindingMember
from .targets import target_ref, target_text

_SENTENCE_END = re.compile(r"""[.!?…]+["'”’)\]]*(?=\s|$)|\n+""")


def iou(span_a, span_b):
    """Intersection over union of two (start, end) spans; 0.0 when they do not overlap."""
    (a_start, a_end), (b_start, b_end) = span_a, span_b
    intersection = max(0, min(a_end, b_end) - max(a_start, b_start))
    union = (a_end - a_start) + (b_end - b_start) - intersection
    return intersection / union if union > 0 else 0.0


def sentence_spans(text):
    """The (start, end) of every sentence of the text, without the whitespace around it."""
    pieces = []
    position = 0
    for match in _SENTENCE_END.finditer(text):
        pieces.append((position, match.end()))
        position = match.end()
    pieces.append((position, len(text)))
    spans = []
    for start, end in pieces:
        chunk = text[start:end]
        stripped = chunk.strip()
        if stripped:
            lead = len(chunk) - len(chunk.lstrip())
            spans.append((start + lead, start + lead + len(stripped)))
    return spans


def snap_span(text, start, end):
    """Widen (start, end) to the sentence boundaries: from the start of the first sentence the span touches to the end
    of the last one. A span that touches no sentence (only whitespace) is returned unchanged."""
    touched = [(s, e) for s, e in sentence_spans(text) if s < end and e > start]
    if not touched:
        return start, end
    return min(s for s, _ in touched), max(e for _, e in touched)


def _rater_name(rater):
    return str(getattr(rater, "name", rater))


class _Entry:
    __slots__ = ("dimension", "start", "end", "rater", "finding")

    def __init__(self, dimension, start, end, rater, finding):
        self.dimension, self.start, self.end, self.rater, self.finding = dimension, start, end, rater, finding

    @property
    def span(self):
        return self.start, self.end

    def sort_key(self):
        finding = self.finding
        intensity = finding.intensity
        return (
            self.start,
            self.end,
            self.rater,
            str(getattr(finding, "local_id", "")),
            intensity is None,
            intensity or 0,
            getattr(finding, "not_scorable_reason", "") or "",
        )


def _cluster(entries, threshold):
    """Greedy clustering of one dimension's entries (already sorted). Returns a list of lists of entries."""
    clusters = []
    for entry in entries:
        best, best_score = None, 0.0
        for cluster in clusters:
            if any(member.rater == entry.rater for member in cluster):
                continue
            score = iou(cluster[0].span, entry.span)
            if score >= threshold and score > best_score:
                best, best_score = cluster, score
        if best is None:
            clusters.append([entry])
        else:
            best.append(entry)
    return clusters


def _panel_size(panel, findings_by_rater):
    saved = panel.raters.count() if panel.pk is not None else 0
    return max(saved, len(findings_by_rater))


def build_consensus(panel, target, findings_by_rater, snap_to_sentences=False, save=False):
    """Merge the raters' findings on one target into consensus findings.

    `panel` supplies the thresholds and the number of raters; `target` is a Message or InterventionAct;
    `findings_by_rater` maps each rater (a Rater, or its name) to that rater's findings on the target, saved or not
    (a rater who found nothing may map to an empty list). Returns the consensus findings, sorted by dimension, start
    and end.

    By default they are returned UNSAVED: each has `panel`, `target_type`, `target_id`, `dimension`, `start`, `end`,
    `n_raters`, `intensity_mean`, `intensity_range` and `needs_adjudication` set, and an extra attribute
    `merged_findings` listing the findings it merges (a many-to-many link cannot exist before the row does). With
    `save=True` they are saved together with their links in one transaction; the panel and all findings must then be
    saved, and every finding must belong to a rating of this target.
    """
    target_type, target_id = target_ref(target)
    text = target_text(target)
    entries = []
    for rater, findings in findings_by_rater.items():
        name = _rater_name(rater)
        for finding in findings:
            start, end = finding.start, finding.end
            if snap_to_sentences:
                start, end = snap_span(text, start, end)
            entries.append(_Entry(finding.dimension, start, end, name, finding))

    multi_rater = _panel_size(panel, findings_by_rater) > 1
    results = []
    entries.sort(key=lambda e: (e.dimension,) + e.sort_key())
    for dimension, group in groupby(entries, key=lambda e: e.dimension):
        for cluster in _cluster(list(group), panel.span_match_min_iou):
            intensities = [e.finding.intensity for e in cluster if e.finding.intensity is not None]
            scorable_count = len(intensities)
            intensity_range = max(intensities) - min(intensities) if intensities else None
            needs = (
                (multi_rater and len(cluster) == 1)
                or (intensity_range is not None and intensity_range >= panel.intensity_disagreement_threshold)
                or (0 < scorable_count < len(cluster))
            )
            consensus = ConsensusFinding(
                panel=panel,
                target_type=target_type,
                target_id=target_id,
                dimension=dimension,
                start=min(e.start for e in cluster),
                end=max(e.end for e in cluster),
                n_raters=len(cluster),
                intensity_mean=sum(intensities) / scorable_count if intensities else None,
                intensity_range=intensity_range,
                needs_adjudication=bool(needs),
            )
            consensus.merged_findings = [e.finding for e in cluster]
            results.append(consensus)
    results.sort(key=lambda c: (c.dimension, c.start, c.end))

    if save:
        with transaction.atomic():
            for consensus in results:
                consensus.save()
                for finding in consensus.merged_findings:
                    ConsensusFindingMember.objects.create(consensus_finding=consensus, finding=finding)
    return results
