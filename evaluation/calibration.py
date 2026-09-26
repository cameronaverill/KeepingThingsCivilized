"""The calibration-set builder (plan section 9, "Calibration of the LLM panel against humans", step 1).

`build_calibration_set(name, seed, ...)` draws the sample of messages human raters label, stratified so that the
comparison is meaningful. For each dimension it draws `per_dimension` USER messages (never moderator messages) from
these strata, in the shares given by the tunable `CALIBRATION_STRATUM_SHARES`:

* `no_issue`: no planted phrase on the dimension and no valid Master issue on it;
* `planted_low` / `planted_high`: a planted phrase on the dimension (`Message.planted`), banded by the highest planted
  intensity (up to `CALIBRATION_LOW_INTENSITY_MAX` is low);
* `flagged_low` / `flagged_high`: no planted phrase, but a valid Master issue on the dimension with an intensity
  (`Issue.intensity`), banded by the highest such intensity. Their default share is 0 (a first calibration set is built
  before any Master run exists); strata with a zero quota are left out of the report. Messages whose only issues have no
  intensity fit no stratum and are not drawn.

Within a stratum the draw is balanced across sides: a message's side is its conversation's `variant` when that is
`left` or `right`, else `unknown`. Known sides are drawn alternately (in a seeded order) and `unknown` messages only
fill what the known sides cannot. A message is never drawn twice in one set, even for two dimensions (dimensions are
processed in the order given). Everything random comes from `seed`, so the same seed on the same data gives the same
set, in the same presentation order; the order is stored in `CalibrationItem.order` (1, 2, 3, ...).

`CalibrationSet.strata` records what was asked and achieved:
    {"per_dimension": n, "allow_short": bool, "total": items, "shortfall": missing items,
     "dimensions": {dimension: {stratum: {"requested": q, "achieved": a, "shortfall": q - a, "by_side": {side: n}}}}}
When a stratum cannot be filled, `CalibrationShortfall` is raised and nothing is saved, unless `allow_short=True`, in
which case the set is saved with what exists and the shortfall is recorded in `strata`.
"""

import random

from django.conf import settings
from django.db import transaction

from forum.models import Message
from moderation import taxonomy
from moderation.models import Issue

from .models import CalibrationItem, CalibrationSet

STRATA = ("no_issue", "planted_low", "planted_high", "flagged_low", "flagged_high")
KNOWN_SIDES = ("left", "right")
UNKNOWN_SIDE = "unknown"


class CalibrationShortfall(ValueError):
    """A stratum cannot be filled. `strata` holds the report that would have been stored."""

    def __init__(self, message, strata):
        super().__init__(message)
        self.strata = strata


def side_of(variant):
    """The side of a message from its conversation's variant (`left` or `right`), else `unknown`."""
    return variant if variant in KNOWN_SIDES else UNKNOWN_SIDE


def _quotas(total, shares):
    """Split `total` by `shares` (largest remainder, ties by share order) so that the parts add up to `total`."""
    if abs(sum(shares.values()) - 1) > 1e-9:
        raise ValueError("CALIBRATION_STRATUM_SHARES must add up to 1.")
    raw = {stratum: total * share for stratum, share in shares.items()}
    quotas = {stratum: int(value + 1e-9) for stratum, value in raw.items()}
    leftover = total - sum(quotas.values())
    by_remainder = sorted(shares, key=lambda s: (-(raw[s] - quotas[s]), list(shares).index(s)))
    for stratum in by_remainder[:leftover]:
        quotas[stratum] += 1
    return quotas


def _band(intensity):
    return "low" if intensity <= settings.CALIBRATION_LOW_INTENSITY_MAX else "high"


def _classify(dimension, messages, issue_intensities):
    """{stratum: [(message_id, side)]} for one dimension. `messages` is [(id, variant, planted)] sorted by id."""
    pools = {stratum: [] for stratum in STRATA}
    for message_id, variant, planted in messages:
        side = side_of(variant)
        planted_here = [p for p in (planted or []) if isinstance(p, dict) and p.get("dimension") == dimension]
        if planted_here:
            intensities = [p["intensity"] for p in planted_here if isinstance(p.get("intensity"), int)]
            if intensities:
                pools[f"planted_{_band(max(intensities))}"].append((message_id, side))
            continue
        if message_id in issue_intensities:
            intensities = [i for i in issue_intensities[message_id] if i is not None]
            if intensities:
                pools[f"flagged_{_band(max(intensities))}"].append((message_id, side))
            continue
        pools["no_issue"].append((message_id, side))
    return pools


