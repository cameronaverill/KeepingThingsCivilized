"""Helpers for tests/seeding, built only from docs/step11_brief.md. Factories return validated Fact objects.

HAND-WORKED NUMBERS (factors 1.10 / 1.50 / 3.00), used across the test files:
  integer range fact, true [500, 560]:
    inflate  L1 [550, 616]   L2 [750, 840]   L3 [1500, 1680]
    deflate  L1 [455, 509]   L2 [333, 373]   L3 [167, 187]      (500/1.1 = 454.5 -> 455, 560/1.1 = 509.09 -> 509, ...)
  non-integer single fact, true [100]:
    inflate  L1 110          L2 150          L3 300
    deflate  L1 90.909..     L2 66.666..     L3 33.333..        (formatted "90.9", "66.7", "33.3")
"""
from seeding.facts import Fact

LEVELS = (1, 2, 3)


def stat_kwargs(**over):
    base = dict(
        id="stat_fact",
        claim_true="About 100 things exist",
        source_note="note",
        type="statistic",
        claim_template="About {v0} things exist",
        true_values=[100],
    )
    base.update(over)
    return base


def stat(**over):
    return Fact(**stat_kwargs(**over))


def ready_stat(**over):
    over.setdefault("owner_verified_true", True)
    over.setdefault("inflate_favors", "left")
    return stat(**over)


def range_kwargs(**over):
    base = stat_kwargs(
        id="range_fact",
        claim_true="Between 500 and 560 things exist",
        claim_template="Between {v0} and {v1} things exist",
        true_values=[500, 560],
        integer=True,
    )
    base.update(over)
    return base


def ready_range(**over):
    over.setdefault("owner_verified_true", True)
    over.setdefault("inflate_favors", "left")
    return Fact(**range_kwargs(**over))


def claims(prefix="claim"):
    return {side: f"{prefix} {side}" for side in ("left", "right")}


def nonstat_kwargs(kind="law", **over):
    base = dict(id=f"{kind}_fact", claim_true="The true claim", source_note="note", type=kind)
    base.update(over)
    return base


def nonstat(kind="law", **over):
    return Fact(**nonstat_kwargs(kind, **over))


def ready_nonstat(kind="law", **over):
    over.setdefault("owner_verified_true", True)
    over.setdefault("mirrors_approved", True)
    over.setdefault("error_claims", claims())
    return nonstat(kind, **over)
