"""Step 20a item 4 (docs/step20a_brief.md, moderation/pipeline.py): the `offer_research` act's source-validation
rule in `validate_acts` (an `offer_research` act must cite at least one issue and every issue it cites must have
`time_sensitive=True`, rejected `not_time_sensitive` otherwise), and the Master's `time_sensitive` flag copied
verbatim onto the stored `Issue` row.

Follows the conventions of test_pipeline_intervenor_validation.py (`fake`, `kit.build`, `kit.go`, `kit.act_summary`,
`kit.issues_of`) rather than inventing a new pattern.

These tests depend on step20a items 1-3 (moderation/taxonomy.py's `offer_research`/`DEFINITIONS`,
moderation/schemas.py's `MasterIssue.time_sensitive` and `ActType`, moderation/models.py's `Issue.time_sensitive`),
built by other groups working in parallel on this same brief; they are expected to fail (typically with a pydantic
ValidationError from FakeLLM's schema check, or an unexpected keyword argument / unknown field error on `Issue`)
until those land. moderation/pipeline.py's side of item 4 is already in place as of this writing.
"""
import pipeline_run_kit as kit
import pytest

pytestmark = pytest.mark.django_db


def ts_issue_d(id, message, time_sensitive, issue_type="unsupported_claim", quote=kit.QUOTE_1):
    """A Master issue dict (kit.issue_d) with an explicit `time_sensitive` flag."""
    d = kit.issue_d(id, message, issue_type, quote)
    d["time_sensitive"] = time_sensitive
    return d


def two_ts_issues(world, *, ts1, ts2):
    """i1 (unsupported_claim, QUOTE_1) and i2 (fallacy, QUOTE_2) of the default conversation, each with a chosen
    `time_sensitive` flag -- otherwise exactly test_pipeline_intervenor_validation.two_issues."""
    return [
        ts_issue_d("i1", world.last, ts1, "unsupported_claim", kit.QUOTE_1),
        ts_issue_d("i2", world.last, ts2, "fallacy", kit.QUOTE_2),
    ]


def go_with(fake, *, acts, ts1=True, ts2=False, world=None, extra_issues=(), decision="intervene"):
    """A run over the default conversation: i1 (time_sensitive=`ts1`) and i2 (time_sensitive=`ts2`), plus
    `extra_issues`, then the given Intervenor answer. Both i1 and i2 are dispositioned "acted"."""
    world = world or kit.build()
    run = kit.new_run(world.last)
    client = fake(
        kit.master_d(*two_ts_issues(world, ts1=ts1, ts2=ts2), *extra_issues),
        kit.interv_d(decision, "Reason.", [kit.disp_d("i1"), kit.disp_d("i2")], list(acts)),
    )
    returned, stored = kit.go(run)
    return world, stored, client


OTHER_ACT_TYPES = [
    "provide_information", "correct_factual_error", "improve_argumentation", "clarify_argument",
    "restate_positions", "identify_agreement_disagreement", "request_information", "request_clarification",
    "enforce_conduct", "enforce_process",
]  # fmt: skip


class TestOfferResearchSourceValidation:
    def test_citing_only_time_sensitive_issues_is_valid(self, fake):
        world, stored, _ = go_with(fake, acts=[kit.act_d(type="offer_research", issues=["i1"])], ts1=True, ts2=False)
        assert kit.act_summary(stored) == [(1, "valid", "")]

    def test_citing_a_mix_of_time_sensitive_and_not_is_rejected_not_time_sensitive(self, fake):
        world, stored, _ = go_with(
            fake, acts=[kit.act_d(type="offer_research", issues=["i1", "i2"])], ts1=True, ts2=False
        )
        assert kit.act_summary(stored) == [(1, "rejected", "not_time_sensitive")]

    def test_empty_source_issue_ids_is_rejected_not_time_sensitive(self, fake):
        world, stored, _ = go_with(fake, acts=[kit.act_d(type="offer_research", issues=[])], ts1=True, ts2=False)
        assert kit.act_summary(stored) == [(1, "rejected", "not_time_sensitive")]

    def test_citing_an_issue_id_the_master_never_reported_is_still_bad_source_issue(self, fake):
        """bad_source_issue must still win over not_time_sensitive for an unknown id (brief item 4b: the elif chain
        order matters -- bad_source_issue comes before the new check)."""
        world, stored, _ = go_with(fake, acts=[kit.act_d(type="offer_research", issues=["nope"])], ts1=True, ts2=False)
        assert kit.act_summary(stored) == [(1, "rejected", "bad_source_issue")]

    def test_citing_only_a_rejected_known_issue_is_bad_source_issue_not_not_time_sensitive(self, fake):
        world = kit.build()
        rejected = ts_issue_d("r1", world.last, False, "fallacy", "zzz absent phrase")
        world, stored, _ = go_with(
            fake, world=world, extra_issues=[rejected], acts=[kit.act_d(type="offer_research", issues=["r1"])],
            ts1=True, ts2=False,
        )  # fmt: skip
        assert kit.act_summary(stored) == [(1, "rejected", "bad_source_issue")]

    @pytest.mark.parametrize("act_type", OTHER_ACT_TYPES)
    def test_every_other_act_type_is_unaffected_by_an_issues_time_sensitive_value(self, fake, act_type):
        """A non-offer_research act citing a time_sensitive=True issue validates exactly as it does today: no new
        rule silently applies to it."""
        world, stored, _ = go_with(fake, acts=[kit.act_d(type=act_type, issues=["i1"])], ts1=True, ts2=False)
        assert kit.act_summary(stored) == [(1, "valid", "")]

    def test_correct_factual_error_citing_a_time_sensitive_issue_validates_exactly_as_it_does_today(self, fake):
        world, stored, _ = go_with(
            fake, acts=[kit.act_d(type="correct_factual_error", issues=["i1"])], ts1=True, ts2=False
        )
        assert kit.act_summary(stored) == [(1, "valid", "")]

    def test_correct_factual_error_with_no_sources_at_all_is_still_valid(self, fake):
        """Unlike offer_research, an ordinary act with an empty source_issue_ids is valid (existing behaviour,
        untouched by item 4 -- this check only ever runs for offer_research acts)."""
        world, stored, _ = go_with(
            fake, acts=[kit.act_d(type="correct_factual_error", issues=[], messages=[])], ts1=True, ts2=False
        )
        assert kit.act_summary(stored) == [(1, "valid", "")]


class TestTimeSensitiveStorage:
    def test_the_masters_time_sensitive_flag_is_copied_onto_the_stored_issue(self, fake):
        world, stored, _ = go_with(fake, acts=[kit.act_d(type="offer_research", issues=["i1"])], ts1=True, ts2=False)
        issues = {i.local_id: i for i in kit.issues_of(stored)}
        assert issues["i1"].time_sensitive is True
        assert issues["i2"].time_sensitive is False

    def test_time_sensitive_true_is_stored_for_both_issues_when_the_master_says_so(self, fake):
        world, stored, _ = go_with(fake, acts=[kit.act_d(type="offer_research", issues=["i1", "i2"])], ts1=True, ts2=True)
        issues = {i.local_id: i for i in kit.issues_of(stored)}
        assert issues["i1"].time_sensitive is True
        assert issues["i2"].time_sensitive is True
        # both time_sensitive: the offer_research act citing both is valid.
        assert kit.act_summary(stored) == [(1, "valid", "")]
