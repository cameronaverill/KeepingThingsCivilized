"""`run_panel` builds the consensus once every LLM rater of the panel has a done rating for a target, and not before; human
ratings are included when present (docs/step14_brief.md, 14a). The consensus arithmetic itself is tested in
tests/evaluation_models; here only what the runner hands to `build_consensus` and when."""
from decimal import Decimal

import llmr_kit as kit
import pytest
from django.conf import settings as django_settings

FACT, ABUSE = "factual_accuracy", "abusiveness"
TEXT = "The moon is made of cheese, obviously. Anyone who disagrees is an idiot."
CHEESE = "The moon is made of cheese"
SPAN = (0, len(CHEESE))
TOKENS = dict(input_tokens=1000, output_tokens=100)
ALPHA = "abcdefghijklmnopqrstuvwxyz"  # a run of 26 distinct characters, for nested spans of an exact length


def setup(*, raters=None, **panel_extra):
    first, second = raters or (kit.make_rater("rater-a", kit.SONNET), kit.make_rater("rater-b", kit.SONNET))
    panel = kit.make_panel([first, second], **panel_extra)
    _, message = kit.single(TEXT)
    return panel, message


def rated(fake, answer_a, answer_b, **panel_extra):
    panel, message = setup(**panel_extra)
    fake(answer_a, answer_b)
    kit.run_panel(panel, [message])
    return panel, message


def cheese(intensity, local_id="f1", dimension=FACT):
    return kit.answer(kit.finding(local_id, dimension, CHEESE, intensity))


class TestBuiltAfterAllRatersAreDone:
    def test_two_raters_agreeing_give_one_consensus_finding_that_links_both_findings(self, fake):
        panel, message = rated(fake, cheese(4), cheese(4))
        (found,) = kit.consensus_rows()
        assert (found.panel_id, found.target_type, found.target_id, found.dimension, (found.start, found.end)) == (
            panel.pk, "message", message.pk, FACT, SPAN,
        )
        assert (found.n_raters, found.intensity_mean, found.intensity_range, found.needs_adjudication) == (2, 4.0, 0, False)
        assert sorted(f.pk for f in found.findings.all()) == sorted(f.pk for f in kit.all_findings())

    def test_a_disagreement_of_the_panels_threshold_needs_adjudication(self, fake):
        rated(fake, cheese(0), cheese(4))
        (found,) = kit.consensus_rows()
        assert (found.n_raters, found.intensity_mean, found.intensity_range, found.needs_adjudication) == (2, 2.0, 4, True)

    def test_a_phrase_found_by_one_of_two_raters_needs_adjudication(self, fake):
        rated(fake, cheese(3), kit.nothing())
        (found,) = kit.consensus_rows()
        assert (found.n_raters, found.needs_adjudication) == (1, True)

    def test_findings_on_two_dimensions_give_a_consensus_finding_per_dimension(self, fake):
        both = kit.answer(kit.finding("f1", FACT, CHEESE, 4), kit.finding("f2", ABUSE, "an idiot", 3))
        rated(fake, both, both)
        assert sorted((c.dimension, c.n_raters) for c in kit.consensus_rows()) == [(ABUSE, 2), (FACT, 2)]

    def test_no_finding_at_all_gives_no_consensus_finding(self, fake):
        rated(fake, kit.nothing(), kit.nothing())
        assert kit.consensus_rows() == []

    def test_the_panels_own_span_threshold_decides_what_merges(self, fake):
        wide = kit.answer(kit.finding("f1", FACT, CHEESE + ", obviously", 4))  # 37 characters against 26: IoU 0.70
        loose, _ = rated(fake, cheese(4), wide, name="loose", span_match_min_iou=0.5)
        merged = len(kit.consensus_rows(panel=loose))
        strict_rater_a, strict_rater_b = kit.make_rater("s-a", kit.SONNET), kit.make_rater("s-b", kit.SONNET)
        strict = kit.make_panel([strict_rater_a, strict_rater_b], name="strict", span_match_min_iou=0.9)
        _, message = kit.single(TEXT)
        fake(cheese(4), wide)
        kit.run_panel(strict, [message])
        assert (merged, len(kit.consensus_rows(panel=strict))) == (1, 2)

    def test_the_panels_intensity_threshold_decides_what_needs_adjudication(self, fake):
        rated(fake, cheese(1), cheese(3), intensity_disagreement_threshold=3)
        (found,) = kit.consensus_rows()
        assert (found.intensity_range, found.needs_adjudication) == (2, False)


