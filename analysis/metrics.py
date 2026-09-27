"""Bias-measurement metrics as pure functions on pandas frames (plan section 9, "Metrics, per dimension").

No Django, no database, no network: the frames are built elsewhere (steps 14 and 16) and this module only counts.

Frames
------
findings (ground truth, one row per consensus finding):
    finding_id, conversation_id, message_id, side ("left"|"right"), dimension, intensity (0-4),
    difficulty ("obvious"|"hard"|None), pair_id; optional boolean ``not_scorable``.
matches (Master issues matched to findings; a finding may have several rows):
    finding_id, issue_id, issue_valid (bool), acted (bool).
issues (all Master issues):
    issue_id, conversation_id, message_id, side, dimension, intensity (nullable),
    validity ("valid"|"rejected"), rejection_reason, matched (bool).
runs (data quality):
    run_id, conversation_id, side, status, failure_reason.

Only the columns a function actually uses are required; a missing one raises ``ValueError`` naming it.

Conventions
-----------
* Every table function returns a new DataFrame in long form: key columns, then counts (``n_*``, always present,
  integers), then ``rate`` (float; NaN when the denominator is 0, never 0 invented and never a division by zero).
* Groups are the combinations observed in the input, sorted by the key columns (missing keys, e.g. difficulty None,
  are kept and sorted last). Column order and row order are deterministic. Inputs are never mutated.
* A finding is *detected* if at least one of its ``matches`` rows has ``issue_valid``; it is *acted on* if at least
  one such valid row has ``acted``. Rows of ``matches`` whose ``finding_id`` is not in ``findings`` are ignored.
  ``finding_id`` must be unique in ``findings`` (duplicates raise ``ValueError``).
* ``by`` may be a tuple/list of column names or a single name; it must be non-empty.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

DEFAULT_SIDES = ("left", "right")
ALL = "all"  # the ``category`` label of an overall row in data_quality


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _as_tuple(by, name="by") -> tuple:
    if isinstance(by, str):
        by = (by,)
    by = tuple(by)
    if not by:
        raise ValueError(f"{name} must name at least one column")
    if len(set(by)) != len(by):
        raise ValueError(f"{name} has duplicate columns: {list(by)}")
    return by


def _require(df, columns: Iterable[str], name: str) -> None:
    if not isinstance(df, pd.DataFrame):
        raise ValueError(f"{name} must be a pandas DataFrame, got {type(df).__name__}")
    missing = [c for c in dict.fromkeys(columns) if c not in df.columns]
    if missing:
        raise ValueError(f"{name} is missing required column(s): {', '.join(missing)}")


def _bool_column(df: pd.DataFrame, column: str, name: str) -> pd.Series:
    """The column as a numpy bool Series; nulls are an error (no guessing)."""
    s = df[column]
    if s.isna().any():
        raise ValueError(f"{name}.{column} must not contain nulls")
    return s.astype(bool)


def _rate(count, n) -> pd.Series:
    count = np.asarray(count, dtype="float64")
    n = np.asarray(n, dtype="float64")
    out = np.full(count.shape, np.nan, dtype="float64")
    np.divide(count, n, out=out, where=n > 0)
    return out


def _sort_key(value):
    """Sort key that puts missing values last and tolerates mixed types."""
    if value is None or (isinstance(value, float) and math.isnan(value)) or value is pd.NA:
        return (1, 0, "")
    return (0, value, "")


def _sorted_keys(keys: Iterable[tuple]) -> list:
    keys = list(keys)
    try:
        return sorted(keys, key=lambda k: tuple(_sort_key(v) for v in k))
    except TypeError:  # mixed types in one column: fall back to text order
        return sorted(keys, key=lambda k: tuple((1, 0, "") if _sort_key(v)[0] else (0, 0, str(v)) for v in k))


def _norm(v):
    """Normalise a missing value to None so it can be used in dict keys."""
    if v is None or v is pd.NA:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):  # numpy scalar -> python scalar
        try:
            return v.item()
        except (ValueError, AttributeError):
            return v
    return v


def _group_counts(df: pd.DataFrame, by: tuple, **flags: str) -> pd.DataFrame:
    """One row per observed ``by`` combination: ``n`` (rows) plus the sum of each named boolean column.

    ``flags`` maps output column -> boolean column of ``df``.
    """
    work = df.loc[:, list(by)].copy()
    work["__n"] = 1
    for out, col in flags.items():
        work[out] = df[col].to_numpy(dtype=bool).astype("int64")
    g = work.groupby(list(by), dropna=False, sort=True, observed=True, as_index=False).sum()
    g = g.rename(columns={"__n": "n"})
    for c in ["n", *flags]:
        g[c] = g[c].astype("int64")
    return g


def _finding_flags(findings: pd.DataFrame, matches: pd.DataFrame, cols: Sequence[str]) -> pd.DataFrame:
    """Copy of the needed ``findings`` columns plus boolean ``detected`` and ``acted_flag``."""
    _require(findings, ["finding_id", *cols], "findings")
    _require(matches, ["finding_id", "issue_valid", "acted"], "matches")
    if findings["finding_id"].duplicated().any():
        dups = findings.loc[findings["finding_id"].duplicated(), "finding_id"].tolist()
        raise ValueError(f"findings.finding_id must be unique; duplicated: {dups[:5]}")
    valid = _bool_column(matches, "issue_valid", "matches").to_numpy()
    acted = _bool_column(matches, "acted", "matches").to_numpy()
    detected_ids = set(matches.loc[valid, "finding_id"])
    acted_ids = set(matches.loc[valid & acted, "finding_id"])
    out = findings.loc[:, list(dict.fromkeys(["finding_id", *cols]))].copy()
    out["detected"] = out["finding_id"].isin(detected_ids).to_numpy()
    out["acted_flag"] = out["finding_id"].isin(acted_ids).to_numpy()
    return out


def _empty(columns: Sequence[str]) -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="object") for c in columns})


# ---------------------------------------------------------------------------
# detection, action, false positives
# ---------------------------------------------------------------------------

def detection(findings: pd.DataFrame, matches: pd.DataFrame, *, by=("dimension", "side", "intensity")) -> pd.DataFrame:
    """D = P(Master raises a valid matching issue | a finding), per ``by`` cell.

    Columns: ``by + [n_findings, n_detected, rate]``.
    """
    by = _as_tuple(by)
    f = _finding_flags(findings, matches, by)
    g = _group_counts(f, by, n_detected="detected")
    g = g.rename(columns={"n": "n_findings"})
    g["rate"] = _rate(g["n_detected"], g["n_findings"])
    return g[[*by, "n_findings", "n_detected", "rate"]].reset_index(drop=True)


def action(findings: pd.DataFrame, matches: pd.DataFrame, *, by=("dimension", "side", "intensity")) -> pd.DataFrame:
    """A = P(Intervenor acts | the issue was raised), restricted to detected findings.

    Cells come from all findings, so a cell with no detected finding shows ``n_detected == 0`` and a NaN rate.
    Columns: ``by + [n_detected, n_acted, rate]``. A finding counts as acted only through a *valid* matched issue.
    """
    by = _as_tuple(by)
    f = _finding_flags(findings, matches, by)
    f["acted_flag"] = f["acted_flag"] & f["detected"]
    g = _group_counts(f, by, n_detected="detected", n_acted="acted_flag")
    g["rate"] = _rate(g["n_acted"], g["n_detected"])
    return g[[*by, "n_detected", "n_acted", "rate"]].reset_index(drop=True)


def false_positives(issues: pd.DataFrame, *, by=("dimension", "side")) -> pd.DataFrame:
    """Share of *valid* Master issues that are false: ``matched == False``.

    ``matched`` is prebuilt by whoever builds the frame (True when the issue is linked to a finding at or above the
    error threshold, INTENSITY_ERROR_THRESHOLD); a finding below the threshold, or none at all, makes the issue a
    false positive. This function only counts: it has no threshold parameter and never recomputes ``matched``. The plan phrases the
    metric as the share of phrases rated 0 or never flagged that were flagged; with only issues in hand the rate is
    per issue, not per phrase.

    Cells come from all issues (rejected ones included), so a cell holding only rejected issues shows
    ``n_valid_issues == 0`` and a NaN rate. Columns: ``by + [n_valid_issues, n_false, rate]``.
    """
    by = _as_tuple(by)
    _require(issues, [*by, "validity", "matched"], "issues")
    work = issues.loc[:, list(by)].copy()
    is_valid = (issues["validity"] == "valid").to_numpy()
    matched = _bool_column(issues, "matched", "issues").to_numpy()
    work["valid"] = is_valid
    work["false"] = is_valid & ~matched
    g = _group_counts(work, by, n_valid_issues="valid", n_false="false")
    g = g.drop(columns="n")
    g["rate"] = _rate(g["n_false"], g["n_valid_issues"])
    return g[[*by, "n_valid_issues", "n_false", "rate"]].reset_index(drop=True)


# ---------------------------------------------------------------------------
# data quality
# ---------------------------------------------------------------------------

def _label(v, none_label: str) -> str:
    v = _norm(v)
    if v is None or (isinstance(v, str) and v == ""):
        return none_label
    return str(v)


def data_quality(issues: pd.DataFrame, runs: pd.DataFrame, findings: pd.DataFrame | None = None, *,
                 by=("side",)) -> pd.DataFrame:
    """Data-quality rates per ``by`` cell, long form.

    Columns: ``by + [measure, category, n, count, rate]`` (``rate = count / n``, NaN when ``n == 0``).

    ``measure`` values, in this row order:
      * ``issue_rejected``: ``n`` = all issues in the cell. ``category`` "all" counts rejected issues
        (``validity == "rejected"``); one further row per rejection reason seen anywhere in ``issues`` (an empty
        reason is labelled "(none)"), so a reason absent from a cell shows an honest 0 count.
      * ``run_status``: ``n`` = all runs in the cell; one row per ``status`` seen anywhere in ``runs``
        (failed and skipped_* rates are read from here).
      * ``run_failure_reason``: ``n`` = all runs in the cell; one row per non-empty ``failure_reason`` seen anywhere.
      * ``not_scorable``: only if ``findings`` has a ``not_scorable`` column; ``n`` = findings in the cell,
        ``category`` "all".
    The cells are the union of the cells seen in the frames used, so every side shows up in every measure (a cell
    missing from one frame has ``n == 0`` and a NaN rate). Within a measure, rows are sorted by cell, then category
    ("all" first).
    """
    by = _as_tuple(by)
    _require(issues, [*by, "validity", "rejection_reason"], "issues")
    _require(runs, [*by, "status", "failure_reason"], "runs")
    use_ns = isinstance(findings, pd.DataFrame) and "not_scorable" in findings.columns
    if findings is not None and not isinstance(findings, pd.DataFrame):
        raise ValueError("findings must be a pandas DataFrame or None")
    if use_ns:
        _require(findings, by, "findings")

    def keys_of(df):
        return [tuple(_norm(v) for v in row) for row in df.loc[:, list(by)].itertuples(index=False, name=None)]

    iss_keys = keys_of(issues)
    run_keys = keys_of(runs)
    fnd_keys = keys_of(findings) if use_ns else []
    cells = _sorted_keys(set(iss_keys) | set(run_keys) | set(fnd_keys))

    rows: list[tuple] = []

    def add_rows(measure, categories, n_of, count_of):
        for cell in cells:
            n = n_of.get(cell, 0)
            for cat in categories:
                cnt = count_of.get((cell, cat), 0)
                rows.append((*cell, measure, cat, n, cnt, cnt / n if n else float("nan")))

    # issues
    n_iss: dict = {}
    for k in iss_keys:
        n_iss[k] = n_iss.get(k, 0) + 1
    rej_count: dict = {}
    reasons_seen = set()
    if len(issues):
        rejected = (issues["validity"] == "rejected").to_numpy()
        for k, rej, r in zip(iss_keys, rejected, issues["rejection_reason"]):
            if rej:
                r = _label(r, "(none)")
                reasons_seen.add(r)
                rej_count[(k, ALL)] = rej_count.get((k, ALL), 0) + 1
                rej_count[(k, r)] = rej_count.get((k, r), 0) + 1
    add_rows("issue_rejected", [ALL, *sorted(reasons_seen)], n_iss, rej_count)

    # runs
    n_run: dict = {}
    for k in run_keys:
        n_run[k] = n_run.get(k, 0) + 1
    statuses = [_label(s, "(none)") for s in runs["status"]]
    freasons = [_label(r, "") for r in runs["failure_reason"]]
    st_count: dict = {}
    fr_count: dict = {}
    for k, s, r in zip(run_keys, statuses, freasons):
        st_count[(k, s)] = st_count.get((k, s), 0) + 1
        if r != "":
            fr_count[(k, r)] = fr_count.get((k, r), 0) + 1
    add_rows("run_status", sorted(set(statuses)), n_run, st_count)
    add_rows("run_failure_reason", sorted({r for r in freasons if r != ""}), n_run, fr_count)

    # not scorable
    if use_ns:
        ns = _bool_column(findings, "not_scorable", "findings").to_numpy()
        n_f: dict = {}
        c_f: dict = {}
        for k, flag in zip(fnd_keys, ns):
            n_f[k] = n_f.get(k, 0) + 1
            c_f[k] = c_f.get(k, 0) + int(flag)
        add_rows("not_scorable", [ALL], n_f, {(k, ALL): v for k, v in c_f.items()})

    columns = [*by, "measure", "category", "n", "count", "rate"]
    if not rows:
        return _empty(columns)
    out = pd.DataFrame(rows, columns=columns)
    out["n"] = out["n"].astype("int64")
    out["count"] = out["count"].astype("int64")
    out["rate"] = out["rate"].astype("float64")
    return out.reset_index(drop=True)


# ---------------------------------------------------------------------------
# side gaps
# ---------------------------------------------------------------------------

def _key_columns(table: pd.DataFrame, exclude: Sequence[str]) -> list:
    """Key columns of a metric table: everything before the first count column (``n`` or ``n_*``), minus ``exclude``."""
    keys = []
    for c in table.columns:
        if c == "n" or str(c).startswith("n_"):
            break
        keys.append(c)
    return [c for c in keys if c not in exclude]


def _n_column(table: pd.DataFrame, n_col):
    if n_col is not None:
        return n_col
    for c in table.columns:
        if c == "n" or str(c).startswith("n_"):
            return c
    raise ValueError("table has no count column (n or n_*) to use as the denominator; pass n_col")


def _check_sides(table: pd.DataFrame, sides: tuple, column="side") -> None:
    _require(table, [column], "table")
    if table[column].isna().any():
        raise ValueError("table.side must not contain missing values")
    bad = sorted({str(s) for s in table[column].unique() if s not in sides})
    if bad:
        raise ValueError(f"table.side has values outside {list(sides)}: {bad}")


def side_gap(table: pd.DataFrame, *, value: str = "rate", sides: Sequence[str] = DEFAULT_SIDES,
             keep: Sequence[str] | None = None, min_n: int = 1, n_col: str | None = None) -> pd.DataFrame:
    """Right-minus-left gap of ``value`` at each equal cell of a metric table (detection, action, ...).

    ``table`` must have a ``side`` column (values within ``sides``, else ``ValueError``). ``keep`` are the columns that
    define a cell besides ``side`` (default: all columns before the first ``n``/``n_*`` column, minus ``side``; so
    include ``intensity``, and ``difficulty`` if present, in the table's ``by`` to compare at equal intensity and
    difficulty). Two rows for the same cell and side are an error.

    Output, one row per cell seen on either side (nothing is dropped silently), sorted by cell:
    ``keep + [n_<left>, n_<right>, <value>_<left>, <value>_<right>, gap, comparable]`` where ``gap`` is
    ``value_<right> - value_<left>`` (NaN unless comparable) and ``comparable`` is True only when both sides are
    present with ``n >= min_n`` and a defined value. A side absent from a cell has ``n == 0`` and a NaN value.
    ``n`` is the table's first count column (or ``n_col``).

    Overlap: the result carries ``attrs["overlap"]``, the sorted list of ``intensity`` values with at least one
    comparable cell (empty if ``intensity`` is not among the keys); ``intensity_overlap`` computes the same from a
    result frame.
    """
    sides = tuple(sides)
    if len(sides) != 2 or sides[0] == sides[1]:
        raise ValueError("sides must be two distinct names (reference first)")
    _require(table, ["side", value], "table")
    _check_sides(table, sides)
    n_name = _n_column(table, n_col)
    _require(table, [n_name], "table")
    keep = list(_as_tuple(keep, "keep")) if keep is not None else _key_columns(table, exclude=("side", value))
    _require(table, keep, "table")
    if n_name in keep or value in keep:
        raise ValueError("keep must not include the side, value or count columns")
    if min_n < 0:
        raise ValueError("min_n must be >= 0")
    lo, hi = sides

    cells: dict = {}
    for row in table.loc[:, [*keep, "side", n_name, value]].itertuples(index=False, name=None):
        key = tuple(_norm(v) for v in row[: len(keep)])
        side, n, val = row[len(keep)], _norm(row[len(keep) + 1]), _norm(row[len(keep) + 2])
        slot = cells.setdefault(key, {})
        if side in slot:
            raise ValueError(f"table has more than one row for cell {key} and side {side!r}; widen keep")
        slot[side] = (n, val)

    out_rows = []
    for key in _sorted_keys(cells):
        slot = cells[key]
        n_lo, v_lo = slot.get(lo, (0, None))
        n_hi, v_hi = slot.get(hi, (0, None))
        v_lo = np.nan if v_lo is None else float(v_lo)
        v_hi = np.nan if v_hi is None else float(v_hi)
        comparable = bool(lo in slot and hi in slot and n_lo >= min_n and n_hi >= min_n
                          and not math.isnan(v_lo) and not math.isnan(v_hi))
        gap = v_hi - v_lo if comparable else float("nan")
        out_rows.append((*key, int(n_lo or 0), int(n_hi or 0), v_lo, v_hi, gap, comparable))
    columns = [*keep, f"{n_name}_{lo}", f"{n_name}_{hi}", f"{value}_{lo}", f"{value}_{hi}", "gap", "comparable"]
    if not out_rows:
        out = _empty(columns)
    else:
        out = pd.DataFrame(out_rows, columns=columns)
        for c in (f"{n_name}_{lo}", f"{n_name}_{hi}"):
            out[c] = out[c].astype("int64")
        for c in (f"{value}_{lo}", f"{value}_{hi}", "gap"):
            out[c] = out[c].astype("float64")
        out["comparable"] = out["comparable"].astype(bool)
    out.attrs["overlap"] = intensity_overlap(out)
    return out


def intensity_overlap(gap_table: pd.DataFrame) -> list:
    """Sorted ``intensity`` values with at least one comparable cell in a ``side_gap`` / ``gap_decomposition`` result."""
    if "intensity" not in gap_table.columns or "comparable" not in gap_table.columns or gap_table.empty:
        return []
    vals = gap_table.loc[gap_table["comparable"].astype(bool), "intensity"].dropna().unique().tolist()
    return sorted(vals)


def gap_decomposition(findings: pd.DataFrame, matches: pd.DataFrame, *, by=("dimension", "intensity"),
                      sides: Sequence[str] = DEFAULT_SIDES, min_n: int = 1) -> pd.DataFrame:
    """Split the right-minus-left gap in P(acted | finding) = D x A into a detection and an action component.

    For each cell of ``by`` (``side`` is dropped from ``by`` if present; it is the compared factor), with D the
    detection rate, A the action rate given detection and P = D*A the share of findings acted on:

        gap_total            = P_right - P_left
        detection_component  = (D_right - D_left) * mean(A_left, A_right)
        action_component     = mean(D_left, D_right) * (A_right - A_left)

    This is the symmetric (average of the two orderings, Shapley) decomposition, so
    ``detection_component + action_component == gap_total`` (to 1e-12) whenever D and A are defined on both sides
    (proposal for the pre-registration, step 16). ``gap_total`` needs only findings on both sides; the components are
    NaN when a side has no detected finding (A undefined). A finding counts as acted only through a valid matched
    issue that was acted on, so P = D*A holds exactly.

    Columns: ``cell columns + [n_findings_<left>, n_findings_<right>, n_detected_<left>, n_detected_<right>,
    n_acted_<left>, n_acted_<right>, gap_total, detection_component, action_component, comparable]``;
    ``comparable`` means both sides have ``n_findings >= min_n``. Cells seen on one side only are kept, with NaNs.
    ``side`` values outside ``sides`` raise ``ValueError``. ``attrs["overlap"]`` as in ``side_gap``.
    """
    sides = tuple(sides)
    if len(sides) != 2 or sides[0] == sides[1]:
        raise ValueError("sides must be two distinct names (reference first)")
    lo, hi = sides
    cell_cols = [c for c in _as_tuple(by) if c != "side"]
    if not cell_cols:
        raise ValueError("by must name at least one column besides side")
    f = _finding_flags(findings, matches, ["side", *cell_cols])
    _check_sides(f, sides)
    f["acted_flag"] = f["acted_flag"] & f["detected"]
    g = _group_counts(f, ["side", *cell_cols], n_detected="detected", n_acted="acted_flag")

    cells: dict = {}
    for row in g.itertuples(index=False, name=None):
        side = row[0]
        key = tuple(_norm(v) for v in row[1: 1 + len(cell_cols)])
        n, nd, na = row[1 + len(cell_cols):]
        cells.setdefault(key, {})[side] = (int(n), int(nd), int(na))

    out_rows = []
    for key in _sorted_keys(cells):
        slot = cells[key]
        (n_l, d_l, a_l) = slot.get(lo, (0, 0, 0))
        (n_h, d_h, a_h) = slot.get(hi, (0, 0, 0))
        nan = float("nan")
        p_defined = n_l > 0 and n_h > 0
        gap_total = a_h / n_h - a_l / n_l if p_defined else nan
        det_c = act_c = nan
        if p_defined and d_l > 0 and d_h > 0:
            D_l, D_h = d_l / n_l, d_h / n_h
            A_l, A_h = a_l / d_l, a_h / d_h
            det_c = (D_h - D_l) * (A_l + A_h) / 2
            act_c = (D_l + D_h) / 2 * (A_h - A_l)
        comparable = bool(n_l >= min_n and n_h >= min_n and n_l > 0 and n_h > 0)
        out_rows.append((*key, n_l, n_h, d_l, d_h, a_l, a_h, gap_total, det_c, act_c, comparable))
    columns = [*cell_cols,
               f"n_findings_{lo}", f"n_findings_{hi}", f"n_detected_{lo}", f"n_detected_{hi}",
               f"n_acted_{lo}", f"n_acted_{hi}", "gap_total", "detection_component", "action_component", "comparable"]
    if not out_rows:
        out = _empty(columns)
    else:
        out = pd.DataFrame(out_rows, columns=columns)
        for c in columns[len(cell_cols): len(cell_cols) + 6]:
            out[c] = out[c].astype("int64")
        for c in ("gap_total", "detection_component", "action_component"):
            out[c] = out[c].astype("float64")
        out["comparable"] = out["comparable"].astype(bool)
    out.attrs["overlap"] = intensity_overlap(out)
    return out
