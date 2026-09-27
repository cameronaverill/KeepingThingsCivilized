"""Cross-cutting contract checks: input validation, non-mutation, deterministic order, and randomised exact properties
(counts add up, rates in [0,1], the decomposition identity, side-swap antisymmetry, shuffle invariance). The oracle in
the kit is plain python over the raw rows and shares no code with analysis.metrics."""
import metrics9b_kit as kit
import pandas as pd
import pytest
from analysis import metrics

SEEDS = list(range(12))


# ------------------------------------------------------------------------------------------------ validation errors

@pytest.mark.parametrize("column", ["finding_id", "dimension", "side", "intensity"])
def test_detection_and_action_findings_missing_column_names_it(column):
    findings = kit.findings().drop(columns=column)
    with pytest.raises(ValueError, match=column):
        metrics.detection(findings, kit.matches())
    with pytest.raises(ValueError, match=column):
        metrics.action(findings, kit.matches())


@pytest.mark.parametrize("column", ["finding_id", "issue_valid"])
def test_detection_matches_missing_column_names_it(column):
    with pytest.raises(ValueError, match=column):
        metrics.detection(kit.findings(), kit.matches().drop(columns=column))


@pytest.mark.parametrize("column", ["finding_id", "issue_valid", "acted"])
def test_action_and_decomposition_matches_missing_column_names_it(column):
    with pytest.raises(ValueError, match=column):
        metrics.action(kit.findings(), kit.matches().drop(columns=column))
    with pytest.raises(ValueError, match=column):
        metrics.gap_decomposition(kit.findings(), kit.matches().drop(columns=column))


@pytest.mark.parametrize("column", ["finding_id", "dimension", "side", "intensity"])
def test_decomposition_findings_missing_column_names_it(column):
    with pytest.raises(ValueError, match=column):
        metrics.gap_decomposition(kit.findings().drop(columns=column), kit.matches())


def test_several_missing_columns_are_all_named():
    findings = kit.findings().drop(columns=["finding_id", "intensity"])
    with pytest.raises(ValueError) as caught:
        metrics.detection(findings, kit.matches())
    assert "finding_id" in str(caught.value)
    assert "intensity" in str(caught.value)


@pytest.mark.parametrize("column", ["dimension", "side", "validity", "matched"])
def test_false_positives_missing_column_names_it(column):
    with pytest.raises(ValueError, match=column):
        metrics.false_positives(kit.issues().drop(columns=column))


@pytest.mark.parametrize("column", ["side", "validity", "rejection_reason"])
def test_data_quality_issues_missing_column_names_it(column):
    with pytest.raises(ValueError, match=column):
        metrics.data_quality(kit.issues().drop(columns=column), kit.runs())


@pytest.mark.parametrize("column", ["side", "status", "failure_reason"])
def test_data_quality_runs_missing_column_names_it(column):
    with pytest.raises(ValueError, match=column):
        metrics.data_quality(kit.issues(), kit.runs().drop(columns=column))


def test_a_by_column_the_frame_does_not_have_is_an_error_naming_it():
    with pytest.raises(ValueError, match="nonsense"):
        metrics.detection(kit.findings(), kit.matches(), by=("dimension", "nonsense"))
    with pytest.raises(ValueError, match="nonsense"):
        metrics.false_positives(kit.issues(), by=("nonsense",))


def test_duplicate_finding_ids_raise():
    findings = pd.concat([kit.findings(), kit.findings().head(1)], ignore_index=True)
    with pytest.raises(ValueError, match="f1"):
        metrics.detection(findings, kit.matches())


def test_matches_for_an_unknown_finding_are_ignored():
    extra = pd.DataFrame([("nope", "ix", True, True)], columns=kit.MATCH_COLUMNS)
    with_stray = pd.concat([kit.matches(), extra], ignore_index=True)
    kit.same_frame(metrics.detection(kit.findings(), with_stray), metrics.detection(kit.findings(), kit.matches()))
    kit.same_frame(metrics.action(kit.findings(), with_stray), metrics.action(kit.findings(), kit.matches()))


# -------------------------------------------------------------------------------------------------- non-mutation

def test_no_function_mutates_its_inputs():
    findings, matches, issues, runs = kit.findings(), kit.matches(), kit.issues(), kit.runs()
    findings["not_scorable"] = False
    before = kit.snapshot(findings, matches, issues, runs)
    det = metrics.detection(findings, matches)
    act = metrics.action(findings, matches)
    metrics.false_positives(issues)
    metrics.data_quality(issues, runs, findings)
    metrics.gap_decomposition(findings, matches)
    det_before, act_before = kit.snapshot(det, act)
    metrics.side_gap(det)
    metrics.side_gap(act)
    for after, original in zip((findings, matches, issues, runs), before):
        kit.same_frame(after, original)
    kit.same_frame(det, det_before)
    kit.same_frame(act, act_before)


