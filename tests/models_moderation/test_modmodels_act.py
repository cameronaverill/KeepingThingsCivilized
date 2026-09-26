"""InterventionAct (brief 4b): fields, choices, order uniqueness, the MAX_ACTS_PER_INTERVENTION cap on valid acts, the
features computed in save(), and the source issue / source message rules."""
import modmodels_testkit as kit
import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from moderation import taxonomy

pytestmark = pytest.mark.django_db

ACT_FIELDS = [
    "run", "order", "act_type", "tone", "text", "addressee", "subject", "validity", "rejection_reason", "char_len", "word_count",
    "is_question", "quotes_participant", "source_issues", "source_messages",
]  # fmt: skip


@pytest.fixture
def world():
    conv, parts = kit.make_conversation()
    first = kit.user_msg(conv, parts, "A", "The moon is made of green cheese, everybody knows that.")
    reply = kit.mod_msg(conv, in_reply_to=first)
    trigger = kit.user_msg(conv, parts, "B", "That is simply not true.", in_reply_to=first)
    run = kit.make_run(trigger)
    return type("World", (), dict(conv=conv, parts=parts, first=first, reply=reply, trigger=trigger, run=run))


# --- shape -----------------------------------------------------------------------------------------------------------------
def test_act_has_every_contract_field():
    from moderation.models import InterventionAct

    names = {f.name for f in InterventionAct._meta.get_fields()}
    assert [n for n in ACT_FIELDS if n not in names] == []


def test_relations():
    from forum.models import Message
    from moderation.models import InterventionAct, Issue, ModerationRun

    meta = InterventionAct._meta
    run = meta.get_field("run")
    assert run.remote_field.on_delete is models.PROTECT and run.related_model is ModerationRun
    assert isinstance(meta.get_field("source_issues"), models.ManyToManyField)
    assert meta.get_field("source_issues").related_model is Issue
    assert isinstance(meta.get_field("source_messages"), models.ManyToManyField)
    assert meta.get_field("source_messages").related_model is Message


def test_choices_come_from_the_taxonomy():
    from moderation.models import InterventionAct

    def values(name):
        return {v for v, _ in InterventionAct._meta.get_field(name).choices or []}

    assert values("act_type") == set(taxonomy.ACT_TYPES)
    assert values("tone") == set(taxonomy.TONES) == {"gentle", "neutral", "firm"}
    assert values("validity") == {"valid", "rejected"}


@pytest.mark.parametrize("act_type", taxonomy.ACT_TYPES)
def test_every_act_type_can_be_stored(world, act_type):
    act = kit.make_act(world.run, act_type=act_type)
    act.refresh_from_db()
    assert act.act_type == act_type


@pytest.mark.parametrize("tone", taxonomy.TONES)
def test_every_tone_can_be_stored(world, tone):
    assert kit.make_act(world.run, tone=tone).tone == tone


def test_unknown_act_type_tone_and_validity_are_refused_by_full_clean(world):
    act = kit.make_act(world.run)
    for name in ("act_type", "tone", "validity"):
        old = getattr(act, name)
        setattr(act, name, "bogus")
        with pytest.raises(ValidationError) as caught:
            act.full_clean()
        assert name in caught.value.message_dict
        setattr(act, name, old)


def test_addressee_and_subject_are_stored_as_given(world):
    a = kit.make_act(world.run, order=1, addressee="all", subject="both")
    b = kit.make_act(world.run, order=2, addressee="B", subject="A")
    c = kit.make_act(world.run, order=3, addressee="A", subject="none")
    for act, expected in ((a, ("all", "both")), (b, ("B", "A")), (c, ("A", "none"))):
        act.refresh_from_db()
        assert (act.addressee, act.subject) == expected


def test_defaults(world):
    act = kit.make_act(world.run)
    act.refresh_from_db()
    assert act.validity == "valid"
    assert act.rejection_reason == ""
    assert act.source_issues.count() == 0 and act.source_messages.count() == 0


# --- order ---------------------------------------------------------------------------------------------------------------------
def test_order_is_unique_per_run(world):
    kit.make_act(world.run, order=1)
    with pytest.raises((IntegrityError, ValidationError)), transaction.atomic():
        kit.make_act(world.run, order=1)
    other = kit.make_run(world.first)
    kit.make_act(other, order=1)  # another run has its own numbering


