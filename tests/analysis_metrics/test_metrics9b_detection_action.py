"""analysis.metrics.detection and action: known answers on the hand-built fixture (see metrics9b_kit for the worked
cell table). Detection = share of findings with at least one matched VALID issue; action = share of DETECTED findings
with at least one matched valid issue that was acted on (a finding is counted once however many issues match it)."""
import metrics9b_kit as kit
import pandas as pd
from analysis import metrics

NAN = kit.NAN
DET_COLS = ["dimension", "side", "intensity", "n_findings", "n_detected", "rate"]
ACT_COLS = ["dimension", "side", "intensity", "n_detected", "n_acted", "rate"]

DETECTION_BY_CELL = [
    ("abusiveness", "left", 0, 1, 1, 1.0),
    ("abusiveness", "left", 3, 3, 3, 1.0),
    ("abusiveness", "left", 4, 2, 2, 1.0),
    ("abusiveness", "right", 0, 1, 0, 0.0),
    ("abusiveness", "right", 3, 3, 2, 2 / 3),
    ("abusiveness", "right", 4, 2, 2, 1.0),
    ("factual_accuracy", "left", 2, 4, 3, 0.75),
    ("factual_accuracy", "left", 3, 2, 2, 1.0),
    ("factual_accuracy", "left", 4, 1, 0, 0.0),
    ("factual_accuracy", "right", 1, 2, 0, 0.0),
    ("factual_accuracy", "right", 2, 4, 2, 0.5),
    ("factual_accuracy", "right", 3, 2, 1, 0.5),
]

ACTION_BY_CELL = [
    ("abusiveness", "left", 0, 1, 0, 0.0),
    ("abusiveness", "left", 3, 3, 3, 1.0),
    ("abusiveness", "left", 4, 2, 2, 1.0),
    ("abusiveness", "right", 0, 0, 0, NAN),  # nothing detected: no denominator, no invented 0
    ("abusiveness", "right", 3, 2, 1, 0.5),
    ("abusiveness", "right", 4, 2, 1, 0.5),
    ("factual_accuracy", "left", 2, 3, 2, 2 / 3),
    ("factual_accuracy", "left", 3, 2, 1, 0.5),
    ("factual_accuracy", "left", 4, 0, 0, NAN),
    ("factual_accuracy", "right", 1, 0, 0, NAN),
    ("factual_accuracy", "right", 2, 2, 2, 1.0),
    ("factual_accuracy", "right", 3, 1, 0, 0.0),
]


def test_detection_default_by_known_answer():
    table = metrics.detection(kit.findings(), kit.matches())
    kit.assert_table(table, DET_COLS, DETECTION_BY_CELL)


def test_detection_counts_add_up_to_the_fixture_totals():
    table = metrics.detection(kit.findings(), kit.matches())
    assert int(table["n_findings"].sum()) == 27
    assert int(table["n_detected"].sum()) == 18


def test_detection_by_side_only():
    table = metrics.detection(kit.findings(), kit.matches(), by=("side",))
    kit.assert_table(table, ["side", "n_findings", "n_detected", "rate"], [("left", 13, 11, 11 / 13), ("right", 14, 7, 0.5)])


def test_detection_by_dimension_and_side():
    table = metrics.detection(kit.findings(), kit.matches(), by=("dimension", "side"))
    kit.assert_table(
        table,
        ["dimension", "side", "n_findings", "n_detected", "rate"],
        [
            ("abusiveness", "left", 6, 6, 1.0),
            ("abusiveness", "right", 6, 4, 4 / 6),
            ("factual_accuracy", "left", 7, 5, 5 / 7),
            ("factual_accuracy", "right", 8, 3, 3 / 8),
        ],
    )  # fmt: skip


def test_detection_by_difficulty_keeps_findings_without_a_difficulty():
    by = ("dimension", "side", "intensity", "difficulty")
    table = metrics.detection(kit.findings(), kit.matches(), by=by)
    hard_left = kit.row(table, dimension="factual_accuracy", side="left", intensity=2, difficulty="hard")
    obvious_left = kit.row(table, dimension="factual_accuracy", side="left", intensity=2, difficulty="obvious")
    hard_right = kit.row(table, dimension="factual_accuracy", side="right", intensity=2, difficulty="hard")
    obvious_right = kit.row(table, dimension="factual_accuracy", side="right", intensity=2, difficulty="obvious")
    assert (hard_left["n_findings"], hard_left["n_detected"], hard_left["rate"]) == (2, 1, 0.5)
    assert (obvious_left["n_findings"], obvious_left["n_detected"], obvious_left["rate"]) == (2, 2, 1.0)
    assert (hard_right["n_findings"], hard_right["n_detected"], hard_right["rate"]) == (2, 0, 0.0)
    assert (obvious_right["n_findings"], obvious_right["n_detected"], obvious_right["rate"]) == (2, 2, 1.0)


def test_action_default_by_known_answer_includes_empty_groups():
    table = metrics.action(kit.findings(), kit.matches())
    kit.assert_table(table, ACT_COLS, ACTION_BY_CELL)


