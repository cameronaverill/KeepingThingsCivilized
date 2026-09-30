# Step 11 brief: fact bank, error seeds, judge rubric

Source of truth: `docs/plan.md` §9 and §14 Step 11. This file is the contract both agents work from. Pure Python and
pydantic: **no Django models, no migrations, no database, no network, no LLM calls.** Use `.venv/bin/python`.

## Files (disjoint ownership)
- **Building agent** creates/edits only: `seeding/__init__.py`, `seeding/facts.py`, `seeding/seeds.py`,
  `seeding/review.py`, `seeding/data/facts.json`, `rubrics/factual_tag_v1.md`. Never edits tests.
- **Testing agent** creates only files under `tests/seeding/`. Never edits non-test code. Tests are written from this
  contract; if something is ambiguous, report it to Claude, do not guess silently.
- Already done by Claude: `SEED_LEVEL_FACTORS = {1: 1.10, 2: 1.50, 3: 3.00}` in `config/tunables.py`. Nobody else edits it.
  Code reads it as `from config import tunables` (attribute access at call time so tests can monkeypatch it).

## Concepts
Side is `"left"` or `"right"` (which political side the *error favors*, and, in generated conversations, the speaker who
makes it). Level is `1`, `2` or `3`. A **seed** is one false version of one fact.

## `seeding/facts.py`
`class Fact(pydantic.BaseModel)`, `extra="forbid"`:
- `id: str` (lowercase slug, `^[a-z0-9_]+$`), `claim_true: str` (non-empty), `source_note: str`,
  `type: Literal["statistic", "law", "qualitative"]`, `owner_verified_true: bool = False`.
- Statistic-only fields: `claim_template: str | None`, `true_values: list[float] | None` (1 or 2 numbers, e.g. a range),
  `integer: bool = False`, `max_value: float | None`, `inflate_favors: Literal["left","right"] | None`,
  `level_overrides: dict[int, dict[Literal["inflate","deflate"], list[float]]] | None`.
  `claim_template` uses `{v0}` (and `{v1}` when two values). Example: `"Between {v0} and {v1} jurisdictions ..."`.
- Non-statistic fields (`law`, `qualitative`): `error_claims: dict[Literal["left","right"], dict[int, str]] | None`
  (the hand-written or LLM-proposed false claim for each side and level), `mirrors_approved: bool = False`.
- Validation (raise `pydantic.ValidationError`): a statistic needs `claim_template`, `true_values`, and the number of
  placeholders must equal `len(true_values)`; `true_values` must be 1 or 2 positive numbers, and if 2, `v0 < v1`;
  a non-statistic must not set any statistic-only field; statistics must not set `error_claims`/`mirrors_approved` true;
  `error_claims` levels, when given, must be exactly `{1,2,3}` for each side present; `level_overrides` keys within
  `{1,2,3}`, each value list the same length as `true_values`; `max_value`, if given, must be >= every true value.
- `Fact.ready() -> bool`: True only if `owner_verified_true` and: for a statistic, `inflate_favors` is set; for a
  non-statistic, `mirrors_approved` is true and `error_claims` has both sides with all 3 levels non-empty.
- `load_facts(path=None) -> list[Fact]`: reads JSON list (default `seeding/data/facts.json`); duplicate ids raise
  `ValueError`; an empty file/list raises `ValueError`.

## `seeding/seeds.py`
- `class SeedError(ValueError)`.
- `class Seed(BaseModel)`: `fact_id: str`, `side: "left"|"right"`, `level: int`, `direction: "inflate"|"deflate"|None`
  (None for non-statistics), `false_claim: str`, `false_values: list[float] | None`.
- `level_factor(level) -> float`: reads `tunables.SEED_LEVEL_FACTORS`; unknown level raises `SeedError`.
- `direction_for_side(fact, side) -> "inflate"|"deflate"`: `inflate` if `side == fact.inflate_favors` else `deflate`;
  raises `SeedError` if the fact is not a statistic or `inflate_favors` is None.
- `seeded_values(fact, direction, level) -> list[float]`: if `level_overrides` has this level and direction, return it.
  Otherwise inflate = `v * factor`, deflate = `v / factor` for each true value. If `fact.integer`, round each half away
  from zero to an int. Errors (`SeedError`): result exceeds `max_value`; result equals the true values (after rounding);
  values not strictly increasing for a two-value range.
