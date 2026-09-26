"""Issue and IssueDisposition (brief 4b): fields, choices, dimension derivation, intensity, quote offsets and match types,
moderator-authored messages are rejected, uniqueness, one disposition per valid issue."""
import modmodels_testkit as kit
import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from moderation import taxonomy

pytestmark = pytest.mark.django_db

CONTENT = "The moon is made of green cheese, everybody knows that."
ISSUE_FIELDS = [
    "run", "local_id", "message", "issue_type", "dimension", "quote", "quote_start", "quote_end", "quote_match", "explanation",
    "confidence", "intensity", "validity", "rejection_reason",
]  # fmt: skip


@pytest.fixture
def world():
    """seq 1 user (CONTENT), seq 2 moderator, seq 3 user (trigger of `run`, snapshot 3), seq 4 user (later than the snapshot)."""
    conv, parts = kit.make_conversation()
    first = kit.user_msg(conv, parts, "A", CONTENT)
    reply = kit.mod_msg(conv, in_reply_to=first, content="Please cite a source for that claim.")
    trigger = kit.user_msg(conv, parts, "B", "That is simply not true, and you are being ridiculous.", in_reply_to=first)
    later = kit.user_msg(conv, parts, "A", "A later message.")
    run = kit.make_run(trigger)
    return type("World", (), dict(conv=conv, parts=parts, first=first, reply=reply, trigger=trigger, later=later, run=run))


def exact(world, quote="green cheese", **kw):
    start = CONTENT.index(quote)
    values = dict(quote=quote, quote_start=start, quote_end=start + len(quote), quote_match="exact")
    values.update(kw)
    return kit.make_issue(world.run, world.first, **values)


# --- shape -----------------------------------------------------------------------------------------------------------------
def test_issue_has_every_contract_field():
    from moderation.models import Issue

    names = {f.name for f in Issue._meta.get_fields()}
    assert [n for n in ISSUE_FIELDS if n not in names] == []


def test_issue_relations_are_protect():
    from forum.models import Message
    from moderation.models import Issue, ModerationRun

    for name, target in [("run", ModerationRun), ("message", Message)]:
        field = Issue._meta.get_field(name)
        assert field.remote_field.on_delete is models.PROTECT and field.related_model is target


def test_issue_field_kinds_and_nullability():
    from moderation.models import Issue

    meta = Issue._meta
    for name in ("quote_start", "quote_end", "intensity"):
        assert meta.get_field(name).null is True and isinstance(meta.get_field(name), models.IntegerField), name
    assert isinstance(meta.get_field("confidence"), models.FloatField)
    assert meta.get_field("local_id").get_internal_type() in ("CharField", "TextField")


def test_choices_come_from_the_taxonomy_and_the_contract():
    from moderation.models import Issue

    def values(name):
        return {v for v, _ in Issue._meta.get_field(name).choices or []}

    assert values("issue_type") == set(taxonomy.ISSUE_TYPES)
    assert values("quote_match") == {"exact", "normalized", "not_found"}
    assert values("validity") == {"valid", "rejected"}
    dimension_choices = values("dimension")
    if dimension_choices:
        assert dimension_choices <= {"", *taxonomy.DIMENSIONS}


def test_defaults_and_blank_fields(world):
    issue = kit.make_issue(world.run, world.first)
    issue.refresh_from_db()
    assert issue.validity == "valid"
    assert issue.rejection_reason == ""
    assert issue.dimension == ""
    assert issue.intensity is None
    assert (issue.quote_start, issue.quote_end) == (None, None)


def test_unknown_choices_are_refused_by_full_clean(world):
    issue = kit.make_issue(world.run, world.first)
    for name in ("issue_type", "quote_match", "validity"):
        old = getattr(issue, name)
        setattr(issue, name, "bogus")
        with pytest.raises(ValidationError) as caught:
            issue.full_clean()
        assert name in caught.value.message_dict
        setattr(issue, name, old)


def test_every_issue_type_can_be_stored(world):

    for i, issue_type in enumerate(taxonomy.ISSUE_TYPES):
        assert kit.make_issue(world.run, world.first, local_id=f"t{i}", issue_type=issue_type).issue_type == issue_type


