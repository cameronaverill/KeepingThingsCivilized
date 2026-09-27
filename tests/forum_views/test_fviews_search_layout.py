"""7b: the Search button keeps its label on one line and is never squeezed by the input beside it."""
import re
from pathlib import Path

CSS = (Path(__file__).resolve().parents[2] / "forum" / "static" / "forum" / "site.css").read_text()


def rule_body(selector):
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert match is not None, f"no rule for {selector}"
    return match.group(1)


def test_the_search_button_does_not_shrink_or_wrap():
    body = rule_body(".search-row .btn")
    assert "flex: 0 0 auto" in body
    assert "white-space: nowrap" in body


def test_the_search_input_takes_the_remaining_width_and_may_shrink():
    body = rule_body(".search-row input")
    assert "flex: 1 1 auto" in body
    assert "min-width: 0" in body