def test_results_are_new_frames_not_views_of_the_inputs():
    findings, matches = kit.findings(), kit.matches()
    table = metrics.detection(findings, matches)
    table.loc[:, "rate"] = 99.0
    kit.same_frame(findings, kit.findings())
    kit.same_frame(metrics.detection(findings, matches), metrics.detection(kit.findings(), kit.matches()))


# ---------------------------------------------------------------------------------------- deterministic order

def test_repeated_calls_give_identical_frames():
    for build in (
        lambda: metrics.detection(kit.findings(), kit.matches()),
        lambda: metrics.action(kit.findings(), kit.matches()),
        lambda: metrics.false_positives(kit.issues()),
        lambda: metrics.data_quality(kit.issues(), kit.runs()),
        lambda: metrics.gap_decomposition(kit.findings(), kit.matches()),
        lambda: metrics.side_gap(metrics.detection(kit.findings(), kit.matches())),
    ):
        kit.same_frame(build(), build())


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_input_row_order_does_not_change_any_output(seed):
    findings, matches, issues, runs = kit.findings(), kit.matches(), kit.issues(), kit.runs()
    s_findings, s_matches, s_issues, s_runs = (kit.shuffled(f, seed) for f in (findings, matches, issues, runs))
    kit.same_frame(metrics.detection(s_findings, s_matches), metrics.detection(findings, matches))
    kit.same_frame(metrics.action(s_findings, s_matches), metrics.action(findings, matches))
    kit.same_frame(metrics.false_positives(s_issues), metrics.false_positives(issues))
    kit.same_frame(metrics.data_quality(s_issues, s_runs), metrics.data_quality(issues, runs))
    kit.same_frame(metrics.gap_decomposition(s_findings, s_matches), metrics.gap_decomposition(findings, matches))
    det, s_det = metrics.detection(findings, matches), metrics.detection(s_findings, s_matches)
    kit.same_frame(metrics.side_gap(kit.shuffled(det, seed)), metrics.side_gap(s_det))


def test_rows_come_out_sorted_by_the_key_columns_with_a_fresh_range_index():
    table = metrics.detection(kit.findings(), kit.matches())
    keys = list(zip(table["dimension"], table["side"], table["intensity"]))
    assert keys == sorted(keys)
    assert table.index.tolist() == list(range(len(table)))
    fp = metrics.false_positives(kit.issues())
    assert list(zip(fp["dimension"], fp["side"])) == sorted(zip(fp["dimension"], fp["side"]))
    assert fp.index.tolist() == list(range(len(fp)))


def test_rows_with_a_missing_key_are_kept_and_sorted_last():
    by = ("side", "difficulty")
    table = metrics.detection(kit.findings(), kit.matches(), by=by)
    assert table["difficulty"].isna().tolist() == [False, False, True, False, False, True]
    assert table["side"].tolist() == ["left", "left", "left", "right", "right", "right"]
    assert table["n_findings"].tolist() == [3, 9, 1, 2, 9, 3]
    assert int(table["n_findings"].sum()) == 27


# ----------------------------------------------------------------------------------- randomised, exact properties

@pytest.mark.parametrize("seed", SEEDS)
def test_random_detection_and_action_match_the_python_oracle(seed):
    findings, matches = kit.random_case(seed)
    keys = ("dimension", "side", "intensity")
    oracle = kit.oracle_counts(findings, matches, keys)
    det = metrics.detection(findings, matches)
    act = metrics.action(findings, matches)
    assert len(det) == len(oracle) == len(act)
    assert int(det["n_findings"].sum()) == len(findings)
    for r in det.to_dict("records"):
        n, d, _ = oracle[(r["dimension"], r["side"], r["intensity"])]
        assert (r["n_findings"], r["n_detected"]) == (n, d)
        assert r["rate"] == d / n
    for r in act.to_dict("records"):
        _, d, a = oracle[(r["dimension"], r["side"], r["intensity"])]
        assert (r["n_detected"], r["n_acted"]) == (d, a)
        assert a <= d
        assert kit._equal(r["rate"], kit.ratio(a, d))


@pytest.mark.parametrize("seed", SEEDS)
def test_random_rates_are_within_bounds_and_nan_only_when_the_denominator_is_zero(seed):
    findings, matches = kit.random_case(seed)
    det = metrics.detection(findings, matches, by=("side", "difficulty"))
    act = metrics.action(findings, matches, by=("side", "difficulty"))
    assert ((det["rate"] >= 0) & (det["rate"] <= 1)).all()
    assert det["rate"].notna().all()
    assert ((act["rate"].dropna() >= 0) & (act["rate"].dropna() <= 1)).all()
    assert act["rate"].isna().tolist() == (act["n_detected"] == 0).tolist()
    assert int(det["n_findings"].sum()) == len(findings)
    assert int(act["n_detected"].sum()) == int(det["n_detected"].sum())


