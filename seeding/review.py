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
            if not claims.get(side, "").strip():
                out.append(f"error claim missing for {side}")
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
            f"- Ready: {'yes' if ready else 'no'}",
        ]
        if fact.subject is not None:
            lines.append(f"- Subject: {fact.subject}")
        if fact.framing is not None:
            lines.append(f"- Framing: {fact.framing}")
        lines.append("")
        if not ready:
            lines += ["Missing:"] + [f"- {m}" for m in _missing(fact)] + [""]
        try:
            seeds = build_seeds(fact, require_ready=ready)
        except SeedError as e:
            if ready:
                lines += [f"Seeds cannot be built: {e}", ""]
            continue
        if not ready:
            lines += ["Draft seeds (not ready)", ""]
        lines += ["| Side | Level | Direction | False claim |", "| --- | --- | --- | --- |"]
        lines += [f"| {s.side} | {'-' if s.level is None else s.level} | {s.direction or '-'} | {_cell(s.false_claim)} |" for s in seeds]
        lines.append("")
    return "\n".join(lines)
