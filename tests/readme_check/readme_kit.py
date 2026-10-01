"""Helpers for the README checks: reading README.md and pulling out the things it claims (commands, paths, tunables,
environment variables, headings). Pure functions; the tests themselves contain no conditional logic."""
import ast
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
README_PATH = REPO_ROOT / "README.md"
ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"

# Directories that are not part of the repository proper when searching for a bare file name.
SEARCH_SKIP_DIRS = {".venv", ".git", "node_modules", "__pycache__", ".pytest_cache", "scratchpad"}

# Paths the README may name although they are created at run time (or are the owner's private files), so they need
# not exist in a fresh checkout. Matched as the path itself or anything below it.
RUNTIME_CREATED_PATHS = (
    ".env",  # the owner's private settings file, copied from .env.example
    "db.sqlite3",  # the SQLite database, created by migrate
    ".venv",  # the virtual environment
    "scratchpad",  # scratch space, git-ignored
    "index.json",  # written by `export_all --output-dir DIR` into the chosen output directory
)

# A path the README names that does not exist is still fine on a line that says it is planned, not built.
NOT_BUILT_LINE = re.compile(r"(?i)\b(not built|not yet built|not yet|planned|not implemented|does not exist)\b")

PATH_EXTENSIONS = (
    "py", "md", "sh", "txt", "html", "js", "css", "json", "jsonl", "csv", "toml", "ini", "cfg", "yml", "yaml",
    "sqlite3", "example", "env",
)