@pytest.mark.parametrize("seed", SEEDS)
def test_random_decomposition_identity_and_total(seed):
    findings, matches = kit.random_case(seed)
    oracle = kit.oracle_counts(findings, matches, ("dimension", "intensity", "side"))
    table = metrics.gap_decomposition(findings, matches)
    both_sides = {c[:2] for c in oracle if (*c[:2], "left") in oracle and (*c[:2], "right") in oracle}
    two_sided = table[[(d, i) in both_sides for d, i in zip(table["dimension"], table["intensity"])]]
    assert len(two_sided) == len(both_sides)
    for r in two_sided.to_dict("records"):
        n_l, d_l, a_l = oracle[(r["dimension"], r["intensity"], "left")]
        n_r, d_r, a_r = oracle[(r["dimension"], r["intensity"], "right")]
        assert abs(r["gap_total"] - (a_r / n_r - a_l / n_l)) <= 1e-12
    defined = table.dropna(subset=["detection_component", "action_component", "gap_total"])
    assert ((defined["detection_component"] + defined["action_component"] - defined["gap_total"]).abs() <= 1e-12).all()
    for r in defined.to_dict("records"):
        n_l, d_l, a_l = oracle[(r["dimension"], r["intensity"], "left")]
        n_r, d_r, a_r = oracle[(r["dimension"], r["intensity"], "right")]
        want_det = (d_r / n_r - d_l / n_l) * (a_l / d_l + a_r / d_r) / 2
        want_act = (d_l / n_l + d_r / n_r) / 2 * (a_r / d_r - a_l / d_l)
        assert abs(r["detection_component"] - want_det) <= 1e-12
        assert abs(r["action_component"] - want_act) <= 1e-12


@pytest.mark.parametrize("seed", SEEDS)
def test_random_swapping_left_and_right_negates_every_gap(seed):
    findings, matches = kit.random_case(seed)
    table = metrics.gap_decomposition(findings, matches)
    swapped = metrics.gap_decomposition(kit.swap_sides(findings), matches)
    assert table[["dimension", "intensity"]].equals(swapped[["dimension", "intensity"]])
    for column in ("gap_total", "detection_component", "action_component"):
        assert (table[column].isna() == swapped[column].isna()).all()
        assert ((table[column] + swapped[column]).abs().dropna() <= 1e-12).all()
    gap = metrics.side_gap(metrics.detection(findings, matches))
    swapped_gap = metrics.side_gap(metrics.detection(kit.swap_sides(findings), matches))
    assert gap["comparable"].tolist() == swapped_gap["comparable"].tolist()
    assert ((gap["gap"] + swapped_gap["gap"]).abs().dropna() <= 1e-12).all()


@pytest.mark.parametrize("seed", SEEDS)
def test_random_shuffled_inputs_give_identical_outputs(seed):
    findings, matches = kit.random_case(seed)
    s_findings, s_matches = kit.shuffled(findings, seed), kit.shuffled(matches, seed + 100)
    kit.same_frame(metrics.detection(s_findings, s_matches), metrics.detection(findings, matches))
    kit.same_frame(metrics.action(s_findings, s_matches), metrics.action(findings, matches))
    kit.same_frame(metrics.gap_decomposition(s_findings, s_matches), metrics.gap_decomposition(findings, matches))


@pytest.mark.parametrize("seed", SEEDS)
def test_random_side_gap_is_right_minus_left_where_comparable_and_lists_every_cell(seed):
    findings, matches = kit.random_case(seed)
    det = metrics.detection(findings, matches, by=("dimension", "side", "intensity", "difficulty"))
    gap = metrics.side_gap(det)
    cells = {(d, i, x if isinstance(x, str) else None) for d, i, x in zip(det["dimension"], det["intensity"], det["difficulty"])}
    assert len(gap) == len(cells)
    for r in gap[gap["comparable"]].to_dict("records"):
        assert abs(r["gap"] - (r["rate_right"] - r["rate_left"])) <= 1e-12
    assert gap[~gap["comparable"]]["gap"].isna().all()


@pytest.mark.parametrize("seed", SEEDS)
def test_random_false_positive_counts_add_up(seed):
    findings, _ = kit.random_case(seed)
    issues = pd.DataFrame(
        {
            "issue_id": [f"i{k}" for k in range(len(findings))],
            "conversation_id": findings["conversation_id"],
            "message_id": findings["message_id"],
            "side": findings["side"],
            "dimension": findings["dimension"],
            "intensity": findings["intensity"].astype(float),
            "validity": ["rejected" if k % 4 == 0 else "valid" for k in range(len(findings))],
            "rejection_reason": ["quote_not_found" if k % 4 == 0 else "" for k in range(len(findings))],
            "matched": [k % 3 == 0 for k in range(len(findings))],
        }
    )
    table = metrics.false_positives(issues)
    valid = issues[issues["validity"] == "valid"]
    assert int(table["n_valid_issues"].sum()) == len(valid)
    assert int(table["n_false"].sum()) == int((~valid["matched"]).sum())
    assert (table["n_false"] <= table["n_valid_issues"]).all()
    assert table["rate"].isna().tolist() == (table["n_valid_issues"] == 0).tolist()
    rates = table["rate"].dropna()
    assert ((rates >= 0) & (rates <= 1)).all()
