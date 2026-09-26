"""Shared helpers for the step 3 (prompt spike and golden set) tests. Not a test module (no test_ prefix).

Imports of moderation.* and forum.* happen inside functions, so a missing module fails the test that needs it and not
the collection of the whole folder.

Nothing here (or in the test_step3_* files) may make a network call, read .env, or write into the real
golden/results directory.
"""
import html
import json
import re
import unicodedata
from decimal import Decimal
from html.parser import HTMLParser
from itertools import product
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TRANSCRIPT_DIR = ROOT / "golden" / "transcripts"
REAL_RESULTS_DIR = ROOT / "golden" / "results"
PROMPT_DIR = ROOT / "moderation" / "prompts"
RUBRIC_DIR = ROOT / "rubrics"

SONNET = "claude-sonnet-5"
HAIKU = "claude-haiku-4-5"

D = Decimal

ISSUE_TYPES = {
    "unsupported_claim",
    "possible_factual_error",
    "unclear_statement",
    "fallacy",
    "strawman",
    "abusive_language",
    "repetition",
    "process_violation",
}
ACT_TYPES = {
    "provide_information",
    "correct_factual_error",
    "improve_argumentation",
    "clarify_argument",
    "restate_positions",
    "identify_agreement_disagreement",
    "request_information",
    "request_clarification",
    "enforce_conduct",
    "enforce_process",
}
DECISIONS = {"intervene", "no_intervention"}
TONES = {"gentle", "neutral", "firm"}
NOT_SCORABLE_REASONS = {"unverifiable", "contested", "needs_context"}
DIMENSION_TO_ISSUE_TYPE = {"factual_accuracy": "possible_factual_error", "abusiveness": "abusive_language"}


# --- transcripts ----------------------------------------------------------------------------------------------------

