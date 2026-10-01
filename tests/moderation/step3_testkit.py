"""Shared helpers for the step 3 (prompts, schemas, taxonomy) tests. Not a test module (no test_ prefix).

Imports of moderation.* and forum.* happen inside functions, so a missing module fails the test that needs it and not
the collection of the whole folder.

Nothing here (or in the test_step3_* files) may make a network call or read .env.
"""
from html.parser import HTMLParser
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROMPT_DIR = ROOT / "moderation" / "prompts"
RUBRIC_DIR = ROOT / "rubrics"


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
    "offer_research",
}
DECISIONS = {"intervene", "no_intervention"}
TONES = {"gentle", "neutral", "firm"}
NOT_SCORABLE_REASONS = {"unverifiable", "contested", "needs_context"}
DIMENSION_TO_ISSUE_TYPE = {"factual_accuracy": "possible_factual_error", "abusiveness": "abusive_language"}


# These are NOT the truth about a text. They are simple, stable rules that both the tests and the transcript author can run,
# so "the two variants of a pair make the same number of checkable claims" is a mechanical statement.


# --- tolerant builders for the step 3 schemas -----------------------------------------------------------------------
# The brief fixes the field NAMES but not every field TYPE (is an issue id a string or an int? is confidence a number or a
# word?). Rather than guess, the builders below find, once, which style validates, so the tests survive either choice.

_STYLE = {}


def _try_issue(id_v, mid_v, conf_v):
    from moderation.schemas import MasterIssue

    try:
        MasterIssue(
            id=id_v, message_id=mid_v, issue_type="unsupported_claim", quote="q", explanation="e",
            confidence=conf_v, intensity=None, needs_verification=False,
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


def issue_dict(n, seq, quote, *, issue_type="unsupported_claim", explanation="An explanation.", intensity=None, needs_verification=False):
    return dict(
        id=make_id(n), message_id=make_mid(seq), issue_type=issue_type, quote=quote, explanation=explanation,
        confidence=style()["conf"], intensity=intensity, needs_verification=needs_verification,
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
