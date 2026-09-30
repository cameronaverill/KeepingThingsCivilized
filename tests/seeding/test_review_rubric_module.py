"""review_markdown, the judge rubric file, and the purity of the seeding package."""
import ast
import re
from pathlib import Path

import pytest
import seeding_kit as kit
from seeding.facts import load_facts
from seeding.review import review_markdown
from seeding.seeds import build_seeds

ROOT = Path(__file__).resolve().parents[2]
RUBRIC = ROOT / "rubrics" / "factual_tag_v1.md"


class TestReview:
    def test_returns_string(self):
        assert isinstance(review_markdown([kit.ready_range()]), str)

    def test_ready_fact_shows_metadata(self):
        out = review_markdown([kit.ready_range(source_note="Source XYZ")])
        for needle in ("range_fact", "statistic", "Between 500 and 560 things exist", "Source XYZ"):
            assert needle in out

    def test_ready_fact_shows_all_six_seed_claims(self):
        f = kit.ready_range()
        out = review_markdown([f])
        assert [s.false_claim in out for s in build_seeds(f)] == [True] * 6

    def test_ready_fact_shows_directions_and_sides(self):
        out = review_markdown([kit.ready_range()]).lower()
        assert ("inflate" in out, "deflate" in out, "left" in out, "right" in out) == (True,) * 4

    def test_ready_non_statistic_shows_claims(self):
        out = review_markdown([kit.ready_nonstat("qualitative")])
        assert ("claim left 1" in out, "claim right 3" in out, "qualitative_fact" in out) == (True,) * 3

    def test_one_section_per_fact_all_ids_present(self):
        out = review_markdown([kit.ready_range(), kit.ready_nonstat("law"), kit.stat(id="third")])
        assert ("range_fact" in out, "law_fact" in out, "third" in out) == (True,) * 3

    def test_unverified_reason_listed(self):
        assert "owner has not verified" in review_markdown([kit.stat()]).lower()

    def test_inflate_favors_reason_listed(self):
        out = review_markdown([kit.stat(owner_verified_true=True)]).lower()
        assert "inflate_favors not set" in out
        assert "owner has not verified" not in out

    def test_mirrors_reason_listed(self):
        out = review_markdown([kit.nonstat(owner_verified_true=True)]).lower()
        assert "mirrors not approved" in out
        assert "owner has not verified" not in out

    def test_not_ready_fact_shows_no_seed_table(self):
        assert "| Side |" not in review_markdown([kit.stat()])

    def test_ready_fact_shows_seed_table_header(self):
        assert "| Side |" in review_markdown([kit.ready_range()])

    def test_ready_fact_whose_seeds_cannot_be_built_does_not_raise(self):
        out = review_markdown([kit.ready_stat(true_values=[60], max_value=100)])
        assert "stat_fact" in out

    def test_ready_fact_has_no_missing_reasons(self):
        out = review_markdown([kit.ready_range()]).lower()
        assert ("owner has not verified" in out, "inflate_favors not set" in out) == (False, False)

    @pytest.mark.parametrize("fact", [
        kit.stat(), kit.nonstat(), kit.nonstat("qualitative", owner_verified_true=True),
        kit.stat(true_values=[60], max_value=100, owner_verified_true=True),
        kit.ready_nonstat(error_claims=None),
    ])
    def test_never_raises_for_not_ready(self, fact):
        assert isinstance(review_markdown([fact]), str)

    def test_shipped_facts_review_lists_all_ids(self):
        facts = load_facts()
        out = review_markdown(facts)
        assert [f.id in out for f in facts] == [True] * 8

    def test_shipped_facts_all_report_missing_verification(self):
        assert review_markdown(load_facts()).lower().count("owner has not verified") >= 8

    def test_empty_list_does_not_raise(self):
        assert isinstance(review_markdown([]), str)


@pytest.fixture(scope="module")
def rubric():
    return RUBRIC.read_text()


class TestRubric:
    def test_file_exists(self):
        assert RUBRIC.is_file()

    @pytest.mark.parametrize("tag", ["0", "1", "2", "3", "N/A"])
    def test_all_five_tags_defined_as_own_line(self, rubric, tag):
        assert re.search(rf"(?m)^[\s`*#>-]*{re.escape(tag)}[`*]*\s*[=:.)-]", rubric)

    def test_exactly_five_tag_lines(self, rubric):
        assert len(re.findall(r"(?m)^[\s`*#>-]*(?:0|1|2|3|N/A)[`*]*\s*[=:.)-]", rubric)) == 5

    @pytest.mark.parametrize("phrase", [
        "did not spot",
        "did not correct",
        "incorrect correction",
        "no factual claim",
    ])
    def test_tag_meanings(self, rubric, phrase):
        assert phrase in rubric.lower()

    def test_second_task_counts_unseeded_errors(self, rubric):
        low = rubric.lower()
        assert ("count" in low, "not the seeded" in low or "other than the seeded" in low) == (True, True)

    @pytest.mark.parametrize("phrase", ["count only", "do not judge whether"])
    def test_second_task_is_count_only(self, rubric, phrase):
        assert phrase in rubric.lower()

    def test_judge_inputs_named_together_as_given(self, rubric):
        low = rubric.lower().replace("\u2019", "'")
        assert re.search(r"given[^.]*seeded false claim[^.]*true fact[^.]*moderator's response", low)

    @pytest.mark.parametrize("phrase", ["never told", "political side", "severity level"])
    def test_blindness_statement(self, rubric, phrase):
        assert phrase in rubric.lower()


PKG = ROOT / "seeding"
FORBIDDEN = {"django", "anthropic", "requests", "httpx", "urllib", "urllib3", "socket", "http", "aiohttp", "moderation"}


def py_files():
    return sorted(PKG.rglob("*.py"))


def imported_roots(path):
    roots = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            roots += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.append(node.module.split(".")[0])
    return roots


class TestPurity:
    def test_package_files_exist(self):
        assert [p.name for p in py_files() if p.parent == PKG] == ["__init__.py", "facts.py", "review.py", "seeds.py"]

    def test_no_forbidden_imports_anywhere(self):
        found = {p.name: sorted(set(imported_roots(p)) & FORBIDDEN) for p in py_files()}
        assert {k: v for k, v in found.items() if v} == {}

    def test_importing_does_not_load_django_or_anthropic(self):
        import subprocess
        import sys

        code = ("import sys, seeding.facts, seeding.seeds, seeding.review;"
                "print(sorted(m for m in ('django','anthropic') if m in sys.modules))")
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
        assert (out.returncode, out.stdout.strip()) == (0, "[]")

    def test_tunables_read_by_attribute_at_call_time(self):
        src = (PKG / "seeds.py").read_text()
        assert "from config import tunables" in src
        assert "SEED_LEVEL_FACTORS" in src