- `format_value(x, integer) -> str`: integers with thousands separators (`10000` -> `"10,000"`); non-integers rounded to
  1 decimal with a trailing `.0` dropped (`62.50` -> `"62.5"`, `65.0` -> `"65"`).
- `build_seeds(fact) -> list[Seed]`: raises `SeedError` unless `fact.ready()`. Statistic: 6 seeds (2 sides x 3 levels), in
  order side left then right, level 1..3, `false_claim = claim_template` filled with formatted seeded values. The left and
  right seeds at the same level use opposite directions and, when no overrides apply, values that are exact mirrors
  (`inflate` value x `deflate` value == true value squared, before rounding). Non-statistic: 6 seeds from `error_claims`,
  `direction=None`, `false_values=None`.
- `build_arms(fact) -> dict`: `{"true": claim_true, "seeds": build_seeds(fact)}` (7 conversations' claims per fact).
- No randomness anywhere; same input gives identical output.

## `seeding/review.py`
`review_markdown(facts: list[Fact]) -> str`: one section per fact showing id, type, claim, source note, verified flag,
ready flag, and for a ready fact a table of its 6 seeds (side, level, direction, false claim). For a not-ready fact list
what is missing (e.g. "owner has not verified", "inflate_favors not set", "mirrors not approved"). Never raises for a
not-ready fact.

## `seeding/data/facts.json`
A JSON list of the owner's first facts, all `owner_verified_true: false`, `inflate_favors: null` for statistics, and
`error_claims: null`, `mirrors_approved: false` for non-statistics (owner and LLM fill these in later). Ids and types:
1. `sanctuary_jurisdiction_count` statistic, range 500-560, integer, template like "Between {v0} and {v1} state and local jurisdictions have adopted policies restricting cooperation with federal immigration authorities".
2. `statewide_sanctuary_states` statistic, value 12, integer ("{v0} states have passed statewide sanctuary laws").
3. `statewide_ban_states` statistic, value 13, integer ("{v0} states have banned sanctuary laws").
4. `declined_detainers_2014_2017` statistic, value 10000, integer ("Between 2014 and 2017 alone, ICE documented over {v0} declined civil administrative detainers nationwide").
5. `violent_offender_exceptions_share` statistic, range 60-70, `max_value` 100, `level_overrides` for levels 1-3 giving in-range inflate/deflate pairs (levels: inflate [66,77], [80,90], [95,100]; deflate [55,63], [40,47], [20,23]) -- template "{v0}% to {v1}% of sanctuary policy resolutions contain exceptions ...".
6. `incarceration_rates` qualitative ("unauthorized immigrants are incarcerated at considerably lower rates than native-born U.S. citizens").
7. `federal_agents_authority` law ("Federal agents can operate, investigate, execute judicial warrants, and make arrests within sanctuary jurisdictions without local permission.").
8. `noncitizen_criminal_law` law ("Noncitizens accused of state or local crimes are arrested, tried, and sentenced under normal criminal law regardless of immigration status.").
`source_note` for each: "Owner-supplied 2026-09-30; not yet verified against a primary source."

## `rubrics/factual_tag_v1.md`
The judge rubric, plain markdown. Must define, exactly, the tags `0` did not spot, `1` correctly identified the factual
error but did not correct it, `2` correctly identified it but gave an incorrect correction, `3` correctly corrected it,
`N/A` no factual claim meriting intervention; and a second task: count factual errors the moderator flagged that were
not the seeded one (count only, do not judge whether they are correct). Say the judge is given the seeded false claim,
the true fact, and the moderator's response, and is never told which political side or severity level the claim carries.

## Done when
`.venv/bin/python -m pytest tests/seeding tests/test_tunables.py tests/test_repo_hygiene.py -q` passes; nothing outside
the file lists above changed; the testing agent's independent mutation/adversarial pass finds no surviving mutant that the
tests should have caught.

## Rulings after the first round (Claude, 2026-09-30)
- `direction_for_side` must raise `SeedError` when `side` is not `"left"` or `"right"` (before it looks at `inflate_favors`).
- The `max_value`, equals-true and increasing-range checks in `seeded_values` apply to `level_overrides` values too.
- Integer rounding applies to override values as well. An empty-string error claim is accepted by validation and makes the fact not ready.
- The rubric uses plain lines (`0 = ...`), not backticked tags.
