"""Shared fixture and helpers for the step 9b tests (tests/analysis_metrics/). Nothing here calls the code under test.

THE HAND-BUILT FIXTURE (every expected table in the tests was worked out by hand from the rows below, never computed
with analysis.metrics).

Findings (27 rows, id f1..f27). Cell = (dimension, side, intensity): n findings / detected / acted.
    factual_accuracy, left,  2 : f1-f4    4 / 3 / 2   (f1,f2 obvious; f3,f4 hard; f1 acted, f2 not, f3 acted twice-matched, f4 only an INVALID issue)
    factual_accuracy, right, 2 : f5-f8    4 / 2 / 2   (f5,f6 obvious both detected and acted; f7,f8 hard, unmatched)
    factual_accuracy, left,  3 : f9,f10   2 / 2 / 1   (obvious)
    factual_accuracy, right, 3 : f11,f12  2 / 1 / 0   (obvious; f12 only an INVALID issue)
    factual_accuracy, left,  4 : f13      1 / 0 / 0   (hard; no right counterpart)
    factual_accuracy, right, 1 : f14,f15  2 / 0 / 0   (difficulty None; no left counterpart)
    abusiveness,      left,  3 : f16-f18  3 / 3 / 3   (obvious)
    abusiveness,      right, 3 : f19-f21  3 / 2 / 1   (obvious; f21 only an INVALID issue)
    abusiveness,      left,  4 : f22,f23  2 / 2 / 2   (obvious)
    abusiveness,      right, 4 : f24,f25  2 / 2 / 1   (obvious)
    abusiveness,      left,  0 : f26      1 / 1 / 0   (difficulty None)
    abusiveness,      right, 0 : f27      1 / 0 / 0   (difficulty None)
Totals: left 13 findings, 11 detected, 8 acted; right 14 findings, 7 detected, 4 acted.

Issues (18 rows, i1..i18), grouped by (dimension, side): n valid / n false (matched False):
    abusiveness, left    : 5 / 3      abusiveness, right : 0 / 0 (two rejected only)   clarity, left : 1 / 0
    factual_accuracy, left : 4 / 2    factual_accuracy, right : 3 / 0

Runs (12 rows): see RUNS below.
"""
import math

import pandas as pd

FINDING_COLUMNS = [
    "finding_id", "conversation_id", "message_id", "side", "dimension", "intensity", "difficulty", "pair_id",
]  # fmt: skip
MATCH_COLUMNS = ["finding_id", "issue_id", "issue_valid", "acted"]
ISSUE_COLUMNS = [
    "issue_id", "conversation_id", "message_id", "side", "dimension", "intensity", "validity", "rejection_reason",
    "matched",
]  # fmt: skip
RUN_COLUMNS = ["run_id", "conversation_id", "side", "status", "failure_reason"]

NAN = float("nan")

F, A = "factual_accuracy", "abusiveness"
L, R = "left", "right"

# (finding_id, dimension, side, intensity, difficulty)
_FINDINGS = [
    ("f1", F, L, 2, "obvious"), ("f2", F, L, 2, "obvious"), ("f3", F, L, 2, "hard"), ("f4", F, L, 2, "hard"),
    ("f5", F, R, 2, "obvious"), ("f6", F, R, 2, "obvious"), ("f7", F, R, 2, "hard"), ("f8", F, R, 2, "hard"),
    ("f9", F, L, 3, "obvious"), ("f10", F, L, 3, "obvious"),
    ("f11", F, R, 3, "obvious"), ("f12", F, R, 3, "obvious"),
    ("f13", F, L, 4, "hard"),
    ("f14", F, R, 1, None), ("f15", F, R, 1, None),
    ("f16", A, L, 3, "obvious"), ("f17", A, L, 3, "obvious"), ("f18", A, L, 3, "obvious"),
    ("f19", A, R, 3, "obvious"), ("f20", A, R, 3, "obvious"), ("f21", A, R, 3, "obvious"),
    ("f22", A, L, 4, "obvious"), ("f23", A, L, 4, "obvious"),
    ("f24", A, R, 4, "obvious"), ("f25", A, R, 4, "obvious"),
    ("f26", A, L, 0, None), ("f27", A, R, 0, None),
]  # fmt: skip

