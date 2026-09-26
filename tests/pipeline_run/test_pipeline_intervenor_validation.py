"""Validation of the Intervenor's output (plan section 6, brief "5b details" step 4): dispositions, acts, the act cap, the
no-naming rule, labels and sources."""
import pipeline_run_kit as kit
import pytest

pytestmark = pytest.mark.django_db


def two_issues(world):
    return [
        kit.issue_d("i1", world.last, "unsupported_claim", kit.QUOTE_1),
        kit.issue_d("i2", world.last, "fallacy", kit.QUOTE_2),
    ]


def go_with(fake, *, acts=(), dispositions=None, decision="intervene", extra_issues=(), world=None, rationale="Reason."):
    """A run over the default conversation: valid issues i1 and i2 (plus `extra_issues`), then the given Intervenor answer.
    `dispositions` defaults to acted for i1 and i2."""
    world = world or kit.build()
    run = kit.new_run(world.last)
    if dispositions is None:
        dispositions = [kit.disp_d("i1"), kit.disp_d("i2")]
    client = fake(
        kit.master_d(*two_issues(world), *extra_issues),
        kit.interv_d(decision, rationale, dispositions, list(acts)),
    )
    returned, stored = kit.go(run)
    return world, stored, client


def texts(n):
    return [f"Message {i} makes a claim that has no figure attached." for i in range(1, n + 1)]


class TestDispositions:
    def test_each_valid_issue_gets_exactly_one_disposition_as_the_intervenor_gave_it(self, fake):
        world, stored, _ = go_with(
            fake, dispositions=[kit.disp_d("i1", "acted", "Worth a note."), kit.disp_d("i2", "declined", "Minor.")],
            acts=[kit.act_d(issues=["i1"])],
        )
        assert [(i.local_id, i.disposition.disposition, i.disposition.reason) for i in kit.issues_of(stored)] == [
            ("i1", "acted", "Worth a note."), ("i2", "declined", "Minor."),
        ]  # fmt: skip

    def test_a_missing_disposition_is_created_as_declined_with_reason_no_disposition(self, fake):
        world, stored, _ = go_with(fake, dispositions=[kit.disp_d("i1", "acted", "Worth a note.")], acts=[kit.act_d(issues=["i1"])])
        first, second = kit.issues_of(stored)
        assert (first.disposition.disposition, first.disposition.reason) == ("acted", "Worth a note.")
        assert (second.disposition.disposition, second.disposition.reason) == ("declined", "no_disposition")
        assert stored.status == "done"

    def test_no_dispositions_at_all_gives_every_valid_issue_a_declined_no_disposition(self, fake):
        world, stored, _ = go_with(fake, dispositions=[], decision="no_intervention")
        assert [(i.disposition.disposition, i.disposition.reason) for i in kit.issues_of(stored)] == [
            ("declined", "no_disposition"), ("declined", "no_disposition"),
        ]  # fmt: skip

    def test_a_disposition_for_an_unknown_issue_is_ignored_and_logged_in_the_run_error(self, fake):
        from moderation.models import IssueDisposition

        world, stored, _ = go_with(
            fake, dispositions=[kit.disp_d("i1"), kit.disp_d("i2"), kit.disp_d("zz9")], acts=[kit.act_d(issues=["i1"])]
        )
        assert stored.status == "done"
        assert IssueDisposition.objects.filter(issue__run=stored).count() == 2
        assert "zz9" in stored.error

    def test_a_second_disposition_for_the_same_issue_is_ignored_and_logged(self, fake):
        from moderation.models import IssueDisposition

        world, stored, _ = go_with(
            fake,
            dispositions=[kit.disp_d("i1", "acted", "First."), kit.disp_d("i1", "declined", "Second."), kit.disp_d("i2")],
            acts=[kit.act_d(issues=["i1"])],
        )
        assert stored.status == "done"
        first = kit.issues_of(stored)[0]
        assert (first.disposition.disposition, first.disposition.reason) == ("acted", "First.")
        assert IssueDisposition.objects.filter(issue__run=stored).count() == 2
        assert "i1" in stored.error

    def test_a_disposition_for_a_rejected_issue_is_ignored_and_logged(self, fake):
        from moderation.models import IssueDisposition

        world = kit.build()
        rejected = kit.issue_d("r1", world.last, "fallacy", "zzz absent phrase")
        world, stored, _ = go_with(
            fake, world=world, extra_issues=[rejected],
            dispositions=[kit.disp_d("i1"), kit.disp_d("i2"), kit.disp_d("r1", "acted", "Hmm.")],
            acts=[kit.act_d(issues=["i1"])],
        )  # fmt: skip
        assert IssueDisposition.objects.filter(issue__run=stored, issue__validity="rejected").count() == 0
        assert IssueDisposition.objects.filter(issue__run=stored).count() == 2
        assert "r1" in stored.error

    def test_a_clean_run_has_an_empty_error(self, fake):
        world, stored, _ = go_with(fake, acts=[kit.act_d(issues=["i1"])])
        assert stored.error == ""


