"""Step 18 audit, owner-approved bypass-regression test (docs/test_audit_plan.md).

forum.Message.seq_no has two halves to its rule: "unique per conversation" (a real UniqueConstraint, already covered
elsewhere, e.g. tests/models_forum/test_forum_message_db.py) and "increasing per conversation" (enforced only in
Message.save(), which compares the new seq_no against the current maximum). Only the first half is backed by the
database. This test does NOT prove monotonicity holds; it documents that bulk_create, which skips save(), can insert
a seq_no that goes backwards, with nothing raised. If this ever starts raising, a database constraint/trigger was
added to close the gap; update this test and its comment rather than treating the change as a regression to fix.

Unrelated to Topic.proposition; touches only forum.Message via bulk_create.
"""
import pytest

from forum_testkit import make_pair, post, raw_message

pytestmark = pytest.mark.django_db


class TestSeqNoMonotonicityHasNoDatabaseBacking:
    def test_bulk_create_bypasses_the_increasing_seq_no_rule_known_gap(self):
        from forum.models import Message

        conv, a, _ = make_pair()
        post(conv, a, content="first", seq_no=1)
        post(conv, a, content="second", seq_no=2)
        post(conv, a, content="third", seq_no=3)
        post(conv, a, content="tenth", seq_no=10)

        Message.objects.bulk_create([raw_message(conv, 5, "user", a, "out of order")])

        stored = Message.objects.get(conversation=conv, seq_no=5)
        assert stored.content == "out of order"
        assert list(Message.objects.filter(conversation=conv).order_by("id").values_list("seq_no", flat=True)) == [
            1,
            2,
            3,
            10,
            5,
        ]
