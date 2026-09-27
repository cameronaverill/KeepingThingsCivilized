"""analysis.metrics.false_positives and data_quality: known answers on the hand-built fixture (kit docstring).
false_positives counts valid issues with matched == False (rate is per issue); data_quality is long form
`by + [measure, category, n, count, rate]` (shape pinned in the step 9 brief amendment)."""
import inspect

import metrics9b_kit as kit
import pandas as pd
import pytest
from analysis import metrics

NAN = kit.NAN
FP_COLS = ["dimension", "side", "n_valid_issues", "n_false", "rate"]
DQ_COLS = ["side", "measure", "category", "n", "count", "rate"]


def test_false_positives_known_answer_default_by():
    table = metrics.false_positives(kit.issues())
    kit.assert_table(
        table,
        FP_COLS,
        [
            ("abusiveness", "left", 5, 3, 0.6),
            ("abusiveness", "right", 0, 0, NAN),  # two rejected issues only: no valid issue, no rate, no invented 0
            ("clarity", "left", 1, 0, 0.0),  # a dimension nobody planned for is kept as it is
            ("factual_accuracy", "left", 4, 2, 0.5),
            ("factual_accuracy", "right", 3, 0, 0.0),
        ],
    )  # fmt: skip


def test_false_positives_by_side_only():
    table = metrics.false_positives(kit.issues(), by=("side",))
    kit.assert_table(table, ["side", "n_valid_issues", "n_false", "rate"], [("left", 10, 5, 0.5), ("right", 3, 0, 0.0)])


def test_false_positives_rejected_issues_are_never_counted_as_valid_or_false():
    table = metrics.false_positives(kit.issues(), by=("side",))
    assert int(table["n_valid_issues"].sum()) == 13  # 18 issues, 5 rejected
    assert int(table["n_false"].sum()) == 5


def test_false_positives_counts_are_integers_and_rate_is_float():
    table = metrics.false_positives(kit.issues())
    assert pd.api.types.is_integer_dtype(table["n_valid_issues"])
    assert pd.api.types.is_integer_dtype(table["n_false"])
    assert pd.api.types.is_float_dtype(table["rate"])


def test_false_positives_empty_issues_give_empty_table():
    table = metrics.false_positives(pd.DataFrame(columns=kit.ISSUE_COLUMNS))
    assert list(table.columns) == FP_COLS
    assert len(table) == 0


def test_false_positives_has_no_error_threshold_parameter():
    assert "error_threshold" not in inspect.signature(metrics.false_positives).parameters


def test_false_positives_all_matched_is_zero_not_nan():
    issues = kit.issues()
    issues["matched"] = True
    table = metrics.false_positives(issues, by=("side",))
    kit.assert_table(table, ["side", "n_valid_issues", "n_false", "rate"], [("left", 10, 0, 0.0), ("right", 3, 0, 0.0)])


# ---------------------------------------------------------------------------------------------------------- data quality

ISSUE_ROWS = [
    ("left", "issue_rejected", "all", 11, 1, 1 / 11),
    ("left", "issue_rejected", "quote_not_found", 11, 1, 1 / 11),
    ("left", "issue_rejected", "unknown_message", 11, 0, 0.0),
    ("right", "issue_rejected", "all", 7, 4, 4 / 7),
    ("right", "issue_rejected", "quote_not_found", 7, 2, 2 / 7),
    ("right", "issue_rejected", "unknown_message", 7, 2, 2 / 7),
]
STATUS_ROWS = [
    ("left", "run_status", "done", 6, 3, 0.5),
    ("left", "run_status", "failed", 6, 2, 2 / 6),
    ("left", "run_status", "skipped_budget", 6, 1, 1 / 6),
    ("left", "run_status", "skipped_disabled", 6, 0, 0.0),
    ("right", "run_status", "done", 6, 2, 2 / 6),
    ("right", "run_status", "failed", 6, 1, 1 / 6),
    ("right", "run_status", "skipped_budget", 6, 2, 2 / 6),
    ("right", "run_status", "skipped_disabled", 6, 1, 1 / 6),
]
REASON_ROWS = [
    ("left", "run_failure_reason", "api_error", 6, 1, 1 / 6),
    ("left", "run_failure_reason", "breaker_open", 6, 0, 0.0),
    ("left", "run_failure_reason", "budget_exceeded", 6, 1, 1 / 6),
    ("left", "run_failure_reason", "llm_disabled", 6, 0, 0.0),
    ("left", "run_failure_reason", "structural", 6, 1, 1 / 6),
    ("right", "run_failure_reason", "api_error", 6, 0, 0.0),
    ("right", "run_failure_reason", "breaker_open", 6, 1, 1 / 6),
    ("right", "run_failure_reason", "budget_exceeded", 6, 1, 1 / 6),
    ("right", "run_failure_reason", "llm_disabled", 6, 1, 1 / 6),
    ("right", "run_failure_reason", "structural", 6, 1, 1 / 6),
]


