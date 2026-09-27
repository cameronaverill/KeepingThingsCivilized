"""README.md exists, stays short enough to read, and has a section for each theme a newcomer needs."""
import re

import pytest

import readme_kit

# theme name -> pattern that at least one heading must match (case-insensitive, deliberately tolerant)
REQUIRED_THEMES = {
    "what the project is": r"what|overview|about|introduction|the project|purpose",
    "setup": r"set ?up|install|getting started|quick ?start|prerequisite|requirements",
    "running": r"\brun|\bstart|development server|dev server",
    "tunables": r"tunable|configur|settings",
    "cost controls": r"cost|spend|budget|money|kill switch",
    "end-to-end trace": r"trace|end.to.end|walk.?through|life of|lifecycle|follow|how a (message|conversation)|flow",
    "management commands": r"command",
    "tests": r"\btest",
    "security rules": r"secur|secret|safety",
    "repository map": r"map|layout|structure|repository|directory|folder|what.s where|where things",
    "docs": r"\bdocs\b|documentation|further reading|read more",
    "known limits": r"limit|known|not yet|caveat|missing|not built|not implemented|roadmap|future",
}


def test_readme_exists():
    assert readme_kit.README_PATH.is_file()


def test_readme_is_under_500_lines(readme):
    assert len(readme.lines) < 500


def test_readme_is_not_trivially_short(readme):
    assert len(readme.lines) >= 60


def test_readme_starts_with_a_top_level_title(readme):
    assert re.match(r"# \S", readme.lines[0])


def test_readme_has_exactly_one_top_level_title(readme):
    top_level = [line for line in _outside_fences(readme.text) if line.startswith("# ")]
    assert len(top_level) == 1


def _outside_fences(text):
    inside = False
    for line in text.splitlines():
        if line.startswith("```"):
            inside = not inside
            continue
        if not inside:
            yield line


@pytest.mark.parametrize("theme", sorted(REQUIRED_THEMES))
def test_readme_has_a_section_for_theme(readme, theme):
    pattern = re.compile(REQUIRED_THEMES[theme], re.IGNORECASE)
    matching = [h for h in readme.headings if pattern.search(h)]
    assert matching, f"no heading matches the theme {theme!r} ({REQUIRED_THEMES[theme]}); headings: {readme.headings}"


def _sections(text):
    """(level, title, body lines) for every heading outside fenced code; a section's body runs to the next heading of
    the same or a higher level and includes fenced blocks and sub-sections."""
    headings = []
    inside = False
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("```"):
            inside = not inside
        match = None if inside else re.match(r"(#{1,6}) (.+)", line)
        if match:
            headings.append((index, len(match.group(1)), match.group(2)))
    sections = []
    for position, (index, level, title) in enumerate(headings):
        end = len(lines)
        for later_index, later_level, _ in headings[position + 1:]:
            if later_level <= level:
                end = later_index
                break
        sections.append((level, title, lines[index + 1:end]))
    return sections


def test_no_heading_is_left_empty(readme):
    empty = [title for _, title, body in _sections(readme.text) if not "".join(body).strip()]
    assert empty == []


def test_no_placeholder_text_is_left_behind(readme):
    hits = readme_kit.lines_matching(readme.text, r"(?i)\bTODO\b|\bTBD\b|FIXME|lorem ipsum|XXX")
    assert hits == []