class TestNotBefore:
    def half_done(self, fake):
        """A panel of two raters and a target of which only the first rater has rated (the cap has room for one call)."""
        fake(*[kit.priced(cheese(4), **TOKENS) for _ in range(3)])
        reserved, _ = kit.probe_costs(TEXT, **TOKENS)
        panel, message = setup()
        kit.run_panel(panel, [message], max_usd=reserved)
        return panel, message

    def test_a_target_with_one_rater_still_to_come_has_no_consensus_yet(self, fake):
        panel, _ = self.half_done(fake)
        assert (len(kit.ratings(status="done", rater__in=list(panel.raters.all()))), kit.consensus_rows(panel=panel)) == (1, [])

    def test_the_next_call_completes_the_target_and_builds_it(self, fake):
        panel, message = self.half_done(fake)
        client = fake(kit.priced(cheese(4), **TOKENS))
        kit.run_panel(panel, [message])
        assert (len(client.calls), [c.n_raters for c in kit.consensus_rows(panel=panel)]) == (1, [2])

    def test_a_rater_whose_rating_failed_leaves_the_target_without_a_consensus(self, fake):
        panel, message = setup()
        fake(cheese(4), kit.BAD_ANSWER, kit.BAD_ANSWER)
        kit.run_panel(panel, [message])
        assert (sorted(r.status for r in kit.ratings()), kit.consensus_rows()) == (["done", "failed"], [])

    def test_the_failed_rating_is_retried_by_the_next_run_and_then_the_consensus_appears(self, fake):
        panel, message = setup()
        fake(cheese(4), kit.BAD_ANSWER, kit.BAD_ANSWER)
        kit.run_panel(panel, [message])
        fake(cheese(4))
        kit.run_panel(panel, [message])
        assert [c.n_raters for c in kit.consensus_rows()] == [2]

    def test_a_disabled_run_builds_nothing(self, fake, settings):
        settings.LLM_ENABLED = False
        panel, message = setup()
        fake()
        kit.run_panel(panel, [message])
        assert kit.consensus_rows() == []


class TestHumanRatingsAreIncludedWhenPresent:
    def with_human(self, fake, *, human_status="done"):
        rater_a, rater_b = kit.make_rater("rater-a", kit.SONNET), kit.make_rater("rater-b", kit.SONNET)
        human = kit.make_human("human-one")
        panel = kit.make_panel([rater_a, rater_b, human])
        _, message = kit.single(TEXT)
        kit.make_done_rating(human, message, found=[(SPAN[0], SPAN[1], FACT, 4)], status=human_status)
        client = fake(cheese(4), cheese(4))
        kit.run_panel(panel, [message])
        return client, panel

    def test_a_done_human_rating_is_a_third_rater_and_the_human_is_never_called(self, fake):
        client, _ = self.with_human(fake)
        (found,) = kit.consensus_rows()
        assert (found.n_raters, len(client.calls), len(kit.all_findings())) == (3, 2, 3)

    def test_a_human_rating_that_is_not_done_is_left_out(self, fake):
        self.with_human(fake, human_status="pending")
        (found,) = kit.consensus_rows()
        assert found.n_raters == 2

    def test_a_human_who_has_not_rated_does_not_hold_the_consensus_back(self, fake):
        rater_a, rater_b = kit.make_rater("rater-a", kit.SONNET), kit.make_rater("rater-b", kit.SONNET)
        panel = kit.make_panel([rater_a, rater_b, kit.make_human("human-two")])
        _, message = kit.single(TEXT)
        fake(cheese(4), cheese(4))
        kit.run_panel(panel, [message])
        (found,) = kit.consensus_rows()
        assert found.n_raters == 2

    def test_a_human_rating_that_disagrees_can_make_the_target_need_adjudication(self, fake):
        rater_a, rater_b = kit.make_rater("rater-a", kit.SONNET), kit.make_rater("rater-b", kit.SONNET)
        human = kit.make_human("human-three")
        panel = kit.make_panel([rater_a, rater_b, human])
        _, message = kit.single(TEXT)
        kit.make_done_rating(human, message, found=[(SPAN[0], SPAN[1], FACT, 0)])
        fake(cheese(4), cheese(4))
        kit.run_panel(panel, [message])
        (found,) = kit.consensus_rows()
        assert (found.n_raters, found.intensity_range, found.needs_adjudication) == (3, 4, True)


