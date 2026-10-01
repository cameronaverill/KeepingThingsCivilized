"""manage.py summarize_pilot: reads the judgments file, prints and writes the markdown, calls nothing."""
import json
from pathlib import Path

import judge_kit as kit
import pytest

from test_analyze import STANDARD, true_row

PATH = Path(f"generated/judgments/{kit.EXPERIMENT}.jsonl")


def write_rows(rows, path=PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def command(*args):
    return kit.summarize_pilot("--experiment", kit.EXPERIMENT, *args)


class TestSummarizePilot:
    def test_it_prints_the_summary(self):
        write_rows(STANDARD)
        result = command()
        assert (result.exc, "PRELIMINARY: n = 6" in result.text) == (None, True)

    def test_it_writes_pilot_markdown_by_default_equal_to_what_it_prints(self):
        write_rows(STANDARD)
        result = command()
        written = Path(f"generated/pilot_{kit.EXPERIMENT}.md").read_text(encoding="utf-8")
        assert (written.startswith("# PRELIMINARY: n = 6"), written.strip() in result.text) == (True, True)

    def test_output_chooses_the_file(self, tmp_path):
        write_rows(STANDARD)
        target = tmp_path / "elsewhere" / "summary.md"
        command("--output", str(target))
        assert (target.read_text(encoding="utf-8").startswith("# PRELIMINARY"), Path(f"generated/pilot_{kit.EXPERIMENT}.md").exists()) == (True, False)

    def test_it_matches_render_markdown_of_the_rows(self):
        from seeding import analyze

        rows = STANDARD + [true_row("left", flagged=2)]
        write_rows(rows)
        command()
        assert Path(f"generated/pilot_{kit.EXPERIMENT}.md").read_text(encoding="utf-8") == analyze.render_markdown(analyze.summarize(rows))

    def test_a_missing_judgments_file_is_an_error_and_writes_nothing(self):
        result = command()
        assert (result.exc is not None, kit.files_under("generated")) == (True, [])

    def test_the_experiment_argument_is_required(self):
        assert kit.summarize_pilot().exc is not None

    def test_it_makes_no_llm_call_and_needs_no_database_rows(self, fake):
        write_rows(STANDARD)
        client = fake(kit.verdict())
        command()
        assert (client.calls, kit.ledger()) == ([], [])

    def test_it_never_prints_the_key(self):
        write_rows(STANDARD)
        assert kit.DUMMY_KEY not in command().text
