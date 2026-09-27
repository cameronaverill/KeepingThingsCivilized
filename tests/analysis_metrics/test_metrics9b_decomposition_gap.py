"""analysis.metrics.gap_decomposition and side_gap: known answers on the hand-built fixture (kit docstring).

Decomposition (by dimension, intensity), P = acted/findings = D x A, worked by hand (right minus left):
  (abusiveness,0):  L D=1 A=0    R D=0 A=nan  -> total 0, components NaN (right detected nothing)
  (abusiveness,3):  L D=1 A=1    R D=2/3 A=1/2 P=1/3 -> total -2/3; detection -1/3*3/4 = -1/4; action 5/6*-1/2 = -5/12
  (abusiveness,4):  L D=1 A=1    R D=1 A=1/2   -> total -1/2; detection 0; action -1/2
  (factual,2):      L D=3/4 A=2/3 P=1/2   R D=1/2 A=1 P=1/2 -> total 0; detection -1/4*5/6 = -5/24; action 5/8*1/3 = 5/24
  (factual,3):      L D=1 A=1/2   R D=1/2 A=0 -> total -1/2; detection -1/2*1/4 = -1/8; action 3/4*-1/2 = -3/8
  (factual,4) left only, (factual,1) right only -> NaN
By intensity only (dimensions pooled): 0 as above; 2 = (factual,2); 1 right only;
  3: L 5 findings 5 detected 4 acted (D=1,A=4/5) R 5,3,1 (D=3/5, A=1/3) -> total 1/5-4/5 = -3/5;
     detection -2/5*17/30 = -17/75; action 4/5*-7/15 = -28/75
  4: L 3,2,2 (D=2/3,A=1) R 2,2,1 (D=1,A=1/2) -> total -1/6; detection 1/3*3/4 = 1/4; action 5/6*-1/2 = -5/12
"""
import math

import metrics9b_kit as kit
import pandas as pd
import pytest
from analysis import metrics

NAN = kit.NAN


def cell(table, **key):
    r = kit.row(table, **key)
    return r["gap_total"], r["detection_component"], r["action_component"]


def close(actual, expected):
    return kit._equal(actual, expected)


def test_decomposition_default_by_known_answer():
    table = metrics.gap_decomposition(kit.findings(), kit.matches())
    assert list(table.columns[:2]) == ["dimension", "intensity"]
    assert len(table) == 7
    expected = {
        ("abusiveness", 3): (-2 / 3, -1 / 4, -5 / 12),
        ("abusiveness", 4): (-1 / 2, 0.0, -1 / 2),
        ("factual_accuracy", 2): (0.0, -5 / 24, 5 / 24),
        ("factual_accuracy", 3): (-1 / 2, -1 / 8, -3 / 8),
    }
    for (dimension, intensity), want in expected.items():
        got = cell(table, dimension=dimension, intensity=intensity)
        assert all(close(g, w) for g, w in zip(got, want)), (dimension, intensity, got, want)


def test_decomposition_cell_where_the_right_side_detected_nothing():
    table = metrics.gap_decomposition(kit.findings(), kit.matches())
    r = kit.row(table, dimension="abusiveness", intensity=0)
    assert pd.isna(r["detection_component"])
    assert pd.isna(r["action_component"])


def test_decomposition_one_sided_cells_are_kept_with_nan():
    table = metrics.gap_decomposition(kit.findings(), kit.matches())
    for key in ({"dimension": "factual_accuracy", "intensity": 4}, {"dimension": "factual_accuracy", "intensity": 1}):
        r = kit.row(table, **key)
        assert pd.isna(r["gap_total"])
        assert pd.isna(r["detection_component"])
        assert pd.isna(r["action_component"])
        assert r["comparable"] == False  # noqa: E712


def test_decomposition_components_sum_to_the_total_wherever_defined():
    table = metrics.gap_decomposition(kit.findings(), kit.matches())
    defined = table.dropna(subset=["detection_component", "action_component"])
    assert len(defined) == 4
    assert ((defined["detection_component"] + defined["action_component"] - defined["gap_total"]).abs() <= 1e-12).all()


def test_decomposition_by_intensity_only_pools_the_dimensions():
    table = metrics.gap_decomposition(kit.findings(), kit.matches(), by=("intensity",))
    assert len(table) == 5
    assert all(close(g, w) for g, w in zip(cell(table, intensity=3), (-3 / 5, -17 / 75, -28 / 75)))
    assert all(close(g, w) for g, w in zip(cell(table, intensity=4), (-1 / 6, 1 / 4, -5 / 12)))
    assert all(close(g, w) for g, w in zip(cell(table, intensity=2), (0.0, -5 / 24, 5 / 24)))
    assert pd.isna(cell(table, intensity=1)[0])


