"""Shared helpers for the step 10a tests (tests/seed_topics/). Test modules import this module by name (pytest puts the
test folder on sys.path); nothing imports from a conftest. The code under test is imported inside functions so a missing
feature fails the tests that need it, one by one."""
import copy
import itertools
import json
import re
from html.parser import HTMLParser
from io import StringIO
from pathlib import Path

from django.core.management import call_command

REPO = Path(__file__).resolve().parents[2]
PACKAGED = REPO / "forum" / "seed_topics.json"
COMMAND_SOURCE = REPO / "forum" / "management" / "commands" / "seed_topics.py"
GOLDEN = REPO / "golden" / "transcripts"

WARMUP_LEANS_RATIONALE = "No clear left/right coding: a low-stakes topic used to validate the pipeline (plan section 9)."
GOLDEN_TITLES = ("Rent control", "Drug decriminalization", "Sanctuary cities")
WARMUP_TITLES_FOLDED = ("school start times", "bike lanes", "remote work")
RENT_PROPOSITION = "Cities should cap how much landlords can raise rents each year."

SCHEMES = {"compass": ("economic", "social"), "us_partisan": ("party",)}
SIDES = ("pro", "con")
ENTRY_KEYS = ("title", "description", "proposition", "opposing_position", "leans")

SUMMARY = re.compile(r"created (\d+), updated (\d+), unchanged (\d+)", re.IGNORECASE)

_counter = itertools.count(1)


def uniq(prefix="x"):
    return f"{prefix}{next(_counter):04d}"


# --- data builders -------------------------------------------------------------------------------------------------------

def make_leans(value=0.5, rationale="This side's usual case rests on a plain, stated reason."):
    """A valid leans object: both sides, both schemes, every axis."""
    return {
        side: {
            scheme: {axis: {"value": value if side == "pro" else -value, "rationale": rationale} for axis in axes}
            for scheme, axes in SCHEMES.items()
        }
        for side in SIDES
    }


def make_entry(title=None, **overrides):
    title = title or uniq("Seeded testing topic ")
    entry = {
        "title": title,
        "description": f"About {title}.",
        "proposition": f"The claim behind {title} is worth talking through.",
        "opposing_position": f"The claim behind {title} is not worth talking through.",
        "leans": make_leans(),
    }
    entry.update(overrides)
    return entry


def make_entries(n=3):
    return [make_entry(f"Testing topic {chr(65 + i)}") for i in range(n)]


def write_file(directory, data, name=None):
    """Write ``data`` (a Python object, or a str written as is) as the seed file; return its path as a str."""
    path = Path(directory) / (name or f"{uniq('seed')}.json")
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return str(path)


def packaged():
    return json.loads(PACKAGED.read_text(encoding="utf-8"))


def packaged_copy():
    return copy.deepcopy(packaged())


def by_title(entries, title):
    return next(e for e in entries if e["title"] == title)


def is_warmup(entry):
    return entry["title"].casefold() in WARMUP_TITLES_FOLDED


# --- running the command -------------------------------------------------------------------------------------------------

def run(*args):
    """Run ``manage.py seed_topics *args`` in-process and return what it printed on stdout."""
    out = StringIO()
    call_command("seed_topics", *args, stdout=out, stderr=StringIO())
    return out.getvalue()


def run_file(path, *extra):
    return run("--file", str(path), *extra)


def counts(output):
    """(created, updated, unchanged) from the summary line of a report."""
    found = SUMMARY.search(output)
    assert found, f"no 'created n, updated m, unchanged k' line in the output: {output!r}"
    return tuple(int(part) for part in found.groups())


# --- database views ------------------------------------------------------------------------------------------------------

def snapshot():
    """Every Topic row with every column, in id order (created_at and leans included)."""
    from forum.models import Topic

    return list(Topic.objects.order_by("id").values())


def topic_count():
    from forum.models import Topic

    return Topic.objects.count()


def topic_by_title(title):
    from forum.models import Topic

    return Topic.objects.get(title=title)


def make_user(name=None):
    from django.contrib.auth import get_user_model

    name = name or uniq("quillmoth")
    return get_user_model().objects.create_user(username=name, email=f"{name}@mailbox.example")


def make_user_topic(user, proposition, title="", hidden=False, leans=None, description=""):
    from forum.models import Topic

    return Topic.objects.create(
        title=title, proposition=proposition, created_by=user, hidden=hidden, leans=leans or {}, description=description
    )


# --- the home page, read as forms and buttons ----------------------------------------------------------------------------

class _Forms(HTMLParser):
    """Collects every <form>: its action, its named inputs and the visible text of its buttons."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self._form = None
        self._button = None

    def handle_starttag(self, tag, attrs):
        attrs = {k: (v if v is not None else "") for k, v in attrs}
        if tag == "form":
            self._form = {"action": attrs.get("action", ""), "method": attrs.get("method", "get").lower(), "inputs": {}, "buttons": []}
            self.forms.append(self._form)
        elif tag == "input" and self._form is not None and attrs.get("name"):
            self._form["inputs"][attrs["name"]] = attrs.get("value", "")
        elif tag == "button" and self._form is not None:
            self._button = []
            if attrs.get("name"):
                self._form["inputs"][attrs["name"]] = attrs.get("value", "")

    def handle_data(self, data):
        if self._button is not None:
            self._button.append(data)

    def handle_endtag(self, tag):
        if tag == "button" and self._button is not None:
            self._form["buttons"].append(re.sub(r"\s+", " ", "".join(self._button)).strip())
            self._button = None
        elif tag == "form":
            self._form = None


def enter_forms(page_html):
    """{topic id: [(side, button text), ...]} for every form on the page that posts to /p/<id>/enter/. The side is the form's
    `side` field (hidden input or the button's own name/value)."""
    parser = _Forms()
    parser.feed(page_html)
    found = {}
    for form in parser.forms:
        match = re.fullmatch(r"/p/(\d+)/enter/", form["action"])
        if match and form["method"] == "post":
            found.setdefault(int(match.group(1)), []).append(
                (form["inputs"].get("side"), " ".join(form["buttons"]))
            )
    return found
