"""Turn a ready fact into its six false versions (docs/plan.md section 9, docs/step11_brief.md).

Pure and deterministic. For a statistic the left and right seeds at the same level go in opposite directions by the same
factor, so their sizes mirror each other; `level_overrides` replace the computed numbers where a plain scale would be
impossible (for example a percentage that would pass 100).
"""
import math
from typing import Literal

from pydantic import BaseModel

from config import tunables
from seeding.facts import Fact

SIDES = ("left", "right")
LEVELS = (1, 2, 3)


class SeedError(ValueError):
    pass


class Seed(BaseModel):
    fact_id: str
    side: Literal["left", "right"]
    level: int
    direction: Literal["inflate", "deflate"] | None
    false_claim: str
    false_values: list[float] | None


def level_factor(level: int) -> float:
    try:
        return tunables.SEED_LEVEL_FACTORS[level]
    except KeyError:
        raise SeedError(f"unknown seed level {level!r}") from None


def direction_for_side(fact: Fact, side: str) -> str:
    if side not in SIDES:
        raise SeedError(f"unknown side {side!r}")
    if fact.type != "statistic" or fact.inflate_favors is None:
        raise SeedError(f"fact {fact.id} has no inflate_favors")
    return "inflate" if side == fact.inflate_favors else "deflate"


def _round_half_away(x: float) -> int:
    return int(math.copysign(math.floor(abs(x) + 0.5), x))


def seeded_values(fact: Fact, direction: str, level: int) -> list[float]:
    override = (fact.level_overrides or {}).get(level, {}).get(direction)
    if override is not None:
        values = list(override)
    else:
        factor = level_factor(level)
        values = [v * factor if direction == "inflate" else v / factor for v in fact.true_values]
    if fact.integer:
        values = [_round_half_away(round(v, 9)) for v in values]
    if fact.max_value is not None and any(v > fact.max_value for v in values):
        raise SeedError(f"{fact.id}: level {level} {direction} exceeds max_value")
    if values == list(fact.true_values):
        raise SeedError(f"{fact.id}: level {level} {direction} equals the true values")
    if len(values) == 2 and not values[0] < values[1]:
        raise SeedError(f"{fact.id}: level {level} {direction} is not an increasing range")
    return values


def format_value(x: float, integer: bool) -> str:
    if integer:
        return f"{_round_half_away(x):,}"
    text = f"{x:.1f}"
    return text[:-2] if text.endswith(".0") else text


def build_seeds(fact: Fact) -> list[Seed]:
    if not fact.ready():
        raise SeedError(f"fact {fact.id} is not ready")
    seeds = []
    for side in SIDES:
        for level in LEVELS:
            if fact.type == "statistic":
                direction = direction_for_side(fact, side)
                values = seeded_values(fact, direction, level)
                fills = {f"v{i}": format_value(v, fact.integer) for i, v in enumerate(values)}
                claim = fact.claim_template.format(**fills)
            else:
                direction, values, claim = None, None, fact.error_claims[side][level]
            seeds.append(Seed(fact_id=fact.id, side=side, level=level, direction=direction,
                              false_claim=claim, false_values=values))
    return seeds


def build_arms(fact: Fact) -> dict:
    return {"true": fact.claim_true, "seeds": build_seeds(fact)}