def test_decomposition_sign_convention_is_right_minus_left():
    findings = pd.DataFrame(
        [("a", "left", "d", 3), ("b", "right", "d", 3)], columns=["finding_id", "side", "dimension", "intensity"]
    )
    for column in ("conversation_id", "message_id", "difficulty", "pair_id"):
        findings[column] = 1
    matches = pd.DataFrame([("a", "ia", True, True)], columns=kit.MATCH_COLUMNS)  # only the LEFT finding is acted on
    table = metrics.gap_decomposition(findings, matches)
    assert cell(table, dimension="d", intensity=3)[0] == -1.0


def test_decomposition_empty_findings_give_empty_table_with_columns():
    table = metrics.gap_decomposition(pd.DataFrame(columns=kit.FINDING_COLUMNS), kit.matches())
    assert len(table) == 0
    assert {"dimension", "intensity", "gap_total", "detection_component", "action_component"} <= set(table.columns)


def test_decomposition_unknown_side_value_raises():
    findings = kit.findings()
    findings.loc[0, "side"] = "centre"
    with pytest.raises(ValueError, match="centre"):
        metrics.gap_decomposition(findings, kit.matches())


# ------------------------------------------------------------------------------------------------------------ side_gap

DET_GAPS = {  # (dimension, intensity) -> (rate_left, rate_right)
    ("abusiveness", 0): (1.0, 0.0),
    ("abusiveness", 3): (1.0, 2 / 3),
    ("abusiveness", 4): (1.0, 1.0),
    ("factual_accuracy", 2): (0.75, 0.5),
    ("factual_accuracy", 3): (1.0, 0.5),
}


def det_table():
    return metrics.detection(kit.findings(), kit.matches())


def act_table():
    return metrics.action(kit.findings(), kit.matches())


def test_side_gap_detection_known_answer():
    gap = metrics.side_gap(det_table())
    assert len(gap) == 7  # one row per cell seen on either side; nothing dropped
    for (dimension, intensity), (left, right) in DET_GAPS.items():
        r = kit.row(gap, dimension=dimension, intensity=intensity)
        assert close(r["gap"], right - left)
        assert r["comparable"] == True  # noqa: E712
        assert close(r["rate_left"], left)
        assert close(r["rate_right"], right)


def test_side_gap_reports_cells_where_one_side_is_missing_as_not_comparable():
    gap = metrics.side_gap(det_table())
    left_only = kit.row(gap, dimension="factual_accuracy", intensity=4)
    right_only = kit.row(gap, dimension="factual_accuracy", intensity=1)
    for r in (left_only, right_only):
        assert r["comparable"] == False  # noqa: E712
        assert pd.isna(r["gap"])
    assert pd.isna(left_only["rate_right"])
    assert not pd.isna(left_only["rate_left"])
    assert pd.isna(right_only["rate_left"])
    assert not pd.isna(right_only["rate_right"])


def test_side_gap_comparable_cells_and_overlap_values():
    gap = metrics.side_gap(det_table())
    comparable = gap[gap["comparable"]]
    assert sorted(comparable["intensity"].unique().tolist()) == [0, 2, 3, 4]
    assert gap.attrs["overlap"] == [0, 2, 3, 4]
    assert metrics.intensity_overlap(gap) == [0, 2, 3, 4]


def test_side_gap_action_known_answer_with_undefined_rates_not_comparable():
    gap = metrics.side_gap(act_table())
    expected = {
        ("abusiveness", 3): -0.5,
        ("abusiveness", 4): -0.5,
        ("factual_accuracy", 2): 1 / 3,
        ("factual_accuracy", 3): -0.5,
    }
    for (dimension, intensity), want in expected.items():
        r = kit.row(gap, dimension=dimension, intensity=intensity)
        assert close(r["gap"], want)
        assert r["comparable"] == True  # noqa: E712
    # abusiveness 0: the right side detected nothing, its action rate is NaN, so no gap
    r0 = kit.row(gap, dimension="abusiveness", intensity=0)
    assert r0["comparable"] == False  # noqa: E712
    assert pd.isna(r0["gap"])
    assert sorted(gap[gap["comparable"]]["intensity"].unique().tolist()) == [2, 3, 4]


