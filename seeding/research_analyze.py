"""Analysis of the judged research notes (docs/step15_research_eval_brief.md). Pure Python: no Django, no database.

Rows are the dicts of `generated/judgments/research_<experiment>.jsonl`. Every rate carries its numerator and denominator
(`{"num", "den", "rate"}`, rate None when den is 0). No p-values.
"""

from seeding.analyze import load_judgments, rate  # noqa: F401  (load_judgments re-exported for the command)

SIDES = ("left", "right")
ERR_METRICS = ("correct", "disputes")
ERR_METRIC_NAMES = {"correct": "correct note (tag 3)", "disputes": "disputes the claim (verdict)"}
TRUE_VERDICTS = ("confirms_claim", "disputes_claim", "unclear")
CAVEAT = ("Caveat: unaudited transcripts, a single run, live web search varies from run to run, no controls and no "
          "significance tests; treat these as a check that the pipeline works, not as findings.")


def _label(row):
    return str(row["level"]) if row.get("level") is not None else "err"


def _mean(values):
    values = [v for v in values if v is not None]
    return {"sum": sum(values), "den": len(values), "mean": (sum(values) / len(values)) if values else None}


def _diff(left, right):
    a, b = left["rate"], right["rate"]
    return {"left": left, "right": right, "diff": (a - b) if a is not None and b is not None else None}


def _error_metrics(rows):
    n = len(rows)
    return {
        "correct": rate(sum(1 for r in rows if r.get("tag") == "3"), n),
        "disputes": rate(sum(1 for r in rows if r.get("verdict") == "disputes_claim"), n),
        "words": _mean([r.get("note_words") for r in rows]),
        "sources": _mean([r.get("n_sources") for r in rows]),
        "confidence": _mean([r.get("confidence") for r in rows]),
    }


def _true_metrics(rows):
    n = len(rows)
    return {v: rate(sum(1 for r in rows if r.get("verdict") == v), n) for v in TRUE_VERDICTS}


def summarize(rows) -> dict:
    rows = list(rows)
    err = [r for r in rows if r.get("is_error_arm")]
    true = [r for r in rows if not r.get("is_error_arm")]
    labels = sorted({_label(r) for r in err}, key=lambda s: (s == "err", s))
    by_side_level = {s: {lab: _error_metrics([r for r in err if r["side"] == s and _label(r) == lab]) for lab in labels}
                     for s in SIDES}
    by_fact = {f: _error_metrics([r for r in err if r["fact_id"] == f]) for f in sorted({r["fact_id"] for r in err})}
    lmr = {lab: {m: _diff(by_side_level["left"][lab][m], by_side_level["right"][lab][m]) for m in ERR_METRICS}
           for lab in labels}
    true_by_side = {s: _true_metrics([r for r in true if r["side"] == s]) for s in SIDES}
    true_lmr = {v: _diff(true_by_side["left"][v], true_by_side["right"][v]) for v in TRUE_VERDICTS}
    return {
        "counts": {"rows": len(rows), "error_arms": len(err), "true_arms": len(true),
                   "facts": len({r["fact_id"] for r in rows})},
        "levels": labels,
        "error": {"overall": _error_metrics(err), "by_side_level": by_side_level, "by_fact": by_fact,
                  "left_minus_right": lmr},
        "true": {"overall": _true_metrics(true), "by_side": true_by_side, "left_minus_right": true_lmr},
    }


# --- Markdown ---------------------------------------------------------------------------------------------------------

def _pct(r):
    return "n/a (0/0)" if r["rate"] is None else f"{r['rate'] * 100:.0f}% ({r['num']}/{r['den']})"


def _pp(d):
    return "n/a" if d is None else f"{d * 100:+.0f} pts"


def _m(m, digits=1):
    return "n/a" if m["mean"] is None else f"{m['mean']:.{digits}f} (n={m['den']})"


def _table(header, body):
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in body]
    return "\n".join(lines)


def _lab(lab):
    return "err (non-statistic)" if lab == "err" else f"level {lab}"


def render_markdown(summary) -> str:
    c = summary["counts"]
    err, true, labs = summary["error"], summary["true"], summary["levels"]
    out = [f"# PRELIMINARY: n = {c['rows']} judged research notes ({c['error_arms']} error arms, {c['true_arms']} true arms, "
           f"{c['facts']} facts)", "", CAVEAT, ""]
    o = err["overall"]
    out += ["## Error arms: overall", "",
            _table(["measure", "value"], [
                [ERR_METRIC_NAMES["correct"], _pct(o["correct"])], [ERR_METRIC_NAMES["disputes"], _pct(o["disputes"])],
                ["mean note words", _m(o["words"])], ["mean sources", _m(o["sources"])],
                ["mean confidence", _m(o["confidence"], 2)]]), ""]
    body = []
    for lab in labs:
        for s in SIDES:
            m = err["by_side_level"][s][lab]
            body.append([_lab(lab), s, _pct(m["correct"]), _pct(m["disputes"]), _m(m["words"]), _m(m["sources"]),
                         _m(m["confidence"], 2)])
    out += ["## Error arms by side and level", "",
            _table(["level", "side", "correct (3)", "disputes", "words", "sources", "confidence"], body), ""]
    out += ["## Left minus right, error arms", "",
            _table(["level", "correct (3)", "disputes"],
                   [[_lab(lab)] + [_pp(err["left_minus_right"][lab][k]["diff"]) for k in ERR_METRICS] for lab in labs]), ""]
    out += ["## Error arms by fact", "",
            _table(["fact", "correct (3)", "disputes", "words", "sources", "confidence"],
                   [[f, _pct(m["correct"]), _pct(m["disputes"]), _m(m["words"]), _m(m["sources"]), _m(m["confidence"], 2)]
                    for f, m in err["by_fact"].items()]), ""]
    out += ["## True arms (no error planted): what the note says about the true claim", "",
            _table(["side"] + list(TRUE_VERDICTS),
                   [[s] + [_pct(true["by_side"][s][v]) for v in TRUE_VERDICTS] for s in SIDES]
                   + [["all"] + [_pct(true["overall"][v]) for v in TRUE_VERDICTS]]), "",
            "Left minus right: " + ", ".join(f"{v} {_pp(true['left_minus_right'][v]['diff'])}" for v in TRUE_VERDICTS), ""]
    out += [CAVEAT]
    return "\n".join(out) + "\n"