def test_order_uniqueness_in_the_database(world):
    from moderation.models import InterventionAct

    kit.make_act(world.run, order=1)
    with pytest.raises(IntegrityError), transaction.atomic():
        InterventionAct.objects.bulk_create([kit.unsaved_act(world.run, order=1)])


def test_order_must_be_at_least_one_in_the_database(world):
    from moderation.models import InterventionAct

    for bad in (0, -1):
        with pytest.raises(IntegrityError), transaction.atomic():
            InterventionAct.objects.bulk_create([kit.unsaved_act(world.run, order=bad)])


@pytest.mark.parametrize("bad", [0, -2])
def test_order_must_be_at_least_one_at_save(world, bad):
    with pytest.raises((ValidationError, IntegrityError)), transaction.atomic():
        kit.make_act(world.run, order=bad)


def test_rejected_acts_keep_their_place_in_the_order(world):
    kit.make_act(world.run, order=1)
    kit.make_act(world.run, order=2, validity="rejected", rejection_reason="cites a rejected issue")
    with pytest.raises((IntegrityError, ValidationError)), transaction.atomic():
        kit.make_act(world.run, order=2)


# --- the cap ---------------------------------------------------------------------------------------------------------------------
def test_the_default_cap_is_three(settings):
    assert settings.MAX_ACTS_PER_INTERVENTION == 3


def test_a_run_may_have_up_to_the_cap_of_valid_acts_and_no_more(world, settings):
    from moderation.models import InterventionAct

    settings.MAX_ACTS_PER_INTERVENTION = 3
    for order in (1, 2, 3):
        kit.make_act(world.run, order=order)
    with pytest.raises(ValidationError):
        kit.make_act(world.run, order=4)
    assert InterventionAct.objects.filter(run=world.run).count() == 3


def test_rejected_acts_do_not_count_towards_the_cap(world, settings):
    from moderation.models import InterventionAct

    settings.MAX_ACTS_PER_INTERVENTION = 3
    for order in (1, 2, 3):
        kit.make_act(world.run, order=order)
    for order in (4, 5):
        kit.make_act(world.run, order=order, validity="rejected", rejection_reason="over the cap")
    assert InterventionAct.objects.filter(run=world.run).count() == 5
    assert InterventionAct.objects.filter(run=world.run, validity="valid").count() == 3


def test_rejected_acts_first_then_valid_acts_up_to_the_cap(world, settings):
    settings.MAX_ACTS_PER_INTERVENTION = 2
    kit.make_act(world.run, order=1, validity="rejected", rejection_reason="bad")
    kit.make_act(world.run, order=2)
    kit.make_act(world.run, order=3)
    with pytest.raises(ValidationError):
        kit.make_act(world.run, order=4)


@pytest.mark.parametrize("cap", [1, 2, 5])
def test_the_cap_follows_the_setting(world, settings, cap):
    settings.MAX_ACTS_PER_INTERVENTION = cap
    for order in range(1, cap + 1):
        kit.make_act(world.run, order=order)
    with pytest.raises(ValidationError):
        kit.make_act(world.run, order=cap + 1)


def test_the_cap_is_per_run(world, settings):
    settings.MAX_ACTS_PER_INTERVENTION = 1
    kit.make_act(world.run, order=1)
    other = kit.make_run(world.first)
    kit.make_act(other, order=1)


def test_resaving_an_existing_valid_act_at_the_cap_is_fine(world, settings):
    settings.MAX_ACTS_PER_INTERVENTION = 2
    acts = [kit.make_act(world.run, order=o) for o in (1, 2)]
    acts[1].text = "Edited text?"
    acts[1].save()
    acts[1].refresh_from_db()
    assert acts[1].text == "Edited text?"


def test_turning_a_rejected_act_valid_over_the_cap_is_refused(world, settings):
    settings.MAX_ACTS_PER_INTERVENTION = 1
    kit.make_act(world.run, order=1)
    extra = kit.make_act(world.run, order=2, validity="rejected", rejection_reason="over the cap")
    extra.validity = "valid"
    extra.rejection_reason = ""
    with pytest.raises(ValidationError):
        extra.save()
    extra.refresh_from_db()
    assert extra.validity == "rejected"