def load_transcripts():
    """{id: data} for every golden/transcripts/*.json (an empty dict while the directory does not exist).
    Each data dict also gets `_path`."""
    result = {}
    if not TRANSCRIPT_DIR.is_dir():
        return result
    for path in sorted(TRANSCRIPT_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["_path"] = path
        result[data.get("id", path.stem)] = data
    return result


def count_chars(text):
    """The message length rule of plan section 4 (forum/limits.py: count_message_chars) when that exists, else the same
    rule spelled out: normalize line endings to \\n, NFC, strip, count code points."""
    try:
        from forum.limits import count_message_chars
    except ImportError:
        count_message_chars = None
    if count_message_chars is not None:
        return count_message_chars(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return len(unicodedata.normalize("NFC", text).strip())


def planted_items(transcript):
    """[(message dict, planted dict), ...] for a transcript."""
    return [(m, p) for m in transcript["messages"] for p in m.get("planted", [])]


def is_series(transcript):
    return bool(transcript.get("series"))


def is_single(transcript):
    """A single transcript: not a member of a matched pair and not a member of a mechanical series."""
    return not transcript.get("pair_id") and not is_series(transcript)


def pairs(transcripts):
    """{pair_id: [transcript, ...]} for the paired transcripts."""
    grouped = {}
    for t in transcripts.values():
        if t.get("pair_id"):
            grouped.setdefault(t["pair_id"], []).append(t)
    return grouped


def pair_dimensions(pair):
    return {p["dimension"] for t in pair for _m, p in planted_items(t)}


def find_pair(transcripts, dimension):
    """The (left, right) transcripts of the first pair (sorted by pair id) that plants `dimension`."""
    for pair_id, members in sorted(pairs(transcripts).items()):
        if dimension in pair_dimensions(members):
            by_variant = {t.get("variant"): t for t in members}
            return by_variant["left"], by_variant["right"]
    raise AssertionError(f"no pair plants {dimension!r} (is golden/transcripts complete?)")


def benign_single(transcripts):
    for t in transcripts.values():
        if is_single(t) and not planted_items(t):
            return t
    raise AssertionError("no benign single transcript (a single with nothing planted)")


def phrase_of(transcript):
    """(message, planted) of the first planted item."""
    items = planted_items(transcript)
    assert items, f"{transcript['id']} plants nothing"
    return items[0]


def _fragment(target, all_transcripts):
    """A unique fragment if there is one, else a plain window of the newest message (only used to look for a message in a file)."""
    try:
        return unique_fragment(target, all_transcripts)
    except AssertionError:
        text = target["messages"][-1]["text"]
        match = re.search(r"[A-Za-z][A-Za-z ,]{25,}", text)
        return match.group(0)[:30].strip()


def unique_fragment(target, all_transcripts):
    """A run of plain letters, spaces and commas taken from a message of `target` that occurs in no other transcript.
    Plain letters survive any escaping, so it can be looked for in the text sent to the model."""
    others = " \n".join(m["text"] for t in all_transcripts.values() if t is not target for m in t["messages"])
    for message in target["messages"]:
        for match in re.finditer(r"[A-Za-z][A-Za-z ,]{25,}", message["text"]):
            run = match.group(0).strip()
            for start in range(0, max(1, len(run) - 30), 7):
                fragment = run[start:start + 30]
                if len(fragment) >= 26 and fragment not in others:
                    return fragment
    raise AssertionError(f"could not find a fragment unique to {target['id']}")


# --- a shared, deterministic measuring stick for "how many checkable assertions / hedges does a message make" -------------
# These are NOT the truth about a text. They are simple, stable rules that both the tests and the transcript author can run,
# so "the two variants of a pair make the same number of checkable claims" is a mechanical statement.

_NUMBER_WORDS = (
    "two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|"
    "ninety|hundred|thousand|million|billion|dozen|dozens|decade|decades|century|centuries|percent|half|quarter|double|triple"
)
_NUMBER_RE = re.compile(rf"\d|\b(?:{_NUMBER_WORDS})\b", re.I)
_CAUSAL_RE = re.compile(
    r"\b(?:because|leads?\s+to|led\s+to|causes?|caused|causing|reduc(?:e|es|ed|ing)|increas(?:e|es|ed|ing)|decreas(?:e|es|ed|ing)|"
    r"discourag(?:e|es|ed|ing)|encourag(?:e|es|ed|ing)|lower(?:s|ed|ing)?|rais(?:e|es|ed|ing)|results?\s+in|"
    r"means\s+that|shows?\s+that|showed\s+that|proves?|proven|more\s+likely|less\s+likely|"
    r"studies|study|evidence|research|data|statistics|experts?|"
    r"(?:some|many|most|few|all|every|no)\s+(?:people|immigrants|tenants|renters|landlords|families|residents|users|cities|officers|"
    r"police|drugs?|countries|states)|costs?|costly|frees?\s+up|pushes?|push)\b",
    re.I,
)
_HEDGE_RE = re.compile(
    r"\b(?:may|might|could|can|often|some|perhaps|maybe|likely|probably|possibly|seems?|appears?|tends?|generally|usually|"
    r"sometimes|arguably|somewhat|rarely|at\s+least|i\s+think|i\s+worry|i\s+doubt|i\s+believe|i\s+suspect|i\s+wonder|"
    r"not\s+always|in\s+some)\b",
    re.I,
)
_CLAUSE_SPLIT_RE = re.compile(r"[;,:]\s+|\s+(?:and|but|so|which|while|whereas|although|though|because)\s+", re.I)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def count_assertions(text):
    """Concrete, checkable assertions in a text, by these exact rules:

    1. Split the text into sentences at ". ", "! " or "? " and drop every sentence that ends with "?" (questions assert nothing).
    2. Split each remaining sentence into clauses at "; , :" followed by a space, and at the words and, but, so, which, while,
       whereas, although, though, because (a clause is a piece of a sentence that can be true or false on its own).
    3. A clause counts as ONE assertion if it contains any of:
       (a) a numeral (any digit) or a number word / quantity word (two .. twelve, fifteen .. ninety; "one" is not counted because
           it is mostly a pronoun: "one another", "a one-line summary"; hundred, thousand, million,
           billion, dozen, decade(s), century, percent, half, quarter, double, triple);
       (b) a proper name: a capitalised word that is not the first word of its sentence and is not "I" or a contraction of it;
       (c) a causal or empirical pattern: because, leads to, causes, reduces, increases, decreases, discourages, encourages,
           lowers, raises, results in, means that, shows that, proves, more/less likely, study/studies, evidence, research,
           data, statistics, expert(s), a quantifier followed by a group noun (some/many/most/few/all/every/no + people,
           immigrants, tenants, renters, landlords, families, residents, users, cities, officers, police, drugs, countries,
           states), cost(s)/costly, frees up, push(es).
       A clause with several of these still counts once; a clause with none counts zero.
    Returns the number of counted clauses over the whole text. Deterministic; it is a measuring stick, not a truth."""
    total = 0
    for sentence in _SENTENCE_SPLIT_RE.split(text.strip()):
        sentence = sentence.strip()
        if not sentence or sentence.endswith("?"):
            continue
        names = {
            w.strip(".,;:!\"'()") for i, w in enumerate(sentence.split())
            if i > 0 and re.match(r"[A-Z][A-Za-z\-]*", w.strip("\"'(")) and w.strip(".,;:!\"'()") not in ("I", "I'm", "I'd", "I'll", "I've")
        }
        for clause in _CLAUSE_SPLIT_RE.split(sentence):
            if not clause or not clause.strip():
                continue
            has_name = any(re.search(rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])", clause) for name in names if name)
            if _NUMBER_RE.search(clause) or has_name or _CAUSAL_RE.search(clause):
                total += 1
    return total


def count_hedges(text):
    """Hedge words and phrases, counted as separate occurrences (case-insensitive, whole words): may, might, could, can, often,
    some, perhaps, maybe, likely, probably, possibly, seem(s), appear(s), tend(s), generally, usually, sometimes, arguably,
    somewhat, rarely, "at least", "I think", "I worry", "I doubt", "I believe", "I suspect", "I wonder", "not always",
    "in some". A phrase and a word inside it (for example "in some") count as two, deliberately: it is a simple stick."""
    return len(_HEDGE_RE.findall(text))


# --- text flattening of what was sent to the fake client ------------------------------------------------------------

def flatten(value):
    """All the text inside a `system` value or a `messages` list (strings, block lists, dicts)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if "text" in value:
            return flatten(value["text"])
        if "content" in value:
            return flatten(value["content"])
        return json.dumps(value, sort_keys=True, default=str)
    if isinstance(value, (list, tuple)):
        return "\n".join(flatten(v) for v in value)
    return str(value)


def call_text(kwargs):
    """The user-visible input of one recorded client call (the messages, not the system prompt)."""
    return flatten(kwargs.get("messages"))


def call_agent(kwargs):
    name = getattr(kwargs["output_format"], "__name__", "")
    return {"MasterOutput": "master", "IntervenorOutput": "intervenor"}.get(name, name)


# --- tolerant builders for the step 3 schemas -----------------------------------------------------------------------
# The brief fixes the field NAMES but not every field TYPE (is an issue id a string or an int? is confidence a number or a
# word?). Rather than guess, the builders below find, once, which style validates, so the tests survive either choice.

_STYLE = {}


def _try_issue(id_v, mid_v, conf_v):
    from moderation.schemas import MasterIssue

    try:
        MasterIssue(
            id=id_v, message_id=mid_v, issue_type="unsupported_claim", quote="q", explanation="e",
            confidence=conf_v, intensity=None,
        )
    except Exception:
        return False
    return True


def style():
    """{'id': 'str'|'int', 'mid': 'int'|'str', 'conf': value} of the MasterIssue fields, found by trial."""
    if not _STYLE:
        for id_v, mid_v, conf_v in product(("i1", 1), (1, "1"), (0.9, "high", 4)):
            if _try_issue(id_v, mid_v, conf_v):
                _STYLE.update(id="str" if isinstance(id_v, str) else "int", mid="int" if isinstance(mid_v, int) else "str", conf=conf_v)
                break
        else:
            raise AssertionError("no combination of id/message_id/confidence styles validates as a MasterIssue")
    return _STYLE


def make_id(n):
    return f"i{n}" if style()["id"] == "str" else n


def make_mid(seq):
    return int(seq) if style()["mid"] == "int" else str(seq)


def issue_dict(n, seq, quote, *, issue_type="unsupported_claim", explanation="An explanation.", intensity=None):
    return dict(
        id=make_id(n), message_id=make_mid(seq), issue_type=issue_type, quote=quote, explanation=explanation,
        confidence=style()["conf"], intensity=intensity,
    )


def discussion_map_dict():
    return {"agreements": ["Both value affordable housing."], "disagreements": [{"summary": "Whether it works.", "kind": "factual"}]}


def master_output(issues=()):
    from moderation.schemas import MasterOutput

    return MasterOutput.model_validate({"issues": list(issues), "discussion_map": discussion_map_dict()})


def disposition_dict(n, disposition, reason="A reason."):
    return {"issue_id": make_id(n), "disposition": disposition, "reason": reason}


def act_dict(*, type="correct_factual_error", addressee="A", subject="A", issue_ns=(1,), seqs=(1,), tone="gentle", text="Some text."):
    return dict(
        type=type, addressee=addressee, subject=subject, source_issue_ids=[make_id(n) for n in issue_ns],
        source_message_ids=[make_mid(s) for s in seqs], tone=tone, text=text,
    )


def intervenor_output(decision="no_intervention", rationale="Nothing needs doing.", dispositions=(), acts=()):
    from moderation.schemas import IntervenorOutput

    return IntervenorOutput.model_validate(
        {"decision": decision, "rationale": rationale, "issue_dispositions": list(dispositions), "acts": list(acts)}
    )


# --- a scripted stand-in for both agents, driven by what the client is asked --------------------------------------

class Scripted:
    """A FakeLLM script item (a callable) that answers each call according to WHICH transcript and WHICH agent it is for,
    so the tests do not depend on the order the command processes transcripts in. The transcript is recognized by a
    fragment of its text that occurs in no other transcript.

    `fail[(transcript_id, agent)]` may be 'provider' (HTTP 500), 'connection' (no status), 'validation' (the SDK's
    ValidationError), 'invalid' (no parsed output) or 'truncated'.
    Every answer carries recognizable markers (explanation-<id>-<n>, reason-<id>-<n>, rationale-<id>, actxt-<id>...).
    """

    def __init__(self, transcripts, master_usage=(1234, 321), intervenor_usage=(2345, 432)):
        self.transcripts = transcripts
        self.fragments = {tid: _fragment(t, transcripts) for tid, t in transcripts.items()}
        self.signatures = {
            tid: [(str(m["seq"]), m["author"], m["text"].strip()) for m in t["messages"] if m["seq"] <= t["trigger_seq"]]
            for tid, t in transcripts.items()
        }
        self.master_usage = master_usage
        self.intervenor_usage = intervenor_usage
        self.fail = {}
        self.weird = set()  # transcript ids whose Master output cites a message that does not exist and an empty quote
        self.seen = []  # (transcript_id, agent) in call order
        self.contents = {}  # (transcript_id, agent) -> the user-visible input text

    def identify(self, text):
        """Which transcript a call is for: the one whose visible messages (ids, labels and texts) are exactly the message
        blocks in the input. Exact, so transcripts that share most of their text (matched pairs) cannot be confused."""
        seen = [(a["id"], a["participant"], t.strip()) for a, t in parse_rendered(text)]
        found = [tid for tid, signature in self.signatures.items() if signature == seen]
        assert len(found) == 1, f"could not tell which transcript this call is for: {found}"
        return found[0]

    def act_text(self, tid):
        return f"actxt-{tid}-" + "x" * 11

    def master_issues(self, tid):
        t = self.transcripts[tid]
        items = planted_items(t)
        if not items:
            return []
        message, planted = items[0]
        seq = message["seq"]
        if tid in self.weird:
            return [
                issue_dict(1, 9999, planted["phrase"], explanation=f"explanation-{tid}-1"),
                issue_dict(2, seq, "", explanation=f"explanation-{tid}-2"),
            ]
        issue_type = DIMENSION_TO_ISSUE_TYPE[planted["dimension"]]
        phrase = planted["phrase"]
        shouted = phrase.upper() if phrase.upper() != phrase else phrase.lower()
        return [
            issue_dict(1, seq, phrase, issue_type=issue_type, explanation=f"explanation-{tid}-1", intensity=planted["intensity"]),
            issue_dict(2, seq, shouted, issue_type=issue_type, explanation=f"explanation-{tid}-2", intensity=planted["intensity"]),
            issue_dict(3, seq, "this wording appears nowhere in the message qzxv", issue_type="unclear_statement", explanation=f"explanation-{tid}-3"),
        ]

    def intervenor(self, tid):
        t = self.transcripts[tid]
        issues = self.master_issues(tid)
        if not issues or tid in self.weird:
            return intervenor_output("no_intervention", f"rationale-{tid}")
        message, planted = planted_items(t)[0]
        factual = planted["dimension"] == "factual_accuracy"
        return intervenor_output(
            "intervene",
            f"rationale-{tid}",
            dispositions=[
                disposition_dict(1, "acted", f"reason-{tid}-1"),
                disposition_dict(2, "declined", f"reason-{tid}-2"),
                disposition_dict(3, "declined", f"reason-{tid}-3"),
            ],
            acts=[
                act_dict(
                    type="correct_factual_error" if factual else "enforce_conduct",
                    addressee="A", subject=message["author"], issue_ns=(1,), seqs=(message["seq"],),
                    tone="gentle", text=self.act_text(tid),
                )
            ],
        )

    def __call__(self, kwargs):
        from moderation.fake_llm import FakeProviderError, make_message

        agent = call_agent(kwargs)
        text = call_text(kwargs)
        tid = self.identify(text)
        self.seen.append((tid, agent))
        self.contents[(tid, agent)] = text
        mode = self.fail.get((tid, agent))
        if mode == "provider":
            raise FakeProviderError(500, "api_error", "scripted provider failure")
        if mode == "connection":
            # no status code: the gateway cannot know whether it was billed, so it counts the whole reservation
            raise FakeProviderError(None, "connection_error", "scripted dropped connection")
        if mode == "validation":
            # what the SDK raises for truncated or invalid JSON; the usage is lost, so the gateway bills the reservation
            from moderation.schemas import MasterOutput

            MasterOutput.model_validate({})
        usage = self.master_usage if agent == "master" else self.intervenor_usage
        kw = dict(input_tokens=usage[0], output_tokens=usage[1])
        if mode == "invalid":
            return make_message(None, **kw)
        if mode == "truncated":
            return make_message(None, stop_reason="max_tokens", **kw)
        parsed = master_output(self.master_issues(tid)) if agent == "master" else self.intervenor(tid)
        return make_message(parsed, **kw)

    def install(self, install_fake, copies=400):
        return install_fake(*([self] * copies))

    def calls_for(self, tid):
        return [agent for t, agent in self.seen if t == tid]


# --- running the spike command -----------------------------------------------------------------------------------

class SpikeRun:
    def __init__(self, out, err, exc):
        self.out = out
        self.err = err
        self.exc = exc

    @property
    def text(self):
        return "\n".join(part for part in (self.out, self.err, str(self.exc) if self.exc else "") if part)


def run_spike(*args):
    """manage.py spike, in process. A CommandError is captured (self.exc); any other exception propagates."""
    from io import StringIO

    from django.core.management import call_command
    from django.core.management.base import CommandError

    out, err = StringIO(), StringIO()
    exc = None
    try:
        call_command("spike", *args, stdout=out, stderr=err)
    except CommandError as caught:
        if "Unknown command" in str(caught):
            raise AssertionError(f"manage.py spike does not exist: {caught}") from caught
        exc = caught
    return SpikeRun(out.getvalue(), err.getvalue(), exc)


def decimals_in(text):
    """All plain decimal numbers in a text, as Decimals."""
    return [D(m) for m in re.findall(r"(?<![\w.])\d+(?:\.\d+)?", text)]


def dollars_in(text):
    return [D(m) for m in re.findall(r"\$\s?(\d+(?:\.\d+)?)", text)]


def integers_in(text):
    return [int(m) for m in re.findall(r"(?<![\d.$])\d+(?![\d.])", text)]


def find_report(out_dir):
    reports = list(Path(out_dir).rglob("report.md"))
    assert len(reports) == 1, f"expected exactly one report.md under {out_dir}, found {reports}"
    return reports[0]


def result_json(results_dir, tid):
    files = [p for p in Path(results_dir).glob("*.json") if p.stem == tid] or [
        p for p in Path(results_dir).glob("*.json") if tid in p.name
    ]
    assert len(files) == 1, f"expected one result JSON for {tid} in {results_dir}, found {files}"
    return files[0], json.loads(files[0].read_text(encoding="utf-8"))


def walk(node):
    """Every dict, list and scalar inside a JSON value, depth first."""
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def values_under_key(node, key_part):
    """Values (scalars) stored under any key whose lowercase name contains `key_part`, at any depth."""
    found = []
    for item in walk(node):
        if isinstance(item, dict):
            for key, value in item.items():
                if key_part in str(key).lower():
                    found.extend(v for v in walk(value) if not isinstance(v, (dict, list)))
    return found


def strings_in(node):
    return [x for x in walk(node) if isinstance(x, str)]


def markdown_tables(text):
    """Blocks of consecutive lines that start with '|'."""
    blocks, current = [], []
    for line in text.splitlines():
        if line.strip().startswith("|"):
            current.append(line)
        elif current:
            blocks.append("\n".join(current))
            current = []
    if current:
        blocks.append("\n".join(current))
    return blocks


# --- a parser for the rendered transcript ---------------------------------------------------------------------------

class _MessageCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.messages = []  # [(attrs dict, text)]
        self._current = None

    def handle_starttag(self, tag, attrs):
        if tag == "message":
            self._current = (dict(attrs), [])

    def handle_endtag(self, tag):
        if tag == "message" and self._current is not None:
            attrs, parts = self._current
            self.messages.append((attrs, "".join(parts)))
            self._current = None

    def handle_data(self, data):
        if self._current is not None:
            self._current[1].append(data)


def parse_rendered(rendered):
    """[(attrs, text)] of the <message> blocks in a rendered transcript, seen the way a real HTML parser sees them."""
    parser = _MessageCollector()
    parser.feed(rendered)
    parser.close()
    return parser.messages


# --- fixtures (import them into a test module to use them) ----------------------------------------------------------

@pytest.fixture(autouse=True)
def real_results_untouched():
    """No step 3 test may write into the real golden/results directory."""

    def snapshot():
        if not REAL_RESULTS_DIR.exists():
            return None
        return sorted((str(p.relative_to(REAL_RESULTS_DIR)), p.stat().st_mtime_ns) for p in REAL_RESULTS_DIR.rglob("*"))

    before = snapshot()
    yield
    assert snapshot() == before, "a test wrote into the real golden/results directory"


@pytest.fixture
def spike_env(llm_ready, tune, settings, monkeypatch):
    """LLM switched on with a dummy key, roomy caps (so only the cap a test is about can bite), Haiku as the spike model."""
    from config import tunables

    tune(
        BUDGET_SITE_USD_TOTAL=D("100"),
        BUDGET_SITE_USD_PER_DAY=D("100"),
        BUDGET_PER_CONVERSATION_USD=D("100"),
        BUDGET_EVAL_USD_TOTAL=D("100"),
        SPIKE_MODEL=HAIKU,
    )
    settings.BUDGET_SPIKE_USD_TOTAL = D("100")  # exists only once the spike cap has landed
    monkeypatch.setattr(tunables, "BUDGET_SPIKE_USD_TOTAL", D("100"), raising=False)
    return llm_ready


@pytest.fixture
def golden():
    """The golden transcripts; fails (the right reason) while there are none."""
    transcripts = load_transcripts()
    assert transcripts, f"no transcripts in {TRANSCRIPT_DIR}"
    return transcripts


def escape_like_xml(text):
    return html.escape(text, quote=True)