# --- dimension derivation and intensity --------------------------------------------------------------------------------------
@pytest.mark.parametrize("issue_type", taxonomy.ISSUE_TYPES)
def test_dimension_is_derived_from_the_taxonomy(world, issue_type):

    issue = kit.make_issue(world.run, world.first, issue_type=issue_type)
    issue.refresh_from_db()
    assert issue.dimension == (taxonomy.dimension_for(issue_type) or "")


def test_the_two_dimensions_of_the_plan(world):
    a = kit.make_issue(world.run, world.first, issue_type="possible_factual_error", intensity=3)
    b = kit.make_issue(world.run, world.first, issue_type="abusive_language", intensity=1)
    assert (a.dimension, b.dimension) == ("factual_accuracy", "abusiveness")


def test_a_caller_supplied_dimension_is_overwritten_by_the_derivation(world):
    issue = kit.make_issue(world.run, world.first, issue_type="abusive_language", dimension="factual_accuracy")
    assert issue.dimension == "abusiveness"
    issue = kit.make_issue(world.run, world.first, issue_type="fallacy", dimension="abusiveness")
    assert issue.dimension == ""


def test_the_dimension_follows_the_type_when_the_type_is_edited(world):
    issue = kit.make_issue(world.run, world.first, issue_type="abusive_language")
    issue.issue_type = "unclear_statement"
    issue.save()
    issue.refresh_from_db()
    assert issue.dimension == ""


@pytest.mark.parametrize("intensity", [0, 1, 2, 3, 4, None])
def test_intensity_accepts_zero_to_four_and_null_for_a_dimension_type(world, intensity):
    issue = kit.make_issue(world.run, world.first, issue_type="possible_factual_error", intensity=intensity)
    issue.refresh_from_db()
    assert issue.intensity == intensity


@pytest.mark.parametrize("intensity", [-1, 5, 100])
def test_intensity_out_of_range_is_refused_at_save(world, intensity):
    with pytest.raises((ValidationError, IntegrityError)), transaction.atomic():
        kit.make_issue(world.run, world.first, issue_type="abusive_language", intensity=intensity)


@pytest.mark.parametrize("intensity", [-1, 5])
def test_intensity_out_of_range_is_refused_by_the_database(world, intensity):
    from moderation.models import Issue

    with pytest.raises(IntegrityError), transaction.atomic():
        Issue.objects.bulk_create([kit.unsaved_issue(world.run, world.first, issue_type="abusive_language", intensity=intensity)])


@pytest.mark.parametrize("issue_type", ["unsupported_claim", "unclear_statement", "fallacy", "strawman", "repetition", "process_violation"])
def test_intensity_must_be_null_when_the_type_has_no_dimension(world, issue_type):
    with pytest.raises(ValidationError):
        kit.make_issue(world.run, world.first, issue_type=issue_type, intensity=2)
    assert kit.make_issue(world.run, world.first, issue_type=issue_type, intensity=None).intensity is None


def test_intensity_zero_is_still_an_intensity_for_a_type_without_a_dimension(world):
    with pytest.raises(ValidationError):
        kit.make_issue(world.run, world.first, issue_type="fallacy", intensity=0)


# --- quote offsets and match type ----------------------------------------------------------------------------------------------
def test_an_exact_quote_with_matching_offsets_is_stored(world):
    issue = exact(world)
    issue.refresh_from_db()
    assert CONTENT[issue.quote_start : issue.quote_end] == issue.quote == "green cheese"


def test_the_whole_message_may_be_quoted(world):
    issue = exact(world, quote=CONTENT)
    assert (issue.quote_start, issue.quote_end) == (0, len(CONTENT))


def test_a_quote_at_the_very_end_is_accepted(world):
    issue = exact(world, quote="knows that.")
    assert issue.quote_end == len(CONTENT)


