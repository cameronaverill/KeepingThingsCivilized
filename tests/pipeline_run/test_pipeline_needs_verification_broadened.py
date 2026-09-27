"""Step 20a revision (docs/step20a_revision_brief.md items 1 and its "Tests" section): the broadened
`needs_verification` concept in moderation/pipeline.py's `validate_acts`.

`time_sensitive` was scoped only to recency; it is being renamed to `needs_verification` and broadened to also cover
"the model might be wrong or fabricating this specific detail, regardless of when it happened" (docs/plan.md
section 2, "Factual research via web search", 2026-09-27 update). The mechanism is unchanged: a boolean field on
`MasterIssue`/`Issue`, and a single pipeline rule (an `offer_research` act must cite at least one issue and every
issue it cites must have `needs_verification=True`, else rejected `not_needs_verification`). The pipeline has no
concept of *why* the flag is true -- only that it is -- so this file exercises that broadened meaning directly,
with both a recency-style and a fabrication-style scenario, and asserts they behave identically.

This is a NEW file, not an edit of tests/pipeline_run/test_pipeline_offer_research.py (which a coding agent is
concurrently renaming/rewriting for the mechanical part of this same rename -- old field name to new field name,
old reason code to new reason code, same test shapes). Follows the same conventions as that file and as
test_pipeline_intervenor_validation.py (`fake`, `kit.build`, `kit.go`, `kit.act_summary`, `kit.issues_of`).

Depends on the concurrent rename of `MasterIssue.time_sensitive` -> `.needs_verification` (moderation/schemas.py),
`Issue.time_sensitive` -> `.needs_verification` (moderation/models.py), and the pipeline's rejection reason code
`not_time_sensitive` -> `not_needs_verification` (moderation/pipeline.py) -- built by other groups working in
parallel on this same brief. Expected to fail (typically a pydantic ValidationError from FakeLLM's schema check, an
unexpected keyword argument / unknown field error on `Issue`, or a rejection reason of `not_time_sensitive` instead
of `not_needs_verification`) until those land.
"""
import pipeline_run_kit as kit
import pytest

pytestmark = pytest.mark.django_db

# Two explanations for why an issue's `needs_verification` might be true, standing in for the two reasons
# docs/plan.md describes. The pipeline-level validation must not care which one applies -- it only reads the
# boolean -- so these are used interchangeably below to prove that.
RECENCY_EXPLANATION = "The claim concerns a current statistic whose true value could have changed since training."
FABRICATION_EXPLANATION = (
    "The message asserts a 2024 Harvard study proved remote work reduces productivity by 40%; the specific "
    "citation and figure are not ones the model is confident actually exist."
)


def nv_issue_d(id, message, needs_verification, issue_type="unsupported_claim", quote=kit.QUOTE_1,
               explanation=RECENCY_EXPLANATION):
    """A Master issue dict (kit.issue_d), tolerant of whichever field name `kit.issue_d` currently produces for the
    verification flag (`time_sensitive` pre-rename or `needs_verification` post-rename): build the dict, then drop
    any `time_sensitive` key and set `needs_verification` explicitly, so this file works unchanged once the kit's
    own mechanical rename pass lands."""
    d = kit.issue_d(id, message, issue_type, quote, explanation=explanation)
    d.pop("time_sensitive", None)
    d["needs_verification"] = needs_verification
    return d


def two_nv_issues(world, *, nv1, nv2, explanation1=RECENCY_EXPLANATION, explanation2=RECENCY_EXPLANATION):
    """i1 (unsupported_claim, QUOTE_1) and i2 (fallacy, QUOTE_2) of the default conversation, each with a chosen
    `needs_verification` flag and explanation -- otherwise exactly test_pipeline_intervenor_validation.two_issues."""
    return [
        nv_issue_d("i1", world.last, nv1, "unsupported_claim", kit.QUOTE_1, explanation=explanation1),
        nv_issue_d("i2", world.last, nv2, "fallacy", kit.QUOTE_2, explanation=explanation2),
    ]


def go_with(fake, *, acts, nv1=True, nv2=False, world=None, extra_issues=(), decision="intervene",
            explanation1=RECENCY_EXPLANATION, explanation2=RECENCY_EXPLANATION):
    """A run over the default conversation: i1 (needs_verification=`nv1`) and i2 (needs_verification=`nv2`), plus
    `extra_issues`, then the given Intervenor answer. Both i1 and i2 are dispositioned "acted"."""
    world = world or kit.build()
    run = kit.new_run(world.last)
    client = fake(
        kit.master_d(*two_nv_issues(world, nv1=nv1, nv2=nv2, explanation1=explanation1, explanation2=explanation2),
                     *extra_issues),
        kit.interv_d(decision, "Reason.", [kit.disp_d("i1"), kit.disp_d("i2")], list(acts)),
    )  # fmt: skip
    returned, stored = kit.go(run)
    return world, stored, client


