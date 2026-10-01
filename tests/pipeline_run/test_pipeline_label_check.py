"""moderation/label_check.py: the naming check (moved out of the retired spike command unchanged in behaviour), and used by the pipeline (brief
"5b details", the label_check.py paragraph). The word lists are the ones the step 3 tests pin, copied here on purpose."""
import re

import pipeline_run_kit as kit
import pytest

FLAGGED = [
    "Participant A", "Participant B, please cite a source.", "Participants A and B should keep to the topic.",
    "Both Participant B and Participant A agree.", "Participant Z", "Participant Alpha", "Participant Ann", "Participants\nB",
    "Participant  B", "Note for Participant A: keep it short.",
    "B's message needs a source.", "A's claim is unsupported.", "The B's replies were short.", "B's questions are fair.",
    "A's answer is missing.", "See B's point.", "A's statement and B's arguments differ.", "B's messages", "B's message.",
    "This comment is directed at the other participant.", "Please respond to the other person.",
    "The other side raised a fair point.", "Another participant already answered.", "the other party",
    "Another person asked this.", "Directed at The other participant.",
]
NOT_FLAGGED = [
    "participant b", "participant B", "Participant", "Participants and moderators", "Participant 4",
    "Plan B.", "Plan B is fine.", "Option A is fine.", "Vitamin B, and C.", "Section A: scope.", "A source would help.",
    "A cap on annual increases", "Please give a source for the claim in message 4.",
    "The question in message 3 has not been answered.", 'The letter "B" is unclear.', "'A' is a variable here.",
    "(A) is the first option.", "[B] marks the second.", "“B”", "participants in general are welcome",
    "the other message", "another point", "the other day", "Another message", "the other posts", "another participation",
    "the other personality", "the other sidewalk", "another personal insult", "b's message", "B's own view", "A's and B's",
    "the value of A's", "Bs message", "AB's message", "Type B's", "B'sX message", "",
]
ALTERNATIVES = [
    r"\bParticipants?\s+[A-Z]",
    r"\b[A-Z]'s\s+(?:message|messages|reply|replies|claim|claims|point|points|statement|statements|argument|arguments|answer|answers|question|questions)\b",
    r"\b[Tt]he other (?:participant|person|side|party)\b|\b[Aa]nother (?:participant|person)\b",
]


class TestTheModule:
    @pytest.mark.parametrize("text", FLAGGED)
    def test_these_texts_are_flagged(self, text):
        from moderation.label_check import names_a_label

        assert names_a_label(text) is True

    @pytest.mark.parametrize("text", NOT_FLAGGED)
    def test_these_texts_are_not_flagged(self, text):
        from moderation.label_check import names_a_label

        assert names_a_label(text) is False

    def test_the_regex_is_the_three_documented_alternatives_and_case_sensitive(self):
        from moderation.label_check import NAMES_LABEL_RE

        assert isinstance(NAMES_LABEL_RE, re.Pattern)
        assert NAMES_LABEL_RE.flags & re.IGNORECASE == 0
        for alternative in ALTERNATIVES:
            assert alternative in NAMES_LABEL_RE.pattern

    def test_the_function_agrees_with_the_regex(self):
        from moderation.label_check import NAMES_LABEL_RE, names_a_label

        for text in FLAGGED + NOT_FLAGGED:
            assert names_a_label(text) == bool(NAMES_LABEL_RE.search(text))


@pytest.mark.django_db
class TestThePipelineAppliesTheSameCheck:
    def run_acts(self, fake, tune, texts):
        tune(MAX_ACTS_PER_INTERVENTION=100)
        world = kit.build()
        run = kit.new_run(world.last)
        master = kit.master_d(kit.issue_d("i1", world.last))
        acts = [kit.act_d(t) for t in texts]
        fake(master, kit.interv_d(dispositions=[kit.disp_d("i1")], acts=acts))
        _, stored = kit.go(run)
        return stored

    def test_every_text_the_check_flags_is_rejected_as_naming_a_participant(self, fake, tune):
        stored = self.run_acts(fake, tune, FLAGGED)
        assert [(a.validity, a.rejection_reason) for a in kit.acts_of(stored)] == [("rejected", "names_participant")] * len(FLAGGED)
        assert stored.decision == "no_intervention"
        assert stored.posted_message is None

    def test_no_text_the_check_passes_is_rejected_for_naming(self, fake, tune):
        passing = [t for t in NOT_FLAGGED if t.strip()]
        stored = self.run_acts(fake, tune, passing)
        assert [(a.validity, a.rejection_reason) for a in kit.acts_of(stored)] == [("valid", "")] * len(passing)
        assert stored.posted_message.content == "\n\n".join(passing)
