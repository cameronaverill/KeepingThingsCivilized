"""Step 20a revision (docs/step20a_revision_brief.md items 2-3): the broadened `needs_verification` guidance in
`moderation/prompts/master_v1.md` and the new "when to prefer which" rule in `moderation/prompts/intervenor_v1.md`.

No real LLM call is made here (same constraint as the existing step 20a prompt-text checks in
test_step3_prompt_files.py): these are static-text checks on the prompt files themselves, using substring/regex on
the key phrases the revision brief introduces, following the same conventions as that file (`PROMPT_DIR`, `squash`).

This is a NEW file, additive to test_step3_prompt_files.py's existing "prompt contains X" coverage of the original
(recency-only) Step 20a guidance -- it does not edit that file (a coding agent owns the prompt files and is expected
to also rename that file's own `time_sensitive`-named tests concurrently). These tests depend on the prompts group's
work (docs/step20a_revision_brief.md items 2-3: broadening master_v1.md's guidance and adding intervenor_v1.md's new
rule) and are expected to fail until that lands.
"""
import re

import pytest
from step3_testkit import PROMPT_DIR


def prompt_text(name):
    path = PROMPT_DIR / f"{name}.md"
    assert path.is_file(), f"missing {path}"
    return path.read_text(encoding="utf-8")


def squash(text):
    return re.sub(r"\s+", " ", text).strip()


# --- master_v1.md: the broadened `needs_verification` field ---------------------------------------------------------

def test_master_prompt_uses_the_renamed_needs_verification_field():
    """Item 1: `time_sensitive` is renamed to `needs_verification` throughout, including in the prompt's own
    guidance text and its field-by-field description of an issue."""
    text = prompt_text("master_v1")
    assert "needs_verification" in text


def test_master_prompt_states_the_second_broadened_reason_independent_of_recency():
    """Item 2: alongside the existing recency reason, the Master must also set `needs_verification: true` when it
    isn't confident a specific checkable detail (a citation, a named study, a statistic, a precise figure) is
    accurate, regardless of whether the claim is time-sensitive -- because it might be misremembering the detail or
    the detail might not exist at all."""
    text = prompt_text("master_v1")
    lowered = text.lower()
    assert "needs_verification" in text
    # A citation/study/statistic/figure-shaped detail is named as its own, independent trigger.
    assert re.search(r"citation|named study|a study", lowered)
    assert re.search(r"statistic|precise figure|specific figure|a figure", lowered)
    # The "misremembering or might not exist" framing, or a close paraphrase of it.
    assert re.search(r"misremember", lowered) or re.search(r"might not exist|does not exist|no such", lowered)
    # It is explicitly independent of / regardless of recency or time-sensitivity.
    assert re.search(r"regardless of (?:whether|when|its recency|time)", lowered) or \
        re.search(r"independent(?:ly)? of (?:recency|time|whether)", lowered)


def test_master_prompt_has_the_fabrication_style_worked_example():
    """Item 2's suggested worked example: a message asserting a 2024 Harvard study proved remote work reduces
    productivity by 40% -- checkable, not about recency, and not confident enough in the specific citation/figure
    to assert a correction outright."""
    text = prompt_text("master_v1")
    lowered = text.lower()
    assert re.search(r"harvard", lowered)
    assert re.search(r"\b2024\b", text)
    assert re.search(r"remote work", lowered)
    assert re.search(r"productivity", lowered)
    assert re.search(r"40\s*%|40 ?percent", lowered)


def test_master_prompt_still_has_the_original_recency_worked_example():
    """Item 2: the fabrication example is added side by side with the existing recency example, not in place of
    it (unemployment rate vs. Treaty of Westphalia)."""
    lowered = prompt_text("master_v1").lower()
    assert re.search(r"unemployment rate", lowered)
    assert re.search(r"treaty of westphalia|\b1648\b", lowered)


def test_master_prompt_says_needs_verification_never_changes_whether_an_issue_is_reported():
    """Item 2: this is an additional flag, not a replacement judgement -- confidence/intensity are still reported
    exactly as before, and setting the flag never changes whether the issue is reported at all."""
    text = prompt_text("master_v1")
    lowered = text.lower()
    assert "needs_verification" in text
    assert "confidence" in lowered and "intensity" in lowered
    assert re.search(r"never changes? whether", lowered) or re.search(r"not a replacement judg?ement", lowered)


def test_master_prompt_says_the_specific_reason_is_never_surfaced_to_users():
    """Item 2: the specific reason (recency vs. fabrication risk) is never distinguished in the flag itself and
    must never appear in anything posted to users -- only that an independent check could be requested."""
    text = prompt_text("master_v1")
    lowered = text.lower()
    assert "needs_verification" in text
    assert re.search(r"never (?:distinguish|appear|surfaced?|shown|reveal)", lowered) or \
        re.search(r"not (?:distinguish|surfaced?|shown|revealed)", lowered)


