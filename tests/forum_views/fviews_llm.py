"""Scripted model answers for the composer's draft check (step 19). Plain dicts shaped like the Master and Intervenor outputs;
the FakeLLM validates them. Nothing imports a conftest."""

PLACEHOLDER = -1  # the message id the preview gives the draft
QUOTE = "nobody disagrees on that"
MARKER = "zqxjv4417"
DRAFT = f"Rent control has failed everywhere it was tried, {QUOTE}. {MARKER}"
NOTE = "Could a source be given for the claim in message 3?"
NOTE_2 = "The two messages disagree about whether landlords leave the market."


def issue(id, message, quote=QUOTE, issue_type="unsupported_claim"):
    return {"id": id, "message_id": message, "issue_type": issue_type, "quote": quote,
            "explanation": "The claim is stated without support.", "confidence": 0.8, "intensity": None,
            "needs_verification": False}


def master(*issues):
    return {"issues": list(issues), "discussion_map": {"agreements": [], "disagreements": []}}


def act(text, message):
    return {"type": "request_information", "addressee": "all", "subject": "none", "source_issue_ids": ["i1"],
            "source_message_ids": [message], "tone": "neutral", "text": text}


def intervenor(texts, message, decision="intervene"):
    return {"decision": decision, "rationale": "A source would help both readers.",
            "issue_dispositions": [{"issue_id": "i1", "disposition": "acted", "reason": "It matters for the discussion."}],
            "acts": [act(t, message) for t in texts]}


def concern(texts=(NOTE,), message=PLACEHOLDER, quote=QUOTE):
    """[Master, Intervenor] for a draft the moderator would reply to (one issue on the draft, one act per text)."""
    return [master(issue("i1", message, quote)), intervenor(texts, message)]


def no_concern():
    """[Master] with no issue: the Intervenor is never called."""
    return [master()]


def declined(message=PLACEHOLDER, quote=QUOTE):
    return [master(issue("i1", message, quote)),
            {"decision": "no_intervention", "rationale": "Not worth a post.",
             "issue_dispositions": [{"issue_id": "i1", "disposition": "declined", "reason": "Minor."}], "acts": []}]


# --- answers for a LIVE run (the message has its real id, which the script reads from the prompt) ---------------------------

import re as _re


def _newest_id(kwargs):
    return int(_re.search(r'<newest_message id="(-?\d+)"', kwargs["messages"][0]["content"]).group(1))


def live_concern(texts=(NOTE,), quote=QUOTE):
    """[Master, Intervenor] as callables that answer about the message the run is for, whatever its id."""
    from moderation.fake_llm import make_message

    def first(kwargs):
        return make_message(master(issue("i1", _newest_id(kwargs), quote)))

    def second(kwargs):
        return make_message(intervenor(texts, _newest_id(kwargs)))

    return [first, second]