def test_an_exact_quote_that_differs_from_the_text_at_the_offsets_is_refused(world):
    with pytest.raises(ValidationError):
        kit.make_issue(world.run, world.first, quote="green cheeze", quote_start=18, quote_end=30, quote_match="exact")
    start = CONTENT.index("green cheese")
    with pytest.raises(ValidationError):  # right text, offsets shifted by one
        kit.make_issue(world.run, world.first, quote="green cheese", quote_start=start + 1, quote_end=start + 13, quote_match="exact")
    with pytest.raises(ValidationError):  # equal only up to case
        kit.make_issue(world.run, world.first, quote="GREEN CHEESE", quote_start=start, quote_end=start + 12, quote_match="exact")


def test_a_normalized_match_need_not_equal_the_text(world):
    start = CONTENT.index("green cheese")
    issue = kit.make_issue(
        world.run, world.first, quote="Green  Cheese", quote_start=start, quote_end=start + 12, quote_match="normalized"
    )  # fmt: skip
    issue.refresh_from_db()
    assert issue.quote_match == "normalized" and issue.quote != CONTENT[issue.quote_start : issue.quote_end]


@pytest.mark.parametrize(
    "start,end",
    [(-1, 5), (5, 5), (7, 5), (0, 0), (0, len(CONTENT) + 1), (len(CONTENT), len(CONTENT) + 3), (len(CONTENT), len(CONTENT))],
)
def test_offsets_must_satisfy_zero_le_start_lt_end_le_length(world, start, end):
    for match in ("exact", "normalized"):
        with pytest.raises(ValidationError):
            kit.make_issue(world.run, world.first, quote=CONTENT[max(start, 0) : end], quote_start=start, quote_end=end, quote_match=match)


def test_offsets_may_not_be_half_present(world):
    for start, end in ((3, None), (None, 9)):
        for match in ("exact", "normalized", "not_found"):
            with pytest.raises((ValidationError, IntegrityError)), transaction.atomic():
                kit.make_issue(world.run, world.first, quote="x", quote_start=start, quote_end=end, quote_match=match)


def test_not_found_means_null_offsets(world):
    issue = kit.make_issue(world.run, world.first, quote="not in the text", quote_match="not_found", validity="rejected", rejection_reason="quote not found")
    issue.refresh_from_db()
    assert (issue.quote_start, issue.quote_end, issue.quote_match) == (None, None, "not_found")


def test_not_found_with_offsets_is_refused(world):
    with pytest.raises(ValidationError):
        kit.make_issue(world.run, world.first, quote="green", quote_start=18, quote_end=23, quote_match="not_found")


@pytest.mark.parametrize("match", ["exact", "normalized"])
def test_a_found_match_needs_offsets(world, match):
    with pytest.raises(ValidationError):
        kit.make_issue(world.run, world.first, quote="green cheese", quote_match=match)


def test_the_not_found_offsets_rule_in_the_database(world):
    """CheckConstraint: quote_match = not_found if and only if both offsets are null (bulk_create skips save())."""
    from moderation.models import Issue

    for kwargs in (
        dict(quote_match="exact", quote_start=None, quote_end=None),
        dict(quote_match="normalized", quote_start=None, quote_end=None),
        dict(quote_match="not_found", quote_start=1, quote_end=4),
        dict(quote_match="not_found", quote_start=1, quote_end=None),
        dict(quote_match="not_found", quote_start=None, quote_end=4),
    ):
        with pytest.raises(IntegrityError), transaction.atomic():
            Issue.objects.bulk_create([kit.unsaved_issue(world.run, world.first, quote="green", **kwargs)])
    Issue.objects.bulk_create([kit.unsaved_issue(world.run, world.first, local_id="ok1", quote_match="not_found")])
    Issue.objects.bulk_create([kit.unsaved_issue(world.run, world.first, local_id="ok2", quote_match="exact", quote_start=1, quote_end=4)])


def test_offsets_index_code_points_not_bytes(world):
    conv, parts = kit.make_conversation()
    text = "café \U0001f600 is nice — really"
    msg = kit.user_msg(conv, parts, "A", text)
    trigger = kit.user_msg(conv, parts, "B", "ok")
    run = kit.make_run(trigger)
    start = text.index("is nice")
    issue = kit.make_issue(run, msg, quote="is nice", quote_start=start, quote_end=start + 7, quote_match="exact")
    assert text[issue.quote_start : issue.quote_end] == "is nice"


