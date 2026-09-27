"""The per-participant cap on checks (PREVIEW_MAX_CHECKS_PER_MINUTE, default 6, a 60 second window): the boundary, the window
rolling, that it is per participant, and that a refused check costs nothing."""
from datetime import datetime, timedelta, timezone

import preview_kit as pk
import pytest
from django.utils import timezone as dj_timezone

pytestmark = pytest.mark.django_db

T0 = datetime(2026, 5, 4, 10, 0, 0, tzinfo=timezone.utc)


def six_checks(fake, w, who="B", at=None):
    fake(*[pk.quiet_master() for _ in range(6)])
    return [pk.check(w, who, f"Draft number {i}.", now=at)[1] for i in range(6)]


class TestTheCap:
    def test_the_defaults(self):
        from config import tunables

        assert tunables.PREVIEW_MAX_CHECKS_PER_MINUTE == 6
        assert tunables.PREVIEW_REUSE_SECONDS == 900

    def test_six_checks_in_a_minute_are_allowed_and_the_seventh_is_refused_without_a_call(self, fake):
        w = pk.world()
        first_six = six_checks(fake, w)
        assert [c.outcome for c in first_six] == ["no_concern"] * 6
        client = fake()  # any call would raise
        calls_before, counts_before = pk.total_calls(), pk.counts()
        returned, seventh = pk.check(w, "B", "Draft number 7.")
        assert (returned.outcome, returned.unavailable_reason) == ("unavailable", "rate_limited")
        assert (seventh.outcome, seventh.unavailable_reason, seventh.mode) == ("unavailable", "rate_limited", "on")
        assert (seventh.note_texts, seventh.master_output, seventh.intervenor_output, seventh.llm_call_ids) == ([], None, None, [])
        assert client.calls == []
        assert pk.total_calls() == calls_before
        assert pk.counts() == counts_before

    def test_a_refused_check_still_records_the_draft(self, fake):
        w = pk.world()
        six_checks(fake, w)
        fake()
        seventh = pk.check(w, "B", "Draft number 7.")[1]
        assert (seventh.draft_text, seventh.draft_sha256, seventh.participant_id) == (
            "Draft number 7.", pk.sha("Draft number 7."), w.parts["B"].pk
        )

    def test_the_tunable_sets_the_cap(self, fake, tune):
        tune(PREVIEW_MAX_CHECKS_PER_MINUTE=2)
        w = pk.world()
        fake(pk.quiet_master(), pk.quiet_master())
        assert [pk.check(w, "B", f"Draft {i}.")[1].outcome for i in range(2)] == ["no_concern", "no_concern"]
        fake()
        assert pk.check(w, "B", "Draft 3.")[1].unavailable_reason == "rate_limited"

    def test_the_cap_is_per_participant(self, fake):
        w = pk.world()
        six_checks(fake, w, "B")
        fake(pk.quiet_master())
        other = pk.check(w, "A", "The other one writes.")[1]
        assert other.outcome == "no_concern"

    def test_the_cap_is_per_participant_of_different_conversations_too(self, fake):
        w1, w2 = pk.world(), pk.world()
        six_checks(fake, w1, "B")
        fake(pk.quiet_master())
        assert pk.check(w2, "B", "Somewhere else.")[1].outcome == "no_concern"

    def test_failed_checks_count_toward_the_cap(self, fake, settings):
        settings.LLM_ENABLED = False
        w = pk.world()
        fake()
        reasons = [pk.check(w, "B", f"Draft {i}.")[1].unavailable_reason for i in range(7)]
        assert reasons == ["llm_disabled"] * 6 + ["rate_limited"]


class TestTheWindowRolls:
    def test_checks_older_than_a_minute_do_not_count(self, fake):
        from moderation.models import PreviewCheck

        w = pk.world()
        six_checks(fake, w)
        PreviewCheck.objects.update(created_at=dj_timezone.now() - timedelta(seconds=61))
        fake(pk.quiet_master())
        assert pk.check(w, "B", "A later draft.")[1].outcome == "no_concern"

    def test_checks_younger_than_a_minute_still_count(self, fake):
        from moderation.models import PreviewCheck

        w = pk.world()
        six_checks(fake, w)
        PreviewCheck.objects.update(created_at=dj_timezone.now() - timedelta(seconds=55))
        fake()
        assert pk.check(w, "B", "A later draft.")[1].unavailable_reason == "rate_limited"

    def test_the_window_is_measured_from_the_time_passed_in(self, fake):
        w = pk.world()
        first_six = six_checks(fake, w, at=T0)
        assert [c.created_at for c in first_six] == [T0] * 6
        fake()
        refused = pk.check(w, "B", "Too soon.", now=T0 + timedelta(seconds=30))[1]
        assert refused.unavailable_reason == "rate_limited"
        fake(pk.quiet_master())
        allowed = pk.check(w, "B", "After the window.", now=T0 + timedelta(seconds=61))[1]
        assert allowed.outcome == "no_concern"

    def test_one_slot_frees_up_when_the_oldest_check_leaves_the_window(self, fake):
        w = pk.world()
        fake(*[pk.quiet_master() for _ in range(6)])
        for i in range(6):
            pk.check(w, "B", f"Draft {i}.", now=T0 + timedelta(seconds=10 * i))
        fake(pk.quiet_master())
        at_65 = pk.check(w, "B", "Just after the first left.", now=T0 + timedelta(seconds=65))[1]
        assert at_65.outcome == "no_concern"
        fake()
        at_66 = pk.check(w, "B", "Full again.", now=T0 + timedelta(seconds=66))[1]
        assert at_66.unavailable_reason == "rate_limited"