# --- computed features ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text,chars,words,question,quoting",
    [
        ("Could you share a source?", 25, 5, True, False),
        ("Please stay on topic.", 21, 4, False, False),
        ('You said "the moon is cheese" earlier.', 38, 7, False, True),
        ('Did you mean "green cheese"?', 28, 5, True, True),
        ("one", 3, 1, False, False),
        ("  spaced   out \n words\t here  ", 30, 4, False, False),
        ("An unmatched \" quote mark only.", 31, 6, False, False),
        ("What? Really? Yes.", 18, 3, True, False),
        ("multi\nline\ntext", 15, 3, False, False),
    ],
)
def test_features_are_computed_from_the_text(world, text, chars, words, question, quoting):
    assert len(text) == chars and len(text.split()) == words  # the table itself is right
    act = kit.make_act(world.run, text=text)
    act.refresh_from_db()
    assert (act.char_len, act.word_count, act.is_question, act.quotes_participant) == (chars, words, question, quoting)


def test_features_ignore_values_the_caller_supplies(world):
    act = kit.make_act(world.run, text="Plain text.", char_len=999, word_count=999, is_question=True, quotes_participant=True)
    act.refresh_from_db()
    assert (act.char_len, act.word_count, act.is_question, act.quotes_participant) == (11, 2, False, False)


def test_features_are_recomputed_when_the_text_changes(world):
    act = kit.make_act(world.run, text="Short.")
    act.text = 'Now it is longer, and quotes "someone", right?'
    act.save()
    act.refresh_from_db()
    assert act.char_len == len(act.text) and act.word_count == 8
    assert act.is_question is True and act.quotes_participant is True


def test_char_len_counts_code_points(world):
    text = "café \U0001f600"
    act = kit.make_act(world.run, text=text)
    assert act.char_len == len(text) == 6


def test_a_question_mark_anywhere_counts(world):
    assert kit.make_act(world.run, order=1, text="Is this so? Say more.").is_question is True
    assert kit.make_act(world.run, order=2, text="No mark at all").is_question is False


def test_features_are_fields_of_the_right_kind():
    from moderation.models import InterventionAct

    meta = InterventionAct._meta
    assert isinstance(meta.get_field("is_question"), models.BooleanField)
    assert isinstance(meta.get_field("quotes_participant"), models.BooleanField)
    assert isinstance(meta.get_field("char_len"), models.IntegerField)
    assert isinstance(meta.get_field("word_count"), models.IntegerField)


# --- sources -----------------------------------------------------------------------------------------------------------------------
def test_source_issues_of_the_same_run_can_be_linked(world):
    issue = kit.make_issue(world.run, world.first)
    act = kit.make_act(world.run)
    act.source_issues.add(issue)
    assert list(act.source_issues.all()) == [issue]


def test_source_issues_of_another_run_are_refused(world):
    other_run = kit.make_run(world.first)
    foreign = kit.make_issue(other_run, world.first)
    act = kit.make_act(world.run)
    with pytest.raises(ValidationError), transaction.atomic():
        act.source_issues.add(foreign)
    assert act.source_issues.count() == 0


def test_set_and_add_by_pk_are_guarded_too(world):
    other_run = kit.make_run(world.first)
    foreign = kit.make_issue(other_run, world.first)
    mine = kit.make_issue(world.run, world.first)
    act = kit.make_act(world.run)
    with pytest.raises(ValidationError), transaction.atomic():
        act.source_issues.set([mine, foreign])
    with pytest.raises(ValidationError), transaction.atomic():
        act.source_issues.add(foreign.pk)
    assert act.source_issues.count() == 0


def test_the_guard_also_works_from_the_issue_side(world):
    other_run = kit.make_run(world.first)
    foreign = kit.make_issue(other_run, world.first)
    act = kit.make_act(world.run)
    with pytest.raises(ValidationError), transaction.atomic():
        foreign.acts.add(act)


def test_source_messages_of_the_same_conversation_can_be_linked(world):
    act = kit.make_act(world.run)
    act.source_messages.add(world.first, world.trigger, world.reply)
    assert act.source_messages.count() == 3


def test_source_messages_of_another_conversation_are_refused(world):
    other_conv, other_parts = kit.make_conversation()
    foreign = kit.user_msg(other_conv, other_parts)
    act = kit.make_act(world.run)
    with pytest.raises(ValidationError), transaction.atomic():
        act.source_messages.add(foreign)
    with pytest.raises(ValidationError), transaction.atomic():
        act.source_messages.set([world.first, foreign])
    assert act.source_messages.count() == 0


