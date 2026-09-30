"""A markdown page for the owner to review the fact bank (docs/plan.md section 9, docs/step11_brief.md)."""
from seeding.facts import Fact
from seeding.seeds import SeedError, build_seeds


def _missing(fact: Fact) -> list[str]:
    out = []
    if not fact.owner_verified_true:
        out.append("owner has not verified")
    if fact.type == "statistic":
        if fact.inflate_favors is None:
            out.append("inflate_favors not set")
    else:
        if not fact.mirrors_approved:
            out.append("mirrors not approved")
        claims = fact.error_claims or {}
        for side in ("left", "right"):
            for level in (1, 2, 3):
                if not claims.get(side, {}).get(level, "").strip():
                    out.append(f"error claim missing for {side} level {level}")
    return out


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def review_markdown(facts: list[Fact]) -> str:
    lines = ["# Fact bank review", ""]
    for fact in facts:
        ready = fact.ready()
        lines += [
            f"## {fact.id}", "",
            f"- Type: {fact.type}",
            f"- Claim: {fact.claim_true}",
            f"- Source note: {fact.source_note}",
            f"- Owner verified: {'yes' if fact.owner_verified_true else 'no'}",
            f"- Ready: {'yes' if ready else 'no'}", "",
        ]
        if ready:
            try:
                seeds = build_seeds(fact)
            except SeedError as e:
                lines += [f"Seeds cannot be built: {e}", ""]
                continue
            lines += ["| Side | Level | Direction | False claim |", "| --- | --- | --- | --- |"]
            lines += [f"| {s.side} | {s.level} | {s.direction or '-'} | {_cell(s.false_claim)} |" for s in seeds]
            lines.append("")
        else:
            lines += ["Missing:"] + [f"- {m}" for m in _missing(fact)] + [""]
    return "\n".join(lines)