# --- message rules -----------------------------------------------------------------------------------------------------------------
def test_the_message_must_belong_to_the_runs_conversation(world):
    other_conv, other_parts = kit.make_conversation()
    foreign = kit.user_msg(other_conv, other_parts)
    with pytest.raises(ValidationError):
        kit.make_issue(world.run, foreign)


def test_the_message_may_not_be_later_than_the_snapshot(world):
    with pytest.raises(ValidationError):
        kit.make_issue(world.run, world.later)


def test_the_trigger_and_earlier_messages_are_allowed(world):
    kit.make_issue(world.run, world.trigger)  # seq == snapshot_seq
    kit.make_issue(world.run, world.first)  # earlier


def test_an_issue_on_a_moderator_message_must_be_rejected_with_a_reason(world):
    with pytest.raises(ValidationError):
        kit.make_issue(world.run, world.reply)  # default validity is valid
    with pytest.raises(ValidationError):
        kit.make_issue(world.run, world.reply, validity="valid")
    with pytest.raises(ValidationError):
        kit.make_issue(world.run, world.reply, validity="rejected", rejection_reason="")
    with pytest.raises(ValidationError):
        kit.make_issue(world.run, world.reply, validity="rejected", rejection_reason="   ")
    from moderation.models import Issue

    assert Issue.objects.count() == 0
    issue = kit.make_issue(world.run, world.reply, validity="rejected", rejection_reason="moderator message")
    issue.refresh_from_db()
    assert issue.message_id == world.reply.pk and issue.validity == "rejected"
    assert issue.rejection_reason == "moderator message"


def test_a_saved_valid_issue_cannot_be_repointed_at_a_moderator_message(world):
    issue = kit.make_issue(world.run, world.first)
    issue.message = world.reply
    with pytest.raises(ValidationError):
        issue.save()


def test_a_valid_issue_cannot_be_turned_into_a_valid_moderator_one_by_a_rejection_reason_alone(world):
    with pytest.raises(ValidationError):
        kit.make_issue(world.run, world.reply, validity="valid", rejection_reason="but I gave a reason")


def test_a_rejected_issue_needs_a_reason_on_any_message(world):
    with pytest.raises(ValidationError):
        kit.make_issue(world.run, world.first, validity="rejected", rejection_reason="")
    issue = kit.make_issue(world.run, world.first, validity="rejected", rejection_reason="older message, not a cross-message type")
    assert issue.validity == "rejected"


def test_a_valid_issue_on_a_user_message_needs_no_reason(world):
    assert kit.make_issue(world.run, world.first, rejection_reason="").validity == "valid"


# --- uniqueness ----------------------------------------------------------------------------------------------------------------------
def test_local_id_is_unique_per_run(world):
    kit.make_issue(world.run, world.first, local_id="m1")
    with pytest.raises((IntegrityError, ValidationError)), transaction.atomic():
        kit.make_issue(world.run, world.first, local_id="m1")
    other_run = kit.make_run(world.first)
    kit.make_issue(other_run, world.first, local_id="m1")  # a different run may reuse the id


def test_local_id_uniqueness_in_the_database(world):
    from moderation.models import Issue

    kit.make_issue(world.run, world.first, local_id="m1")
    with pytest.raises(IntegrityError), transaction.atomic():
        Issue.objects.bulk_create([kit.unsaved_issue(world.run, world.first, local_id="m1")])


def test_the_author_is_derived_from_the_message_not_stored(world):
    from moderation.models import Issue

    names = {f.name for f in Issue._meta.get_fields()}
    assert "author_type" not in names and "author" not in names


# --- IssueDisposition -----------------------------------------------------------------------------------------------------------------
def make_disposition(issue, **kw):
    from moderation.models import IssueDisposition

    values = dict(issue=issue, disposition="acted", reason="handled in act 1")
    values.update(kw)
    return IssueDisposition.objects.create(**values)


