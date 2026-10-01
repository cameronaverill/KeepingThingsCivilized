"""Pilot analysis of the seeded-error judgments (docs/step14_judge_brief.md). Pure Python: no Django, no database.

Rows are the dicts of `generated/judgments/<experiment>.jsonl`. Every rate carries its numerator and denominator
(`{"num", "den", "rate"}`, rate None when den is 0). No p-values: n is tiny in this version.
"""
import json
from pathlib import Path

METRICS = ("intervention", "correct", "detection")
METRIC_NAMES = {"intervention": "intervened", "correct": "correct correction (tag 3)", "detection": "detected (tag 1-3)"}


def load_judgments(path) -> list[dict]:
    path = Path(path)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def rate(num, den):
    return {"num": num, "den": den, "rate": (num / den) if den else None}


def _label(row):
    return str(row["level"]) if row.get("level") is not None else "err"


def _error_metrics(rows):
    return {
        "intervention": rate(sum(1 for r in rows if r.get("intervened")), len(rows)),
        "correct": rate(sum(1 for r in rows if r.get("tag") == "3"), len(rows)),
        "detection": rate(sum(1 for r in rows if r.get("tag") in ("1", "2", "3")), len(rows)),
    }


def _true_metrics(rows):
    total = sum(r.get("unseeded_flagged") or 0 for r in rows)
    return {
        "intervention": rate(sum(1 for r in rows if r.get("intervened")), len(rows)),
        "unseeded_flagged": {"sum": total, "den": len(rows), "mean": (total / len(rows)) if rows else None},
    }


def _mean_words(rows):
    total = sum(r.get("response_words") or 0 for r in rows)
    return {"sum": total, "den": len(rows), "mean": (total / len(rows)) if rows else None}


def _diff(left, right):
    a, b = left["rate"], right["rate"]
    return {"left": left, "right": right, "diff": (a - b) if a is not None and b is not None else None}


def summarize(rows) -> dict:
    rows = list(rows)
    err = [r for r in rows if r.get("is_error_arm")]
    true = [r for r in rows if not r.get("is_error_arm")]
    labels = sorted({_label(r) for r in err}, key=lambda s: (s == "err", s))
    sides = ("left", "right")

    by_side_level = {s: {lab: _error_metrics([r for r in err if r["side"] == s and _label(r) == lab]) for lab in labels}
                     for s in sides}
    by_fact = {f: _error_metrics([r for r in err if r["fact_id"] == f]) for f in sorted({r["fact_id"] for r in err})}
    lmr = {lab: {m: _diff(by_side_level["left"][lab][m], by_side_level["right"][lab][m]) for m in METRICS} for lab in labels}
    true_by_side = {s: _true_metrics([r for r in true if r["side"] == s]) for s in sides}
    true_lmr = {"intervention": _diff(true_by_side["left"]["intervention"], true_by_side["right"]["intervention"])}
    words = {s: {lab: _mean_words([r for r in err if r["side"] == s and _label(r) == lab]) for lab in labels} for s in sides}
    words["true"] = {s: _mean_words([r for r in true if r["side"] == s]) for s in sides}
    return {
        "counts": {"rows": len(rows), "error_arms": len(err), "true_arms": len(true),
                   "facts": len({r["fact_id"] for r in rows}),
                   "assignments": sorted({r.get("assignment", "as-is") for r in rows}),
                   "no_response": sum(1 for r in rows if not r.get("intervened"))},
        "levels": labels,
        "error": {"overall": _error_metrics(err), "by_side_level": by_side_level, "by_fact": by_fact,
                  "left_minus_right": lmr},
        "true": {"overall": _true_metrics(true), "by_side": true_by_side, "left_minus_right": true_lmr},
        "words": words,
    }


# --- Markdown ---------------------------------------------------------------------------------------------------------

def _pct(r):
    return "n/a (0/0)" if r["rate"] is None else f"{r['rate'] * 100:.0f}% ({r['num']}/{r['den']})"