class TestNeedsVerificationIsIndifferentToTheReason:
    """The pipeline-level rule has no concept of *why* `needs_verification` is true (recency vs. fabrication risk):
    it only reads the boolean. A fabrication-style scenario must validate an `offer_research` act citing it exactly
    as a recency-style one does."""

    def test_recency_style_issue_validates_offer_research(self, fake):
        world, stored, _ = go_with(
            fake, acts=[kit.act_d(type="offer_research", issues=["i1"])], nv1=True, nv2=False,
            explanation1=RECENCY_EXPLANATION,
        )
        assert kit.act_summary(stored) == [(1, "valid", "")]

    def test_fabrication_style_issue_validates_offer_research_exactly_the_same_way(self, fake):
        """Same shape as the recency-style test above, only the explanation text differs (a fabricated-citation
        scenario, nothing to do with recency): the pipeline's answer must be identical."""
        world, stored, _ = go_with(
            fake, acts=[kit.act_d(type="offer_research", issues=["i1"])], nv1=True, nv2=False,
            explanation1=FABRICATION_EXPLANATION,
        )
        assert kit.act_summary(stored) == [(1, "valid", "")]

    def test_a_fabrication_style_issue_that_is_not_flagged_still_fails_the_same_way_a_non_time_sensitive_one_did(self, fake):
        """needs_verification=False for a fabrication-style issue is rejected exactly like needs_verification=False
        for a recency-style one -- the content of the explanation never matters, only the flag."""
        world, stored, _ = go_with(
            fake, acts=[kit.act_d(type="offer_research", issues=["i1"])], nv1=False, nv2=False,
            explanation1=FABRICATION_EXPLANATION,
        )
        assert kit.act_summary(stored) == [(1, "rejected", "not_needs_verification")]

    def test_the_masters_needs_verification_flag_is_copied_onto_the_stored_issue_for_a_fabrication_style_issue(self, fake):
        world, stored, _ = go_with(
            fake, acts=[kit.act_d(type="offer_research", issues=["i1"])], nv1=True, nv2=False,
            explanation1=FABRICATION_EXPLANATION,
        )
        issues = {i.local_id: i for i in kit.issues_of(stored)}
        assert issues["i1"].needs_verification is True
        assert issues["i2"].needs_verification is False


class TestRejectionReasonCodeIsRenamed:
    """Same cases the original Step 20a tests covered for `not_time_sensitive`, now expecting the renamed reason
    code `not_needs_verification` (docs/step20a_revision_brief.md item 1: "keep it a single reason code")."""

    def test_citing_only_a_needs_verification_issue_is_valid(self, fake):
        world, stored, _ = go_with(fake, acts=[kit.act_d(type="offer_research", issues=["i1"])], nv1=True, nv2=False)
        assert kit.act_summary(stored) == [(1, "valid", "")]

    def test_citing_a_mix_of_needs_verification_and_not_is_rejected_not_needs_verification(self, fake):
        world, stored, _ = go_with(
            fake, acts=[kit.act_d(type="offer_research", issues=["i1", "i2"])], nv1=True, nv2=False
        )
        assert kit.act_summary(stored) == [(1, "rejected", "not_needs_verification")]

    def test_empty_source_issue_ids_is_rejected_not_needs_verification(self, fake):
        world, stored, _ = go_with(fake, acts=[kit.act_d(type="offer_research", issues=[])], nv1=True, nv2=False)
        assert kit.act_summary(stored) == [(1, "rejected", "not_needs_verification")]

    def test_the_old_reason_code_not_time_sensitive_is_gone(self, fake):
        """The reason code is renamed, not duplicated or aliased: a rejected offer_research act never reports the
        old code."""
        world, stored, _ = go_with(fake, acts=[kit.act_d(type="offer_research", issues=[])], nv1=True, nv2=False)
        reasons = {reason for _order, _validity, reason in kit.act_summary(stored)}
        assert "not_time_sensitive" not in reasons

    def test_citing_an_issue_id_the_master_never_reported_is_still_bad_source_issue(self, fake):
        """bad_source_issue must still win over not_needs_verification for an unknown id (the elif chain order is
        unchanged by this rename)."""
        world, stored, _ = go_with(fake, acts=[kit.act_d(type="offer_research", issues=["nope"])], nv1=True, nv2=False)
        assert kit.act_summary(stored) == [(1, "rejected", "bad_source_issue")]

    def test_citing_only_a_rejected_known_issue_is_bad_source_issue_not_not_needs_verification(self, fake):
        world = kit.build()
        rejected = nv_issue_d("r1", world.last, False, "fallacy", "zzz absent phrase")
        world, stored, _ = go_with(
            fake, world=world, extra_issues=[rejected], acts=[kit.act_d(type="offer_research", issues=["r1"])],
            nv1=True, nv2=False,
        )  # fmt: skip
        assert kit.act_summary(stored) == [(1, "rejected", "bad_source_issue")]

    @pytest.mark.parametrize("act_type", [
        "provide_information", "correct_factual_error", "improve_argumentation", "clarify_argument",
        "restate_positions", "identify_agreement_disagreement", "request_information", "request_clarification",
        "enforce_conduct", "enforce_process",
    ])  # fmt: skip
    def test_every_other_act_type_is_unaffected_by_an_issues_needs_verification_value(self, fake, act_type):
        """A non-offer_research act citing a needs_verification=True issue validates exactly as it does today: no
        new rule silently applies to it, regardless of the flag's underlying reason."""
        world, stored, _ = go_with(
            fake, acts=[kit.act_d(type=act_type, issues=["i1"])], nv1=True, nv2=False,
            explanation1=FABRICATION_EXPLANATION,
        )
        assert kit.act_summary(stored) == [(1, "valid", "")]