# (finding_id, issue_id, issue_valid, acted)
_MATCHES = [
    ("f1", "i1", True, True),
    ("f2", "i2", True, False),
    ("f3", "i3", True, True), ("f3", "i3b", True, False),  # two valid issues on one finding: detected once, acted once
    ("f4", "i4", False, False),  # only an invalid issue: NOT detected
    ("f5", "i5", True, True), ("f6", "i6", True, True),
    ("f9", "i9", True, True), ("f10", "i10", True, False),
    ("f11", "i11", True, False), ("f12", "i12", False, False),
    ("f16", "i16", True, True), ("f17", "i17", True, True), ("f18", "i18", True, True),
    ("f19", "i19", True, True), ("f20", "i20", True, False), ("f21", "i21", False, False),
    ("f22", "i22", True, True), ("f23", "i23", True, True),
    ("f24", "i24", True, True), ("f25", "i25", True, False),
    ("f26", "i26", True, False),
]  # fmt: skip

# (issue_id, dimension, side, intensity, validity, rejection_reason, matched)
_ISSUES = [
    ("i1", F, L, 2.0, "valid", "", True), ("i2", F, L, 3.0, "valid", "", True),
    ("i3", F, L, NAN, "valid", "", False), ("i4", F, L, 1.0, "valid", "", False),
    ("i5", F, L, NAN, "rejected", "quote_not_found", False),
    ("i6", F, R, 2.0, "valid", "", True), ("i7", F, R, 4.0, "valid", "", True), ("i8", F, R, 2.0, "valid", "", True),
    ("i9", F, R, NAN, "rejected", "quote_not_found", False),
    ("i10", F, R, NAN, "rejected", "unknown_message", False),
    ("i11", A, L, 3.0, "valid", "", True), ("i12", A, L, 4.0, "valid", "", True),
    ("i13", A, L, 0.0, "valid", "", False), ("i14", A, L, NAN, "valid", "", False),
    ("i15", A, L, NAN, "valid", "", False),
    ("i16", A, R, NAN, "rejected", "quote_not_found", False), ("i17", A, R, NAN, "rejected", "unknown_message", False),
    ("i18", "clarity", L, 2.0, "valid", "", True),
]  # fmt: skip

# (run_id, side, status, failure_reason)
RUNS = [
    ("r1", L, "done", ""), ("r2", L, "done", ""), ("r3", L, "done", ""), ("r4", L, "failed", "structural"),
    ("r5", L, "failed", "api_error"), ("r6", L, "skipped_budget", "budget_exceeded"),
    ("r7", R, "done", ""), ("r8", R, "done", ""), ("r9", R, "failed", "structural"),
    ("r10", R, "skipped_budget", "breaker_open"), ("r11", R, "skipped_disabled", "llm_disabled"),
    ("r12", R, "skipped_budget", "budget_exceeded"),
]  # fmt: skip