def test_side_gap_min_n_filters_thin_cells_on_the_denominator_column():
    gap = metrics.side_gap(det_table(), min_n=3)  # n_findings: abusiveness 3 has 3/3, factual 2 has 4/4, rest thinner
    comparable = gap[gap["comparable"]]
    assert sorted(zip(comparable["dimension"], comparable["intensity"])) == [("abusiveness", 3), ("factual_accuracy", 2)]
    thin = kit.row(gap, dimension="abusiveness", intensity=4)
    assert pd.isna(thin["gap"])
    assert thin["comparable"] == False  # noqa: E712
    assert len(gap) == 7


def test_side_gap_min_n_uses_the_action_denominator_n_detected():
    gap = metrics.side_gap(act_table(), min_n=2)
    factual3 = kit.row(gap, dimension="factual_accuracy", intensity=3)  # n_detected 2 left, 1 right
    factual2 = kit.row(gap, dimension="factual_accuracy", intensity=2)  # 3 left, 2 right
    assert factual3["comparable"] == False  # noqa: E712
    assert pd.isna(factual3["gap"])
    assert factual2["comparable"] == True  # noqa: E712
    assert close(factual2["gap"], 1 / 3)


def test_side_gap_equal_difficulty_cells_including_missing_difficulty():
    table = metrics.detection(kit.findings(), kit.matches(), by=("dimension", "side", "intensity", "difficulty"))
    gap = metrics.side_gap(table)
    obvious = kit.row(gap, dimension="factual_accuracy", intensity=2, difficulty="obvious")
    hard = kit.row(gap, dimension="factual_accuracy", intensity=2, difficulty="hard")
    assert close(obvious["gap"], 0.0)  # 2/2 on both sides
    assert close(hard["gap"], -0.5)  # left 1/2, right 0/2
    none_both = gap[gap["difficulty"].isna() & (gap["dimension"] == "abusiveness") & (gap["intensity"] == 0)]
    assert len(none_both) == 1
    assert none_both["comparable"].tolist() == [True]  # a missing difficulty on both sides is the same cell
    assert close(none_both["gap"].iloc[0], -1.0)
    assert len(gap) == 8  # F2 obvious, F2 hard, F3, F4 (left only), F1 (right only), A3, A4, A0
    assert int(gap["comparable"].sum()) == 6


def test_side_gap_false_positives_table():
    gap = metrics.side_gap(metrics.false_positives(kit.issues()))
    factual = kit.row(gap, dimension="factual_accuracy")
    assert close(factual["gap"], -0.5)
    assert factual["comparable"] == True  # noqa: E712
    abusive = kit.row(gap, dimension="abusiveness")  # right side has n_valid_issues 0
    assert abusive["comparable"] == False  # noqa: E712
    assert pd.isna(abusive["gap"])
    clarity = kit.row(gap, dimension="clarity")
    assert clarity["comparable"] == False  # noqa: E712
    assert len(gap) == 3


def test_side_gap_explicit_keep_pools_over_other_columns_only_by_being_absent_from_the_table():
    table = metrics.detection(kit.findings(), kit.matches(), by=("side", "intensity"))
    gap = metrics.side_gap(table, keep=("intensity",))
    assert len(gap) == 5  # intensities 0,1,2,3,4
    r2 = kit.row(gap, intensity=2)
    assert close(r2["gap"], 0.5 - 0.75)
    r1 = kit.row(gap, intensity=1)
    assert r1["comparable"] == False  # noqa: E712


def test_side_gap_rejects_sides_other_than_left_and_right():
    table = det_table()
    table.loc[0, "side"] = "centre"
    with pytest.raises(ValueError, match="centre"):
        metrics.side_gap(table)


def test_side_gap_missing_side_or_value_column_raises_naming_it():
    with pytest.raises(ValueError, match="side"):
        metrics.side_gap(det_table().drop(columns="side"))
    with pytest.raises(ValueError, match="rate"):
        metrics.side_gap(det_table().drop(columns="rate"))


def test_side_gap_swapping_the_sides_argument_flips_the_sign():
    normal = metrics.side_gap(det_table())
    flipped = metrics.side_gap(det_table(), sides=("right", "left"))
    for (dimension, intensity), (left, right) in DET_GAPS.items():
        assert close(kit.row(normal, dimension=dimension, intensity=intensity)["gap"], right - left)
        assert close(kit.row(flipped, dimension=dimension, intensity=intensity)["gap"], left - right)