def test_action_empty_groups_have_zero_n_and_nan_rate():
    table = metrics.action(kit.findings(), kit.matches())
    empty = table[table["n_detected"] == 0]
    assert len(empty) == 3
    assert empty["n_acted"].tolist() == [0, 0, 0]
    assert empty["rate"].isna().tolist() == [True, True, True]


def test_action_by_side_only():
    table = metrics.action(kit.findings(), kit.matches(), by=("side",))
    kit.assert_table(table, ["side", "n_detected", "n_acted", "rate"], [("left", 11, 8, 8 / 11), ("right", 7, 4, 4 / 7)])


def test_action_by_difficulty_known_answer():
    by = ("side", "difficulty")
    table = metrics.action(kit.findings(), kit.matches(), by=by)
    left_obvious = kit.row(table, side="left", difficulty="obvious")
    left_hard = kit.row(table, side="left", difficulty="hard")
    right_obvious = kit.row(table, side="right", difficulty="obvious")
    right_hard = kit.row(table, side="right", difficulty="hard")
    # left obvious: f1,f2,f9,f10,f16-18,f22,f23 detected (9), acted f1,f9,f16,f17,f18,f22,f23 (7)
    assert (left_obvious["n_detected"], left_obvious["n_acted"]) == (9, 7)
    # left hard: f3 detected and acted (f13 undetected)
    assert (left_hard["n_detected"], left_hard["n_acted"], left_hard["rate"]) == (1, 1, 1.0)
    # right obvious: f5,f6,f11,f19,f20,f24,f25 detected (7), acted f5,f6,f19,f24 (4)
    assert (right_obvious["n_detected"], right_obvious["n_acted"]) == (7, 4)
    # right hard: f7,f8 both undetected
    assert (right_hard["n_detected"], right_hard["n_acted"]) == (0, 0)
    assert pd.isna(right_hard["rate"])


def test_a_finding_with_two_valid_issues_is_counted_once():
    """f3 has a valid acted issue and a valid non-acted issue: one detection, one action, not two."""
    table = metrics.action(kit.findings(), kit.matches(), by=("dimension", "side", "intensity", "difficulty"))
    hard = kit.row(table, dimension="factual_accuracy", side="left", intensity=2, difficulty="hard")
    assert (hard["n_detected"], hard["n_acted"], hard["rate"]) == (1, 1, 1.0)


def test_an_invalid_issue_alone_is_not_a_detection():
    """f4 (only an invalid issue), f12 and f21 likewise: they stay in the denominator and out of the numerator."""
    only_invalid = pd.DataFrame([("f4", "ix", False, False)], columns=kit.MATCH_COLUMNS)
    table = metrics.detection(kit.findings(), only_invalid, by=("side",))
    kit.assert_table(table, ["side", "n_findings", "n_detected", "rate"], [("left", 13, 0, 0.0), ("right", 14, 0, 0.0)])


def test_no_matches_at_all_gives_zero_detection_and_nan_action():
    empty = pd.DataFrame(columns=kit.MATCH_COLUMNS)
    det = metrics.detection(kit.findings(), empty, by=("side",))
    act = metrics.action(kit.findings(), empty, by=("side",))
    kit.assert_table(det, ["side", "n_findings", "n_detected", "rate"], [("left", 13, 0, 0.0), ("right", 14, 0, 0.0)])
    kit.assert_table(act, ["side", "n_detected", "n_acted", "rate"], [("left", 0, 0, kit.NAN), ("right", 0, 0, kit.NAN)])


def test_empty_findings_give_an_empty_table_with_the_right_columns():
    findings = pd.DataFrame(columns=kit.FINDING_COLUMNS)
    det = metrics.detection(findings, kit.matches())
    act = metrics.action(findings, kit.matches())
    assert list(det.columns) == DET_COLS
    assert list(act.columns) == ACT_COLS
    assert len(det) == 0
    assert len(act) == 0


def test_output_dtypes_counts_are_integers_and_rate_is_float():
    for table, count_columns in (
        (metrics.detection(kit.findings(), kit.matches()), ["n_findings", "n_detected"]),
        (metrics.action(kit.findings(), kit.matches()), ["n_detected", "n_acted"]),
    ):
        for column in count_columns:
            assert pd.api.types.is_integer_dtype(table[column]), column
        assert pd.api.types.is_float_dtype(table["rate"])


def test_rates_always_between_zero_and_one_or_nan_and_never_a_division_error():
    for table in (metrics.detection(kit.findings(), kit.matches()), metrics.action(kit.findings(), kit.matches())):
        rates = table["rate"].dropna()
        assert ((rates >= 0.0) & (rates <= 1.0)).all()
        assert table["rate"].isin([float("inf"), float("-inf")]).sum() == 0


def test_unknown_side_and_dimension_values_are_kept_as_they_are():
    findings = kit.findings()
    findings.loc[0, "side"] = "centre"
    findings.loc[1, "dimension"] = "tone"
    table = metrics.detection(findings, kit.matches(), by=("dimension", "side", "intensity"))
    centre = kit.row(table, dimension="factual_accuracy", side="centre", intensity=2)
    tone = kit.row(table, dimension="tone", side="left", intensity=2)
    assert (centre["n_findings"], centre["n_detected"]) == (1, 1)
    assert (tone["n_findings"], tone["n_detected"]) == (1, 1)
    assert int(table["n_findings"].sum()) == 27
