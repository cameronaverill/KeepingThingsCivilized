"""Helpers for the step 21 tests: conversations with a chosen number of user messages (and moderator messages mixed in),
scripted Master/Intervenor answers that carry a discussion map and an `identify_agreement_disagreement` act, and a way to
recognise the "summary due" block in what the Intervenor was sent.

Built on tests/pipeline_run/pipeline_run_kit.py (imported as `prk`). Model imports happen inside functions.
"""
from pathlib import Path

import pipeline_run_kit as prk

ROOT = Path(__file__).resolve().parents[2]
PROMPT_DIR = ROOT / "moderation" / "prompts"
RUBRIC_DIR = ROOT / "rubrics"

TUNABLE = "AGREEMENT_MAP_EVERY_N_USER_MESSAGES"
SUMMARY_TYPE = "identify_agreement_disagreement"

AGREEMENT = "Both positions want rents to stay affordable."
DISAGREEMENT = {"summary": "Whether capping rents shrinks the supply of housing.", "kind": "factual"}
SUMMARY_TEXT = (
    "Both positions want rents to stay affordable. One position holds that caps shrink the supply of housing; "
    "another holds that supply barely changes. That disagreement is about facts."
)


def user_texts(count):
    return [f"Message number {i} about rent caps and the supply of housing." for i in range(1, count + 1)]


def world_with_users(count, *, mods_after=(), extra_users=0):
    """(world, trigger): `count` user messages alternating A, B; a moderator message after each user message number in
    `mods_after`; then `extra_users` more user messages after the trigger. The trigger is the `count`-th user message."""
    specs = []
    for index, text in enumerate(user_texts(count), start=1):
        specs.append(("AB"[(index - 1) % 2], text))
        if index in mods_after:
            specs.append(("mod", "A moderator note that is not a user message."))
    world = prk.build(specs)
    trigger = [m for m in world.msgs if m.author_type == "user"][count - 1]
    for i in range(extra_users):
        world.add_user("AB"[i % 2], f"A later message number {i + 1} about rent caps.")
    return world, trigger


def map_master(*issues, agreements=(AGREEMENT,), disagreements=(DISAGREEMENT,)):
    return prk.master_d(*issues, agreements=list(agreements), disagreements=list(disagreements))


def empty_map_master(*issues):
    return prk.master_d(*issues)


def summary_act(text=SUMMARY_TEXT, **kwargs):
    values = dict(type=SUMMARY_TYPE, addressee="all", subject="both")
    values.update(kwargs)
    return prk.act_d(text, **values)


def summary_intervenor(text=SUMMARY_TEXT, extra_acts=(), dispositions=()):
    return prk.interv_d(
        rationale="The discussion has run long enough for a summary.",
        dispositions=list(dispositions), acts=[*extra_acts, summary_act(text)],
    )


def due_script(text=SUMMARY_TEXT):
    """[Master (non-empty map, no issue), Intervenor (one summary act)]."""
    return [map_master(), summary_intervenor(text)]


def posted_text(run):
    return run.posted_message.content if run.posted_message_id else None


def intervenor_calls(client):
    return prk.calls_of(client, "intervenor")


def master_calls(client):
    return prk.calls_of(client, "master")


def summary_block_lines():
    """The lines `render_intervenor_input` adds when `summary_due=True` (empty set means the flag changes nothing)."""
    from moderation import prompting

    messages = [(1, "Participant A", "text one"), (2, "Participant B", "text two")]
    plain = prompting.render_intervenor_input(messages, [])
    due = prompting.render_intervenor_input(messages, [], summary_due=True)
    plain_lines = set(plain.splitlines())
    return [line for line in due.splitlines() if line not in plain_lines]


def sent_summary_flag(call):
    """True if the Intervenor input of `call` carries the summary-due block."""
    lines = summary_block_lines()
    text = prk.user_input(call)
    return bool(lines) and all(line in text for line in lines)


def run_trigger(fake, world, trigger, script, kind="live", **kwargs):
    """Install `script` and run a fresh run of `kind` on `trigger`. Returns (client, stored run)."""
    client = fake(*script)
    run = prk.new_run(trigger, kind=kind, **kwargs)
    _, stored = prk.go(run)
    return client, stored


def research_kit():
    """tests/research/research_kit.py loaded under its own name (another folder has a different `research_kit`)."""
    import importlib.util

    name = "dim21_research_kit_copy"
    import sys

    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / "tests" / "research" / "research_kit.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]
