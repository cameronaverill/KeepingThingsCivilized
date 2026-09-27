"""Step 9a: `list_conversations(...)` (brief 9a): flat rows, newest first, filters that combine with AND, ValueError for
unknown filter values. Rows are checked against hand-written numbers, not recomputed from the database."""
import json
from decimal import Decimal
from types import SimpleNamespace

import exports_kit as kit
import pytest

pytestmark = pytest.mark.django_db

STATUSES = ("pending", "running", "done", "failed", "skipped_budget", "skipped_disabled")


def ids(rows):
    return [row["id"] for row in rows]


def run_counts(row):
    return {status: row["runs"].get(status, 0) for status in STATUSES}


def listing_fn():
    from moderation.queries import list_conversations

    return list_conversations


@pytest.fixture
def lw(db):
    """Six small conversations, created (by `created_at`) in the order c1 .. c6."""
    from forum.models import Experiment

    obs = Experiment.objects.create(name="obs-x", kind="observational")
    paired = Experiment.objects.create(name="pair-x", kind="paired")
    series = Experiment.objects.create(name="series-x", kind="series")
    shared_topic = kit.make_topic("A shared proposition for the pair.")
    w = SimpleNamespace(obs=obs, paired=paired, series=series, shared_topic=shared_topic)
    w.c1 = kit.light(source="human", status="open", created=kit.at(10)).conv
    w.c2 = kit.light(source="human", status="active", experiment=obs, created=kit.at(20), user_messages=3,
                     run_statuses=["done", "failed"]).conv
    w.c3 = kit.light(source="synthetic", status="closed", experiment=paired, created=kit.at(30), pair_id="p1",
                     variant="left", topic=shared_topic, run_statuses=["done"], cost="0.010000").conv
    w.c4 = kit.light(source="synthetic", status="closed", experiment=paired, created=kit.at(40), pair_id="p1",
                     variant="right", topic=shared_topic, user_messages=4,
                     run_statuses=["done", "skipped_budget", "skipped_disabled"], cost="0.020000").conv
    w.c5 = kit.light(source="human", status="closed", created=kit.at(50), moderator_messages=2, run_statuses=["done"],
                     cost="0.005000").conv
    w.c6 = kit.light(source="synthetic", status="open", experiment=series, created=kit.at(60)).conv
    return w


class TestOrderAndShape:
    def test_newest_first(self, lw):
        assert ids(listing_fn()()) == [lw.c6.pk, lw.c5.pk, lw.c4.pk, lw.c3.pk, lw.c2.pk, lw.c1.pk]

    def test_an_empty_database_lists_nothing(self, db):
        assert listing_fn()() == []

    def test_the_rows_are_plain_json_data(self, lw):
        rows = listing_fn()()
        assert [r["id"] for r in json.loads(json.dumps(rows))] == ids(rows)

    def test_a_row_of_a_synthetic_paired_conversation(self, lw):
        row = next(r for r in listing_fn()() if r["id"] == lw.c4.pk)
        assert row["proposition"] == "A shared proposition for the pair."
        assert (row["source"], row["status"], row["experiment"], row["pair_id"], row["variant"]) == (
            "synthetic", "closed", "pair-x", "p1", "right",
        )
        assert (row["user_messages"], row["moderator_messages"]) == (4, 0)
        assert run_counts(row) == {"pending": 0, "running": 0, "done": 1, "failed": 0, "skipped_budget": 1, "skipped_disabled": 1}
        assert kit.canon(row)["cost_usd"] == Decimal("0.020000")
        assert kit.canon(row)["created_at"] == kit.at(40)

    def test_a_row_with_moderator_messages_and_no_experiment(self, lw):
        row = next(r for r in listing_fn()() if r["id"] == lw.c5.pk)
        assert (row["experiment"], row["user_messages"], row["moderator_messages"]) == (None, 2, 2)
        assert run_counts(row)["done"] == 1
        assert kit.canon(row)["cost_usd"] == Decimal("0.005000")

    def test_a_conversation_with_no_runs_has_zero_counts_and_zero_cost(self, lw):
        row = next(r for r in listing_fn()() if r["id"] == lw.c1.pk)
        assert run_counts(row) == dict.fromkeys(STATUSES, 0)
        assert kit.canon(row)["cost_usd"] == Decimal("0.000000")

    def test_the_run_counts_add_up(self, lw):
        rows = {r["id"]: r for r in listing_fn()()}
        assert sum(run_counts(rows[lw.c2.pk]).values()) == 2
        assert run_counts(rows[lw.c2.pk])["failed"] == 1

    def test_counts_and_costs_of_the_rich_fixture(self, world):
        rows = {r["id"]: r for r in listing_fn()()}
        h, s, e = rows[world.h.pk], rows[world.s.pk], rows[world.e.pk]
        assert (h["user_messages"], h["moderator_messages"]) == (7, 2)
        assert run_counts(h) == {"pending": 0, "running": 0, "done": 4, "failed": 1, "skipped_budget": 1, "skipped_disabled": 0}
        assert kit.canon(h)["cost_usd"] == kit.H_TOTAL_COST
        assert h["experiment"] == "observational-2026"
        assert (s["user_messages"], s["moderator_messages"], s["experiment"], s["pair_id"], s["variant"]) == (
            4, 0, "paired-rent-set", "rent-1", "left",
        )
        assert run_counts(s) == {"pending": 0, "running": 0, "done": 2, "failed": 1, "skipped_budget": 0, "skipped_disabled": 0}
        assert kit.canon(s)["cost_usd"] == kit.S_TOTAL_COST
        assert (e["user_messages"], e["moderator_messages"], sum(run_counts(e).values())) == (0, 0, 0)

    def test_the_listing_cost_agrees_with_the_bundle_total(self, world):
        rows = {r["id"]: r for r in listing_fn()()}
        convs = (world.h, world.s, world.e)
        assert [kit.canon(rows[c.pk])["cost_usd"] for c in convs] == [kit.canon(kit.bundle(c))["cost_usd"] for c in convs]

    def test_the_proposition_is_the_topics_text(self, world):
        rows = {r["id"]: r for r in listing_fn()()}
        assert rows[world.h.pk]["proposition"] == "Cities should ban cars from downtown centres."
        assert rows[world.s.pk]["proposition"] == "Cities should cap annual rent increases."


