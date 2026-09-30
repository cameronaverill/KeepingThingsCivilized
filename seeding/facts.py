"""The fact bank for error seeding (docs/plan.md section 9, docs/step11_brief.md).

A fact is one true claim plus what is needed to make false versions of it. Statistics carry a template and numbers, so
`seeding.seeds` can scale them by level; laws and qualitative claims carry hand-written or LLM-proposed false claims.
A fact is only usable (`ready()`) once the owner has verified it and the side-specific choices are filled in.
"""
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

Side = Literal["left", "right"]
DEFAULT_PATH = Path(__file__).parent / "data" / "facts.json"
_PLACEHOLDER = re.compile(r"\{v(\d+)\}")
_LEVELS = {1, 2, 3}


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    claim_true: str
    source_note: str
    type: Literal["statistic", "law", "qualitative"]
    owner_verified_true: bool = False
    # statistic only
    claim_template: str | None = None
    true_values: list[float] | None = None
    integer: bool = False
    max_value: float | None = None
    inflate_favors: Side | None = None
    level_overrides: dict[int, dict[Literal["inflate", "deflate"], list[float]]] | None = None
    # law and qualitative only
    error_claims: dict[Side, dict[int, str]] | None = None
    mirrors_approved: bool = False

    @model_validator(mode="after")
    def _check(self):
        if not re.fullmatch(r"[a-z0-9_]+", self.id):
            raise ValueError("id must be a lowercase slug (a-z, 0-9, _)")
        if not self.claim_true.strip():
            raise ValueError("claim_true must not be empty")
        if self.type == "statistic":
            self._check_statistic()
        else:
            self._check_non_statistic()
        return self

    def _check_statistic(self):
        if self.error_claims is not None or self.mirrors_approved:
            raise ValueError("a statistic must not set error_claims or mirrors_approved")
        if not self.claim_template or not self.true_values:
            raise ValueError("a statistic needs claim_template and true_values")
        values = self.true_values
        if len(values) not in (1, 2) or any(v <= 0 for v in values):
            raise ValueError("true_values must be 1 or 2 positive numbers")
        if len(values) == 2 and not values[0] < values[1]:
            raise ValueError("a two-value range needs v0 < v1")
        found = sorted(int(n) for n in _PLACEHOLDER.findall(self.claim_template))
        if found != list(range(len(values))):
            raise ValueError("claim_template placeholders must be {v0} (and {v1}) matching true_values")
        if self.max_value is not None and self.max_value < max(values):
            raise ValueError("max_value must be >= every true value")
        for level, by_direction in (self.level_overrides or {}).items():
            if level not in _LEVELS:
                raise ValueError(f"level_overrides level {level} is not 1, 2 or 3")
            if any(len(v) != len(values) for v in by_direction.values()):
                raise ValueError("each level_overrides list must be as long as true_values")

    def _check_non_statistic(self):
        if (self.claim_template is not None or self.true_values is not None or self.integer
                or self.max_value is not None or self.inflate_favors is not None
                or self.level_overrides is not None):
            raise ValueError("only a statistic may set statistic fields")
        for side_claims in (self.error_claims or {}).values():
            if set(side_claims) != _LEVELS:
                raise ValueError("error_claims must cover exactly levels 1, 2 and 3 for each side")

    def ready(self) -> bool:
        if not self.owner_verified_true:
            return False
        if self.type == "statistic":
            return self.inflate_favors is not None
        if not self.mirrors_approved or not self.error_claims:
            return False
        return all(
            side in self.error_claims and all(self.error_claims[side].get(lv, "").strip() for lv in _LEVELS)
            for side in ("left", "right")
        )


def load_facts(path=None) -> list[Fact]:
    raw = json.loads(Path(path or DEFAULT_PATH).read_text(encoding="utf-8"))
    if not raw:
        raise ValueError("fact bank is empty")
    facts = [Fact.model_validate(item) for item in raw]
    ids = [f.id for f in facts]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"duplicate fact ids: {dupes}")
    return facts
