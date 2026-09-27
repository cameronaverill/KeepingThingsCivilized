"""Arguments (min_iou validation, conversation scope, the recorded threshold) and the module rules of evaluation/matching.py."""
import ast
from pathlib import Path

import matching14b_kit as kit
import pytest

pytestmark = pytest.mark.django_db

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def w():
    return kit.build_world()


@pytest.mark.parametrize("bad", [0, 0.0, -0.1, 1.0000001, 2, float("nan")])
def test_min_iou_outside_zero_exclusive_to_one_inclusive_is_refused(w, bad):
    with pytest.raises(ValueError):
        kit.match(w.panel, min_iou=bad)
    assert kit.link_count() == 0


def test_a_refused_min_iou_creates_nothing_even_with_save_true(w):
    with pytest.raises(ValueError):
        kit.match(w.panel, min_iou=0, save=True)
    assert kit.link_count() == 0


def test_min_iou_one_links_only_identical_spans(w):
    report = kit.match(w.panel, min_iou=1)
    assert kit.links() == {(w.i1.pk, w.c1.pk, 1.0), (w.i4.pk, w.c1.pk, 1.0), (w.i5.pk, w.c5.pk, 1.0)}
    assert report.min_iou == 1


def test_the_report_records_the_threshold_used_the_panels_by_default(w):
    assert kit.match(w.panel, save=False).min_iou == 0.5


def test_the_report_records_an_explicit_threshold(w):
    assert kit.match(w.panel, min_iou=0.25, save=False).min_iou == 0.25


def test_a_lower_threshold_only_adds_links_and_a_later_run_at_the_panel_default_keeps_them(w):
    kit.match(w.panel, min_iou=0.4)
    assert kit.links() == kit.world_links(w) | {(w.i3.pk, w.c3.pk, 4 / 10)}
    kit.match(w.panel)
    assert kit.link_count() == 8  # links are never removed by a stricter run


def test_the_lower_threshold_report_lists_change_with_it(w):
    report = kit.match(w.panel, min_iou=0.4, save=False)
    assert report.issues_unmatched == [(w.i6.pk, w.m1.pk, kit.FA)]
    assert report.findings_unmatched == [(w.c4.pk, w.m1.pk, kit.FA, None), (w.c8.pk, w.m3.pk, kit.AB, 2.0)]
    assert (report.linked, report.created) == (8, 0)


def test_an_empty_conversation_list_means_no_conversation(w):
    report = kit.match(w.panel, [])
    assert (report.linked, report.created) == (0, 0)
    assert report.issues_unmatched == []
    assert report.findings_unmatched == []
    assert report.unrated_messages == []
    assert report.not_found_issues == []
    assert kit.link_count() == 0


def test_none_means_every_conversation(w):
    report = kit.match(w.panel, None)
    assert report.linked == 7


def test_conversation_ids_may_be_any_iterable_of_ids(w):
    report = kit.match(w.panel, (w.conv2.pk,))
    assert report.findings_unmatched == [(w.c8.pk, w.m3.pk, kit.AB, 2.0)]


def test_an_unknown_conversation_id_selects_nothing(w):
    report = kit.match(w.panel, [999999])
    assert (report.linked, report.issues_unmatched, report.findings_unmatched) == (0, [], [])


def test_scope_also_limits_which_issues_are_linked(w):
    kit.match(w.panel, [w.conv2.pk])
    assert kit.link_count() == 0
    kit.match(w.panel, [w.conv1.pk])
    assert kit.links() == kit.world_links(w)


def test_unmatched_helpers_accept_conversation_ids_and_an_empty_list_means_none(w):
    assert kit.pks(kit.unmatched_issues(w.panel, [])) == []
    assert kit.pks(kit.unmatched_findings(w.panel, [])) == []


def test_unmatched_helpers_are_ordered_by_id(w):
    ids = [f.pk for f in kit.unmatched_findings(w.panel)]
    assert ids == sorted(ids) and len(ids) == 8
    issue_ids = [i.pk for i in kit.unmatched_issues(w.panel)]
    assert issue_ids == sorted(issue_ids) and len(issue_ids) == 8


def test_the_helpers_agree_with_the_report_after_a_saved_run_at_the_same_threshold(w):
    report = kit.match(w.panel)
    assert kit.pks(kit.unmatched_issues(w.panel)) == [row[0] for row in report.issues_unmatched]
    assert kit.pks(kit.unmatched_findings(w.panel)) == [row[0] for row in report.findings_unmatched]


# --- module rules ------------------------------------------------------------------------------------------------------
def test_matching_uses_the_consensus_iou_function_itself():
    from evaluation import consensus, matching

    assert matching.iou is consensus.iou


def test_matching_exports_the_agreed_names():
    from evaluation import matching

    assert all(hasattr(matching, name) for name in ("match_issues", "MatchReport", "unmatched_issues", "unmatched_findings"))


def imported_modules(path):
    tree = ast.parse(path.read_text())
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = "." * node.level + (node.module or "")
            found.append(base)
            found += [f"{base}.{alias.name}" for alias in node.names]
    return found


def test_matching_imports_no_llm_client_gateway_errors_or_quote_module():
    modules = imported_modules(REPO / "evaluation" / "matching.py")
    forbidden = ("anthropic", "moderation.llm", "moderation.errors", "moderation.quotes", "moderation.fake_llm", "moderation.budget", "moderation.pricing", "requests", "httpx", "urllib")
    hits = [m for m in modules if any(m == f or m.startswith(f + ".") for f in forbidden)]
    assert hits == []


def test_the_site_apps_do_not_import_matching():
    hits = []
    for package in ("accounts", "forum", "moderation", "config"):
        for path in (REPO / package).rglob("*.py"):
            hits += [(str(path), m) for m in imported_modules(path) if m.split(".")[0] == "evaluation"]
    assert hits == []
