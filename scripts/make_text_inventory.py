#!/usr/bin/env python
"""Regenerate docs/user_facing_text.md: an inventory of every word a participant can see.

Run:  .venv/bin/python scripts/make_text_inventory.py

How it works
- Django is started with config.settings, then a THROWAWAY in-memory SQLite test database is created with Django's
  DiscoverRunner (the real db.sqlite3 is never opened: the script stops if the database is not an in-memory one).
- Pages are rendered for real with django.test.Client, then reduced to visible text by a small html.parser helper.
- Each visible line gets a source pointer (file:line) found by matching the text against the templates and against the
  string literals of forum/services.py, forum/viewmodels.py, forum/views.py and the accounts modules. The pointer is a
  hint found by text matching; a line with no pointer is dynamic text (a proposition, a number, a message).
- No real API call, no network, no .env, no git. The moderation worker is never started (MODERATION_RUN_MODE=worker).
- Only docs/user_facing_text.md is written.
"""
import ast
import datetime
import html
import io
import logging
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
os.environ.pop("ANTHROPIC_API_KEY", None)  # nothing here may ever reach the API

OUT = ROOT / "docs" / "user_facing_text.md"
OUT_OVERRIDE = None  # set by --out PATH (for example docs/user_facing_text.new.md when the owner has edited the file)
FT = "forum/templates/forum/"
AT = "accounts/templates/accounts/"


# =====================================================================================================================
# 1. HTML to visible text
# =====================================================================================================================

BLOCK = {
    "p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol", "section", "article", "header", "footer", "main",
    "nav", "form", "aside", "details", "summary", "table", "tr", "td", "th", "label", "select", "option", "fieldset",
    "legend", "blockquote", "pre", "hr", "body", "html",
}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
TEXT_ATTRS = ("placeholder", "aria-label", "title", "alt")


class Extractor(HTMLParser):
    """Visible text of a page, one line per block element.

    Links become "[link: text]", buttons "[button: text]" (a link styled as a button counts as a button). The attributes
    placeholder, aria-label, title and alt become their own lines "(attr) value". script, style, head and the content of
    textareas are dropped (the <title> is kept apart). An element carrying the `hidden` attribute is shown with the
    prefix "(hidden until the page needs it)". ``only_class`` / ``only_attr`` keep only what is inside such an element;
    ``skip_classes`` drops those subtrees. ``codes`` collects the values of ``only_attr`` (for example data-code).
    """

    HIDDEN = "(hidden until the page needs it) "

    def __init__(self, skip_classes=(), only_class=None, only_attr=None):
        super().__init__(convert_charrefs=True)
        self.skip_classes = set(skip_classes)
        self.only_class, self.only_attr = only_class, only_attr
        self.lines, self.codes, self.title = [], [], ""
        self.stack, self.buf, self.link = [], [], None
        self.in_title = False
        self.link_kind = "link"

    # -- state
    def _skipping(self):
        return any(e["skip"] for e in self.stack)

    def _hidden(self):
        return any(e["hidden"] for e in self.stack)

    def _allowed(self):
        if self._skipping():
            return False
        if self.only_class or self.only_attr:
            return any(e["only"] for e in self.stack)
        return True

    def _put(self, text):
        if not self._allowed():
            return
        if self.link is not None:
            self.link.append(text)
        else:
            self.buf.append(text)

    def _flush(self):
        text = " ".join("".join(self.buf).split())
        self.buf = []
        if text:
            self.lines.append((self.HIDDEN if self._hidden() else "") + text)

    # -- parser hooks
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = set((a.get("class") or "").split())
        if tag == "title":
            self.in_title = True
            return
        if tag in BLOCK or tag == "button" or (tag == "a" and "btn" in classes):
            self._flush()
        own_hidden = "hidden" in a
        skip = tag in ("script", "style", "textarea", "head") or bool(classes & self.skip_classes)
        only = bool((self.only_class and self.only_class in classes) or (self.only_attr and self.only_attr in a))
        if self.only_attr and self.only_attr in a:
            self.codes.append(a[self.only_attr])
        blockish = tag == "span" and any(c.startswith("my-") for c in classes)  # the parts of a row in Your discussions
        if blockish:
            self._flush()
        entry = {"tag": tag, "hidden": own_hidden, "skip": skip, "only": only, "block": blockish}
        if tag in VOID:
            if tag == "br":
                self._flush()
            # An element that is its own subtree: check the filters against a stack that includes it.
            self.stack.append(entry)
            if self._allowed():
                self._attr_lines(a)
                if tag == "input" and a.get("type") in ("submit", "button") and a.get("value"):
                    self.buf.append(f"[button: {a['value']}]")
                    self._flush()
            self.stack.pop()
            return
        self.stack.append(entry)
        if self._allowed():
            self._attr_lines(a)
        if tag == "a":
            self.link = []
            self.link_kind = "button" if "btn" in classes else "link"
        elif tag == "button":
            self.link = []
            self.link_kind = "button"

    def _attr_lines(self, a):
        for attr in TEXT_ATTRS:
            if a.get(attr):
                self._flush()
                self.lines.append((self.HIDDEN if self._hidden() else "") + f"({attr}) {a[attr]}")

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
            return
        if tag in ("a", "button") and self.link is not None:
            text = " ".join("".join(self.link).split())
            self.link = None
            if text:
                self.buf.append(f"[{self.link_kind}: {text}]")
            if tag == "button" or self.link_kind == "button":
                self._flush()
        if tag in BLOCK or (self.stack and self.stack[-1]["tag"] == tag and self.stack[-1].get("block")):
            self._flush()
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self.in_title:
            self.title += data
            return
        self._put(data)

    def close(self):
        super().close()
        self._flush()


def extract(page_html, **kw):
    ex = Extractor(**kw)
    ex.feed(page_html)
    ex.close()
    return ex


# =====================================================================================================================
# 2. Source pointers
# =====================================================================================================================

_TAG = re.compile(r"\{%.*?%\}|\{\{.*?\}\}|\{#.*?#\}")
_COMMENT = re.compile(r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}")
_ATTR = re.compile(r'(placeholder|aria-label|title|alt)="([^"{}]*)"')