UPPER_SNAKE = re.compile(r"(?<![A-Za-z0-9_])[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+(?![A-Za-z0-9_])")
ENV_PREFIXES = ("DJANGO_", "ANTHROPIC_", "ALERT_EMAIL", "EMAIL_BACKEND", "DEFAULT_FROM_EMAIL")
MANAGE_COMMAND = re.compile(r"manage\.py\s+([a-z][a-z0-9_]*)")
FENCE = re.compile(r"^```.*?^```[ \t]*$", re.MULTILINE | re.DOTALL)
INLINE = re.compile(r"`([^`\n]+)`")
HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)
ASSIGNMENT = re.compile(r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)[ \t]*=[ \t]*([^\s`,;)]+)")


@dataclass(frozen=True)
class Readme:
    text: str

    @classmethod
    def load(cls):
        assert README_PATH.is_file(), "README.md does not exist"
        return cls(README_PATH.read_text(encoding="utf-8"))

    @property
    def lines(self):
        return self.text.splitlines()

    @property
    def headings(self):
        return [m.group(2) for m in HEADING.finditer(_without_fences(self.text))]

    @property
    def fenced_blocks(self):
        return [m.group(0) for m in FENCE.finditer(self.text)]

    @property
    def inline_spans(self):
        return [m.group(1) for m in INLINE.finditer(_without_fences(self.text))]

    @property
    def code_spans(self):
        """Inline code spans plus the body lines of fenced blocks (the fence lines themselves dropped)."""
        block_lines = []
        for block in self.fenced_blocks:
            block_lines.extend(block.splitlines()[1:-1])
        return self.inline_spans + block_lines


def _without_fences(text):
    return FENCE.sub("", text)


# --- commands ---------------------------------------------------------------------------------------------------


def mentioned_commands(text):
    return sorted(set(MANAGE_COMMAND.findall(text)))


def project_command_names():
    """Every command defined under an app's management/commands folder (excluding the virtual environment)."""
    names = set()
    for path in REPO_ROOT.glob("*/management/commands/*.py"):
        if path.parts[len(REPO_ROOT.parts)] in SEARCH_SKIP_DIRS or path.name.startswith("_"):
            continue
        names.add(path.stem)
    return sorted(names)


# --- paths ------------------------------------------------------------------------------------------------------


def _strip_token(token):
    token = token.strip("\"'()[]{}<>,;:!?")
    token = token.rstrip(".")
    token = re.sub(r":\d+(?:-\d+)?$", "", token)  # file.py:120
    return token


def looks_like_repo_path(token):
    if not token or token.startswith(("-", "$", "~", "/", "#")):
        return False
    if any(ch in token for ch in ("=", "<", ">", "|", "@", " ")) or "://" in token:
        return False
    if token.endswith("/") and "/" in token[:-1]:
        return True
    if "/" in token:
        return re.fullmatch(r"[A-Za-z0-9_.*{}\-/]+", token) is not None
    ext = token.rsplit(".", 1)[-1] if "." in token else ""
    return ext in PATH_EXTENSIONS and re.fullmatch(r"[A-Za-z0-9_.*\-]+", token) is not None


def _tokens(span):
    return [_strip_token(t) for t in span.split()]


def path_candidates(readme):
    """Repository paths named in the README as (path, in_fenced_block). Inline code spans: the whole span or each
    word. Fenced blocks (commands and the repository map): non-comment words that end in a known extension or in `/`."""
    found = set()
    for span in readme.inline_spans:
        found.update((t, False) for t in _tokens(span) if looks_like_repo_path(t))
    for block in readme.fenced_blocks:
        for line in block.splitlines()[1:-1]:
            code = "" if line.lstrip().startswith("#") else line.split(" #")[0]
            for token in _tokens(code):
                if looks_like_repo_path(token) and (token.endswith("/") or _has_known_extension(token)):
                    found.add((token, True))
    return sorted(found)


def _has_known_extension(token):
    return "." in token and token.rsplit(".", 1)[-1] in PATH_EXTENSIONS


def gitignore_patterns():
    return {line.strip().rstrip("/") for line in (REPO_ROOT / ".gitignore").read_text().splitlines() if line.strip()}


def is_runtime_created(path):
    normalized = path.removeprefix("./").rstrip("/")
    return any(normalized == p or normalized.startswith(p + "/") for p in RUNTIME_CREATED_PATHS)


def _exists_at_root(rel):
    if "*" in rel:
        return any(True for _ in REPO_ROOT.glob(rel))
    if "{" in rel:
        return True  # brace patterns are not expanded; they are not checked
    return (REPO_ROOT / rel).exists()


def _exists_anywhere(rel):
    return any(not (SEARCH_SKIP_DIRS & set(p.relative_to(REPO_ROOT).parts)) for p in REPO_ROOT.rglob(rel))


def path_exists(path, in_fence=False):
    """True when the path (relative to the repository root) exists; a glob needs at least one match. A bare file name
    (no slash) may live anywhere in the repository, and so may a path inside the repository-map block (which shows
    entries relative to their parent folder)."""
    rel = path.removeprefix("./").rstrip("/")
    if _exists_at_root(rel):
        return True
    if "/" not in rel or in_fence:
        return _exists_anywhere(rel)
    return False


def _line_of(readme, path):
    return [line for line in readme.lines if path in line]


def missing_paths(readme):
    """Named paths that do not exist, apart from runtime-created ones, patterns that .gitignore really lists, and
    paths named only on lines that say they are planned / not built."""
    ignored = gitignore_patterns()
    missing = []
    for path, in_fence in path_candidates(readme):
        rel = path.removeprefix("./").rstrip("/")
        if is_runtime_created(path) or path_exists(path, in_fence) or rel in ignored:
            continue
        if all(NOT_BUILT_LINE.search(line) for line in _line_of(readme, path)):
            continue
        missing.append(path)
    return missing


# --- tunables and environment variables -------------------------------------------------------------------------


def tunable_names():
    from config import tunables

    return {n for n in vars(tunables) if n.isupper()}


def env_example_names():
    return set(re.findall(r"^([A-Z][A-Z0-9_]*)=", ENV_EXAMPLE_PATH.read_text(encoding="utf-8"), re.MULTILINE))


def upper_snake_in_code(readme):
    names = set()
    for span in readme.code_spans:
        names.update(UPPER_SNAKE.findall(span))
    return sorted(names)


def unknown_upper_snake(readme):
    known = tunable_names() | env_example_names()
    return [n for n in upper_snake_in_code(readme) if n not in known]


def env_like_names(text):
    """Names anywhere in the README (code or prose) that look like environment variables."""
    return sorted({n for n in UPPER_SNAKE.findall(text) if n.startswith(ENV_PREFIXES)})


def unknown_env_names(readme):
    known = env_example_names() | tunable_names()
    return [n for n in env_like_names(readme.text) if n not in known]


def _readme_value(raw):
    try:
        return ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return None


def wrong_tunable_values(readme):
    """`NAME = value` claims where NAME is a tunable: a number or None in the README must equal the real value.
    Booleans and strings are not compared (the README also shows values one can switch to, e.g. LLM_ENABLED = True)."""
    from config import tunables

    wrong = []
    for span in readme.code_spans:
        for name, raw in ASSIGNMENT.findall(span):
            if name not in vars(tunables):
                continue
            claimed = _readme_value(raw)
            actual = getattr(tunables, name)
            numeric_or_none = raw == "None" or isinstance(claimed, (int, float)) and not isinstance(claimed, bool)
            if not numeric_or_none:
                continue  # True/False/"sync": the README may show a value one can set, not the current one
            if isinstance(actual, Decimal) and isinstance(claimed, (int, float)):
                same = Decimal(str(claimed)) == actual
            else:
                same = claimed == actual
            if not same:
                wrong.append(f"{name} = {raw} (real value: {actual!r})")
    return wrong


# --- sentences and lines ----------------------------------------------------------------------------------------

NEGATION = re.compile(r"(?i)\b(never|not|no|don'?t|do not|must not|ignored|gitignored|git-ignored|instead of)\b|n't")


def lines_matching(text, pattern):
    return [(n, line) for n, line in enumerate(text.splitlines(), start=1) if re.search(pattern, line)]


def lines_matching_without_negation(text, pattern):
    return [(n, line) for n, line in lines_matching(text, pattern) if not NEGATION.search(line)]


def sentences(text):
    """The prose split into sentences, whitespace normalised (a sentence that wraps over two lines is one sentence).
    Fenced blocks are dropped; table rows and list items each start a new sentence."""
    out = []
    for block in re.split(r"\n\s*\n", _without_fences(text)):
        current = []
        for line in block.splitlines():
            if re.match(r"\s*(\||[-*+] |\d+\. |#)", line) and current:
                out.append(" ".join(current))
                current = []
            current.append(line.strip())
        out.append(" ".join(current))
    result = []
    for chunk in out:
        result.extend(part.strip() for part in re.split(r"(?<=[.!?;])\s+", chunk) if part.strip())
    return result


def sentences_matching(text, pattern):
    return [s for s in sentences(text) if re.search(pattern, s)]


def sentences_matching_without_negation(text, pattern):
    return [s for s in sentences_matching(text, pattern) if not NEGATION.search(s)]


def command_table_rows(text):
    """{command name: row text} for each table row whose first cell is a code span starting with a command name."""
    rows = {}
    for line in text.splitlines():
        match = re.match(r"\|\s*`([a-z][a-z0-9_]*)\b[^`]*`\s*\|", line)
        if match:
            rows[match.group(1)] = line
    return rows


def option_flags(text):
    return sorted(set(re.findall(r"(?<![\w-])(--[a-z][a-z0-9-]*)", text)))


def command_options(name):
    """Every option string the real command's argument parser accepts."""
    from django.core.management import get_commands, load_command_class

    command = load_command_class(get_commands()[name], name)
    parser = command.create_parser("manage.py", name)
    return {opt for action in parser._actions for opt in action.option_strings}


def wildcard_prefixes(readme):
    """`CALIBRATION_*` style names in code spans: the prefix before the star."""
    prefixes = set()
    for span in readme.code_spans:
        prefixes.update(re.findall(r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_)\*", span))
    return sorted(prefixes)


def django_base_options():
    """The options every Django command has (--verbosity, --settings, --skip-checks, ...)."""
    from django.core.management.base import BaseCommand

    parser = BaseCommand().create_parser("manage.py", "x")
    return {opt for action in parser._actions for opt in action.option_strings}