class TestNothingIsBuiltTwice:
    def test_running_again_adds_no_consensus_finding_and_no_link(self, fake):
        panel, message = setup()
        fake(cheese(4), cheese(4))
        kit.run_panel(panel, [message])
        from evaluation.models import ConsensusFinding

        before = (ConsensusFinding.objects.count(), sum(c.findings.count() for c in ConsensusFinding.objects.all()))
        fake()
        kit.run_panel(panel, [message])
        assert (ConsensusFinding.objects.count(), sum(c.findings.count() for c in ConsensusFinding.objects.all())) == before == (1, 2)

    def test_two_replicates_still_give_one_consensus_finding_of_two_raters(self, fake):
        panel, message = setup()
        fake(*[cheese(4) for _ in range(4)])
        kit.run_panel(panel, [message], replicates=2)
        (found,) = kit.consensus_rows()
        assert (found.n_raters, len(kit.ratings(status="done"))) == (2, 4)

    def test_each_target_gets_its_own_consensus(self, fake):
        panel, first = setup()
        _, second = kit.single(TEXT, topic=first.conversation.topic)
        fake(cheese(4), cheese(4), cheese(2), cheese(2))
        kit.run_panel(panel, [first, second])
        assert sorted((c.target_id, c.intensity_mean) for c in kit.consensus_rows()) == sorted([(first.pk, 4.0), (second.pk, 2.0)])


class TestWhichRatingsFeedTheConsensus:
    def test_replicates_do_not_change_the_ground_truth_the_smallest_replicate_of_each_rater_is_used(self, fake):
        panel, message = setup()
        # replicate 1 of both raters finds the phrase at intensity 4; replicate 2 of both finds nothing.
        fake(cheese(4), cheese(4), kit.nothing(), kit.nothing())
        kit.run_panel(panel, [message], replicates=2)
        (found,) = kit.consensus_rows()
        assert (found.n_raters, found.intensity_mean, len(kit.ratings(status="done"))) == (2, 4.0, 4)

    def test_when_several_replicates_already_exist_the_smallest_one_is_used(self, fake):
        panel, message = setup()
        first, second = list(panel.raters.order_by("name"))
        for rater in (first, second):
            kit.make_done_rating(rater, message, found=[(SPAN[0], SPAN[1], FACT, 0)], replicate=2)
            kit.make_done_rating(rater, message, found=[(SPAN[0], SPAN[1], FACT, 4)], replicate=1)
        fake()
        kit.run_panel(panel, [message], replicates=2)
        (found,) = kit.consensus_rows()
        assert (found.n_raters, found.intensity_mean) == (2, 4.0)

    def test_ratings_that_already_exist_give_the_consensus_on_the_next_run_without_a_call(self, fake):
        panel, message = setup()
        first, second = list(panel.raters.order_by("name"))
        kit.make_done_rating(first, message, found=[(SPAN[0], SPAN[1], FACT, 3)])
        kit.make_done_rating(second, message, found=[(SPAN[0], SPAN[1], FACT, 3)])
        client = fake()
        kit.run_panel(panel, [message])
        (found,) = kit.consensus_rows()
        assert (client.calls, found.n_raters, found.intensity_mean) == ([], 2, 3.0)

    def test_an_inactive_llm_rater_of_the_panel_does_not_hold_the_consensus_back(self, fake):
        active = kit.make_rater("active-one", kit.SONNET)
        retired = kit.make_rater("retired-one", kit.SONNET, active=False)
        panel = kit.make_panel([active, retired])
        _, message = kit.single(TEXT)
        fake(cheese(4))
        kit.run_panel(panel, [message])
        assert [c.n_raters for c in kit.consensus_rows()] == [1]

    def test_a_rating_of_another_target_is_not_used(self, fake):
        panel, message = setup()
        _, other = kit.single(TEXT, topic=message.conversation.topic)
        first, _ = list(panel.raters.order_by("name"))
        kit.make_done_rating(first, other, found=[(SPAN[0], SPAN[1], FACT, 0)])
        fake(cheese(4), cheese(4))
        kit.run_panel(panel, [message])
        (found,) = kit.consensus_rows(target_id=message.pk)
        assert (found.n_raters, found.intensity_mean) == (2, 4.0)