class TestActCap:
    def test_acts_beyond_the_cap_are_stored_rejected_with_reason_act_cap(self, fake):
        world, stored, _ = go_with(fake, acts=[kit.act_d(t) for t in texts(5)])
        assert kit.act_summary(stored) == [
            (1, "valid", ""), (2, "valid", ""), (3, "valid", ""), (4, "rejected", "act_cap"), (5, "rejected", "act_cap"),
        ]  # fmt: skip
        assert stored.decision == "intervene"
        assert stored.posted_message.content == "\n\n".join(texts(3))

    def test_the_cap_is_the_tunable(self, fake, tune):
        tune(MAX_ACTS_PER_INTERVENTION=1)
        world, stored, _ = go_with(fake, acts=[kit.act_d(t) for t in texts(2)])
        assert kit.act_summary(stored) == [(1, "valid", ""), (2, "rejected", "act_cap")]
        assert stored.posted_message.content == texts(2)[0]

    def test_exactly_the_cap_is_accepted(self, fake):
        world, stored, _ = go_with(fake, acts=[kit.act_d(t) for t in texts(3)])
        assert kit.act_summary(stored) == [(1, "valid", ""), (2, "valid", ""), (3, "valid", "")]

    def test_invalid_acts_do_not_use_up_the_cap(self, fake):
        acts = [kit.act_d("Participant B, please cite a source.")] + [kit.act_d(t) for t in texts(4)]
        world, stored, _ = go_with(fake, acts=acts)
        assert kit.act_summary(stored) == [
            (1, "rejected", "names_participant"), (2, "valid", ""), (3, "valid", ""), (4, "valid", ""), (5, "rejected", "act_cap"),
        ]  # fmt: skip
        assert stored.posted_message.content == "\n\n".join(texts(4)[:3])

    def test_the_order_of_a_stored_act_is_its_position_in_the_answer_rejected_ones_included(self, fake):
        acts = [kit.act_d(texts(1)[0]), kit.act_d(""), kit.act_d(texts(3)[2])]
        world, stored, _ = go_with(fake, acts=acts)
        assert [(a.order, a.text) for a in kit.acts_of(stored)] == [(1, texts(1)[0]), (2, ""), (3, texts(3)[2])]


class TestNamingRule:
    @pytest.mark.parametrize("text", kit.NAMING_TEXTS)
    def test_an_act_that_names_a_participant_is_rejected_while_a_clean_act_posts(self, fake, text):
        world, stored, _ = go_with(fake, acts=[kit.act_d(text), kit.act_d(kit.CLEAN_TEXT)])
        assert kit.act_summary(stored) == [(1, "rejected", "names_participant"), (2, "valid", "")]
        assert stored.decision == "intervene"
        assert stored.posted_message.content == kit.CLEAN_TEXT
        assert text not in stored.posted_message.content

    @pytest.mark.parametrize(
        "text",
        ["Option A is a plan B that nobody has costed.", "The letter \"B\" in message 2 is unclear.", "Both messages cite no source."],
    )
    def test_ordinary_text_with_bare_letters_is_not_flagged(self, fake, text):
        world, stored, _ = go_with(fake, acts=[kit.act_d(text)])
        assert kit.act_summary(stored) == [(1, "valid", "")]

    def test_when_every_act_is_rejected_the_stored_decision_is_no_intervention_and_the_rationale_is_kept(self, fake):
        acts = [kit.act_d("Participant A, please cite a source."), kit.act_d("")]
        world, stored, _ = go_with(fake, acts=acts, rationale="A note on sources is due.")
        before = kit.message_count(world.conv)
        assert stored.status == "done"
        assert stored.decision == "no_intervention"
        assert stored.rationale == "A note on sources is due."
        assert stored.failure_reason == ""
        assert stored.posted_message is None
        assert kit.message_count(world.conv) == before
        assert kit.act_summary(stored) == [(1, "rejected", "names_participant"), (2, "rejected", "empty_text")]
        assert all(i.disposition is not None for i in kit.issues_of(stored))