class TestSparseRunCounts:
    """Pinned by the architect's amendment: `runs` is a sparse dict of counts by status; a missing key reads 0."""

    def test_a_missing_status_reads_zero_by_subscript(self, lw):
        row = next(r for r in listing_fn()() if r["id"] == lw.c2.pk)
        assert (row["runs"]["done"], row["runs"]["failed"], row["runs"]["pending"], row["runs"]["skipped_budget"]) == (
            1, 1, 0, 0,
        )

    def test_a_conversation_without_runs_reads_zero_everywhere(self, lw):
        row = next(r for r in listing_fn()() if r["id"] == lw.c1.pk)
        assert [row["runs"][status] for status in STATUSES] == [0] * 6

    def test_the_counts_survive_json(self, lw):
        row = next(r for r in listing_fn()() if r["id"] == lw.c4.pk)
        assert json.loads(json.dumps(row))["runs"] == {"done": 1, "skipped_budget": 1, "skipped_disabled": 1}

    def test_created_at_is_iso_8601_text(self, lw):
        stamps = [r["created_at"] for r in listing_fn()()]
        assert [x for x in stamps if not (isinstance(x, str) and kit.ISO_8601_RE.match(x))] == []
        assert len(stamps) == 6


class TestFilters:
    def test_source(self, lw):
        assert ids(listing_fn()(source="human")) == [lw.c5.pk, lw.c2.pk, lw.c1.pk]
        assert ids(listing_fn()(source="synthetic")) == [lw.c6.pk, lw.c4.pk, lw.c3.pk]

    def test_status(self, lw):
        assert ids(listing_fn()(status="closed")) == [lw.c5.pk, lw.c4.pk, lw.c3.pk]
        assert ids(listing_fn()(status="open")) == [lw.c6.pk, lw.c1.pk]
        assert ids(listing_fn()(status="active")) == [lw.c2.pk]

    def test_experiment_by_name(self, lw):
        assert ids(listing_fn()(experiment="pair-x")) == [lw.c4.pk, lw.c3.pk]
        assert ids(listing_fn()(experiment="obs-x")) == [lw.c2.pk]
        assert ids(listing_fn()(experiment="series-x")) == [lw.c6.pk]

    def test_topic_id(self, lw):
        assert ids(listing_fn()(topic_id=lw.shared_topic.pk)) == [lw.c4.pk, lw.c3.pk]
        assert ids(listing_fn()(topic_id=lw.c1.topic_id)) == [lw.c1.pk]

    def test_has_runs(self, lw):
        assert ids(listing_fn()(has_runs=True)) == [lw.c5.pk, lw.c4.pk, lw.c3.pk, lw.c2.pk]
        assert ids(listing_fn()(has_runs=False)) == [lw.c6.pk, lw.c1.pk]

    def test_since_and_until_between_creation_times(self, lw):
        assert ids(listing_fn()(since=kit.at(25))) == [lw.c6.pk, lw.c5.pk, lw.c4.pk, lw.c3.pk]
        assert ids(listing_fn()(until=kit.at(35))) == [lw.c3.pk, lw.c2.pk, lw.c1.pk]
        assert ids(listing_fn()(since=kit.at(15), until=kit.at(45))) == [lw.c4.pk, lw.c3.pk, lw.c2.pk]

    def test_since_and_until_are_inclusive_at_the_exact_moment(self, lw):
        # Pinned by the architect's amendment (the brief does not say).
        assert ids(listing_fn()(since=kit.at(30), until=kit.at(30))) == [lw.c3.pk]

    def test_a_window_with_nothing_in_it(self, lw):
        assert listing_fn()(since=kit.at(61)) == []
        assert listing_fn()(until=kit.at(9)) == []
        assert listing_fn()(since=kit.at(45), until=kit.at(46)) == []

    def test_limit_keeps_the_newest(self, lw):
        assert ids(listing_fn()(limit=2)) == [lw.c6.pk, lw.c5.pk]
        assert ids(listing_fn()(limit=1)) == [lw.c6.pk]

    def test_a_limit_above_the_count_returns_everything(self, lw):
        assert len(listing_fn()(limit=50)) == 6

    def test_no_filter_and_none_filters_are_the_same(self, lw):
        every = listing_fn()(source=None, status=None, experiment=None, topic_id=None, has_runs=None, since=None,
                             until=None, limit=None)
        assert every == listing_fn()()


