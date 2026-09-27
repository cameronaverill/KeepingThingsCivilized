"""manage.py run_raters --match: 14b's matching runs after the ratings and only then (docs/step14_brief.md, 14a). One group
replaces `evaluation.matching` with a recorder, so this file does not depend on how 14b matches; the last group runs the real
matching end to end."""
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import llmr_kit as kit
import pytest

PANEL = "match-panel"
EXPERIMENT = "match-exp"
TEXT = "Rent control always lowers rents. Anyone who disagrees is an idiot."
PHRASE = "always lowers rents"


@pytest.fixture
def world():
    raters = kit.two_raters()
    panel = kit.make_panel(raters, name=PANEL)
    experiment = kit.experiment(EXPERIMENT)
    topic = kit.shared_topic()
    first = kit.single(TEXT, topic=topic, experiment=experiment)[1]
    second = kit.single(TEXT, topic=topic, experiment=experiment)[1]
    return SimpleNamespace(panel=panel, targets=[first, second])


@pytest.fixture
def recorder(monkeypatch):
    """Replaces `evaluation.matching` (and the already-imported command module, which may have bound its name) by a module
    whose `match_issues` records how it was called and how many ratings were done at that moment."""
    import types

    import evaluation
    from evaluation.models import Rating

    calls = []

    def match_issues(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs, "done": Rating.objects.filter(status="done").count(), "all": Rating.objects.count()})
        return MagicMock()

    module = types.ModuleType("evaluation.matching")
    module.match_issues = match_issues
    monkeypatch.setitem(sys.modules, "evaluation.matching", module)
    monkeypatch.setattr(evaluation, "matching", module, raising=False)
    monkeypatch.delitem(sys.modules, "evaluation.management.commands.run_raters", raising=False)
    return calls


def command(*args):
    return kit.run_raters("--panel", PANEL, "--experiment", EXPERIMENT, *args)


def script(count=4):
    return [kit.nothing() for _ in range(count)]


def panel_argument(call):
    return (call["args"] + (call["kwargs"].get("panel"),))[0]


class TestWhenMatchingRuns:
    def test_without_match_it_never_runs(self, world, fake, recorder):
        fake(*script())
        command("--max-usd", "5", "--live", "--yes")
        assert recorder == []

    def test_with_match_it_runs_once_for_the_panel(self, world, fake, recorder):
        fake(*script())
        result = command("--max-usd", "5", "--live", "--yes", "--match")
        assert (result.exc, len(recorder), panel_argument(recorder[0])) == (None, 1, world.panel)

    def test_it_runs_after_every_rating_is_stored(self, world, fake, recorder):
        fake(*script())
        command("--max-usd", "5", "--live", "--yes", "--match")
        assert (recorder[0]["done"], recorder[0]["all"]) == (4, 4)

    def test_a_dry_run_never_matches(self, world, recorder):
        command("--dry-run", "--match")
        assert recorder == []

    def test_a_refused_live_run_never_matches(self, world, fake, typed, recorder):
        typed.reply("no")
        fake(*script())
        result = command("--max-usd", "5", "--live", "--match")
        assert (result.exc is not None, recorder) == (True, [])

    def test_a_missing_max_usd_never_matches(self, world, recorder):
        result = command("--match")
        assert (result.exc is not None, recorder) == (True, [])

    def test_a_missing_key_never_matches(self, world, fake, settings, recorder):
        settings.ANTHROPIC_API_KEY = ""
        fake(*script())
        command("--max-usd", "5", "--live", "--yes", "--match")
        assert recorder == []

    def test_its_output_is_never_a_key_or_a_message_text(self, world, fake, recorder):
        fake(*script())
        result = command("--max-usd", "5", "--live", "--yes", "--match")
        assert (kit.DUMMY_KEY in result.text, TEXT in result.text) == (False, False)


class TestWithTheRealMatching:
    def set_up_an_issue(self, world):
        from moderation.models import Issue, ModerationRun

        message = world.targets[0]
        run = ModerationRun.objects.create(
            conversation=message.conversation, trigger_message=message, snapshot_seq=message.seq_no, kind="replay"
        )
        start = TEXT.index(PHRASE)
        return Issue.objects.create(
            run=run, local_id="i1", message=message, issue_type="possible_factual_error", dimension="factual_accuracy",
            quote=PHRASE, quote_start=start, quote_end=start + len(PHRASE), quote_match="exact",
            explanation="It is stated as a fact.", confidence=0.9, intensity=3,
        )

    def agreeing(self, count=4):
        return [kit.answer(kit.finding("f1", "factual_accuracy", PHRASE, 3)) for _ in range(count)]

    def links(self):
        from evaluation.models import IssueFindingLink

        return list(IssueFindingLink.objects.order_by("pk"))

    def test_with_match_the_masters_issue_is_linked_to_the_consensus_finding(self, world, fake):
        issue = self.set_up_an_issue(world)
        fake(*self.agreeing())
        result = command("--max-usd", "5", "--live", "--yes", "--match")
        (link,) = self.links()
        assert (result.exc, link.issue_id, link.consensus_finding.target_id, link.consensus_finding.panel_id, link.overlap) == (
            None, issue.pk, world.targets[0].pk, world.panel.pk, 1.0,
        )

    def test_without_match_nothing_is_linked(self, world, fake):
        self.set_up_an_issue(world)
        fake(*self.agreeing())
        command("--max-usd", "5", "--live", "--yes")
        assert (self.links(), len(kit.consensus_rows())) == ([], 2)

    def test_matching_twice_adds_no_second_link(self, world, fake):
        self.set_up_an_issue(world)
        fake(*self.agreeing())
        command("--max-usd", "5", "--live", "--yes", "--match")
        fake()
        command("--max-usd", "5", "--live", "--yes", "--match")
        assert len(self.links()) == 1