def test_needs_verification_guidance_still_sits_near_the_factual_accuracy_rubric():
    """The natural home is still next to the existing factual_accuracy rubric text (unchanged from the original
    Step 20a placement, per docs/step20a_brief.md item 5)."""
    text = prompt_text("master_v1")
    idx_rubric, idx_nv = text.find("factual_accuracy"), text.find("needs_verification")
    assert idx_rubric != -1 and idx_nv != -1
    assert abs(idx_nv - idx_rubric) < 6000, "needs_verification guidance is not near the factual_accuracy rubric text"


# --- intervenor_v1.md: the new "when to prefer which" rule -----------------------------------------------------------

def test_intervenor_prompt_uses_the_renamed_needs_verification_field():
    text = prompt_text("intervenor_v1")
    assert "needs_verification" in text


def test_intervenor_prompt_has_a_when_to_prefer_which_rule_near_offer_research():
    """Item 3: a short "when to prefer which" note, near the offer_research entry, saying the Intervenor should
    prefer correct_factual_error/provide_information (direct assertion) whenever confident enough, and choose
    offer_research only for an issue where needs_verification: true."""
    text = prompt_text("intervenor_v1")
    lowered = text.lower()
    assert "offer_research" in text and "needs_verification" in text
    assert "correct_factual_error" in text and "provide_information" in text
    assert re.search(r"when to prefer which", lowered), "no 'when to prefer which' note found"
    assert re.search(r"confiden", lowered)
    # The note itself (found via its own heading-like phrase) must be close to both offer_research and
    # needs_verification, not just present somewhere in the file.
    assert re.search(r"when to prefer which.{0,700}offer_research", lowered, re.S) or \
        re.search(r"offer_research.{0,700}when to prefer which", lowered, re.S)
    assert re.search(r"when to prefer which.{0,700}needs_verification", lowered, re.S) or \
        re.search(r"needs_verification.{0,700}when to prefer which", lowered, re.S)


def test_intervenor_prompt_does_not_restate_or_weaken_the_existing_act_type_definitions():
    """Item 3: the new rule points back to the existing act-type definitions rather than duplicating or weakening
    their wording -- the taxonomy definitions must still appear verbatim (same check as
    test_act_type_definitions_appear_verbatim_in_the_intervenor_prompt, exercised again here so this file alone
    catches a regression introduced by the new rule's wording)."""
    from moderation import taxonomy

    text = squash(prompt_text("intervenor_v1"))
    for act_type in taxonomy.ACT_TYPES:
        assert squash(taxonomy.DEFINITIONS[act_type]) in text, f"definition of {act_type} no longer verbatim"


def _markdown_sections(text):
    """{heading (without '## '): body text} for each top-level '## ' section of a prompt file."""
    parts = re.split(r"(?m)^## (.+)$", text)
    return {parts[i].strip(): parts[i + 1] for i in range(1, len(parts), 2)}


@pytest.mark.parametrize("leak_word", ["outdated", "fabricat"])
def test_intervenor_prompt_never_leaks_the_specific_reason_in_user_facing_act_guidance(leak_word):
    """Item 2/3: the specific reason a claim needs verification (recency vs. fabrication risk) must never appear in
    anything that reads as user-facing act text guidance -- the "Act types" section (where offer_research and the
    new "when to prefer which" rule live) and the "Style rules for what you write" section (which governs what an
    act's `text` may say). "fabricat*" covers fabricate/fabricated/fabrication; "outdated" is its own literal word.

    This deliberately does not scan the whole file: the embedded factual_accuracy rubric (a separate, pre-existing
    section quoted verbatim from rubrics/factual_accuracy_v1.md) legitimately uses "fabricated" as a rubric
    category name for the Master's own internal scoring, which has nothing to do with what gets posted to users."""
    sections = _markdown_sections(prompt_text("intervenor_v1"))
    for heading in ("Act types", "Style rules for what you write"):
        assert heading in sections, f"missing section {heading!r}"
        assert leak_word not in sections[heading].lower(), (
            f"intervenor_v1.md's {heading!r} section contains {leak_word!r}, which could leak the specific reason"
        )


def test_intervenor_prompt_has_exactly_one_style_rules_section():
    """Item 3's brief says to confirm the existing style-rule section still covers this new act type rather than
    duplicating a new rule: there must be exactly one "Style rules" heading, not a second one introduced alongside
    the offer_research/needs_verification changes."""
    text = prompt_text("intervenor_v1")
    headings = re.findall(r"(?im)^\s*#+\s*style rules\b", text)
    assert len(headings) == 1, f"expected exactly one 'Style rules' heading, found {len(headings)}"


def test_intervenor_prompt_style_rules_section_still_covers_not_overstating_certainty():
    """The general style rule this brief relies on ("do not overstate your certainty" / neutral, impersonal
    phrasing) must still be present and unweakened -- the new offer_research rule leans on it rather than
    restating a narrower version of it."""
    text = prompt_text("intervenor_v1").lower()
    assert re.search(r"do not overstate your certainty|never overstate", text)