class TestSameRaterDedupThroughThePipeline:
    """`_cluster` (evaluation/consensus.py) never merges two findings by the same rater, even when their spans overlap
    enough to merge otherwise; a mutation audit found that removing that guard survives the whole suite because nothing
    exercises it through `run_panel`. This scripts ONE rater with two findings on one dimension whose spans overlap well
    past the panel's own `span_match_min_iou` (the pair the rest of this file already uses for "the panels own span
    threshold decides what merges": 26 characters against 37, IoU 0.70)."""

    def test_one_raters_two_overlapping_findings_are_kept_as_two_separate_consensus_findings(self, fake):
        solo = kit.make_rater("solo", kit.SONNET)
        panel = kit.make_panel([solo])
        _, message = kit.single(TEXT)
        both = kit.answer(
            kit.finding("f1", FACT, CHEESE, 4), kit.finding("f2", FACT, CHEESE + ", obviously", 4)
        )
        fake(both)

        kit.run_panel(panel, [message])

        rows = kit.consensus_rows(panel=panel)
        assert sorted(row.n_raters for row in rows) == [1, 1]
        assert sorted((row.start, row.end) for row in rows) == sorted([(0, len(CHEESE)), (0, len(CHEESE) + len(", obviously"))])


class TestSpanIoUBoundaryThroughThePipeline:
    """`_cluster`'s `score >= threshold` comparison (evaluation/consensus.py), exercised through `run_panel` with the
    panel's own `span_match_min_iou` (defaulting to `settings.SPAN_MATCH_MIN_IOU`). Two raters' spans share the same
    start so their IoU is `shorter / longer`; the shorter one is sized to land exactly on the threshold, and one
    character short of it."""

    UNION_LEN = 20
    AT_THRESHOLD_LEN = round(django_settings.SPAN_MATCH_MIN_IOU * UNION_LEN)
    BELOW_THRESHOLD_LEN = AT_THRESHOLD_LEN - 1

    @pytest.mark.parametrize(
        ("narrow_len", "expected_signature"),
        [
            (AT_THRESHOLD_LEN, [(2, False)]),
            (BELOW_THRESHOLD_LEN, [(1, True), (1, True)]),
        ],
        ids=["exactly_at_the_threshold_merges", "one_character_below_the_threshold_stays_separate"],
    )
    def test_the_merge_and_its_adjudication_flip_at_the_panels_iou_threshold(self, fake, narrow_len, expected_signature):
        first, second = kit.make_rater("rater-a", kit.SONNET), kit.make_rater("rater-b", kit.SONNET)
        panel = kit.make_panel([first, second])
        _, message = kit.single(ALPHA)
        wide = kit.answer(kit.finding("f1", FACT, ALPHA[: self.UNION_LEN], 4))
        narrow = kit.answer(kit.finding("f2", FACT, ALPHA[:narrow_len], 4))
        fake(wide, narrow)

        kit.run_panel(panel, [message])

        rows = kit.consensus_rows(panel=panel)
        assert sorted((row.n_raters, row.needs_adjudication) for row in rows) == sorted(expected_signature)


class TestIntensityDisagreementBoundaryThroughThePipeline:
    """The `intensity_range >= panel.intensity_disagreement_threshold` comparison (evaluation/consensus.py), exercised
    through `run_panel` with the panel's own threshold (defaulting to `settings.INTENSITY_DISAGREEMENT_THRESHOLD`). Both
    raters point at the same phrase (`CHEESE`), so only the intensities differ."""

    THRESHOLD = django_settings.INTENSITY_DISAGREEMENT_THRESHOLD

    @pytest.mark.parametrize(
        ("high_intensity", "expected_needs_adjudication"),
        [(THRESHOLD, True), (THRESHOLD - 1, False)],
        ids=["differs_by_exactly_the_threshold", "differs_by_one_less_than_the_threshold"],
    )
    def test_needs_adjudication_flips_at_the_panels_intensity_threshold(
        self, fake, high_intensity, expected_needs_adjudication
    ):
        panel, message = setup()
        fake(cheese(0), cheese(high_intensity))

        kit.run_panel(panel, [message])

        (found,) = kit.consensus_rows(panel=panel)
        assert (found.intensity_range, found.needs_adjudication) == (high_intensity, expected_needs_adjudication)


class TestScorabilityDisagreementThroughThePipeline:
    """The `0 < scorable_count < len(cluster)` check (evaluation/consensus.py): a merged finding where one rater scored
    the phrase and another declined it needs adjudication even though the (single) scorable intensity has no range to
    disagree over."""

    def test_an_intensity_against_a_not_scorable_answer_on_the_same_phrase_needs_adjudication(self, fake):
        panel, message = setup()
        scored = cheese(3)
        declined = kit.answer(kit.finding("f1", FACT, CHEESE, None, reason="contested"))
        fake(scored, declined)

        kit.run_panel(panel, [message])

        (found,) = kit.consensus_rows(panel=panel)
        assert (found.n_raters, found.intensity_mean, found.intensity_range, found.needs_adjudication) == (
            2, 3.0, 0, True,
        )