def test_data_quality_known_answer_without_findings():
    table = metrics.data_quality(kit.issues(), kit.runs())
    kit.assert_table(table, DQ_COLS, ISSUE_ROWS + STATUS_ROWS + REASON_ROWS)


def test_data_quality_findings_without_not_scorable_column_adds_no_measure():
    without = metrics.data_quality(kit.issues(), kit.runs(), kit.findings())
    kit.assert_table(without, DQ_COLS, ISSUE_ROWS + STATUS_ROWS + REASON_ROWS)
    assert set(without["measure"]) == {"issue_rejected", "run_status", "run_failure_reason"}


def test_data_quality_not_scorable_rate_when_the_column_is_present():
    findings = kit.findings()
    findings["not_scorable"] = findings["finding_id"].isin(["f1", "f13", "f14", "f15", "f27"])
    table = metrics.data_quality(kit.issues(), kit.runs(), findings)
    # left: f1, f13 of 13 findings; right: f14, f15, f27 of 14
    kit.assert_table(
        table,
        DQ_COLS,
        ISSUE_ROWS + STATUS_ROWS + REASON_ROWS + [("left", "not_scorable", "all", 13, 2, 2 / 13), ("right", "not_scorable", "all", 14, 3, 3 / 14)],
    )  # fmt: skip


def test_data_quality_a_side_absent_from_one_frame_shows_n_zero_and_nan():
    only_left_runs = kit.runs()
    only_left_runs = only_left_runs[only_left_runs["side"] == "left"].reset_index(drop=True)
    table = metrics.data_quality(kit.issues(), only_left_runs)
    right_done = kit.row(table, side="right", measure="run_status", category="done")
    assert (right_done["n"], right_done["count"]) == (0, 0)
    assert pd.isna(right_done["rate"])
    left_done = kit.row(table, side="left", measure="run_status", category="done")
    assert (left_done["n"], left_done["count"], left_done["rate"]) == (6, 3, 0.5)


def test_data_quality_by_dimension_side_for_issues_only_frames_still_needs_runs_columns():
    with pytest.raises(ValueError, match="dimension"):
        metrics.data_quality(kit.issues(), kit.runs(), by=("dimension", "side"))  # runs has no dimension column


def test_data_quality_empty_rejection_reason_is_labelled_none_and_counts_only_rejected():
    issues = kit.issues()
    issues.loc[issues["issue_id"] == "i5", "rejection_reason"] = ""
    table = metrics.data_quality(issues, kit.runs())
    labelled = kit.row(table, side="left", measure="issue_rejected", category="(none)")
    assert (labelled["n"], labelled["count"]) == (11, 1)
    valid_rows_are_not_counted = kit.row(table, side="left", measure="issue_rejected", category="all")
    assert valid_rows_are_not_counted["count"] == 1


def test_data_quality_counts_are_integers_and_rate_is_float():
    table = metrics.data_quality(kit.issues(), kit.runs())
    assert pd.api.types.is_integer_dtype(table["n"])
    assert pd.api.types.is_integer_dtype(table["count"])
    assert pd.api.types.is_float_dtype(table["rate"])


def test_data_quality_every_rate_is_count_over_n_or_nan_and_within_bounds():
    table = metrics.data_quality(kit.issues(), kit.runs())
    positive = table[table["n"] > 0]
    assert (positive["rate"] == positive["count"] / positive["n"]).all()
    assert ((positive["rate"] >= 0) & (positive["rate"] <= 1)).all()
    assert table[table["n"] == 0]["rate"].isna().all()