def findings():
    rows = [
        (fid, 100 + n // 4, 1000 + n, side, dim, inten, diff, f"pair{n // 2}")
        for n, (fid, dim, side, inten, diff) in enumerate(_FINDINGS)
    ]
    frame = pd.DataFrame(rows, columns=FINDING_COLUMNS)
    frame["difficulty"] = pd.Series([r[4] for r in _FINDINGS], dtype=object)
    return frame


def matches():
    return pd.DataFrame(_MATCHES, columns=MATCH_COLUMNS)


def issues():
    rows = [
        (iid, 100 + n // 4, 2000 + n, side, dim, inten, validity, reason, matched)
        for n, (iid, dim, side, inten, validity, reason, matched) in enumerate(_ISSUES)
    ]  # fmt: skip
    return pd.DataFrame(rows, columns=ISSUE_COLUMNS)


def runs():
    rows = [(rid, 100 + n // 3, side, status, reason) for n, (rid, side, status, reason) in enumerate(RUNS)]
    return pd.DataFrame(rows, columns=RUN_COLUMNS)


def shuffled(frame, seed):
    """The same rows in another order, with a fresh index (row order is the only thing that changes)."""
    return frame.sample(frac=1.0, random_state=seed).reset_index(drop=True)


def snapshot(*frames):
    """Deep copies to compare against after a call (non-mutation)."""
    return [frame.copy(deep=True) for frame in frames]


def same_frame(a, b):
    """Exact equality of two frames: columns, dtypes, index, values (NaN equals NaN)."""
    pd.testing.assert_frame_equal(a, b, check_exact=True)


def _equal(actual, expected):
    if isinstance(expected, float) and math.isnan(expected):
        return isinstance(actual, float) and math.isnan(actual)
    if isinstance(expected, float):
        return abs(actual - expected) <= 1e-12
    if expected is None:
        return pd.isna(actual)
    return actual == expected


def assert_table(frame, columns, expected):
    """Columns exactly `columns`; the rows equal `expected` (list of tuples) as a set of keyed rows in ANY order, with
    floats compared to 1e-12 and NaN equal to NaN. The first len(expected[0]) - value columns identify a row; here we
    simply match rows one to one on their full tuple after sorting both by the string form of the leading key."""
    assert list(frame.columns) == list(columns), list(frame.columns)
    assert len(frame) == len(expected), f"{len(frame)} rows, expected {len(expected)}:\n{frame}"
    actual_rows = [tuple(row) for row in frame.itertuples(index=False, name=None)]
    remaining = list(actual_rows)
    for want in expected:
        hit = [got for got in remaining if len(got) == len(want) and all(_equal(g, w) for g, w in zip(got, want))]
        assert hit, f"expected row {want} not found in:\n{frame}"
        remaining.remove(hit[0])
    assert remaining == []


def assert_rows_in_order(frame, columns, expected):
    """Like assert_table, but the row ORDER must match too."""
    assert list(frame.columns) == list(columns), list(frame.columns)
    assert len(frame) == len(expected), f"{len(frame)} rows, expected {len(expected)}:\n{frame}"
    for got, want in zip(frame.itertuples(index=False, name=None), expected):
        assert all(_equal(g, w) for g, w in zip(got, want)), f"row {tuple(got)} != {want}"


def row(frame, **key):
    """The single row of `frame` whose columns equal `key` (asserts exactly one) as a dict."""
    mask = pd.Series(True, index=frame.index)
    for column, value in key.items():
        mask &= frame[column] == value
    hit = frame[mask]
    assert len(hit) == 1, f"{len(hit)} rows for {key}:\n{frame}"
    return hit.iloc[0].to_dict()


def assert_rows_present(frame, columns, expected):
    """Columns exactly `columns`; every tuple of `expected` appears as a row (floats to 1e-12, NaN equals NaN). Rows
    not listed are not checked (used where the brief leaves the set of zero-count categories open)."""
    assert list(frame.columns) == list(columns), list(frame.columns)
    actual_rows = [tuple(r) for r in frame.itertuples(index=False, name=None)]
    for want in expected:
        hit = [got for got in actual_rows if all(_equal(g, w) for g, w in zip(got, want))]
        assert len(hit) == 1, f"expected exactly one row {want} in:\n{frame}"


# ---------------------------------------------------------------------------------------------------------------
# randomised frames with an independent pure-python oracle (no pandas groupby, no code under test)
# ---------------------------------------------------------------------------------------------------------------
import random

DIMENSIONS = ("factual_accuracy", "abusiveness", "clarity")
DIFFICULTIES = ("obvious", "hard", None)


def random_case(seed, n_findings=None):
    """(findings, matches) built from `random.Random(seed)`: 5..60 findings, random matches including several rows
    per finding, invalid issues, acted flags on valid and (deliberately) on invalid rows, and stray finding ids."""
    rng = random.Random(seed)
    n = n_findings if n_findings is not None else rng.randint(5, 60)
    f_rows = []
    for k in range(n):
        f_rows.append((f"g{k}", k // 3, k, rng.choice(["left", "right"]), rng.choice(DIMENSIONS),
                       rng.randint(0, 4), rng.choice(DIFFICULTIES), f"p{k // 2}"))  # fmt: skip
    findings_frame = pd.DataFrame(f_rows, columns=FINDING_COLUMNS)
    findings_frame["difficulty"] = pd.Series([r[6] for r in f_rows], dtype=object)
    m_rows = []
    for k in range(n):
        for j in range(rng.choice([0, 0, 1, 1, 2, 3])):
            valid = rng.random() < 0.7
            m_rows.append((f"g{k}", f"m{k}_{j}", valid, rng.random() < 0.5))
    m_rows.append(("stray", "m_stray", True, True))  # a match whose finding does not exist: must be ignored
    matches_frame = pd.DataFrame(m_rows, columns=MATCH_COLUMNS)
    return findings_frame, matches_frame


def oracle_counts(findings_frame, matches_frame, keys):
    """{cell tuple: (n_findings, n_detected, n_acted)} by plain python. Detected: some VALID match row. Acted: some
    match row that is valid AND acted."""
    valid_ids = {r.finding_id for r in matches_frame.itertuples() if bool(r.issue_valid)}
    acted_ids = {r.finding_id for r in matches_frame.itertuples() if bool(r.issue_valid) and bool(r.acted)}
    counts = {}
    for r in findings_frame.to_dict("records"):
        cell = tuple(r[k] for k in keys)
        n, d, a = counts.get(cell, (0, 0, 0))
        counts[cell] = (n + 1, d + (r["finding_id"] in valid_ids), a + (r["finding_id"] in acted_ids))
    return counts


def swap_sides(frame):
    out = frame.copy()
    out["side"] = out["side"].map({"left": "right", "right": "left"})
    return out


def ratio(count, n):
    """count / n, or NaN when n is 0 (the contract's rate rule)."""
    return count / n if n else NAN
