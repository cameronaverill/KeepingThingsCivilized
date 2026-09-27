"""Step 9a: the queries are not N+1 (brief 9a): the number of database queries does not grow with the number of messages,
runs, issues, acts or calls. The absolute ceilings are generous; the point of each test is that the count is the same for a
small and a large conversation."""
import exports_kit as kit
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

pytestmark = pytest.mark.django_db

BUNDLE_CEILING = 30
LISTING_CEILING = 20
FLAG_SETS = [
    pytest.param(dict(), id="default"),
    pytest.param(dict(include_identities=True, include_raw=True), id="identities+raw"),
]


def count_queries(fn, *args, **kwargs):
    with CaptureQueriesContext(connection) as context:
        result = fn(*args, **kwargs)
    return len(context.captured_queries), result


class TestBundleQueryCount:
    @pytest.mark.parametrize("flags", FLAG_SETS)
    def test_the_count_does_not_grow_with_messages_and_runs(self, db, flags):
        from moderation.queries import get_conversation_bundle

        sizes = [(2, 1), (12, 6), (40, 20)]
        convs = [kit.scaled(messages, runs) for messages, runs in sizes]
        counts, bundles = zip(*(count_queries(get_conversation_bundle, c.pk, **flags) for c in convs))
        assert [len(b["messages"]) for b in bundles] == [2, 12, 40]
        assert [len(b["runs"]) for b in bundles] == [1, 6, 20]
        assert [len(r["llm_calls"]) for r in bundles[2]["runs"]] == [2] * 20
        assert len(set(counts)) == 1, counts
        assert counts[0] <= BUNDLE_CEILING

    def test_identities_add_no_query_per_participant(self, db):
        from moderation.queries import get_conversation_bundle

        conv = kit.scaled(6, 3)
        plain, _ = count_queries(get_conversation_bundle, conv.pk)
        with_identities, bundle = count_queries(get_conversation_bundle, conv.pk, include_identities=True)
        assert [p["username"] is not None for p in bundle["participants"]] == [True, True]
        assert with_identities <= plain + 1

    def test_the_rich_fixture_is_within_the_ceiling(self, world):
        from moderation.queries import get_conversation_bundle

        count, bundle = count_queries(get_conversation_bundle, world.h.pk, include_identities=True, include_raw=True)
        assert len(bundle["runs"]) == 6
        assert count <= BUNDLE_CEILING

    def test_export_text_costs_no_more_queries_than_the_bundle(self, db):
        from moderation.queries import export_conversation, get_conversation_bundle

        conv = kit.scaled(20, 10)
        bundle_count, _ = count_queries(get_conversation_bundle, conv.pk)
        export_count, text = count_queries(export_conversation, conv.pk)
        assert export_count == bundle_count
        assert text.endswith("\n")

    def test_many_ledger_rows_per_run_do_not_add_queries(self, db):
        from moderation.queries import get_conversation_bundle

        conv = kit.scaled(6, 3)
        before, _ = count_queries(get_conversation_bundle, conv.pk)
        run = conv.moderation_runs.order_by("pk").first()
        for attempt in range(2, 30):
            kit.make_call(run, conv, cost="0.000100", attempt=attempt)
        after, bundle = count_queries(get_conversation_bundle, conv.pk)
        assert len(bundle["runs"][0]["llm_calls"]) == 30
        assert after == before


class TestListingQueryCount:
    def test_the_count_does_not_grow_with_the_number_of_conversations(self, db):
        from moderation.queries import list_conversations

        for _ in range(3):
            kit.light(run_statuses=["done", "failed"], cost="0.010000", moderator_messages=1)
        small, rows_small = count_queries(list_conversations)
        for _ in range(27):
            kit.light(run_statuses=["done", "skipped_budget"], cost="0.010000", moderator_messages=2, user_messages=4)
        large, rows_large = count_queries(list_conversations)
        assert (len(rows_small), len(rows_large)) == (3, 30)
        assert large == small
        assert large <= LISTING_CEILING

    def test_filters_do_not_add_queries_per_row(self, db):
        from moderation.queries import list_conversations

        for _ in range(10):
            kit.light(source="synthetic", status="closed", run_statuses=["done"], cost="0.001000")
        base, _ = count_queries(list_conversations)
        filtered, rows = count_queries(list_conversations, source="synthetic", status="closed", has_runs=True, limit=8)
        assert len(rows) == 8
        assert filtered <= base
        assert filtered <= LISTING_CEILING
