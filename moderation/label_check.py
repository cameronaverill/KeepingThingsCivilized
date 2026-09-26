"""Does a moderator act's text name a participant? (docs/plan.md section 2 and section 14 step 5).

A moderator post must not name a participant (the page shows only "You" and "The other participant"): the labels are for
structured fields only. Case-sensitive and deliberately narrow so ordinary English is not flagged:
(a) "Participant" or "Participants" followed by whitespace and an uppercase letter; (b) an uppercase letter used as a
possessive name before a message-like noun ("A's message"); (c) a viewer-relative reference ("the other participant", "the other
person/side/party", "another participant/person"), which each of the two readers would read as someone different. Bare letters
("Plan B", "Option A is") are NOT flagged.

The pipeline rejects an act whose text matches (reason `names_participant`); the spike report uses the same check.
"""
import re

NAMES_LABEL_RE = re.compile(
    r"\bParticipants?\s+[A-Z]"
    r"|\b[A-Z]'s\s+(?:message|messages|reply|replies|claim|claims|point|points|statement|statements|argument|arguments|answer|answers|question|questions)\b"
    r"|\b[Tt]he other (?:participant|person|side|party)\b|\b[Aa]nother (?:participant|person)\b"
)


def names_a_label(text):
    """True if an act's text names a participant by label (see NAMES_LABEL_RE)."""
    return bool(NAMES_LABEL_RE.search(text))