class TestActFields:
    @pytest.mark.parametrize("text", ["", "   ", "\n\t "])
    def test_empty_or_blank_text_is_rejected_as_empty_text(self, fake, text):
        world, stored, _ = go_with(fake, acts=[kit.act_d(text), kit.act_d(kit.CLEAN_TEXT)])
        assert kit.act_summary(stored) == [(1, "rejected", "empty_text"), (2, "valid", "")]
        assert stored.posted_message.content == kit.CLEAN_TEXT

    @pytest.mark.parametrize("addressee", ["Z", "both", "none", "a", "", "C"])
    def test_an_addressee_that_is_not_a_label_of_the_conversation_or_all_is_rejected(self, fake, addressee):
        world, stored, _ = go_with(fake, acts=[kit.act_d(addressee=addressee), kit.act_d(kit.CLEAN_TEXT_2)])
        assert kit.act_summary(stored) == [(1, "rejected", "bad_label"), (2, "valid", "")]
        assert stored.posted_message.content == kit.CLEAN_TEXT_2

    @pytest.mark.parametrize("subject", ["Z", "all", "b", "", "C"])
    def test_a_subject_that_is_not_a_label_both_or_none_is_rejected(self, fake, subject):
        world, stored, _ = go_with(fake, acts=[kit.act_d(subject=subject), kit.act_d(kit.CLEAN_TEXT_2)])
        assert kit.act_summary(stored) == [(1, "rejected", "bad_label"), (2, "valid", "")]

    @pytest.mark.parametrize("addressee, subject", [("A", "A"), ("B", "both"), ("all", "none"), ("all", "B"), ("B", "A")])
    def test_valid_label_combinations_are_kept_as_given(self, fake, addressee, subject):
        world, stored, _ = go_with(fake, acts=[kit.act_d(addressee=addressee, subject=subject)])
        (act,) = kit.acts_of(stored)
        assert (act.validity, act.addressee, act.subject) == ("valid", addressee, subject)


class TestActSources:
    def test_an_act_citing_a_rejected_issue_is_rejected(self, fake):
        world = kit.build()
        rejected = kit.issue_d("r1", world.last, "fallacy", "zzz absent phrase")
        world, stored, _ = go_with(
            fake, world=world, extra_issues=[rejected],
            acts=[kit.act_d(issues=["r1"]), kit.act_d(kit.CLEAN_TEXT_2, issues=["i1"])],
        )  # fmt: skip
        assert kit.act_summary(stored) == [(1, "rejected", "bad_source_issue"), (2, "valid", "")]

    def test_an_act_citing_an_unknown_issue_is_rejected(self, fake):
        world, stored, _ = go_with(fake, acts=[kit.act_d(issues=["nope"]), kit.act_d(kit.CLEAN_TEXT_2, issues=["i2"])])
        assert kit.act_summary(stored) == [(1, "rejected", "bad_source_issue"), (2, "valid", "")]

    def test_one_bad_source_among_good_ones_rejects_the_whole_act(self, fake):
        world, stored, _ = go_with(fake, acts=[kit.act_d(issues=["i1", "nope"])])
        assert kit.act_summary(stored) == [(1, "rejected", "bad_source_issue")]
        assert stored.decision == "no_intervention"

    def test_an_act_citing_valid_issues_records_them_as_its_sources(self, fake):
        world, stored, _ = go_with(fake, acts=[kit.act_d(issues=["i1", "i2"], messages=[])])
        (act,) = kit.acts_of(stored)
        assert sorted(i.local_id for i in act.source_issues.all()) == ["i1", "i2"]

    def test_an_act_with_no_sources_at_all_is_valid(self, fake):
        world, stored, _ = go_with(fake, acts=[kit.act_d(issues=[], messages=[])])
        assert kit.act_summary(stored) == [(1, "valid", "")]

    def test_an_act_citing_an_unknown_message_is_rejected(self, fake):
        world, stored, _ = go_with(fake, acts=[kit.act_d(messages=[10**9]), kit.act_d(kit.CLEAN_TEXT_2, messages=[])])
        assert kit.act_summary(stored) == [(1, "rejected", "bad_source_message"), (2, "valid", "")]

    def test_an_act_citing_a_message_of_another_conversation_is_rejected(self, fake):
        foreign = kit.other_world_message()
        world, stored, _ = go_with(fake, acts=[kit.act_d(messages=[foreign]), kit.act_d(kit.CLEAN_TEXT_2)])
        assert kit.act_summary(stored) == [(1, "rejected", "bad_source_message"), (2, "valid", "")]

    def test_an_act_citing_messages_of_the_conversation_records_them_as_its_sources(self, fake):
        world = kit.build()
        world, stored, _ = go_with(fake, world=world, acts=[kit.act_d(messages=[world[1], world[3]])])
        (act,) = kit.acts_of(stored)
        assert sorted(m.pk for m in act.source_messages.all()) == sorted([world[1].pk, world[3].pk])