class TestFiltersCombineWithAnd:
    def test_source_and_status(self, lw):
        assert ids(listing_fn()(source="synthetic", status="closed")) == [lw.c4.pk, lw.c3.pk]
        assert ids(listing_fn()(source="human", status="closed")) == [lw.c5.pk]

    def test_source_status_and_has_runs(self, lw):
        assert ids(listing_fn()(source="human", status="closed", has_runs=True)) == [lw.c5.pk]
        assert ids(listing_fn()(source="human", status="open", has_runs=True)) == []
        assert ids(listing_fn()(source="synthetic", status="open", has_runs=False)) == [lw.c6.pk]

    def test_contradictory_filters_give_nothing(self, lw):
        assert listing_fn()(source="human", experiment="pair-x") == []
        assert listing_fn()(experiment="pair-x", status="open") == []
        assert listing_fn()(topic_id=lw.shared_topic.pk, source="human") == []

    def test_experiment_topic_and_time(self, lw):
        assert ids(listing_fn()(experiment="pair-x", topic_id=lw.shared_topic.pk, since=kit.at(35))) == [lw.c4.pk]

    def test_the_limit_applies_after_the_filters(self, lw):
        assert ids(listing_fn()(status="closed", limit=1)) == [lw.c5.pk]
        assert ids(listing_fn()(source="synthetic", status="closed", limit=1)) == [lw.c4.pk]
        assert ids(listing_fn()(source="human", limit=2)) == [lw.c5.pk, lw.c2.pk]

    def test_every_filter_at_once(self, lw):
        rows = listing_fn()(source="synthetic", status="closed", experiment="pair-x", topic_id=lw.shared_topic.pk,
                            has_runs=True, since=kit.at(5), until=kit.at(55), limit=1)
        assert ids(rows) == [lw.c4.pk]


class TestUnknownFilterValuesRaiseValueError:
    @pytest.mark.parametrize(
        "kwargs",
        [
            pytest.param(dict(source="robot"), id="source"),
            pytest.param(dict(status="archived"), id="status"),
            pytest.param(dict(experiment="no-such-experiment"), id="experiment"),
            pytest.param(dict(topic_id=999999), id="topic_id"),
        ],
    )
    def test_unknown_value(self, lw, kwargs):
        with pytest.raises(ValueError):
            listing_fn()(**kwargs)

    def test_an_unknown_value_is_refused_even_next_to_valid_filters(self, lw):
        with pytest.raises(ValueError):
            listing_fn()(source="human", status="archived")

    def test_case_matters_for_the_choice_values(self, lw):
        with pytest.raises(ValueError):
            listing_fn()(source="Human")

    def test_an_unknown_keyword_is_a_type_error(self, lw):
        with pytest.raises(TypeError):
            listing_fn()(colour="blue")

    def test_all_filters_are_keyword_only(self, lw):
        with pytest.raises(TypeError):
            listing_fn()("human")


class TestMalformedFilterValues:
    """Pinned by the architect's amendment (the brief only says "unknown filter values")."""

    @pytest.mark.parametrize(
        "kwargs",
        [
            pytest.param(dict(has_runs="yes"), id="has_runs-text"),
            pytest.param(dict(has_runs=1), id="has_runs-int"),
            pytest.param(dict(since="not a date"), id="since"),
            pytest.param(dict(until="not a date"), id="until"),
            pytest.param(dict(limit=0), id="limit-zero"),
            pytest.param(dict(limit=-3), id="limit-negative"),
            pytest.param(dict(limit="ten"), id="limit-text"),
        ],
    )
    def test_malformed_value(self, lw, kwargs):
        with pytest.raises(ValueError):
            listing_fn()(**kwargs)

    def test_a_naive_moment_counts_as_utc(self, lw):
        naive = kit.at(25).replace(tzinfo=None)
        assert ids(listing_fn()(since=naive)) == [lw.c6.pk, lw.c5.pk, lw.c4.pk, lw.c3.pk]
