"""Prompt loading and injection-safe rendering of the input the two agents see (docs/plan.md section 7).

Two rules make prompt injection harmless in form:
1. The static instructions are versioned files in `moderation/prompts/` (`load_prompt`). They contain nothing that
   varies per call (no dates, no ids), so they can be cached as one block. The version and sha256 are recorded.
2. Everything a participant wrote is rendered as escaped DATA inside delimited blocks (`render_*`). `&`, `<` and `>` in
   any text are escaped, so a message can never write a closing tag, open a new block or forge another message.

These functions receive only labels and message text (tuples `(message_id, label, text)`), never `User` objects, so
usernames and emails cannot reach a prompt. Moderator posts use the label `Moderator`.
"""
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

_NAME_RE = re.compile(r"^(?P<base>[a-z][a-z0-9_]*?)(?:_(?P<version>v\d+))?$")


@dataclass(frozen=True)
class Prompt:
    name: str  # the file stem, for example "master_v1"
    version: str  # for example "v1"
    text: str  # the file's text, exactly
    sha256: str  # sha256 of the file's UTF-8 bytes, as hex


def load_prompt(name):
    """Load `moderation/prompts/<name>.md`. `name` may be "master_v1" (or "master_v1.md"), or "master", which means the
    highest version present. Raises ValueError for a name that is not a plain identifier, FileNotFoundError if missing."""
    stem = name[:-3] if isinstance(name, str) and name.endswith(".md") else name
    match = _NAME_RE.match(stem) if isinstance(stem, str) else None
    if match is None:
        raise ValueError(f"invalid prompt name {name!r}")
    if match.group("version") is None:
        candidates = sorted(
            PROMPTS_DIR.glob(f"{stem}_v*.md"), key=lambda p: int(p.stem.rsplit("_v", 1)[1]) if p.stem.rsplit("_v", 1)[1].isdigit() else -1
        )
        if not candidates:
            raise FileNotFoundError(f"no prompt named {name!r} in {PROMPTS_DIR}")
        path = candidates[-1]
    else:
        path = PROMPTS_DIR / f"{stem}.md"
    data = path.read_bytes()
    return Prompt(
        name=path.stem,
        version=path.stem.rsplit("_", 1)[1],
        text=data.decode("utf-8"),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _escape(value):
    """Escape & < > and the double quote (not the apostrophe, which cannot break out of anything and would make the text
    harder to read)."""
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _text(value):
    return "" if value is None else _escape(value)


def _attr(value):
    return "" if value is None else _escape(value)


def render_message(message_id, label, text):
    """One message block. Only plain values are accepted (TypeError otherwise), so a `User` object, or anything else that
    could carry a username or an email, can never be rendered by accident."""
    if isinstance(message_id, bool) or not isinstance(message_id, (int, str)):
        raise TypeError(f"message id must be an int or a str, not {type(message_id).__name__}")
    if not isinstance(label, str):
        raise TypeError(f"label must be a str, not {type(label).__name__}")
    if not isinstance(text, str):
        raise TypeError(f"text must be a str, not {type(text).__name__}")
    return f'<message id="{_attr(message_id)}" participant="{_attr(label)}">\n{_text(text)}\n</message>'


def render_transcript(messages):
    """`messages` is an iterable of (message_id, label, text). Returns the <transcript> block."""
    parts = [render_message(message_id, label, text) for message_id, label, text in messages]
    return "<transcript>\n" + "\n".join(parts) + "\n</transcript>"


def _render_topic(topic_title, proposition):
    if topic_title is None and proposition is None:
        return ""
    lines = ["<topic>"]
    if topic_title is not None:
        lines.append(f"<title>{_text(topic_title)}</title>")
    if proposition is not None:
        lines.append(f"<proposition>{_text(proposition)}</proposition>")
    lines.append("</topic>")
    return "\n".join(lines) + "\n\n"


def _field(issue, name):
    if isinstance(issue, dict):
        return issue.get(name)
    return getattr(issue, name, None)


def _render_issue(issue, *, with_outcome=False):
    lines = [f'<issue id="{_attr(_field(issue, "id"))}">']
    for name in ("message_id", "issue_type", "confidence", "intensity", "time_sensitive", "quote", "explanation"):
        value = _field(issue, name)
        if name == "intensity" and value is None:
            value = "none"
        elif name == "time_sensitive":
            value = "true" if value else "false"
        lines.append(f"<{name}>{_text(value)}</{name}>")
    if with_outcome:
        lines.append(f"<outcome>{_text(_field(issue, 'outcome'))}</outcome>")
    lines.append("</issue>")
    return "\n".join(lines)


def render_master_input(messages, *, topic_title=None, proposition=None, already_raised=(), process_facts=None):
    """The user turn for the Master Moderator. The newest message is the LAST one in `messages`.

    `already_raised`: issues (dicts or objects with id, message_id, issue_type, quote, explanation, outcome) already
    raised earlier, so the Master does not repeat them. `process_facts`: a mapping of facts computed in code."""
    messages = list(messages)
    out = [_render_topic(topic_title, proposition) + render_transcript(messages)]
    if already_raised:
        out.append(
            "<already_raised_issues>\n"
            + "\n".join(_render_issue(issue, with_outcome=True) for issue in already_raised)
            + "\n</already_raised_issues>"
        )
    if process_facts:
        facts = "\n".join(f'<fact name="{_attr(name)}">{_text(value)}</fact>' for name, value in process_facts.items())
        out.append(f"<process_facts>\n{facts}\n</process_facts>")
    if messages:
        out.append(f'<newest_message id="{_attr(messages[-1][0])}" />')
    out.append("Report the issues, following your instructions.")
    return "\n\n".join(out)


def render_intervenor_input(messages, issues, *, topic_title=None, proposition=None, discussion_map=None):
    """The user turn for the Intervenor: the transcript, the Master's valid issues (dicts or objects) and, optionally,
    the Master's discussion map (a dict or object with `agreements` and `disagreements`)."""
    messages = list(messages)
    out = [_render_topic(topic_title, proposition) + render_transcript(messages)]
    issues = list(issues)
    if issues:
        out.append("<issues>\n" + "\n".join(_render_issue(issue) for issue in issues) + "\n</issues>")
    else:
        out.append("<issues>\n</issues>")
    if discussion_map is not None:
        agreements = _field(discussion_map, "agreements") or []
        disagreements = _field(discussion_map, "disagreements") or []
        lines = ["<discussion_map>"]
        lines += [f"<agreement>{_text(a)}</agreement>" for a in agreements]
        for d in disagreements:
            lines.append(f'<disagreement kind="{_attr(_field(d, "kind"))}">{_text(_field(d, "summary"))}</disagreement>')
        lines.append("</discussion_map>")
        out.append("\n".join(lines))
    if messages:
        out.append(f'<newest_message id="{_attr(messages[-1][0])}" />')
    out.append("Decide whether to intervene, following your instructions.")
    return "\n\n".join(out)