class SourceIndex:
    def __init__(self):
        self.cache = {}

    def pieces(self, rel):
        if rel in self.cache:
            return self.cache[rel]
        path = ROOT / rel
        out = []
        if path.exists():
            if rel.endswith((".html", ".txt")):
                for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    line = _COMMENT.sub("", line)
                    for attr, value in _ATTR.findall(line):
                        if len(value.strip()) >= 3:
                            out.append((f"({attr}) {value.strip()}", n))
                    text = re.sub(r"<[^>]*>", "\x00", _TAG.sub("\x00", line))
                    for part in text.split("\x00"):
                        part = html.unescape(part).strip()
                        if len(part) >= 3:
                            out.append((part, n))
            elif rel.endswith(".py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                docstrings = set()
                for node in ast.walk(tree):
                    if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                        first = node.body[0] if node.body else None
                        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                            docstrings.add(id(first.value))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                        s = node.value.strip()
                        if (len(s) >= 8 and " " in s and len(s) < 400) or (len(s) >= 5 and s.isalpha() and s[0].isupper()):
                            out.append((s, node.lineno))
            elif rel.endswith(".json"):
                for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    m = re.match(r'\s*"(?:proposition|opposing_position)":\s*"(.*)",?\s*$', line)
                    if m and len(m.group(1)) >= 8:
                        out.append((m.group(1).replace('\\"', '"'), n))
        self.cache[rel] = out
        return out

    @staticmethod
    def _find(piece, text):
        start = 0
        while True:
            i = text.find(piece, start)
            if i < 0:
                return -1
            before = text[i - 1] if i > 0 else " "
            after = text[i + len(piece)] if i + len(piece) < len(text) else " "
            if (piece[0].isalnum() and before.isalnum()) or (piece[-1].isalnum() and after.isalnum()):
                start = i + 1
                continue
            return i

    def match(self, text, files, cap=4):
        by_piece = {}
        for rel in files:
            for piece, n in self.pieces(rel):
                by_piece.setdefault(piece, []).append((rel, n))
        remaining, found = text, []
        whole = {text.strip(), text.strip().removeprefix("[button: ").removeprefix("[link: ").removesuffix("]")}
        for piece in sorted(by_piece, key=len, reverse=True):
            if len(piece) < 5 and piece not in whole:
                continue  # a very short piece counts only when it is the whole line (or the whole button or link text)
            i = self._find(piece, remaining)
            if i >= 0:
                found.extend(by_piece[piece])
                while i >= 0:
                    remaining = remaining[:i] + "\x01" * len(piece) + remaining[i + len(piece):]
                    i = self._find(piece, remaining)
        merged = {}
        for rel, n in found:
            merged.setdefault(rel, [])
            if n not in merged[rel]:
                merged[rel].append(n)
        parts = [f"{rel}:{', '.join(map(str, sorted(ns)))}" for rel, ns in merged.items()]
        return "; ".join(parts[:cap]) + (" ..." if len(parts) > cap else "")


INDEX = SourceIndex()


def loc(rel, needle, nth=1):
    """file:line of the nth line of ``rel`` containing ``needle`` (or file:? if it is not there any more)."""
    path = ROOT / rel
    if not path.exists():
        return f"{rel} (file not found)"
    seen = 0
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if needle in line:
            seen += 1
            if seen == nth:
                return f"{rel}:{n}"
    return f"{rel}:? (text not found)"


# =====================================================================================================================
# 3. The document
# =====================================================================================================================


def slug(title):
    s = re.sub(r"[^\w\- ]", "", title.lower(), flags=re.UNICODE)
    return s.strip().replace(" ", "-")


class Doc:
    def __init__(self):
        self.out, self.toc = [], []
        self.rows = 0
        self.unique = set()
        self.registry = []  # (text, section) of every captured string, for the automatic scans
        self.section = ""

    def h2(self, title):
        self.section = title
        self.toc.append((2, title))
        self.out += ["", f"## {title}", ""]

    def h3(self, title):
        self.toc.append((3, title))
        self.out += ["", f"### {title}", ""]

    def para(self, text):
        self.out += [text, ""]

    def bullets(self, items):
        self.out += [f"- {i}" for i in items] + [""]

    @staticmethod
    def cell(text):
        text = str(text).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")
        if not text:
            return ""
        return f"`` {text} ``" if "`" in text else f"`{text}`"

    def register(self, text):
        self.rows += 1
        self.unique.add(" ".join(text.split()))
        self.registry.append((text, self.section))

    def table(self, header, rows, text_cols=(0,), plain_cols=()):
        """rows: lists of cell values; text_cols are shown as code spans and counted as captured strings; a cell may
        be a list (several lines, joined with <br>). plain_cols are shown as they are."""
        self.out.append("| " + " | ".join(header) + " |")
        self.out.append("|" + "|".join("---" for _ in header) + "|")
        for row in rows:
            cells = []
            for i, value in enumerate(row):
                if i in plain_cols:
                    cells.append(str(value).replace("|", "\\|").replace("\n", " "))
                elif i in text_cols:
                    values = value if isinstance(value, list) else [value]
                    for v in values:
                        if v:
                            self.register(v)
                    cells.append("<br>".join(self.cell(v) for v in values if v))
                else:
                    cells.append(str(value).replace("|", "\\|").replace("\n", " "))
            self.out.append("| " + " | ".join(cells) + " |")
        self.out.append("")

    def page_table(self, title, page_html, files, note=None, **kw):
        ex = extract(page_html, **kw)
        rows = []
        if ex.title.strip():
            rows.append(["<title> " + ex.title.strip(), INDEX.match(ex.title.strip(), files) or "- (dynamic text)"])
            TITLES.append((title, ex.title.strip()))
        for line in ex.lines:
            plain = line.replace(Extractor.HIDDEN, "")
            rows.append([line, INDEX.match(plain, files) or "- (dynamic text)"])
        self.h3(title)
        if note:
            self.para(note)
        self.table(["Visible text", "Source (file:line)"], rows, text_cols=(0,), plain_cols=(1,))
        return ex

    def render(self, header_lines):
        toc = ["## Table of contents", ""]
        for level, title in self.toc:
            toc.append(f"{'  ' * (level - 2)}- [{title}](#{slug(title)})")
        return "\n".join(header_lines + [""] + toc + self.out) + "\n"


TITLES = []  # (page, <title>)


# =====================================================================================================================
# 4. Main
# =====================================================================================================================


def main():
    import django

    django.setup()
    from django.conf import settings
    from django.db import connection
    from django.test.runner import DiscoverRunner

    logging.disable(logging.CRITICAL)
    runner = DiscoverRunner(verbosity=0, interactive=False)
    runner.setup_test_environment()  # DEBUG False, testserver host, in-memory mail
    old_config = runner.setup_databases()
    try:
        name = str(connection.settings_dict["NAME"])
        if "memory" not in name and name != ":memory:":
            raise SystemExit(f"Refusing to run: the test database is not in memory ({name!r}).")
        real_db = str(settings.DB_PATH)
        if name == real_db:
            raise SystemExit("Refusing to run: the database is the real db.sqlite3.")
        from django.test.utils import override_settings

        # More open conversations than the production cap so the demo data can be built; individual refusals below
        # override it again where they need the real limit to bite. The worker mode means no moderation call ever runs.
        # PREVIEW_SHARE=0 keeps the step 19 preview panel out of the rendered composer (not covered yet).
        with override_settings(MAX_OPEN_CONVERSATIONS=50, MODERATION_RUN_MODE="worker", PREVIEW_SHARE=0.0):
            text = build()
    finally:
        runner.teardown_databases(old_config)
        runner.teardown_test_environment()
    out = Path(OUT_OVERRIDE) if OUT_OVERRIDE else OUT
    out.parent.mkdir(exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out}")


def build():
    import json

    from django.conf import settings
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import AnonymousUser
    from django.core.exceptions import ValidationError
    from django.core.management import call_command
    from django.template.loader import render_to_string
    from django.test import Client, RequestFactory
    from django.test.utils import override_settings
    from django.urls import NoReverseMatch, reverse
    from django.utils import timezone

    from config import tunables
    from forum import services, viewmodels
    from forum.models import Block, Conversation, Message, Participant, Topic
    from forum.templatetags import forum_text
    from moderation.models import InterventionAct, ModerationRun

    User = get_user_model()
    BACKEND = "django.contrib.auth.backends.ModelBackend"
    doc = Doc()
    today = datetime.date.today().isoformat()
    FIXED = datetime.datetime(2026, 1, 15, 9, 0, tzinfo=datetime.timezone.utc)  # fixed clock so reruns give the same times

    # ---- helpers -------------------------------------------------------------------------------------------------
    def mk_user(name):
        return User.objects.create_user(username=name, email=f"{name}@leakcheck.example")

    def client_for(user):
        c = Client()
        c.force_login(user, backend=BACKEND)
        return c

    def body(resp):
        return resp.content.decode()

    def mk_topic(text, owner, hidden=False, opposing=""):
        return Topic.objects.create(title="", proposition=text, opposing_position=opposing, created_by=owner, hidden=hidden)

    def user_msg(conv, participant, text, minutes_ago=60):
        m = Message.objects.create(conversation=conv, author_type="user", participant=participant, content=text)
        if minutes_ago is not None:
            Message.objects.filter(pk=m.pk).update(created_at=FIXED + datetime.timedelta(minutes=60 - minutes_ago))
            m.refresh_from_db()
        return m

    def mod_post(conv, trigger, text, acts):
        """A moderator post as the pipeline leaves it: message, done run, acts. acts: (addressee, subject, sources)."""
        run = ModerationRun.objects.filter(trigger_message=trigger, kind="live").first()
        if run is None:
            run = ModerationRun.objects.create(
                conversation=conv, trigger_message=trigger, snapshot_seq=trigger.seq_no, kind="live", status="pending"
            )
        mod = Message.objects.create(conversation=conv, author_type="moderator", content=text, in_reply_to=trigger)
        Message.objects.filter(pk=mod.pk).update(created_at=trigger.created_at + datetime.timedelta(minutes=1))
        run.status, run.decision, run.posted_message = "done", "intervene", mod
        run.save()
        for order, (addressee, subject, sources) in enumerate(acts, 1):
            act = InterventionAct.objects.create(
                run=run, order=order, act_type="request_clarification", tone="neutral", text=text,
                addressee=addressee, subject=subject,
            )
            act.source_messages.set(sources)
        return mod

    def label_of(conv, user):
        return Participant.objects.get(conversation=conv, user=user).label

    def part_of(conv, user):
        return Participant.objects.get(conversation=conv, user=user)

    def duo(topic, first, second, sides=("pro", "con")):
        """``first`` waits holding sides[0]; ``second`` joins holding sides[1]: an active conversation."""
        services.enter_proposition(first, topic, sides[0])
        conv = services.enter_proposition(second, topic, sides[1])
        assert conv.status == "active"
        return conv

    def url_of(conv):
        return reverse("forum:conversation", args=[conv.pk])

    # ---- people ---------------------------------------------------------------------------------------------------
    NAME_A, NAME_B, NAME_C, NAME_O = "zelda_mox", "quincy_ray", "outsider_kit", "topic_owner"
    ua, ub, uc, uo = (mk_user(n) for n in (NAME_A, NAME_B, NAME_C, NAME_O))
    waiters = {n: mk_user(n) for n in ("maple_fern", "river_stone", "oak_lane", "sable_wren", "birch_hollow", "cedar_finch")}
    stray = mk_user("stray_walker")
    bee, pigeon = mk_user("blocker_bee"), mk_user("loud_pigeon")
    ca, cb, cc, cbee, cpigeon = (client_for(u) for u in (ua, ub, uc, bee, pigeon))
    anon = Client()
    ALL_NAMES = [u.username for u in User.objects.all()]
    Topic.objects.all().delete()

    HOME_FILES = [FT + "home.html", FT + "base.html", "forum/views.py", "forum/services.py", "forum/seed_topics.json"]
    MINE_FILES = [FT + "mine.html", FT + "base.html", "forum/views.py", "forum/services.py", "forum/seed_topics.json"]
    BLOCKED_FILES = [FT + "blocked.html", FT + "base.html", "forum/views.py", "forum/services.py"]
    PROPOSE_FILES = [FT + "propose.html", FT + "base.html", "forum/views.py", "forum/services.py", "forum/seed_topics.json"]
    HOW_FILES = [FT + "how_it_works.html", FT + "base.html"]
    CONV_FILES = [FT + "conversation.html", FT + "_message.html", FT + "base.html", "forum/views.py", "forum/services.py",
                  "forum/viewmodels.py", "forum/seed_topics.json"]
    SKIP = dict(skip_classes={"site-header", "skip-link"})
    page_texts = []  # (name, html, viewer username or None, usernames allowed to appear), for the automatic leak scan

    def note_page(name, resp_or_html, viewer, allowed=()):
        html_ = resp_or_html if isinstance(resp_or_html, str) else body(resp_or_html)
        page_texts.append((name, html_, viewer.username if viewer else None, set(allowed)))

    header = [
        "# User-facing text inventory",
        "",
        f"Generated {today} by `scripts/make_text_inventory.py` (run `.venv/bin/python scripts/make_text_inventory.py` "
        "to regenerate). Read-only analysis: no code, test or database was changed.",
        "",
        "**How to read this.** Every table row is one thing a participant can see, shown exactly as it appears (in a code "
        "span), with the place it appears in and the source location (`file:line`) to edit. Pages were rendered for real "
        "with the Django test client in a throwaway in-memory database, then reduced to visible text: one line per block "
        "element; a link shows as `[link: text]`, a button as `[button: text]`, and `(aria-label) ...`, `(placeholder) "
        "...`, `(title) ...`, `(alt) ...` are attributes. A line starting `(hidden until the page needs it)` is in the "
        "page but only shown by the page's JavaScript. The site header (brand, navigation) is left out of page tables "
        "and listed once in section 8. Source pointers are found by matching text against templates, Python string "
        "literals and the seeded topics file: treat them as hints; a `- (dynamic text)` pointer means the line is not "
        "literal source text (a proposition, a message, a username, a number). Numbers such as 3,000 or 30 seconds come "
        "from `config/tunables.py`. The demonstration usernames (`zelda_mox`, `quincy_ray`, `maple_fern` and so on) exist "
        "only in the throwaway database. The moderator's own message text is written by the AI model at run time and "
        "cannot be listed; only its frame (badge, heading, link) is fixed. The Django admin, exports and command-line "
        "tools are not participant-facing and are not covered. Not yet covered: the step 19 preview panel.",
    ]

    # =================================================================================================================
    # 1. Pages
    # =================================================================================================================
    doc.h2("1. Pages, rendered for real")

    # -- home: empty
    r = cc.get("/")
    doc.page_table("1.1 Home (waiting list): empty state", body(r), HOME_FILES,
                   "Route `/` (`forum:home`), logged in, nobody is waiting. The home page no longer has a search box.", **SKIP)
    note_page("home-empty", r, uc, ALL_NAMES)

    # -- data for the waiting list
    call_command("seed_topics", verbosity=0, stdout=io.StringIO(), stderr=io.StringIO())
    S = list(Topic.objects.filter(created_by__isnull=True).order_by("id"))  # the seeded topics
    assert len(S) >= 6, len(S)
    U = [
        mk_topic("Cats make better pets than dogs.", uo),
        mk_topic("Working from home should be the default for office jobs.", uo),
        mk_topic("Homework should be abolished in primary schools.", uo),
        mk_topic("Public transport should be free.", uo),
        mk_topic("Zoos do more good than harm.", uo),
        mk_topic("Nuclear power is essential for a clean future.", uo),
        mk_topic("NASA should get more funding than it does today.", uo),
        mk_topic("I think tipping should be banned.", uo),
        mk_topic("Every citizen should do a year of national service.", uo),
        mk_topic("Fireworks should be banned.", uo),
        mk_topic("Libraries should stay open on Sundays.", uo),
        mk_topic("A hidden proposition nobody can pick.", uo, hidden=True),
    ]
    W = waiters
    services.enter_proposition(W["maple_fern"], S[0], "pro")      # waiting pro on a seeded topic
    services.enter_proposition(W["river_stone"], S[1], "con")     # waiting con on a seeded topic
    services.enter_proposition(W["oak_lane"], U[2], "pro")        # waiting pro on a user topic
    services.enter_proposition(W["sable_wren"], U[5], "con")      # waiting con on a user topic
    services.enter_proposition(W["birch_hollow"], U[6], "con")    # acronym in the join label
    services.enter_proposition(W["cedar_finch"], U[7], "con")     # "I" in the join label

    # ua's own conversations (used by several pages below)
    w_conv = services.enter_proposition(ua, U[0], "pro")                 # waiting, pro, user topic
    wc_conv = services.enter_proposition(ua, U[1], "con")                # waiting, con, user topic
    c_conv = duo(S[4], ua, ub, ("pro", "con"))                           # active, ua pro, seeded
    c2_conv = duo(S[3], ub, ua, ("pro", "con"))                          # active, ua con, seeded
    c3_conv = duo(U[3], ub, ua, ("pro", "con"))                          # active, ua con, user topic

    r = cc.get("/")
    doc.page_table("1.2 Home (waiting list): with cards", body(r), HOME_FILES,
                   "Each card says who is waiting, quotes the position they hold, and has ONE join button (it takes the "
                   "opposite side) and a quiet `Block <username>` button. The cards here cover: waiting on a seeded topic "
                   "holding the stated position (`maple_fern`) or the opposing one (`river_stone`); waiting on a "
                   "user-written topic holding the position (`oak_lane`, whose join button has no opposing wording to quote so "
                   "it says `I disagree with this position`) or the opposite of it (`sable_wren`, where the quote is "
                   "`They disagree with: ...`); and two join labels showing how the start of a position is worded "
                   "(section 1.9): an acronym and `I` keep their capital. Cards are ordered newest first. The viewer here is a person with no conversations.", **SKIP)
    note_page("home-cards", r, uc, ALL_NAMES)
    tpl_html = render_to_string("forum/home.html", {"cards": [{
        "topic_id": 1, "quote": "A sample position.", "join_side": "con", "join_label": "I disagree with this position",
        "username": ""}]}, request=RequestFactory().get("/"))
    ex = extract(tpl_html, only_class="topic")
    doc.h3("1.3 Home: a card without a username (template branch, not reachable today)")
    doc.para("The template shows `Someone` when a card has no username and hides the Block button. The waiting-list query "
             "always supplies the username of the waiting person, so this branch is not reachable with real data.")
    doc.table(["Visible text", "Source"], [[ln, INDEX.match(ln.replace("[button: ", "").rstrip("]"), HOME_FILES) or "- (dynamic text)"]
                                          for ln in ex.lines], plain_cols=(1,))

    # -- your discussions: build the rest of ua's conversations first
    l_conv = None
    e_conv = duo(U[4], ua, ub, ("pro", "con"))
    user_msg(e_conv, part_of(e_conv, ua), "Let us stop here.", 30)
    services.end_conversation(ua, e_conv)

    # ---- section 1.4 mine
    r = ca.get("/discussions/")
    doc.page_table("1.4 Your discussions: with rows of every kind", body(r), MINE_FILES,
                   "Route `/discussions/` (`forum:mine`), viewer `zelda_mox`. Each row: the viewer's own position line, "
                   "`with <username>` once someone has joined, a status word, and an `Open` button. Rows: waiting holding "
                   "the stated position (`Your position: ...`), waiting holding the opposing position of a user-written topic "
                   "(`You disagree with this position: ...`), active on a seeded topic holding either wording, active holding "
                   "the opposite of a user-written topic, and ended (listed last, in a quieter style). Without a search only the "
                   f"newest {tunables.MY_ENDED_CONVERSATIONS_SHOWN} ended conversations are listed; a search lists all matches. "
                   "A row for an old conversation whose side was never recorded shows `Your position: ...` like the stated "
                   "position.", **SKIP)
    note_page("mine", r, ua, ALL_NAMES)
    r = ca.get("/discussions/?q=zzzz nothing matches")
    doc.page_table("1.5 Your discussions: search with no match", body(r), MINE_FILES, "`/discussions/?q=zzzz nothing matches`.", **SKIP)
    r = ca.get("/discussions/?q=zoos")
    doc.page_table("1.5b Your discussions: search with a match", body(r), MINE_FILES, "`/discussions/?q=zoos` (an ended conversation).", **SKIP)
    r = cc.get("/discussions/")
    doc.page_table("1.5c Your discussions: no discussions yet", body(r), MINE_FILES, "A person with no conversations.", **SKIP)
    note_page("mine-empty", r, uc, [])

    # ---- 1.6 blocked people (before any block)
    r = cbee.get("/blocked/")
    doc.page_table("1.6 Blocked people: nobody blocked", body(r), BLOCKED_FILES, "Route `/blocked/` (`forum:blocked`).", **SKIP)
    note_page("blocked-empty", r, bee, [])

    # ---- 1.7 propose page
    r = cc.get("/propose/")
    doc.page_table("1.7 Start a new discussion (propose page), with the seeded topics", body(r), PROPOSE_FILES,
                   "Route `/propose/` (`forum:propose`), viewer with no conversations. The label `My position is that` is fixed "
                   "text in front of the box (a person who types it again does not store it twice). Below the form, each "
                   "seeded topic has two buttons, one per side, each worded `My position is that <that side, first letter "
                   "lower-cased>`.", **SKIP)
    note_page("propose", r, uc, [])
    r = ca.get("/propose/")
    ex = extract(body(r), only_class="seeded")
    doc.h3("1.8 Propose page: the section for a viewer who already has a conversation on a seeded topic")
    doc.para("Where the viewer already has an open or active conversation on a seeded topic, the two buttons are replaced by "
             "`You already have a conversation here.` and `Open your conversation` (viewer `zelda_mox`, who has conversations on "
             "two of the seeded topics). The other seeded topics keep their two buttons; only the section is shown.")
    doc.table(["Visible text", "Source"], [[ln, INDEX.match(ln.replace("[button: ", "").rstrip("]"), PROPOSE_FILES) or "- (dynamic text)"]
                                          for ln in ex.lines], plain_cols=(1,))
    note_page("propose-own", r, ua, [])

    # ---- 1.9 position_phrase
    doc.h3("1.9 position_phrase: how the start of a position is worded after `My position is that `")
    doc.para(
        f"Defined in `{loc('forum/templatetags/forum_text.py', 'def position_phrase')}`. Only the first letter is lower-cased and nothing "
        "else changes. The first letter is kept when the first word is an acronym or number-like (two or more capitals, or a "
        "digit), is `I` or starts `I'`, or is one of a fixed list of proper nouns "
        f"(`{loc('forum/templatetags/forum_text.py', 'PROPER_NOUNS')}`). Proper nouns outside the list are lower-cased (accepted by the "
        "owner for the MVP). The stored proposition is never changed; the phrase is used in the join buttons on the home page "
        "and in the propose page's seeded-topic buttons, always as `My position is that <phrase>`."
    )
    examples = [
        "Cities should cap how much landlords can raise rents each year.", "NASA should get more funding.",
        "I think tipping should be banned.", "I'm sure that cats are better.", "I’m sure that cats are better.",
        "Trump should not be on the ballot.", "Germany should leave the EU.", "Bob's Burgers should be free.",
        "5G networks should be public.", "The US should ban fireworks.", "   High schools should start later.",
        "already lower case", "élan should be valued.", "", "42 is the answer.",
    ]
    doc.table(["Stored position", "After `My position is that `"],
              [[e or "(empty)", ["My position is that " + forum_text.position_phrase(e)] if e else ["(empty stays empty)"]] for e in examples],
              text_cols=(1,), plain_cols=(0,))

    # ---- 1.10 how it works
    r_anon = anon.get("/how-it-works/")
    r_in = cc.get("/how-it-works/")
    ex_anon, ex_in = extract(body(r_anon), **SKIP), extract(body(r_in), **SKIP)
    same = ex_anon.lines == ex_in.lines and ex_anon.title == ex_in.title
    doc.page_table(
        "1.10 How this works (anonymous and logged in), in full", body(r_in), HOW_FILES,
        "Route `/how-it-works/` (`forum:how_it_works`); it needs no login. Body text anonymous versus logged in: "
        + ("identical (only the site header differs, see section 8)." if same else "DIFFERENT, see 1.10b.")
        + " The numbers (per-day proposition count, characters, seconds, messages) come from settings. This page is also the "
        "target of the moderator card's link.", **SKIP)
    if not same:
        doc.page_table("1.10b How this works (anonymous, differs)", body(r_anon), HOW_FILES, **SKIP)
    note_page("how-it-works", r_in, uc, [])

    # ---- conversation pages -------------------------------------------------------------------------------------
    # waiting (creator alone, may post while waiting)
    user_msg(w_conv, part_of(w_conv, ua), "I will be here for a while; I think cats are calmer.", 50)
    r = ca.get(url_of(w_conv))
    doc.page_table("1.11 Conversation: waiting for someone to take the other position (creator alone)", body(r), CONV_FILES,
                   "State `open`: one participant. The creator can keep posting while waiting (the message box is there). "
                   f"Route `/c/<id>/`. The page polls every {settings.POLL_SECONDS} seconds and reloads itself when someone joins.",
                   **SKIP)
    note_page("conversation-waiting", r, ua, [])

    # position lines
    doc.h3("1.12 The position line under the title (the viewer's own position only)")
    doc.para(f"Built by `{loc('forum/views.py', 'def _own_position_line')}`; the same rule gives the lines in Your discussions. "
             "Nothing is ever shown about the other person's position. Each row was read from a real conversation page.")
    rows = []
    for desc, conv, client_ in (
        ("Holding the stated position of a user-written topic (waiting)", w_conv, ca),
        ("Holding the opposing position of a user-written topic (waiting)", wc_conv, ca),
        ("Holding the stated position of a seeded topic (active)", c_conv, ca),
        ("Holding the opposing position of a seeded topic (active)", c2_conv, ca),
        ("Holding the opposing position of a user-written topic (active)", c3_conv, ca),
        ("The same seeded conversation as the other person sees it", c_conv, cb),
    ):
        ex = extract(body(client_.get(url_of(conv))), only_class="positions")
        rows.append([desc, ex.lines or ["(no line)"], INDEX.match(" ".join(ex.lines), CONV_FILES) or "- (dynamic text)"])
    doc.table(["Situation", "Line shown", "Source"], rows, text_cols=(1,), plain_cols=(0, 2))

    # active with messages
    m1 = user_msg(c_conv, part_of(c_conv, ua), "I think protected lanes make streets safer for everyone.", 50)
    m2 = user_msg(c_conv, part_of(c_conv, ub), "Losing parking would hurt small shops on the street.", 45)
    m3 = user_msg(c_conv, part_of(c_conv, ua), "Shops usually see more customers on foot and by bike.", 40)
    r = ca.get(url_of(c_conv))
    doc.page_table("1.13 Conversation: active with a few messages", body(r), CONV_FILES,
                   "State `active`, viewer can post. Messages 1 and 3 are the viewer's (`You`); message 2 shows the other "
                   "person's username. The `Who is here` card names the other person, and the block card offers to block them "
                   "(both are new; the owner decided the other person's username is shown here). The viewer's own username is not "
                   "shown anywhere on this page, the header included.", **SKIP)
    note_page("conversation-active", r, ua, [NAME_B])
    r = cb.get(url_of(c_conv))
    note_page("conversation-active-b", r, ub, [NAME_A])

    # moderator messages
    mod1 = mod_post(c_conv, m2, "SAMPLE MODERATOR TEXT: could you say what you mean by small shops?",
                    [(label_of(c_conv, ub), label_of(c_conv, ub), [m2])])
    mod2 = mod_post(c_conv, m3, "SAMPLE MODERATOR TEXT: a reminder for both of you to keep to the topic.",
                    [("all", "both", [])])
    r = ca.get(url_of(c_conv))
    doc.page_table("1.14 Conversation: active with moderator messages", body(r), CONV_FILES,
                   "Same conversation after the AI moderator posted twice. The moderator's own text is a placeholder "
                   "(`SAMPLE MODERATOR TEXT`); the frame around it (badge, heading with the other person's username, `automated`, "
                   "link) is fixed text. How the headings are chosen: section 3.3.", **SKIP)
    note_page("conversation-moderator", r, ua, [NAME_B])
    note_page("conversation-moderator-b", cb.get(url_of(c_conv)), ub, [NAME_A])

    # closed by the message limit
    with override_settings(MAX_USER_MESSAGES_PER_CONVERSATION=4):
        l_conv = duo(U[8], ua, ub, ("pro", "con"))
        pla, plb = part_of(l_conv, ua), part_of(l_conv, ub)
        user_msg(l_conv, pla, "First point.", 60)
        user_msg(l_conv, plb, "Second point.", 55)
        user_msg(l_conv, pla, "Third point.", 50)
        user_msg(l_conv, plb, "Fourth point.", 45)
        Conversation.objects.filter(pk=l_conv.pk).update(status="closed")  # what post_message does at the limit
        r = ca.get(url_of(l_conv))
        doc.page_table("1.15 Conversation: closed by the message limit", body(r), CONV_FILES,
                       "State `closed`, nobody ended it. `MAX_USER_MESSAGES_PER_CONVERSATION` overridden to 4 so the page "
                       f"is short; in production the number is {tunables.MAX_USER_MESSAGES_PER_CONVERSATION}.", **SKIP)
        note_page("conversation-limit", r, ua, [NAME_B])

    # ended by you / by the other
    r = ca.get(url_of(e_conv))
    doc.page_table("1.16 Conversation: ended by you", body(r), CONV_FILES,
                   "State `closed`, the viewer pressed End conversation.", **SKIP)
    note_page("conversation-ended-you", r, ua, [NAME_B])
    r = cb.get(url_of(e_conv))
    doc.page_table("1.17 Conversation: ended by the other participant", body(r), CONV_FILES,
                   "The same conversation as the other person sees it. The sentence says `The other participant ended this "
                   "conversation` although the page names them elsewhere.", **SKIP)
    note_page("conversation-ended-other", r, ub, [NAME_A])

    # blocking flows (viewer blocker_bee, other loud_pigeon)
    b_conv = duo(U[9], bee, pigeon, ("pro", "con"))
    user_msg(b_conv, part_of(b_conv, pigeon), "You are all wrong about fireworks.", 20)
    r = cbee.get(url_of(b_conv))
    ex = extract(body(r), only_class="conv-side")
    doc.h3("1.18 Conversation: the side column with the block card")
    doc.para("The right-hand column of an active conversation: `Who is here`, `Limits`, the block card (shown once the other "
             "person has joined) and the end card. Pressing the red button ends the conversation for both and blocks the "
             "other person. In a waiting conversation the block card is absent and `Who is here` says `Only you so far`.")
    doc.table(["Visible text", "Source"], [[ln, INDEX.match(ln.replace("[button: ", "").rstrip("]"), CONV_FILES) or "- (dynamic text)"]
                                          for ln in ex.lines], plain_cols=(1,))
    note_page("conversation-block", r, bee, ["loud_pigeon"])
    ex = extract(body(ca.get(url_of(w_conv))), only_class="conv-side")
    doc.table(["Visible text (waiting conversation)", "Source"], [[ln, INDEX.match(ln.replace("[button: ", "").rstrip("]"), CONV_FILES) or "- (dynamic text)"]
                                                                   for ln in ex.lines], plain_cols=(1,))

    # block from a home card (flash message), then from the conversation, then the blocked page
    r = cbee.post(reverse("forum:block", args=["oak_lane"]), follow=True)
    doc.page_table("1.19 After blocking someone from a waiting card", body(r), HOME_FILES,
                   "POST `/users/oak_lane/block/` from the home card: the person is added to the block list, the page returns to "
                   "the home list (their card is gone) and a green message appears at the top (the flash text, section 8.6).", **SKIP)
    note_page("home-after-block", r, bee, ALL_NAMES)
    r = cbee.post(reverse("forum:block", args=["loud_pigeon"]), follow=True)
    doc.page_table("1.20 After blocking the person you are talking to", body(r), MINE_FILES,
                   "POST `/users/loud_pigeon/block/` from the block card of an active conversation: the conversation ends for "
                   "both, the page goes to Your discussions with a message that says the conversation has ended.", **SKIP)
    note_page("mine-after-block", r, bee, ALL_NAMES)
    Block.objects.update(created_at=FIXED)
    r = cbee.get("/blocked/")
    doc.page_table("1.21 Blocked people: with people", body(r), BLOCKED_FILES,
                   "One row per person, newest first, with the date the block was made (format `j F Y`) and an `Unblock` button.",
                   **SKIP)
    note_page("blocked-rows", r, bee, ["oak_lane", "loud_pigeon"])
    r = cpigeon.get(url_of(b_conv))
    doc.page_table("1.22 Conversation ended because the other person blocked you", body(r), CONV_FILES,
                   "What the blocked person sees: the conversation is closed and says `The other participant ended this "
                   "conversation.` They are not told they were blocked.", **SKIP)
    note_page("conversation-blocked-person", r, pigeon, ["blocker_bee"])
    r = cbee.post(reverse("forum:unblock", args=["oak_lane"]), follow=True)
    doc.page_table("1.23 After unblocking someone (one person still blocked)", body(r), BLOCKED_FILES,
                   "POST `/users/oak_lane/unblock/`: back to the blocked list with a message at the top.", **SKIP)
    r = cbee.post(reverse("forum:unblock", args=["loud_pigeon"]), follow=True)
    doc.page_table("1.24 After unblocking the last person", body(r), BLOCKED_FILES, "Same, the list is now empty.", **SKIP)

    # =================================================================================================================
    # 2. Refusals
    # =================================================================================================================
    doc.h2("2. Every refusal a participant can get")
    doc.para(
        "Each row was produced through the real page path (a POST to the real URL) unless the last column says it is "
        "not reachable from any page. \"Banner\" is the red box next to the form; \"Also on the page\" is the state box "
        "the page shows with it. Numbers come from settings; the settings overridden to trigger a refusal are named. "
        "All refusals keep what the person typed and change nothing (no message, run, proposition or block is saved). "
        "Waiting people can now post, so the old `waiting` refusal no longer exists."
    )
    R_POST = [FT + "conversation.html", "forum/services.py", "forum/views.py"]
    R_PROP = [FT + "propose.html", "forum/services.py", "forum/views.py"]
    R_HOME = [FT + "home.html", "forum/services.py", "forum/views.py"]
    captured_codes = {}

    def refusal_row(scenario, resp, files, where, extra_class="state-box"):
        html_ = body(resp)
        b = extract(html_, only_attr="data-code")
        s = extract(html_, only_class=extra_class) if extra_class else None
        code = b.codes[0] if b.codes else "(none)"
        lines = b.lines
        also = s.lines if s else []
        src = INDEX.match(" ".join(lines + also), files) or "-"
        captured_codes.setdefault(code, " ".join(lines[-1:]))
        return [code, resp.status_code, scenario, where, lines or ["(no banner)"], also, src]

    def home_banner_row(scenario, resp, where, code):
        """The home page banner has no data-code attribute: read it by its class."""
        ex = extract(body(resp), only_class="banner-error")
        captured_codes.setdefault(code, " ".join(ex.lines[-1:]))
        return [code, resp.status_code, scenario, where, ex.lines or ["(no banner)"], [],
                INDEX.match(" ".join(ex.lines), R_HOME) or "-"]

    HEAD = ["Code", "HTTP", "How it was triggered", "Where shown", "Banner (title line, then message)",
            "Also on the page", "Source (file:line)"]

    def refusal_table(title, rows, intro=None):
        doc.h3(title)
        if intro:
            doc.para(intro)
        doc.table(HEAD, rows, text_cols=(4, 5), plain_cols=(0, 1, 2, 3, 6))

    rows = []
    r_conv = duo(U[10], ua, ub, ("pro", "con"))
    post_url = reverse("forum:post", args=[r_conv.pk])
    rows.append(refusal_row("Post an empty box", ca.post(post_url, {"text": ""}), R_POST, "Conversation page, above the message box"))
    rows.append(refusal_row("Post only spaces and blank lines", ca.post(post_url, {"text": "  \n \n "}), R_POST, "same"))
    limit = settings.MAX_MESSAGE_CHARS
    rows.append(refusal_row(f"Post {limit + 50:,} characters", ca.post(post_url, {"text": "x" * (limit + 50)}), R_POST,
                            "Conversation page, above the message box"))
    ok = ca.post(post_url, {"text": "My first real message."})
    assert ok.status_code == 302, ok.status_code
    rows.append(refusal_row("Post twice within the gap (the second post)", ca.post(post_url, {"text": "And another one right away."}),
                            R_POST, "Conversation page, above the message box (the number of seconds varies by a second or two)"))
    rows.append(refusal_row("Post in a conversation closed by the message limit (nobody ended it)",
                            ca.post(reverse("forum:post", args=[l_conv.pk]), {"text": "One more."}), R_POST,
                            "Closed page (the number in the text is the production limit, not the 4 used for page 1.15)"))
    rows.append(refusal_row("Post in a conversation you ended", ca.post(reverse("forum:post", args=[e_conv.pk]), {"text": "One more."}),
                            R_POST, "Closed page"))
    rows.append(refusal_row("Post in a conversation the other participant ended (or that the other person ended by blocking you)",
                            cb.post(reverse("forum:post", args=[e_conv.pk]), {"text": "One more."}), R_POST, "Closed page"))
    rows.append(refusal_row("Press End on a conversation that is already closed", ca.post(reverse("forum:end", args=[e_conv.pk])),
                            R_POST, "Closed page"))
    f_conv = duo(mk_topic("Museums should always be free.", uo), ua, ub, ("pro", "con"))
    with override_settings(MAX_USER_MESSAGES_PER_CONVERSATION=3):
        user_msg(f_conv, part_of(f_conv, ua), "One.", 60)
        user_msg(f_conv, part_of(f_conv, ub), "Two.", 55)
        user_msg(f_conv, part_of(f_conv, ub), "Three.", 50)
        rows.append(refusal_row("Conversation still active but already holds the maximum number of messages (limit overridden to 3; "
                                "only reachable if the limit is lowered after messages exist, because posting the last allowed "
                                "message closes the conversation)",
                                ca.post(reverse("forum:post", args=[f_conv.pk]), {"text": "A fourth."}), R_POST,
                                "Page shows the banner plus a 'You cannot post right now' box"))
    r_np = cc.post(post_url, {"text": "let me in"})
    not_found_text = extract(body(r_np), **SKIP).lines[1]
    rows.append(["not_participant", r_np.status_code, "A logged-in stranger posts to someone else's conversation, or opens a "
                 "conversation that does not exist (same page for both)", "Full 404 page (section 5)",
                 extract(body(r_np), **SKIP).lines[:2], [], INDEX.match(body(r_np), ["forum/templates/404.html", "forum/views.py"]) or "-"])
    captured_codes.setdefault("not_participant", not_found_text)
    with mock.patch.object(services, "post_message", side_effect=RuntimeError("boom")):
        rows.append(refusal_row("Unexpected failure while saving a message (simulated)", ca.post(post_url, {"text": "Some draft text"}),
                                R_POST, "Conversation page, above the message box, text kept", extra_class=None))
    refusal_table("2.1 Posting a message", rows)

    rows = []
    pc = client_for(uo)
    pu = reverse("forum:propose")
    rows.append(refusal_row("Publish an empty box", pc.post(pu, {"text": ""}), R_PROP, "Propose page, above the box", extra_class=None))
    pl = settings.MAX_PROPOSITION_CHARS
    rows.append(refusal_row(f"Publish {pl + 30} characters", pc.post(pu, {"text": "y" * (pl + 30)}), R_PROP,
                            "Propose page, above the box", extra_class=None))
    rows.append(refusal_row("Publish text that matches an existing proposition (any case/spacing; the typed 'My position is "
                            "that' start is ignored too)",
                            pc.post(pu, {"text": "  my position is that  CATS make better   pets than dogs. "}), R_PROP,
                            "Propose page; the page also shows a button 'Discuss the existing proposition'", extra_class=None))
    with override_settings(MAX_PROPOSITIONS_PER_USER_PER_DAY=1):
        uq = mk_user("prolific_writer")
        cq = client_for(uq)
        first = cq.post(pu, {"text": "Libraries should open earlier on weekdays."})
        assert first.status_code == 302, first.status_code
        rows.append(refusal_row("Publish a second proposition on the same UTC day (limit overridden to 1)",
                                cq.post(pu, {"text": "Museums should open late on Fridays."}), R_PROP,
                                "Propose page, above the box", extra_class=None))
    with override_settings(MAX_OPEN_CONVERSATIONS=1):
        ur = mk_user("busy_talker")
        cr = client_for(ur)
        services.enter_proposition(ur, mk_topic("Busy talker topic: bus lanes should be open to bikes.", uo), "pro")
        rows.append(refusal_row("Publish a proposition while at the open-conversation cap (cap overridden to 1; the production "
                                "default is no cap, so this refusal does not occur unless the tunable is set; the proposition "
                                "is not saved)", cr.post(pu, {"text": "Bus lanes should be open to bikes."}), R_PROP,
                                "Propose page, above the box (title falls back to 'Your proposition was not published')",
                                extra_class=None))
        rows.append(home_banner_row("Press a join button on the home page while at the cap",
                                    cr.post(reverse("forum:enter", args=[S[2].pk]), {"side": "pro"}),
                                    "Home page, banner above the list (no title line)", "too_many_open"))
    rows.append(home_banner_row("Press a join button of a proposition that has been hidden since the page loaded",
                                cc.post(reverse("forum:enter", args=[U[11].pk]), {"side": "pro"}),
                                "Home page, banner above the list", "hidden"))
    rows.append(home_banner_row("Press a join button with no side, or an unknown side (a hand-made request; the buttons always "
                                "send a side)", cc.post(reverse("forum:enter", args=[S[0].pk]), {}),
                                "Home page, banner above the list", "invalid_side"))
    with mock.patch.object(services, "create_proposition", side_effect=RuntimeError("boom")):
        rows.append(refusal_row("Unexpected failure while saving a proposition (simulated)",
                                pc.post(pu, {"text": "A perfectly fine proposition."}), R_PROP,
                                "Propose page, above the box, text kept", extra_class=None))
    refusal_table("2.2 Proposing, choosing a side and joining", rows)

    rows = []
    rows.append(home_banner_row("Block yourself (a hand-made request; there is no button for it)",
                                cc.post(reverse("forum:block", args=[NAME_C])), "Home page, banner above the list", "invalid_block"))
    r = cc.post(reverse("forum:block", args=["no_such_person"]))
    rows.append(["(404)", r.status_code, "Block or unblock a username that does not exist", "Full 404 page (section 5)",
                 extract(body(r), **SKIP).lines[:2], [], INDEX.match(body(r), ["forum/templates/404.html"]) or "-"])
    doc.h3("2.3 Blocking")
    doc.para("Blocking has few refusals: it is idempotent, and it never tells the blocked person. The service's own text for an "
             "unknown person (`That person was not found.`) is not reachable from a page, because the view answers an unknown "
             "username with the 404 page first.")
    doc.table(HEAD, rows, text_cols=(4, 5), plain_cols=(0, 1, 2, 3, 6))

    doc.h3("2.4 Other refusals at page level")
    other = []
    r = anon.get("/", follow=False)
    other.append(["Not logged in: any page except How this works", f"HTTP {r.status_code}, Location: {r.headers.get('Location')}",
                  "Redirect to the login page; the login page shows only its form (no sentence explaining the redirect)",
                  loc("config/settings.py", "LOGIN_URL")])
    r = anon.post(post_url, {"text": "x"})
    other.append(["Not logged in: POST to a conversation", f"HTTP {r.status_code}, Location: {r.headers.get('Location')}",
                  "Same redirect; nothing is saved", loc("forum/views.py", "@login_required", 4)])
    r = ca.get(post_url)
    other.append(["Wrong method (opening a POST-only address by link)", f"HTTP {r.status_code}, page body length {len(r.content)} bytes",
                  "Browser shows a blank page: no text at all", loc("forum/views.py", "@require_POST")])
    r = Client(enforce_csrf_checks=True)
    r.force_login(ua, backend=BACKEND)
    rr = r.post(post_url, {"text": "x"})
    txt = extract(body(rr), **SKIP)
    other.append(["Missing or stale form token (CSRF)", f"HTTP {rr.status_code}", "Page: " + " / ".join(txt.lines[:3]),
                  loc("forum/templates/403_csrf.html", "<h1>")])
    doc.table(["Situation", "What happens", "What the person sees", "Source"], other, text_cols=(), plain_cols=(0, 1, 2, 3))
    for t in (other[2][2], other[3][2]):
        doc.register(t)

    # all codes
    doc.h3("2.5 Complete list of PostRejected codes (from the source) and whether the rows above cover it")
    codes_in_source = {}
    for rel in ("forum/services.py", "forum/viewmodels.py", "forum/views.py"):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "PostRejected" and node.args
                    and isinstance(node.args[0], ast.Constant)):
                codes_in_source.setdefault(node.args[0].value, []).append(f"{rel}:{node.lineno}")
    for node in ast.walk(ast.parse((ROOT / "forum/views.py").read_text(encoding="utf-8"))):  # view-level codes
        if isinstance(node, ast.keyword) and node.arg == "code" and isinstance(node.value, ast.Constant):
            codes_in_source.setdefault(node.value.value, []).append(f"forum/views.py:{node.value.lineno} (view-level)")
    direct = {}
    try:
        services.create_proposition(AnonymousUser(), "x")
    except services.PostRejected as exc:
        direct[exc.code] = exc.message
    other_conv = duo(mk_topic("Elsewhere topic for a stray reply.", uo), ub, stray, ("pro", "con"))
    stranger_msg = user_msg(other_conv, part_of(other_conv, ub), "Elsewhere.", 60)
    try:
        services.post_message(ua, c_conv, "A reply to a message from another conversation.", in_reply_to=stranger_msg)
    except services.PostRejected as exc:
        direct[exc.code] = exc.message
    try:
        services.block_user(ua, User(pk=99999999, username="ghost"))
    except services.PostRejected as exc:
        direct.setdefault(exc.code + " (unknown person)", exc.message)
    try:
        services.block_user(AnonymousUser(), ub)
    except services.PostRejected as exc:
        direct.setdefault(exc.code + " (blocking while logged out)", exc.message)
    Message.objects.filter(conversation=c_conv, author_type="user", participant=part_of(c_conv, ua)).update(
        created_at=timezone.now() - datetime.timedelta(hours=1))
    with mock.patch.object(type(Message.objects), "create", side_effect=ValidationError("x")):
        try:
            services.post_message(ua, c_conv, "This will hit a model error.")
        except services.PostRejected as exc:
            direct[exc.code] = exc.message
    rows = []
    unreachable = ("login_required", "invalid_reply", "not_saved")
    for code in sorted(codes_in_source):
        page_msg = captured_codes.get(code)
        if page_msg and code not in unreachable:
            via, message = "through a page (rows above)", page_msg
        elif code in direct or any(k.startswith(code) for k in direct):
            via = "direct service call: no page can reach it today"
            message = direct.get(code) or " / ".join(v for k, v in direct.items() if k.startswith(code))
        elif page_msg:
            via, message = "through a page (rows above)", page_msg
        else:
            via, message = "NOT CAPTURED", ""
        rows.append([code, message, via, "; ".join(codes_in_source[code])])
    doc.table(["Code", "Message a person would see (as shown in the banner, including any page suffix)", "How captured", "Raised at"],
              rows, text_cols=(1,), plain_cols=(0, 2, 3))
    doc.para(
        "Notes: `server_error` is not a `PostRejected` code; it is the page-level fallback in `forum/views.py` "
        "(`OUR_SIDE_FAILED`, `PROPOSITION_OUR_SIDE_FAILED`). `not_participant` never shows its own message on a page: "
        "the views turn it into the 404 page. `invalid_side` and `invalid_block` are also raised by the views themselves for a "
        "hand-made request. The banner title lines (\"Too soon to post again\", \"This message is too long\", \"Your message is "
        "empty\", \"Your message was not sent\", \"You cannot publish another proposition today\", \"This proposition is too "
        "long\", \"This proposition already exists\", \"Your proposition is empty\", \"Your proposition was not published\") are "
        f"chosen by the template from the code, {loc(FT + 'conversation.html', 'Too soon to post again')} and "
        f"{loc(FT + 'propose.html', 'You cannot publish another')}. When the person typed something, the template adds "
        "`Nothing was sent and your text is kept.` (messages) or `Nothing was published and your text is kept.` (propositions, "
        "except for duplicates). On the home page the banner has no title line and no suffix."
    )

    # =================================================================================================================
    # 3. Moderation notices and headings
    # =================================================================================================================
    doc.h2("3. Moderation notices and headings (forum/viewmodels.py)")
    doc.h3("3.1 The notice banner (\"About the AI moderator\")")
    doc.para(
        "Shown above the thread when the latest finished live run did not produce a normal answer. Container: "
        f"`{loc(FT + 'conversation.html', 'id=\"moderation-notice\"')}`, heading `About the AI moderator`. The text is chosen by "
        f"`moderation_notice_for` ({loc('forum/viewmodels.py', 'def moderation_notice_for')}): the newest run of kind live "
        "whose status is not pending or running; status `skipped_disabled` gives the switched-off text, `failed` gives the "
        "problem text, `skipped_budget` looks at words in `failure_reason` (breaker, conversation, day/daily, site/total), "
        "anything else gives the generic pause text; `done` (whether the moderator posted or stayed silent) gives no notice. "
        "While a run is pending or running the previous notice stays, so it does not flicker. The polling script updates "
        "the same banner. The table below was produced by creating runs with each status and reading the notice."
    )
    nt = mk_topic("Notice test proposition, used only for this table.", uo)
    n_conv = duo(nt, ua, ub, ("pro", "con"))
    pna = part_of(n_conv, ua)
    cases = [
        ("skipped_disabled", "llm_disabled", "Moderation switched off (LLM_ENABLED is false)", "_SWITCHED_OFF"),
        ("failed", "structural", "A failure in the moderator's output", "_FAILED"),
        ("failed", "api_error", "Any failed run", "_FAILED"),
        ("skipped_budget", "breaker_open", "Circuit breaker open (real reason set by moderation/pipeline.py)", "_PAUSED_BREAKER"),
        ("skipped_budget", "budget_exceeded", "A cap was reached (real reason set by moderation/pipeline.py)", "_PAUSED_GENERIC"),
        ("skipped_budget", "budget_unavailable", "Cap could not be checked (real reason)", "_PAUSED_GENERIC"),
        ("skipped_budget", "refused", "Model refused (real reason)", "_PAUSED_GENERIC"),
        ("skipped_budget", "conversation cap", "Reason text containing 'conversation' (NOT produced by the pipeline today)", "_PAUSED_CONVERSATION"),
        ("skipped_budget", "daily cap", "Reason text containing 'day' or 'daily' (NOT produced by the pipeline today)", "_PAUSED_DAY"),
        ("skipped_budget", "site total", "Reason text containing 'site' or 'total' (NOT produced by the pipeline today)", "_PAUSED_SITE"),
        ("skipped_budget", "", "skipped_budget with an empty reason", "_PAUSED_GENERIC"),
        ("done", "", "Finished normally (moderator posted or said nothing)", None),
        ("pending", "", "Run still waiting (older notice, if any, stays; none here)", None),
    ]
    rows = []
    for status, reason, why, const in cases:
        trig = user_msg(n_conv, pna, f"trigger for {status}/{reason}", None)
        ModerationRun.objects.create(conversation=n_conv, trigger_message=trig, snapshot_seq=trig.seq_no, kind="live",
                                     status=status, failure_reason=reason)
        notice = viewmodels.moderation_notice_for(n_conv)
        expect = getattr(viewmodels, const) if const else None
        rows.append([status, reason or "(empty)", why, [notice] if notice else ["(no notice)"],
                     loc("forum/viewmodels.py", f"{const} =") if const else "-", "" if notice == expect else "MISMATCH with the constant"])
    doc.table(["Run status", "failure_reason", "Situation", "Notice shown", "Source of the text", "Check"], rows,
              text_cols=(3,), plain_cols=(0, 1, 2, 4, 5))
    trig = user_msg(n_conv, pna, "trigger for banner render", None)
    ModerationRun.objects.create(conversation=n_conv, trigger_message=trig, snapshot_seq=trig.seq_no, kind="live",
                                 status="skipped_budget", failure_reason="breaker_open")
    r = ca.get(url_of(n_conv))
    ex = extract(body(r), only_class="banner-notice")
    doc.h3("3.2 The banner as it appears on the page (circuit breaker example)")
    doc.table(["Visible text", "Source"], [[ln, INDEX.match(ln, [FT + "conversation.html", "forum/viewmodels.py"]) or "-"] for ln in ex.lines],
              plain_cols=(1,))
    note_page("conversation-notice", r, ua, [NAME_B])

    doc.h3("3.3 Headings above a moderator message")
    doc.para(
        f"Chosen by `moderation_heading` / `_act_heading` ({loc('forum/viewmodels.py', 'def _act_heading')}) from the valid "
        "acts of the run that posted the message, worked out per viewer (the same message has different headings for the two "
        "people). Rules: an act addressed to everyone or about both people gives `For both of you`; an act about no one gives "
        "`About the conversation`; otherwise it is `About your message N` when the act is about the viewer, or `About "
        "<username>'s message N` when it is about the other person (the other person's username; `the other participant's` only "
        "if the person has no username). N is the position number of the message the act quotes, preferring one written by the person "
        "the act is about, else the message that triggered the run if that person wrote it. When no such message can be named "
        "the heading is `About your messages` / `About <username>'s messages`. If a run has acts with different headings the "
        "result is `For both of you`; no run, or no valid act, gives `About the conversation`. The table was produced from real runs."
    )
    h_conv = duo(mk_topic("Heading test proposition, used only for this table.", uo), ua, ub, ("pro", "con"))
    la, lb = label_of(h_conv, ua), label_of(h_conv, ub)
    pha, phb = part_of(h_conv, ua), part_of(h_conv, ub)
    scenarios = []

    def scen(desc, trigger_by, acts_spec):
        trig = user_msg(h_conv, pha if trigger_by == "a" else phb, f"trigger: {desc}", None)
        acts = []
        for addressee, subject, src in acts_spec:
            srcs = []
            if src == "a":
                srcs = [user_msg(h_conv, pha, "A's quoted message", None)]
            elif src == "b":
                srcs = [user_msg(h_conv, phb, "B's quoted message", None)]
            acts.append((addressee, subject, srcs))
        scenarios.append((desc, mod_post(h_conv, trig, "SAMPLE", acts)))

    scen("Act about the first person's message (quoted)", "a", [(lb, la, "a")])
    scen("Act about the second person's message (quoted)", "b", [(la, lb, "b")])
    scen("Act about the first person, no quoted message, second person's message triggered the run", "b", [(lb, la, None)])
    scen("Act about the second person, no quoted message, first person's message triggered the run", "a", [(la, lb, None)])
    scen("Act about the first person, no quoted message, their own message triggered the run", "a", [(lb, la, None)])
    scen("Act addressed to everyone", "a", [("all", "both", None)])
    scen("Act about both people", "a", [(la, "both", None)])
    scen("Act about no one", "a", [(la, "none", None)])
    scen("Two acts with different headings", "a", [(lb, la, "a"), (la, lb, "b")])
    orphan_trigger = user_msg(h_conv, pha, "trigger without run result", None)
    orphan = Message.objects.create(conversation=h_conv, author_type="moderator", content="SAMPLE", in_reply_to=orphan_trigger)
    scenarios.append(("Moderator message with no run or no valid act behind it", orphan))
    rows = []
    for desc, mod in scenarios:
        rows.append([desc, [viewmodels.moderation_heading(mod, pha)], [viewmodels.moderation_heading(mod, phb)]])
    doc.table(["Situation", f"Heading for the first person ({NAME_A}; the other is {NAME_B})",
               f"Heading for the second person ({NAME_B}; the other is {NAME_A})"], rows, text_cols=(1, 2), plain_cols=(0,))
    doc.para("Source of the strings: " + "; ".join(
        f"`{loc('forum/viewmodels.py', n)}`" for n in ("HEADING_BOTH =", "HEADING_CONVERSATION =", 'return f"About {whose} message',
                                                        'return f"About {whose} messages"')) + ".")

    # =================================================================================================================
    # 4. Moderator card and message frame
    # =================================================================================================================
    doc.h2("4. The moderator card and the message frame")
    doc.para(f"Template: `{FT}_message.html` (used by the page and by the polling endpoint, so both look the same). Every "
             "message on a conversation page is one of the kinds below. The other person's messages show their username; no "
             "label letter ever appears.")
    for label, kind_msg in (
        ("4.1 Moderator card", {"kind": "moderator", "seq_no": 4, "heading": f"About {NAME_B}'s message 2",
                                "text": "SAMPLE MODERATOR TEXT.", "created_at": FIXED}),
        ("4.2 Moderator card without a heading (a message the server could not attribute)",
         {"kind": "moderator", "seq_no": 5, "heading": "", "text": "SAMPLE MODERATOR TEXT.", "created_at": FIXED}),
        ("4.3 Your message", {"kind": "you", "seq_no": 1, "text": "SAMPLE USER TEXT.", "created_at": FIXED}),
        ("4.4 The other person's message (with their username)", {"kind": "other", "seq_no": 2, "author_name": NAME_B,
                                                                   "text": "SAMPLE USER TEXT.", "created_at": FIXED}),
        ("4.5 The other person's message (view gives no username; fallback text)", {"kind": "other", "seq_no": 2,
                                                                                    "text": "SAMPLE USER TEXT.", "created_at": FIXED}),
    ):
        partial = render_to_string("forum/_message.html", {"m": kind_msg})
        ex = extract(partial)
        doc.h3(label)
        doc.table(["Visible text", "Source"], [[ln, INDEX.match(ln, [FT + "_message.html"]) or "- (dynamic text)"] for ln in ex.lines],
                  plain_cols=(1,))
    doc.para("Elements of the moderator card: the badge `AI MODERATOR` (" + loc(FT + "_message.html", "AI MODERATOR") + "), the heading "
             "(section 3.3), the word `automated`, the time as `HH:MM UTC`, then the moderator's text, then the link "
             "`Why is there an AI moderator, and can it be wrong?` which goes to `/how-it-works/` (no anchor, so it lands at the top "
             "of that page). Times of all messages are shown in UTC.")

    # =================================================================================================================
    # 5. Error pages
    # =================================================================================================================
    doc.h2("5. Error pages and redirects")
    doc.para("Templates in `forum/templates/`. 400, 403_csrf and 500 are standalone pages (they do not depend on the "
             "database or the session); 403 and 404 extend the normal page shell. Rendered with `render_to_string`, and "
             "the CSRF page also live (section 2.4).")
    n5 = [0]
    rf = RequestFactory()
    req = rf.get("/x/")
    req.user = ua
    for name, ctx, note in (
        ("400.html", {}, "Bad request (for example an invalid Host header). Django's handler400."),
        ("403.html", {}, "Permission denied (Django's handler403)."),
        ("403_csrf.html", {}, "Missing or stale form token: Django shows this template for CSRF failures."),
        ("404.html", {}, "Any unknown address (for example /nothing-here/, or blocking a username that does not exist)."),
        ("404.html", {"not_found_text": not_found_text}, "A conversation that does not exist and a conversation the person is "
                                                          "not in: the very same page, so nothing reveals which conversations exist."),
        ("500.html", {}, "Unexpected server error (Django's handler500)."),
    ):
        content = render_to_string(name, ctx, request=req)
        n5[0] += 1
        doc.page_table(f"5.{n5[0]} {name}{' (with the conversation text)' if ctx else ''}", content,
                       [f"forum/templates/{name}", FT + "base.html", "forum/views.py"], note, **SKIP)
        note_page(f"error-{name}", content, ua, [])
    rr = anon.get("/nothing-here/")
    live404 = extract(body(rr), **SKIP).lines
    bad = Client().get("/", HTTP_HOST="evil.example.invalid")
    live400 = extract(body(bad), **SKIP).lines
    with mock.patch("forum.views._waiting_cards", side_effect=RuntimeError("boom")):
        c500 = Client(raise_request_exception=False)
        c500.force_login(ua, backend=BACKEND)
        r500 = c500.get("/")
    live500 = extract(body(r500), **SKIP).lines
    doc.para(f"Live check: an unknown address gives HTTP {rr.status_code} with `{live404[1] if len(live404) > 1 else live404}`; a bad "
             f"Host header gives HTTP {bad.status_code} with `{live400[0] if live400 else '(empty)'}`; a crashing page gives HTTP "
             f"{r500.status_code} with `{live500[0] if live500 else '(empty)'}`. Each shows the same text as its template above.")
    doc.h3(f"5.{n5[0] + 1} Login redirect")
    r = anon.get("/")
    r2 = anon.get(r.headers["Location"])
    ex = extract(body(r2), **SKIP)
    doc.para(
        f"A person who is not logged in and opens any page except How this works is redirected (HTTP {r.status_code}) to "
        f"`{r.headers['Location']}` ({loc('config/settings.py', 'LOGIN_URL')}, `@login_required` in forum/views.py). There is no "
        f"text about the redirect: the login page just shows its normal content (title `{ex.title.strip()}`, heading "
        f"`{ex.lines[0] if ex.lines else ''}`). A logged-in person opening the login page is sent on to the home page "
        "(`redirect_authenticated_user`)."
    )
    doc.register("login redirect: no explanatory text")

    # =================================================================================================================
    # 6. Client side
    # =================================================================================================================
    doc.h2("6. Client-side strings (forum/static/forum/compose.js, poll.js)")
    js_rows = [
        ["Your message is 3,050 characters; the limit is 3,000. Please shorten it by 50 characters. You can still press the "
         "button; the site will explain if it cannot send it.",
         "Red advice line under the box, while the count is over the limit. The word `message` becomes `proposition` on the "
         "propose page. The number is formatted en-US (`3,050`). The word `characters` is always plural, even for 1.",
         loc("forum/static/forum/compose.js", "Your \" + noun")],
        ["(empty: advice line hidden)", "Advice line when the count is at or under the limit", loc("forum/static/forum/compose.js", 'advice.textContent = ""')],
        ["<n> / <limit> characters", "Live counter next to the button: the number is updated by compose.js; the words come from the "
                                     "template", loc(FT + "conversation.html", "characters</span>")],
        ["This conversation has changed. [link: Reload to see the latest.]",
         "Blue banner (hidden by default). poll.js shows it when the conversation changed state (someone joined, someone "
         "ended it, posting became possible or impossible) while the person has text in the box, and keeps polling for messages; "
         "if the box is empty poll.js reloads the page without any message",
         loc(FT + "conversation.html", "This conversation has changed")],
        ["(server text) About the AI moderator: <notice>", "poll.js replaces the notice text with the server's notice and shows or "
                                                           "hides the banner (texts: section 3.1)", loc("forum/static/forum/poll.js", "function updateNotice")],
        ["<n> of 30 messages used", "poll.js updates the number in the Limits card", loc("forum/static/forum/poll.js", "data-message-count")],
        ["(server HTML) new messages", "poll.js inserts the server-rendered message partial (section 4), including the other "
                                       "person's username", loc("forum/static/forum/poll.js", "insertAdjacentHTML")],
    ]
    doc.table(["Text", "Where and when", "Source"], js_rows, text_cols=(0,), plain_cols=(1, 2))
    js_literals = []
    for rel in ("forum/static/forum/compose.js", "forum/static/forum/poll.js"):
        for n, line in enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
            if "preview" in line.lower():
                break  # from here on compose.js is the step 19 preview code, which is not covered yet
            if line.lstrip().startswith(("/*", "*", "//")):
                continue
            for lit in re.findall(r'"([^"\\]{6,}(?:\\.[^"\\]*)*)"', line):
                if re.search(r"[A-Za-z]{3,} [A-Za-z]{3,}", lit) and not re.search(r"[#.\[\]=]", lit):
                    js_literals.append(f"{rel}:{n}: {lit}")
    doc.para("Everything else in the two scripts is technical (element ids, attribute names, HTTP header values). "
             "There is no message shown when polling fails: poll.js silently retries with a longer wait (up to 60 seconds) "
             "and the page just stops updating. String literals with words found by scanning the scripts: "
             + ("; ".join(f"`{x}`" for x in js_literals) if js_literals else "none besides the ones in the table") + ".")
    poll_json = json.loads(ca.get(reverse("forum:messages", args=[c_conv.pk]) + "?after=0").content.decode())

    # =================================================================================================================
    # 7. Accounts
    # =================================================================================================================
    doc.h2("7. Accounts pages (username and password only; no email)")
    brief = "docs/step6c_brief.md"
    doc.para("Step 6c has landed: people register with a username and a password only. There is no email, no confirmation "
             "link and no password reset by email; a forgotten password is reset by the person running the site. The confirmation, "
             "resend and reset pages and their templates no longer exist (their old addresses give the normal 404 page). "
             f"The register wording was fixed verbatim in `{brief}` ({loc(brief, 'Register page text')}); the check below compares the rendered page with it.")
    reg_files = [AT + "register.html", AT + "base.html", "accounts/registration.py", "accounts/validators.py", FT + "base.html"]
    login_files = [AT + "login.html", AT + "password_fields.html", AT + "base.html", "accounts/authviews.py", FT + "base.html"]
    pw_files = [AT + "password_change_form.html", AT + "password_fields.html", AT + "base.html", FT + "base.html"]

    def accounts_table(title, resp, files, note=None):
        ex = extract(body(resp), **SKIP)
        rows = []
        for ln in (["<title> " + ex.title.strip()] if ex.title.strip() else []) + ex.lines:
            rows.append([ln, INDEX.match(ln.replace("<title> ", ""), files) or "- (dynamic text, form label or Django built-in text)"])
        if ex.title.strip():
            TITLES.append((title, ex.title.strip()))
        doc.h3(title)
        if note:
            doc.para(note)
        doc.table(["Visible text", "Source (file:line)"], rows, text_cols=(0,), plain_cols=(1,))
        return ex

    reg_url = reverse("accounts:register")
    ex = accounts_table("7.1 Register page", anon.get(reg_url), reg_files,
                        "Route `/accounts/register/`. On success the person is created active, logged in and sent to the home page.")
    target = ["Choose a username and a password. You do not need an email address.",
              "There is no password reset by email. If you forget your password, ask the person running this site to reset it.",
              "Create an account", "[button: Create account]", "[link: Already have an account? Log in]"]
    joined = "\n".join(ex.lines) + "\n" + ex.title
    missing = [t for t in target if t not in joined]
    doc.para("Check against the brief's fixed wording: " + ("all five texts are present." if not missing else "MISSING: " + "; ".join(missing)))
    accounts_table("7.2 Register: empty submit (validation messages)", anon.post(reg_url, {}), reg_files)
    accounts_table("7.3 Register: bad username, weak password, mismatch (validation messages)",
                   anon.post(reg_url, {"username": "a b", "password1": "password", "password2": "different"}), reg_files)
    accounts_table("7.4 Register: username already taken (any case)",
                   anon.post(reg_url, {"username": NAME_A.upper(), "password1": "correct horse battery staple 42",
                                       "password2": "correct horse battery staple 42"}), reg_files)
    gone = []
    for path in ("/accounts/register/check-email/", "/accounts/resend-confirmation/", "/accounts/confirm/x/", "/accounts/password-reset/"):
        gone.append(f"{path}: HTTP {anon.get(path).status_code}")
    doc.para("Removed addresses now answer with the ordinary 404 page: " + "; ".join(gone) + ".")

    accounts_table("7.5 Login page", anon.get(reverse("accounts:login")), login_files,
                   "Route `/accounts/login/`. There is no 'Forgot your password?' link. The field labels are Django's `Username:` "
                   "and `Password:`.")
    accounts_table("7.6 Login: wrong username or password", anon.post(reverse("accounts:login"), {"username": NAME_B, "password": "not the password"}),
                   login_files, "The same message for a wrong username and a wrong password.")
    lock, lock_client = None, Client()
    for _ in range(settings.LOGIN_MAX_FAILURES + 2):
        lock = lock_client.post(reverse("accounts:login"), {"username": NAME_B, "password": "still wrong"})
        if lock.status_code == 429:
            break
    accounts_table("7.7 Login: lockout page (HTTP 429)", lock, login_files,
                   f"After {settings.LOGIN_MAX_FAILURES} failed attempts, for {settings.LOGIN_COOLOFF_MINUTES} minutes "
                   "(both numbers from tunables). The minutes shown count down.")
    accounts_table("7.8 Logout confirmation page (opening the logout address with a link)", ca.get(reverse("accounts:logout")),
                   [AT + "logout_confirm.html", AT + "base.html", FT + "base.html"],
                   "HTTP 405: logging out needs a button press. The normal way out is the header button.")
    r_lo = client_for(uc).post(reverse("accounts:logout"), follow=True)
    accounts_table("7.9 After logging out (flash message on the login page)", r_lo, login_files,
                   f"The flash message `You have been logged out.` is in the `messages` list of the page shell ({loc('accounts/authviews.py', 'You have been logged out')}).")
    pc_url = reverse("accounts:password_change")
    cp = client_for(mk_user("pw_changer"))
    accounts_table("7.10 Change password page", cp.get(pc_url), pw_files,
                   "Logged in. Labels and help text come from Django's password-change form and the password rules in config/settings.py.")
    accounts_table("7.11 Change password: wrong old password, weak new password",
                   cp.post(pc_url, {"old_password": "nope", "new_password1": "12345678", "new_password2": "123456789"}), pw_files,
                   "Error messages are Django's built-in ones plus the password validators configured in settings.")
    accounts_table("7.12 Change password: done page", cp.get(reverse("accounts:password_change_done")),
                   [AT + "password_change_done.html", AT + "base.html", FT + "base.html"])
    leftovers = [t for t in ("check_email.html", "confirm_done.html", "resend.html", "password_reset_form.html", "password_reset_email.txt")
                 if (ROOT / AT / t).exists()]
    doc.para("Old email templates still on disk: " + (", ".join(leftovers) if leftovers else "none."))

    # =================================================================================================================
    # 8. Everything else
    # =================================================================================================================
    doc.h2("8. Everything else a participant can see")
    hdr_files = [FT + "base.html"]
    for label, client_, url in (("8.1 Site header, logged out (shown on every non-conversation page)", anon, "/how-it-works/"),
                                ("8.2 Site header, logged in (shown on home, discussions, blocked, propose, how it works, accounts pages)", cc, "/how-it-works/"),
                                ("8.3 Site header on a conversation page (no username of the viewer, deliberately)", ca, url_of(c_conv))):
        ex = extract(body(client_.get(url)), only_class="site-header")
        doc.h3(label)
        doc.table(["Visible text", "Source"], [[ln, INDEX.match(ln.replace(Extractor.HIDDEN, ""), hdr_files) or "- (dynamic text)"] for ln in ex.lines],
                  plain_cols=(1,))
    skip = extract(body(anon.get("/how-it-works/")), only_class="skip-link")
    doc.h3("8.4 Skip link (only visible to keyboard and screen-reader users)")
    doc.table(["Visible text", "Source"], [[ln, INDEX.match(ln.replace("[link: ", "").rstrip("]"), hdr_files) or "-"] for ln in skip.lines],
              plain_cols=(1,))
    footer_hits = [str(p.relative_to(ROOT)) for p in list((ROOT / "forum/templates").rglob("*.html")) +
                   list((ROOT / "accounts/templates").rglob("*.html")) if "<footer" in p.read_text(encoding="utf-8")]
    doc.h3("8.5 Footer")
    doc.para("No template has a footer element." if not footer_hits else "Footer found in: " + ", ".join(footer_hits))
    doc.h3("8.6 Flash messages")
    flash_hits = []
    for p in ROOT.rglob("*.py"):
        rel = str(p.relative_to(ROOT))
        if rel.startswith((".venv", "tests", "scripts", "scratchpad")) or "/migrations/" in rel:
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\b(messages|flash)\.(success|error|info|warning|add_message)\(", line):
                flash_hits.append((rel, n, line.strip()))
    doc.para(f"The page shell (`{loc(FT + 'base.html', 'class=\"messages\"')}`) prints any Django flash message in a status list. "
             "Flash messages set anywhere in the site code (`{name}` is the other person's username):")
    doc.table(["Message text", "Source"], [[re.sub(r"\{[^}]*\}", "{name}", (re.findall(r'"([^"]+)"', t) or [t])[-1]), f"{r}:{n}"] for r, n, t in flash_hits],
              text_cols=(0,), plain_cols=(1,))
    doc.h3("8.7 Page titles (<title>) of every page rendered above")
    seen, rows = set(), []
    for page, title in TITLES:
        if (page, title) in seen:
            continue
        seen.add((page, title))
        rows.append([title, page])
    doc.table(["Title", "Page"], rows, text_cols=(0,), plain_cols=(1,))
    doc.para("Default title of the page shell: `AI-Moderated Discussion Forum` (" + loc(FT + "base.html", "block title") + "). "
             "The conversation page's title is the proposition text itself.")
    doc.h3("8.8 Seeded topics (the suggested topics on the propose page and in the waiting list)")
    seeds = json.loads((ROOT / "forum/seed_topics.json").read_text(encoding="utf-8"))
    doc.para("Read from `forum/seed_topics.json` (loaded with `manage.py seed_topics`). Each topic has a stated position and an "
             "opposing position; the two are shown to people as join labels and in position lines.")
    doc.table(["Stated position", "Opposing position", "Source"],
              [[e["proposition"], e["opposing_position"], loc("forum/seed_topics.json", e["proposition"][:40])] for e in seeds],
              text_cols=(0, 1), plain_cols=(2,))
    doc.h3("8.9 Other visible elements")
    doc.bullets([
        "Browser address and tab title come from section 8.7. The proposition text, usernames and the messages are user-supplied, so "
        "they are not source text.",
        f"Character counter format: `<n> / {settings.MAX_MESSAGE_CHARS:,} characters` (message) and `<n> / {settings.MAX_PROPOSITION_CHARS} characters` "
        "(proposition), thousands separated with a comma.",
        "Time format on messages: `HH:MM UTC` (24 hour), with the full time in the `datetime` attribute. The date on the blocked "
        "list is `j F Y` (for example `15 January 2026`).",
        "Static pages other than those above: none. There is no privacy page, contact page or terms page (the How this works page says "
        "`[contact to be added]` and `[Retention period and who can see the data: to be written before launch.]` in square brackets: placeholders).",
    ])

    # =================================================================================================================
    # 9. Observations
    # =================================================================================================================
    doc.h2("9. Observations for the owner")
    doc.para("Automatic checks (a, d, e) are recomputed on every run over every string captured above and the template files. "
             "Items marked (manual) were read from the code as of the generation date and may need re-checking after changes.")

    COST = re.compile(r"\b(costs?|spend\w*|spent|budgets?|tokens?|API|Anthropic|dollars?|USD|prices?|pricing|billing|credits?|paid|quota)\b|\$",
                      re.IGNORECASE)
    EMAIL = re.compile(r"e-?mail|confirmation link|confirm your|resend|password reset|reset (your )?password|forgot", re.IGNORECASE)
    LABEL = re.compile(r"\b(Participant|User|Speaker|Person|Side)\s+[A-B]\b|\(\s*[AB]\s*\)")
    TARGETS = ["Choose a username and a password. You do not need an email address.",
               "There is no password reset by email. If you forget your password, ask the person running this site to reset it."]
    template_texts = []
    for base_ in ("forum/templates", "accounts/templates"):
        for p in sorted((ROOT / base_).rglob("*")):
            if p.suffix in (".html", ".txt"):
                for piece, n in INDEX.pieces(str(p.relative_to(ROOT))):
                    template_texts.append((piece, f"{p.relative_to(ROOT)}:{n}"))
    for rel in ("forum/services.py", "forum/viewmodels.py", "forum/views.py", "forum/templatetags/forum_text.py", "accounts/authviews.py",
                "accounts/validators.py", "accounts/models.py", "accounts/registration.py"):
        for piece, n in INDEX.pieces(rel):
            template_texts.append((piece, f"{rel}:{n}"))
    js_texts = [(lit, "forum/static/forum/*.js") for lit in js_literals]
    template_texts = [(t, s) for t, s in template_texts if not (t.startswith("^") or "(?" in t)]  # regular expressions are not text

    cost_hits = sorted({(t, s) for t, s in template_texts + js_texts if COST.search(t)} |
                       {(t, "rendered: " + sec) for t, sec in doc.registry if COST.search(t)})
    doc.h3("(a) Costs, spending, budgets, tokens, the API")
    if not cost_hits:
        doc.para("No violations: no text a participant can see (rendered pages, refusals, notices, templates, error pages, "
                 "client scripts, the account pages, the seeded topics) contains cost, spend, budget, token, API, price, billing or credit wording "
                 "(scan pattern: `" + COST.pattern + "`).")
    else:
        doc.para("Hits of the scan (each needs a look; some may be harmless):")
        doc.table(["Text", "Where"], [[t, s] for t, s in cost_hits], plain_cols=(1,))
    doc.bullets([
        "(manual) Indirect hints, not violations of the wording rule: the notices `AI moderation is paused for today and will "
        "resume tomorrow`, `...will resume later` and `The AI moderator will not comment further in this conversation` describe a "
        "cap without naming money. The pipeline sets only the reasons `breaker_open`, `budget_exceeded`, `budget_unavailable` "
        "and `refused` (moderation/pipeline.py), so a cap pause shows the generic text and those three specific notices "
        "are never chosen today (section 3.1).",
    ])

    doc.h3("(b) Places where a person is refused but the text does not say why or what to do next")
    doc.bullets([
        f"(manual) Post refused as empty: banner title `Your message is empty` and the text `Your message is empty.` say what, "
        f"not what to do next. Source {loc('forum/services.py', 'Your message is empty.')}. The propose-empty message does say what to do.",
        f"(manual) `hidden` (a proposition hidden since the page loaded): `This proposition is not available.` gives neither a reason "
        f"nor a next step, and appears as a bare banner on the home page. Source {loc('forum/services.py', 'This proposition is not available.')}.",
        f"(manual) `duplicate`: `This proposition already exists. Choose it from the list.` The home page now lists only positions "
        f"someone is waiting on, so the existing proposition may not be in any list; the button `Discuss the existing proposition` "
        f"is the real next step. Source {loc('forum/services.py', 'This proposition already exists.')}.",
        f"(manual) `too_many_open` (only if the tunable is set; the default is no cap): `End one before starting another.` does "
        f"not say where the open conversations are; the header link `Your discussions` now lists them but the message does not mention it. "
        f"Source {loc('forum/services.py', 'End one before starting another.')}.",
        "(manual) Being blocked: the blocked person's conversation simply shows `The other participant ended this conversation.` and "
        "the waiting cards of the blocker vanish. By design they are never told why; noted here because it is a place where a "
        "person cannot post and the text gives the (true but incomplete) reason only.",
        f"(manual) Fallback box text `Posting is not available in this conversation at the moment.` (title `You cannot post right "
        f"now`) has no reason or next step. It is shown only if the page cannot post and no reason exists, which the current view "
        f"model never produces. Source {loc(FT + 'conversation.html', 'Posting is not available')}.",
        f"(manual) `invalid_side` `Choose a position first.` and `invalid_block` `You cannot block yourself.` are reachable only by a "
        "hand-made request; both say what is wrong. `That person was not found.` (service) is not reachable from a page.",
        "(manual) Wrong method (opening a POST-only address such as /c/<id>/post/ or /users/<name>/block/ by link): HTTP 405 with a "
        f"completely blank page. Measured in section 2.4. Source {loc('forum/views.py', '@require_POST')}.",
        "(manual) Not logged in: the redirect to the login page has no sentence saying that login is needed to see the page (section 5).",
        "(manual) Polling failure: if the connection drops, the page silently stops updating; nothing tells the person (section 6).",
        f"(manual) Placeholders a person can read today: `[contact to be added]` in the How this works page "
        f"({loc(FT + 'how_it_works.html', 'contact to be added')}): the reader is told to report abuse to someone but not how.",
        "(manual) The login page after a lockout and the 403 page do say what to do; all closed states have a reason and a next step; "
        "too fast, too long, duplicate, daily limit have both; the empty states of the home page, Your discussions and Blocked people "
        "say what to do or that nothing is there.",
    ])

    way_back = {t for t, _ in doc.registry if re.fullmatch(r"\[(link|button): (← ?)?(Back to [^\]]*|Home|All propositions)\]", t)}
    doc.h3("(c) Inconsistent wording for the same thing")
    doc.bullets([
        "(manual) `discussion`, `conversation`, `debate` (gone), `proposition`, `position` and `topic` are all used for related things: "
        "the header link and page are `Your discussions`, the buttons are `Start a new discussion` (home) and `Start a new "
        "conversation` (closed conversation, which goes to the home page, not to the propose page), the end card says `End "
        "conversation`, the propose page publishes a `proposition` while its label says `My position is that`, and the seeded section "
        "says `topics`.",
        f"(manual) Old wording that no longer matches the waiting-list design: the refusal texts say `You can start a new one by choosing a "
        f"proposition` ({loc('forum/services.py', 'by choosing a proposition')}) and `Choose it from the list`; the home page now offers "
        "positions to take.",
        "(auto) The way back to the home page is worded: " + ", ".join(f"`{x}`" for x in sorted(way_back)) + (
            " (one wording everywhere)." if len({re.sub(r"^\[(link|button): (← ?)?", "", x).rstrip("]") for x in way_back}) == 1
            else " (not one wording everywhere: the conversation page says `Home`, the others `Back to home`, and arrows differ).") ,
        f"(manual) The empty-proposition refusal says `Your message is empty. Write the proposition you want people to discuss...` under "
        f"the banner title `Your proposition is empty` (noun changes mid-message). Source {loc('forum/services.py', 'Write the proposition you want')}.",
        f"(manual) The message-limit-reached text exists in three wordings ({loc('forum/services.py', 'It reached its limit of')}, "
        f"{loc('forum/services.py', 'has reached its limit of')}, and the template fallback {loc(FT + 'conversation.html', 'has reached its limit of')}, "
        "which is not shown today because a reason is always present when closed).",
        f"(manual) Button and hint disagree: the button is `Post message` but the hint says `Nothing you type is sent until you press Post.` "
        f"({loc(FT + 'conversation.html', 'until you press Post')}).",
        "(manual) The other person is `<username>` on the conversation page, in the block card and in moderator headings, but `The other "
        "participant` in every closed-conversation sentence (`The other participant ended this conversation.`) and in the 404 text, "
        "`the other person` in the Who-is-here fallback, and `Someone` in the card fallback.",
        "(manual) Apostrophes: moderator headings use a straight apostrophe (`About quincy_ray's message 2`, from viewmodels.py) while the "
        "templates use a curly one (`moderator&rsquo;s`).",
        "(manual) `Signed in as ...` in the header versus `Log in` / `Log out` / `You have been logged out.`",
        f"(manual) `1 characters`: the browser advice line always says `characters` ({loc('forum/static/forum/compose.js', 'characters. You can still')}); "
        "the server says `1 character`.",
        "(manual) Message numbers versus the message count: headings say `About your message N` where N counts every message including "
        f"the moderator's, while the Limits card says `X of {settings.MAX_USER_MESSAGES_PER_CONVERSATION} messages used`, which counts only people's messages.",
        f"(manual) The Limits list on How this works says `You can post one message every {settings.MIN_SECONDS_BETWEEN_MESSAGES} seconds` and, two "
        f"lines below, `Nothing stops you posting several messages in a row.` ({loc(FT + 'how_it_works.html', 'Nothing stops you')}); it means no "
        "turn-taking, but reads as a contradiction.",
        f"(manual) Hard-coded numbers in text that otherwise comes from settings: `about 500 words` ({loc(FT + 'how_it_works.html', 'about 500 words')}) "
        f"and `Active · 2 participants` ({loc(FT + 'conversation.html', '2 participants')}); they go stale if the tunables change.",
        f"(manual) The 404 page has the title `Page not found` but the heading `Not found` ({loc('forum/templates/404.html', 'Not found')}).",
        "(manual) `Blocked people` says `since 15 January 2026` (a date) while messages show `HH:MM UTC`.",
    ])

    doc.h3("(d) Text that could reveal a participant label, a username or the other person's identity")
    leak = []
    label_re = re.compile(r"\b(Participant|User|Speaker|Person|Side)\s+[AB]\b|\(\s*[AB]\s*\)")
    for name, html_, viewer_name, allowed in page_texts:
        plain = re.sub(r"<[^>]+>", " ", html_)
        low = plain.lower()
        if "@leakcheck.example" in low:
            leak.append(f"{name}: contains an email address")
        if label_re.search(plain):
            leak.append(f"{name}: label-like text")
        ok_names = set(allowed) | ({viewer_name} if viewer_name and not name.startswith("conversation") else set())
        for u in ALL_NAMES:
            if u not in ok_names and re.search(r"(?<![\w-])" + re.escape(u) + r"(?![\w-])", plain):
                leak.append(f"{name}: shows the username {u!r}, which this page should not show")
    poll_blob = json.dumps(poll_json)
    poll_problems = [t for t in (NAME_A, "@leakcheck") if t in poll_blob]
    if any(v in ("A", "B") for m in poll_json["messages"] for v in m.values() if isinstance(v, str)):
        poll_problems.append("label letter value in a message")
    doc.bullets([
        f"Automatic scan of {len(page_texts)} rendered pages (both viewers where relevant): no email address; no `Participant A/B`-style "
        "text; on conversation pages only the OTHER person's username (never the viewer's own, never a third person's); on "
        "propose, How this works, error pages only the viewer's own name (in the header); usernames of others only where they are "
        "meant to show (home cards, Your discussions, Blocked people, conversation). Result: "
        + ("nothing found." if not leak else "FOUND: " + "; ".join(leak)),
        "Automatic scan of the polling response (`/c/<id>/messages/`, viewer `" + NAME_A + "`): " + ("no viewer username, email or label letter; it carries "
        "`other_username` and `author_name` (the other person) by design." if not poll_problems else "FOUND: " + ", ".join(poll_problems)),
        "(manual) Usernames are now shown on purpose: on every waiting card (to any logged-in person who is not blocked), in Your "
        "discussions (`with <username>`), in Blocked people, on the conversation page (other person only), in the block card and in "
        "moderator headings. The header shows the viewer's OWN username on all pages except the conversation page.",
        "(manual) The other person's username and the `Block <username>` card also stay on closed conversations (including the one the "
        "other person ended by blocking you, where the card offers to block them).",
        "(manual) A waiting card also shows the position the person holds (`<username> is waiting to discuss: <position>`), so any logged-in "
        "person can see which position a named person took. By design under the waiting-list model; noted for the neutrality review.",
        "(manual) Label letters never appear. The moderator heading says whose message a note is about by username or `your`; the "
        "moderator's own text is model-written; the pipeline rejects acts whose text names a label (moderation/label_check.py), but a "
        "person's own words can of course be quoted.",
        "(manual) The 404 page uses one text for a missing conversation and for someone else's conversation, so nothing reveals which exist. "
        "Blocking an unknown username gives the plain 404 page while blocking a real one succeeds, so the block address can be used to "
        "test whether a username exists (usernames are visible on waiting cards anyway).",
    ])

    doc.h3("(e) Leftover mentions of email confirmation or password reset by email")
    email_hits = []
    for t, s in template_texts:
        if EMAIL.search(t):
            if any(x in t for x in TARGETS):
                st = "intended: the step 6c target wording"
            elif s.startswith("accounts/models.py"):
                st = "model-level message; the register form has no email field, so only the admin can reach it"
            else:
                st = "NEEDS AN EDIT: still says this after step 6c"
            email_hits.append((t, s, st))
    doc.para("Scan of all template text and the Python strings of the user-facing modules for email, confirmation, resend, "
             "password reset and forgot wording (`" + EMAIL.pattern + "`):")
    if email_hits:
        doc.table(["Text", "Source", "Status"], [[t, s, st] for t, s, st in sorted(email_hits, key=lambda x: (x[2], x[1]))],
                  text_cols=(0,), plain_cols=(1, 2))
    else:
        doc.para("No hits.")
    needs = [h for h in email_hits if h[2].startswith("NEEDS")]
    doc.bullets([
        ("(manual) Items that still need an edit: " + "; ".join(f"`{t[:80]}...` ({s})" for t, s, _ in needs) + ".") if needs else
        "No leftover mention of email confirmation or reset by email in any text a participant can see (the How this works page no longer "
        "mentions email).",
        f"(manual) The register page's own wording about email is the fixed target text ({loc(brief, 'Register page text')}).",
        "(manual) `config/settings.py` still has a mail backend; the brief says it stays for the circuit-breaker alert, which no participant sees.",
    ])

    doc.h3("Counts")
    doc.para(f"Strings captured in this file: {doc.rows} table rows, of which {len(doc.unique)} are distinct.")
    header.append("")
    header.append(f"Strings captured: {doc.rows} rows ({len(doc.unique)} distinct).")
    return doc.render(header)


if __name__ == "__main__":
    if "--out" in sys.argv:
        OUT_OVERRIDE = sys.argv[sys.argv.index("--out") + 1]
    main()