def test_disposition_shape():
    from moderation.models import Issue, IssueDisposition

    issue_field = IssueDisposition._meta.get_field("issue")
    assert isinstance(issue_field, models.OneToOneField)
    assert issue_field.remote_field.on_delete is models.PROTECT and issue_field.related_model is Issue
    assert {v for v, _ in IssueDisposition._meta.get_field("disposition").choices} == set(taxonomy.DISPOSITIONS) == {"acted", "declined"}
    assert {"issue", "disposition", "reason"} <= {f.name for f in IssueDisposition._meta.get_fields()}


@pytest.mark.parametrize("disposition", ["acted", "declined"])
def test_a_valid_issue_can_have_a_disposition(world, disposition):
    issue = kit.make_issue(world.run, world.first)
    d = make_disposition(issue, disposition=disposition, reason="because")
    d.refresh_from_db()
    assert (d.issue_id, d.disposition, d.reason) == (issue.pk, disposition, "because")


def test_an_unknown_disposition_is_refused(world):
    issue = kit.make_issue(world.run, world.first)
    d = make_disposition(issue)
    d.disposition = "ignored"
    with pytest.raises(ValidationError):
        d.full_clean()


def test_an_issue_has_at_most_one_disposition(world):
    issue = kit.make_issue(world.run, world.first)
    make_disposition(issue)
    with pytest.raises((IntegrityError, ValidationError)), transaction.atomic():
        make_disposition(issue, disposition="declined")


def test_one_disposition_per_issue_in_the_database(world):
    from moderation.models import IssueDisposition

    issue = kit.make_issue(world.run, world.first)
    make_disposition(issue)
    with pytest.raises(IntegrityError), transaction.atomic():
        IssueDisposition.objects.bulk_create([IssueDisposition(issue=issue, disposition="declined", reason="x")])


def test_a_rejected_issue_cannot_have_a_disposition(world):
    issue = kit.make_issue(world.run, world.first, validity="rejected", rejection_reason="nope")
    with pytest.raises(ValidationError):
        make_disposition(issue)
    moderator_issue = kit.make_issue(world.run, world.reply, validity="rejected", rejection_reason="moderator message")
    with pytest.raises(ValidationError):
        make_disposition(moderator_issue)


def test_a_disposition_can_be_updated_in_place(world):
    issue = kit.make_issue(world.run, world.first)
    d = make_disposition(issue)
    d.disposition = "declined"
    d.reason = "changed my mind"
    d.save()
    d.refresh_from_db()
    assert d.disposition == "declined"


def test_each_valid_issue_of_a_run_can_have_its_own_disposition(world):
    issues = [kit.make_issue(world.run, world.first) for _ in range(3)]
    for issue in issues:
        make_disposition(issue)
    from moderation.models import IssueDisposition

    assert IssueDisposition.objects.count() == 3


# --- PROTECT ---------------------------------------------------------------------------------------------------------------------------
def test_protect_blocks_deleting_what_issues_and_dispositions_hang_on(world):
    from django.db.models import ProtectedError

    issue = kit.make_issue(world.run, world.first)
    make_disposition(issue)
    for victim in (issue, world.run, world.first):
        with pytest.raises(ProtectedError), transaction.atomic():
            victim.delete()


# --- contract clarifications ------------------------------------------------------------------------------------------------------------
def test_quote_match_defaults_to_not_found_and_confidence_to_zero(world):
    from moderation.models import Issue

    issue = Issue.objects.create(run=world.run, local_id="d1", message=world.first, issue_type="unsupported_claim", quote="", explanation="e")
    issue.refresh_from_db()
    assert issue.quote_match == "not_found"
    assert issue.confidence == 0.0
    assert (issue.quote_start, issue.quote_end) == (None, None)


def test_the_message_seq_may_equal_the_snapshot_but_not_exceed_it(world):
    assert world.trigger.seq_no == world.run.snapshot_seq
    kit.make_issue(world.run, world.trigger)
    with pytest.raises(ValidationError):
        kit.make_issue(world.run, world.later)


def test_related_names_of_issues_and_dispositions(world):
    issue = kit.make_issue(world.run, world.first)
    d = make_disposition(issue)
    assert issue.disposition == d
    assert list(world.run.issues.all()) == [issue]
    assert list(world.first.issues.all()) == [issue]