def _pp(d):
    return "n/a" if d is None else f"{d * 100:+.0f} pts"


def _mean(m):
    return "n/a" if m["mean"] is None else f"{m['mean']:.1f} (n={m['den']})"


def _table(header, body):
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in body]
    return "\n".join(lines)


def largest_differences(summary, top=5):
    """(size, sentence) for each left-minus-right difference on the error arms, largest absolute first."""
    found = []
    for lab, metrics in summary["error"]["left_minus_right"].items():
        for m in METRICS:
            d = metrics[m]
            if d["diff"] is None:
                continue
            where = "the non-statistic error arm" if lab == "err" else f"level {lab}"
            direction = "higher for the left-coded error" if d["diff"] > 0 else "higher for the right-coded error"
            if d["diff"] == 0:
                direction = "equal on both sides"
            found.append((abs(d["diff"]), f"At {where}, the {METRIC_NAMES[m]} rate is {direction}: left {_pct(d['left'])}, "
                                          f"right {_pct(d['right'])} (difference {_pp(d['diff'])})."))
    found.sort(key=lambda x: -x[0])
    return found[:top]


def render_markdown(summary) -> str:
    c = summary["counts"]
    out = [f"# PRELIMINARY: n = {c['rows']} judged conversations ({c['error_arms']} error arms, {c['true_arms']} true arms, "
           f"{c['facts']} facts)", "",
           "Pilot numbers only: unaudited transcripts, a single run, no controls, no significance tests. "
           f"Assignments: {', '.join(c['assignments']) or 'none'}. Conversations with no moderator response: {c['no_response']}.", ""]
    err = summary["error"]
    labs = summary["levels"]
    out += ["## Error arms: overall", "",
            _table(["measure", "rate"], [[METRIC_NAMES[m], _pct(err["overall"][m])] for m in METRICS]), ""]
    out += ["## Error arms by side and level", ""]
    body = []
    for lab in labs:
        for s in ("left", "right"):
            m = err["by_side_level"][s][lab]
            body.append([f"level {lab}" if lab != "err" else "err (non-statistic)", s] + [_pct(m[k]) for k in METRICS])
    out += [_table(["level", "side", "intervened", "correct (3)", "detected (1-3)"], body), ""]
    out += ["## Left minus right, error arms", "",
            _table(["level", "intervened", "correct (3)", "detected (1-3)"],
                   [[lab if lab != "err" else "err"] + [_pp(err["left_minus_right"][lab][k]["diff"]) for k in METRICS]
                    for lab in labs]), ""]
    out += ["## Error arms by fact", "",
            _table(["fact", "intervened", "correct (3)", "detected (1-3)"],
                   [[f] + [_pct(m[k]) for k in METRICS] for f, m in err["by_fact"].items()]), ""]
    t = summary["true"]
    out += ["## True arms (no error planted)", "",
            _table(["side", "intervened", "mean unseeded flags"],
                   [[s, _pct(t["by_side"][s]["intervention"]), _mean(t["by_side"][s]["unseeded_flagged"])]
                    for s in ("left", "right")]
                   + [["all", _pct(t["overall"]["intervention"]), _mean(t["overall"]["unseeded_flagged"])]]), ""]
    out += ["## Mean response words (error arms, by side and level)", "",
            _table(["level", "left", "right"],
                   [[lab, _mean(summary["words"]["left"][lab]), _mean(summary["words"]["right"][lab])] for lab in labs]), ""]
    out += ["## Largest left-right differences", ""]
    diffs = largest_differences(summary)
    out += [f"- {s}" for _, s in diffs] if diffs else ["- None computable."]
    out += ["", "Caveat: these are pilot numbers from unaudited transcripts, a single run and no controls; treat them as a "
                "check that the pipeline works, not as findings."]
    return "\n".join(out) + "\n"
