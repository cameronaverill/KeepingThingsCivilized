"""Edge cases found by the mutation pass: cells that appear in only one frame, missing keys on both sides of a gap,
absent sides reporting n == 0, null booleans, duplicate cells."""
import metrics9b_kit as kit
import pandas as pd
import pytest
from analysis import metrics


def test_data_quality_side_only_in_runs_still_gets_issue_rows_with_n_zero_and_nan():
    issues = kit.issues()
    left_issues = issues[issues["side"] == "left"].reset_index(drop=True)
    table = metrics.data_quality(left_issues, kit.runs())
    right_all = kit.row(table, side="right", measure="issue_rejected", category="all")
    assert (right_all["n"], right_all["count"]) == (0, 0)
    assert pd.isna(right_all["rate"])
    right_reason = kit.row(table, side="right", measure="issue_rejected", category="quote_not_found")
    assert (right_reason["n"], right_reason["count"]) == (0, 0)
    assert pd.isna(right_reason["rate"])
    left_all = kit.row(table, side="left", measure="issue_rejected", category="all")
    assert (left_all["n"], left_all["count"], left_all["rate"]) == (11, 1, 1 / 11)


def test_data_quality_side_only_in_findings_gets_a_cell_in_every_measure():
    findings = kit.findings()
    findings["not_scorable"] = False
    only_left_issues = kit.issues()
    only_left_issues = only_left_issues[only_left_issues["side"] == "left"]
    only_left_runs = kit.runs()
    only_left_runs = only_left_runs[only_left_runs["side"] == "left"]
    table = metrics.data_quality(only_left_issues, only_left_runs, findings)
    right_status = kit.row(table, side="right", measure="run_status", category="done")
    assert (right_status["n"], right_status["count"]) == (0, 0)
    right_scorable = kit.row(table, side="right", measure="not_scorable", category="all")
    assert (right_scorable["n"], right_scorable["count"], right_scorable["rate"]) == (14, 0, 0.0)


def test_side_gap_reports_n_columns_and_zero_n_for_an_absent_side():
    gap = metrics.side_gap(metrics.detection(kit.findings(), kit.matches()))
    assert list(gap.columns) == [
        "dimension", "intensity", "n_findings_left", "n_findings_right", "rate_left", "rate_right", "gap", "comparable",
    ]  # fmt: skip
    both = kit.row(gap, dimension="abusiveness", intensity=3)
    assert (both["n_findings_left"], both["n_findings_right"]) == (3, 3)
    left_only = kit.row(gap, dimension="factual_accuracy", intensity=4)
    assert (left_only["n_findings_left"], left_only["n_findings_right"]) == (1, 0)
    right_only = kit.row(gap, dimension="factual_accuracy", intensity=1)
    assert (right_only["n_findings_left"], right_only["n_findings_right"]) == (0, 2)
    assert pd.api.types.is_integer_dtype(gap["n_findings_left"])
    assert pd.api.types.is_bool_dtype(gap["comparable"])


def test_side_gap_a_missing_float_key_on_both_sides_is_one_cell():
    table = pd.DataFrame(
        [
            ("left", float("nan"), 4, 0.5),
            ("right", float("nan"), 4, 0.25),
            ("left", 2.0, 4, 0.5),
            ("right", 2.0, 4, 1.0),
        ],
        columns=["side", "intensity", "n_valid_issues", "rate"],
    )
    gap = metrics.side_gap(table, keep=("intensity",))
    assert len(gap) == 2
    missing = gap[gap["intensity"].isna()]
    assert missing["comparable"].tolist() == [True]
    assert missing["gap"].tolist() == [-0.25]
    assert kit.row(gap, intensity=2.0)["gap"] == 0.5


def test_side_gap_two_rows_for_one_cell_and_side_is_an_error():
    table = metrics.detection(kit.findings(), kit.matches(), by=("dimension", "side", "intensity", "difficulty"))
    with pytest.raises(ValueError):
        metrics.side_gap(table, keep=("dimension", "intensity"))  # difficulty dropped from the key: cells collide


def test_null_booleans_are_rejected_rather_than_guessed():
    matches = kit.matches()
    matches["acted"] = matches["acted"].astype(object)
    matches.loc[0, "acted"] = None
    with pytest.raises(ValueError, match="acted"):
        metrics.action(kit.findings(), matches)
    issues = kit.issues()
    issues["matched"] = issues["matched"].astype(object)
    issues.loc[0, "matched"] = None
    with pytest.raises(ValueError, match="matched"):
        metrics.false_positives(issues)


def test_side_gap_missing_difficulty_cells_come_after_the_named_difficulties_of_their_dimension():
    table = metrics.detection(kit.findings(), kit.matches(), by=("dimension", "side", "difficulty"))
    gap = metrics.side_gap(table)
    assert gap["dimension"].tolist() == ["abusiveness", "abusiveness", "factual_accuracy", "factual_accuracy", "factual_accuracy"]
    assert gap["difficulty"].isna().tolist() == [False, True, False, False, True]
    assert gap["difficulty"].tolist()[0] == "obvious"
    assert gap["difficulty"].tolist()[2:4] == ["hard", "obvious"]


def test_decomposition_by_with_side_in_it_is_the_same_as_without_it():
    plain = metrics.gap_decomposition(kit.findings(), kit.matches(), by=("dimension", "intensity"))
    with_side = metrics.gap_decomposition(kit.findings(), kit.matches(), by=("side", "dimension", "intensity"))
    kit.same_frame(with_side, plain)


def test_decomposition_min_n_boundary_is_inclusive_on_both_sides():
    at_four = metrics.gap_decomposition(kit.findings(), kit.matches(), min_n=4)
    assert at_four[at_four["comparable"]][["dimension", "intensity"]].values.tolist() == [["factual_accuracy", 2]]
    at_five = metrics.gap_decomposition(kit.findings(), kit.matches(), min_n=5)
    assert at_five["comparable"].sum() == 0
    at_three = metrics.gap_decomposition(kit.findings(), kit.matches(), min_n=3)
    assert at_three[at_three["comparable"]][["dimension", "intensity"]].values.tolist() == [
        ["abusiveness", 3], ["factual_accuracy", 2],
    ]  # fmt: skip


def test_side_gap_default_min_n_is_one_so_an_empty_side_is_not_comparable():
    table = pd.DataFrame(
        [("left", 0, 0.5), ("right", 4, 0.75)], columns=["side", "n_findings", "rate"]
    )
    default = metrics.side_gap(table)
    assert default["comparable"].tolist() == [False]
    permissive = metrics.side_gap(table, min_n=0)
    assert permissive["comparable"].tolist() == [True]
    assert permissive["gap"].tolist() == [0.25]