def _draw(candidates, quota, rng, drawn):
    """Draw up to `quota` messages, alternating between the known sides, `unknown` only as filler."""
    by_side = {}
    for message_id, side in candidates:
        if message_id not in drawn:
            by_side.setdefault(side, []).append(message_id)
    for ids in by_side.values():
        rng.shuffle(ids)
    sides = [side for side in KNOWN_SIDES if side in by_side]
    rng.shuffle(sides)
    chosen = []
    while len(chosen) < quota and any(by_side[side] for side in sides):
        for side in sides:
            if len(chosen) < quota and by_side[side]:
                chosen.append((by_side[side].pop(), side))
    filler = by_side.get(UNKNOWN_SIDE, [])
    while len(chosen) < quota and filler:
        chosen.append((filler.pop(), UNKNOWN_SIDE))
    return chosen


def build_calibration_set(name, seed, per_dimension=None, dimensions=None, allow_short=False):
    """Draw and save a calibration set (see the module docstring). Returns the saved `CalibrationSet`."""
    if per_dimension is None:
        per_dimension = settings.CALIBRATION_ITEMS_PER_DIMENSION
    dimensions = tuple(taxonomy.DIMENSIONS) if dimensions is None else tuple(dimensions)
    if isinstance(per_dimension, bool) or not isinstance(per_dimension, int) or per_dimension < 1:
        raise ValueError("per_dimension must be a whole number of at least 1.")
    for dimension in dimensions:
        if dimension not in taxonomy.DIMENSIONS:
            raise ValueError(f"'{dimension}' is not a dimension; use one of {', '.join(taxonomy.DIMENSIONS)}.")
    if len(set(dimensions)) != len(dimensions):
        raise ValueError("A dimension may be listed only once.")
    if CalibrationSet.objects.filter(name=name).exists():
        raise ValueError(f"A calibration set named '{name}' already exists.")
    unknown = set(settings.CALIBRATION_STRATUM_SHARES) - set(STRATA)
    if unknown:
        raise ValueError(f"Unknown strata in CALIBRATION_STRATUM_SHARES: {sorted(unknown)}.")
    quotas = _quotas(per_dimension, settings.CALIBRATION_STRATUM_SHARES)

    messages = list(
        Message.objects.filter(author_type="user")
        .order_by("pk")
        .values_list("pk", "conversation__variant", "planted")
    )
    drawn = set()
    picked = []  # (dimension, stratum, message_id)
    report = {}
    for dimension in dimensions:
        issue_intensities = {}
        issues = Issue.objects.filter(validity="valid", dimension=dimension).values_list("message_id", "intensity")
        for message_id, intensity in issues:
            issue_intensities.setdefault(message_id, []).append(intensity)
        pools = _classify(dimension, messages, issue_intensities)
        report[dimension] = {}
        for stratum, quota in quotas.items():
            if quota == 0:
                continue
            rng = random.Random(f"{seed}:{dimension}:{stratum}")
            chosen = _draw(pools[stratum], quota, rng, drawn)
            by_side = {}
            for message_id, side in chosen:
                drawn.add(message_id)
                picked.append((dimension, stratum, message_id))
                by_side[side] = by_side.get(side, 0) + 1
            report[dimension][stratum] = {
                "requested": quota,
                "achieved": len(chosen),
                "shortfall": quota - len(chosen),
                "by_side": dict(sorted(by_side.items())),
            }

    shortfall = sum(entry["shortfall"] for strata in report.values() for entry in strata.values())
    strata = {
        "per_dimension": per_dimension,
        "allow_short": allow_short,
        "total": len(picked),
        "shortfall": shortfall,
        "dimensions": report,
    }
    if shortfall and not allow_short:
        raise CalibrationShortfall(
            f"The calibration set cannot be filled: {shortfall} messages are missing. Use allow_short=True to "
            "build the shorter set.",
            strata,
        )

    random.Random(f"{seed}:order").shuffle(picked)
    with transaction.atomic():
        calibration_set = CalibrationSet.objects.create(name=name, seed=seed, strata=strata)
        CalibrationItem.objects.bulk_create(
            [
                CalibrationItem(
                    set=calibration_set,
                    target_type="message",
                    target_id=message_id,
                    stratum=f"{dimension}:{stratum}",
                    order=position,
                )
                for position, (dimension, stratum, message_id) in enumerate(picked, start=1)
            ]
        )
    return calibration_set