def test_acts_can_share_sources(world):
    issue = kit.make_issue(world.run, world.first)
    a = kit.make_act(world.run, order=1)
    b = kit.make_act(world.run, order=2)
    a.source_issues.add(issue)
    b.source_issues.add(issue)
    a.source_messages.add(world.first)
    b.source_messages.add(world.first)
    from moderation.models import InterventionAct

    assert set(InterventionAct.objects.filter(source_issues=issue)) == {a, b}


def test_protect_blocks_deleting_a_run_that_has_acts(world):
    from django.db.models import ProtectedError

    kit.make_act(world.run)
    with pytest.raises(ProtectedError), transaction.atomic():
        world.run.delete()


# --- contract clarifications: curly quotes, addressee/subject, rejected acts ----------------------------------------------------------
@pytest.mark.parametrize(
    "text,expected",
    [
        ("You wrote “green cheese” earlier.", True),
        ('You wrote "x" earlier.', True),
        ('Empty pair "" carries nothing.', False),
        ("Empty curly pair “” carries nothing.", False),
        ("Only an opening “ mark.", False),
        ("Single 'quotes' do not count.", False),
        ('Two spans "a" and "b".', True),
    ],
)
def test_quotes_participant_needs_a_double_quoted_span_with_content(world, text, expected):
    assert kit.make_act(world.run, text=text).quotes_participant is expected


@pytest.mark.parametrize("addressee", ["A", "B", "Z", "all"])
def test_valid_addressees(world, addressee):
    assert kit.make_act(world.run, addressee=addressee).addressee == addressee


@pytest.mark.parametrize("addressee", ["", "a", "AB", "both", "none", "ALL", "1", " "])
def test_invalid_addressees_are_refused_at_save(world, addressee):
    with pytest.raises(ValidationError):
        kit.make_act(world.run, addressee=addressee)


@pytest.mark.parametrize("subject", ["A", "Q", "both", "none"])
def test_valid_subjects(world, subject):
    assert kit.make_act(world.run, subject=subject).subject == subject


@pytest.mark.parametrize("subject", ["", "a", "AB", "all", "None", "BOTH", "1"])
def test_invalid_subjects_are_refused_at_save(world, subject):
    with pytest.raises(ValidationError):
        kit.make_act(world.run, subject=subject)


def test_an_invalid_addressee_on_a_saved_act_is_refused_and_nothing_changes(world):
    act = kit.make_act(world.run, addressee="A")
    act.addressee = "everyone"
    with pytest.raises(ValidationError):
        act.save()
    act.refresh_from_db()
    assert act.addressee == "A"


def test_a_rejected_act_needs_a_reason_at_save_and_in_the_database(world):
    from moderation.models import InterventionAct

    for reason in ("", "   "):
        with pytest.raises(ValidationError):
            kit.make_act(world.run, validity="rejected", rejection_reason=reason)
    with pytest.raises(IntegrityError), transaction.atomic():
        InterventionAct.objects.bulk_create([kit.unsaved_act(world.run, validity="rejected", rejection_reason="")])
    ok = kit.make_act(world.run, validity="rejected", rejection_reason="cites a rejected issue")
    assert ok.rejection_reason == "cites a rejected issue"


def test_a_valid_act_needs_no_reason(world):
    assert kit.make_act(world.run, validity="valid", rejection_reason="").validity == "valid"


def test_the_related_names_of_the_contract(world):
    issue = kit.make_issue(world.run, world.first)
    act = kit.make_act(world.run)
    act.source_issues.add(issue)
    act.source_messages.add(world.first)
    assert list(world.run.acts.all()) == [act]
    assert list(world.run.issues.all()) == [issue]
    assert list(issue.acts.all()) == [act]
    assert list(world.first.acts_sourced.all()) == [act]
    assert list(world.trigger.moderation_runs.all()) == [world.run]
    assert list(world.conv.moderation_runs.all()) == [world.run]


def test_only_other_valid_acts_count_towards_the_cap_so_an_existing_act_can_change_kind(world, settings):
    settings.MAX_ACTS_PER_INTERVENTION = 2
    a = kit.make_act(world.run, order=1)
    kit.make_act(world.run, order=2)
    a.validity = "rejected"
    a.rejection_reason = "withdrawn"
    a.save()
    a.validity = "valid"
    a.rejection_reason = ""
    a.save()  # the other valid act count is 1, below the cap of 2
