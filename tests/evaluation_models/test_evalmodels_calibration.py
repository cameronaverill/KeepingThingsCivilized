"""evaluation.calibration.build_calibration_set (docs/step12_brief.md, plan section 9 step 1).

The population is built with bulk_create: synthetic conversations whose user messages carry planted phrases (dimension,
phrase, intensity) or none, on the "left" and "right" variant. Semantics the brief leaves open are pinned to the weakest
reading that still tests the promise: a stratified draw includes rare kinds (planted low and high, the rarer side) that a
plain random draw would usually miss; what counts as low or high is taken as the extremes (1 and 4)."""
import json
import random

import evalmodels_testkit as kit
import pytest
from django.conf import settings
from django.core.exceptions import ValidationError

from evaluation.calibration import CalibrationShortfall, build_calibration_set

pytestmark = pytest.mark.django_db

FA, AB, CL = "factual_accuracy", "abusiveness", "clarity"


def planted(dimension, intensity):
    return [{"dimension": dimension, "phrase": "PHRASE", "intensity": intensity}]


ISSUE_TYPE = {FA: "possible_factual_error", AB: "abusive_language", CL: "unclear_statement"}


def make_population(spec, per_conversation=10):
    """spec: list of (variant, planted_list) or (variant, planted_list, (dimension, intensity)); the third form is a
    message the Master flagged (a valid issue with that intensity, no planted phrase). One synthetic conversation per
    `per_conversation` messages, each conversation on one variant. Returns the created Messages in creation order."""
    from forum.models import Conversation, Message, Participant, Topic

    topic = Topic.objects.create(title=f"calib topic {kit.n()}", proposition="A proposition for calibration.")
    by_variant = {}
    for variant, items, *flag in spec:
        by_variant.setdefault(variant, []).append((items, flag[0] if flag else None))
    created = []
    for variant, all_items in by_variant.items():
        for chunk_start in range(0, len(all_items), per_conversation):
            conv = Conversation.objects.create(topic=topic, source="synthetic", variant=variant, pair_id=f"pair{kit.n()}")
            parts = [Participant.objects.create(conversation=conv, label=label, join_order=i) for i, label in enumerate("AB", 1)]
            rows, flags = [], []
            for offset, (items, flag) in enumerate(all_items[chunk_start : chunk_start + per_conversation], start=1):
                text = f"Message {kit.n()} in {variant} conversation, position {offset}, with some PHRASE inside."
                rows.append(
                    Message(
                        conversation=conv, seq_no=offset, author_type="user", participant=parts[offset % 2], content=text,
                        char_count=len(text), planted=items,
                    )
                )
                flags.append(flag)
            saved = Message.objects.bulk_create(rows)
            created.extend(saved)
            if any(flags):
                run = kit.make_run(Message.objects.get(conversation=conv, seq_no=len(saved)))
                for message, flag in zip(saved, flags):
                    if flag:
                        dimension, intensity = flag
                        kit.make_issue(run, message, issue_type=ISSUE_TYPE[dimension], intensity=intensity)
    return created


def rich_spec(dimension=AB):
    """360 messages: mostly no-issue; planted low (1) and high (4); flagged by the Master low (1) and high (4); about 3% of
    each kind on the right variant."""
    spec = []
    for kind, count in (("none", 240), ("pl", 30), ("ph", 30), ("fl", 30), ("fh", 30)):
        for i in range(count):
            variant = "right" if i % 33 == 0 else "left"
            if kind == "none":
                spec.append((variant, []))
            elif kind in ("pl", "ph"):
                spec.append((variant, planted(dimension, 1 if kind == "pl" else 4)))
            else:
                spec.append((variant, [], (dimension, 1 if kind == "fl" else 4)))
    return spec


def item_rows(cset):
    return list(kit.items_of(cset).order_by("order").values_list("target_type", "target_id", "stratum", "order"))


def messages_of(cset):
    from forum.models import Message

    ids = list(kit.items_of(cset).values_list("target_id", flat=True))
    return {m.pk: m for m in Message.objects.filter(pk__in=ids).select_related("conversation")}


def tail(label):
    """The stratum name without a 'dimension:' prefix, should the builder label items 'dimension:stratum'."""
    return label.split(":")[-1]


def has_issue(message):
    from moderation.models import Issue

    return Issue.objects.filter(message=message).exists()


# --- the result --------------------------------------------------------------------------------------------------------
def test_it_returns_a_saved_set_with_name_seed_and_items():
    from evaluation.models import CalibrationSet

    make_population(rich_spec())
    cset = build_calibration_set("first", 11, per_dimension=20, dimensions=(AB,))
    assert isinstance(cset, CalibrationSet) and cset.pk
    loaded = CalibrationSet.objects.get(pk=cset.pk)
    assert (loaded.name, loaded.seed) == ("first", 11)
    assert loaded.created_at is not None
    assert kit.items_of(cset).count() == 20


def test_every_item_points_at_an_existing_message():
    make_population(rich_spec())
    cset = build_calibration_set("exists", 3, per_dimension=20, dimensions=(AB,))
    rows = item_rows(cset)
    assert {r[0] for r in rows} == {"message"}
    assert len(messages_of(cset)) == 20


def test_only_user_messages_are_drawn():
    from forum.models import Message

    made = make_population(rich_spec())
    conv = made[0].conversation
    Message.objects.create(conversation=conv, author_type="moderator", participant=None, content="A moderator note. PHRASE", seq_no=999)
    cset = build_calibration_set("users only", 5, per_dimension=20, dimensions=(AB,))
    assert {m.author_type for m in messages_of(cset).values()} == {"user"}


def test_two_dimensions_draw_per_dimension_each_and_never_the_same_message_twice():
    make_population(rich_spec(FA) + rich_spec(AB))
    cset = build_calibration_set("two", 8, per_dimension=15, dimensions=(FA, AB))
    rows = item_rows(cset)
    assert len(rows) == 30
    assert len({(r[0], r[1]) for r in rows}) == 30


def test_the_default_dimensions_are_the_taxonomys():
    make_population(rich_spec(FA) + rich_spec(AB) + rich_spec(CL))
    cset = build_calibration_set("defaults", 8, per_dimension=10)
    assert kit.items_of(cset).count() == 30


# --- reproducibility ---------------------------------------------------------------------------------------------------
def test_the_same_seed_gives_the_same_set_in_the_same_order():
    make_population(rich_spec())
    first = build_calibration_set("run one", 77, per_dimension=20, dimensions=(AB,))
    second = build_calibration_set("run two", 77, per_dimension=20, dimensions=(AB,))
    assert item_rows(first) == item_rows(second)
    assert first.strata == second.strata


def test_the_same_seed_gives_the_same_set_for_two_dimensions():
    make_population(rich_spec(FA) + rich_spec(AB))
    first = build_calibration_set("a", 5, per_dimension=12, dimensions=(FA, AB))
    second = build_calibration_set("b", 5, per_dimension=12, dimensions=(FA, AB))
    assert item_rows(first) == item_rows(second)


def test_different_seeds_give_different_sets():
    make_population(rich_spec())
    rows = [item_rows(build_calibration_set(f"s{seed}", seed, per_dimension=20, dimensions=(AB,))) for seed in (1, 2, 3, 4)]
    orders = {tuple(r[1] for r in one) for one in rows}
    assert len(orders) == 4
    assert len({frozenset(r[1] for r in one) for one in rows}) > 1


def test_the_seed_is_stored_on_the_set():
    make_population(rich_spec())
    assert build_calibration_set("stored", 123456789012, per_dimension=10, dimensions=(AB,)).seed == 123456789012


def test_the_draw_does_not_depend_on_the_global_random_state():
    make_population(rich_spec())
    random.seed(1)
    first = item_rows(build_calibration_set("g1", 9, per_dimension=20, dimensions=(AB,)))
    random.seed(999)
    random.random()
    second = item_rows(build_calibration_set("g2", 9, per_dimension=20, dimensions=(AB,)))
    assert first == second


# --- strata ------------------------------------------------------------------------------------------------------------
def test_the_set_includes_no_issue_messages_and_planted_low_and_high_items_though_planted_ones_are_rare():
    make_population(rich_spec())
    for seed in range(1, 6):
        cset = build_calibration_set(f"strata{seed}", seed, per_dimension=20, dimensions=(AB,))
        drawn = list(messages_of(cset).values())
        found = [m.planted for m in drawn]
        assert any(m.planted == [] and not has_issue(m) for m in drawn), seed
        assert any(p and p[0]["intensity"] == 1 for p in found), seed
        assert any(p and p[0]["intensity"] == 4 for p in found), seed


def test_both_variants_are_represented_though_one_is_rare():
    make_population(rich_spec())
    for seed in range(1, 6):
        cset = build_calibration_set(f"sides{seed}", seed, per_dimension=20, dimensions=(AB,))
        variants = {m.conversation.variant for m in messages_of(cset).values()}
        assert variants == {"left", "right"}, seed


class TestPerStratumSideBalance:
    """The module docstring promises the draw is balanced across sides WITHIN a stratum. The pooled check above
    (test_both_variants_are_represented_though_one_is_rare) stays green even when one specific stratum draws every
    item from a single side, as long as some other stratum supplies the missing side. rich_spec's candidate pool has
    at least one message of each known side for every stratum whose default CALIBRATION_STRATUM_SHARES quota is
    nonzero, so each such stratum's own achieved draw, not just the pooled total, must show both sides."""

    def test_every_nonzero_quota_stratum_draws_from_both_sides_when_both_are_available(self):
        make_population(rich_spec())

        cset = build_calibration_set("per stratum sides", 2, per_dimension=20, dimensions=(AB,))

        quotas_used = {stratum for stratum, share in settings.CALIBRATION_STRATUM_SHARES.items() if share > 0}
        rows = messages_of(cset)
        sides_by_stratum = {}
        for label, message_id in kit.items_of(cset).values_list("stratum", "target_id"):
            sides_by_stratum.setdefault(tail(label), set()).add(rows[message_id].conversation.variant)

        assert sides_by_stratum == {stratum: {"left", "right"} for stratum in quotas_used}


def test_planted_items_of_each_dimension_appear_when_two_dimensions_are_asked_for():
    make_population(rich_spec(FA) + rich_spec(AB))
    for seed in range(1, 4):
        cset = build_calibration_set(f"dims{seed}", seed, per_dimension=20, dimensions=(FA, AB))
        dims = {p["dimension"] for m in messages_of(cset).values() for p in m.planted}
        assert dims == {FA, AB}, seed


def test_the_items_carry_a_stratum_label_and_the_set_reports_the_achieved_strata():
    make_population(rich_spec())
    cset = build_calibration_set("report", 4, per_dimension=20, dimensions=(AB,))
    labels = set(kit.items_of(cset).values_list("stratum", flat=True))
    assert len(labels) >= 3 and "" not in labels
    dumped = json.dumps(cset.strata)
    assert isinstance(cset.strata, dict) and cset.strata
    assert all(tail(label) in dumped for label in labels)
    cset.refresh_from_db()
    assert json.dumps(cset.strata) == dumped


def assert_strata_match_items(cset):
    """Somewhere in the report every stratum's achieved count is stored as a number equal to its items."""
    per_label = {}
    for label in kit.items_of(cset).values_list("stratum", flat=True):
        per_label[label] = per_label.get(label, 0) + 1

    def numbers_under(node, label):
        found = []
        if isinstance(node, dict):
            for key, value in node.items():
                if key == label:
                    found.extend(v for v in (value.values() if isinstance(value, dict) else [value]) if isinstance(v, int))
                found.extend(numbers_under(value, label))
        elif isinstance(node, list):
            for item in node:
                found.extend(numbers_under(item, label))
        return found

    for label, count in per_label.items():
        assert count in numbers_under(cset.strata, tail(label)), (label, count, cset.strata)


def test_the_reported_strata_account_for_every_drawn_item():
    make_population(rich_spec())
    assert_strata_match_items(build_calibration_set("counts", 4, per_dimension=20, dimensions=(AB,)))


def test_the_reported_strata_account_for_every_drawn_item_of_a_short_set_too():
    make_population([("left", [])] * 40 + [("right", planted(AB, 4))] * 6)
    assert_strata_match_items(build_calibration_set("short counts", 4, per_dimension=100, dimensions=(AB,), allow_short=True))


# --- no duplicates -----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_no_message_is_drawn_twice(seed):
    make_population(rich_spec(FA) + rich_spec(AB))
    cset = build_calibration_set(f"dup{seed}", seed, per_dimension=40, dimensions=(FA, AB))
    ids = [r[1] for r in item_rows(cset)]
    assert len(ids) == len(set(ids)) == 80


def test_a_message_planted_on_two_dimensions_is_drawn_once():
    both = [("left", [{"dimension": FA, "phrase": "PHRASE", "intensity": 4}, {"dimension": AB, "phrase": "PHRASE", "intensity": 4}])] * 12
    plain = [("left", [])] * 80 + [("right", [])] * 8 + [("left", planted(FA, 1))] * 12 + [("left", planted(AB, 1))] * 12
    make_population(both + plain)
    cset = build_calibration_set("shared", 6, per_dimension=20, dimensions=(FA, AB), allow_short=True)
    ids = [r[1] for r in item_rows(cset)]
    assert len(ids) == len(set(ids))


# --- presentation order ------------------------------------------------------------------------------------------------
def test_order_is_stored_distinct_and_contiguous():
    make_population(rich_spec())
    cset = build_calibration_set("order", 2, per_dimension=20, dimensions=(AB,))
    orders = sorted(kit.items_of(cset).values_list("order", flat=True))
    assert len(set(orders)) == 20
    assert orders[-1] - orders[0] == 19


def test_the_presentation_order_is_shuffled_not_grouped_by_stratum_or_by_message_id():
    make_population(rich_spec())
    for seed in (1, 2, 3):
        rows = item_rows(build_calibration_set(f"shuffle{seed}", seed, per_dimension=20, dimensions=(AB,)))
        strata_in_order = [r[2] for r in rows]
        switches = sum(1 for a, b in zip(strata_in_order, strata_in_order[1:]) if a != b)
        assert switches > len(set(strata_in_order)), seed
        ids_in_order = [r[1] for r in rows]
        assert ids_in_order != sorted(ids_in_order), seed


# --- shortfall ---------------------------------------------------------------------------------------------------------
def refuses(**call):
    from evaluation.models import CalibrationItem, CalibrationSet

    with pytest.raises(CalibrationShortfall):
        build_calibration_set(**call)
    assert CalibrationSet.objects.count() == 0 and CalibrationItem.objects.count() == 0


def test_it_refuses_when_there_are_too_few_messages():
    make_population([("left", [])] * 6 + [("right", planted(AB, 4))] * 2)
    refuses(name="short", seed=1, per_dimension=100, dimensions=(AB,))


def test_it_refuses_when_no_planted_message_exists():
    make_population([("left", [])] * 150 + [("right", [])] * 20)
    refuses(name="no planted", seed=1, per_dimension=20, dimensions=(AB,))


def test_it_refuses_when_every_message_is_planted_so_no_no_issue_message_exists():
    make_population([("left", planted(AB, 1))] * 80 + [("right", planted(AB, 4))] * 80)
    refuses(name="all planted", seed=1, per_dimension=20, dimensions=(AB,))


def test_it_refuses_when_there_are_no_messages_at_all():
    refuses(name="empty", seed=1, per_dimension=10, dimensions=(AB,))


def test_allow_short_returns_what_exists_and_records_the_shortfall():
    made = make_population([("left", [])] * 6 + [("right", planted(AB, 4))] * 2)
    cset = build_calibration_set("short ok", 1, per_dimension=100, dimensions=(AB,), allow_short=True)
    rows = item_rows(cset)
    assert 0 < len(rows) <= len(made)
    assert len({r[1] for r in rows}) == len(rows)
    assert "short" in json.dumps(cset.strata).lower()
    cset.refresh_from_db()
    assert "short" in json.dumps(cset.strata).lower()


def test_allow_short_still_stores_a_random_order_and_the_seed():
    make_population([("left", [])] * 40 + [("right", planted(AB, 4))] * 6)
    first = build_calibration_set("s1", 31, per_dimension=100, dimensions=(AB,), allow_short=True)
    second = build_calibration_set("s2", 31, per_dimension=100, dimensions=(AB,), allow_short=True)
    assert first.seed == 31 and item_rows(first) == item_rows(second)
    orders = sorted(r[3] for r in item_rows(first))
    assert 0 < len(orders) <= 46 and len(set(orders)) == len(orders)


def test_a_missing_planted_stratum_is_reported_as_a_shortfall_with_allow_short():
    make_population([("left", [])] * 150 + [("right", [])] * 20)
    cset = build_calibration_set("no planted ok", 1, per_dimension=20, dimensions=(AB,), allow_short=True)
    assert "short" in json.dumps(cset.strata).lower()
    assert kit.items_of(cset).count() > 0


def test_a_full_draw_needs_no_allow_short():
    make_population(rich_spec())
    assert kit.items_of(build_calibration_set("full", 1, per_dimension=20, dimensions=(AB,))).count() == 20


def test_a_refused_build_does_not_leave_a_half_written_set():
    from evaluation.models import CalibrationSet

    make_population([("left", [])] * 6)
    with pytest.raises(CalibrationShortfall):
        build_calibration_set("half", 1, per_dimension=100, dimensions=(AB,))
    assert not CalibrationSet.objects.filter(name="half").exists()


def test_an_unknown_dimension_is_refused_even_with_allow_short():
    from evaluation.models import CalibrationSet

    make_population(rich_spec())
    with pytest.raises((ValueError, ValidationError, KeyError)):
        build_calibration_set("bad dim", 1, per_dimension=10, dimensions=("sarcasm",), allow_short=True)
    assert CalibrationSet.objects.count() == 0


def test_the_draw_is_unaffected_by_a_second_calibration_set_already_existing():
    """Building one set does not consume messages: another set may draw the same ones."""
    make_population(rich_spec())
    first = build_calibration_set("early", 5, per_dimension=20, dimensions=(AB,))
    second = build_calibration_set("late", 5, per_dimension=20, dimensions=(AB,))
    assert item_rows(first) == item_rows(second)


# --- architect rulings: default shares, zero-quota strata, CalibrationShortfall -------------------------------------------
def test_a_purely_synthetic_corpus_without_master_issues_can_be_filled():
    """The brief names the strata as no-issue messages, low and high PLANTED intensities, planted items and both sides.
    A corpus of synthetic messages that no Master ever saw (so no Issue rows) has all of those, so it must not need
    allow_short."""
    spec = []
    for kind, count in (("none", 240), ("pl", 30), ("ph", 30)):
        for i in range(count):
            variant = "right" if i % 33 == 0 else "left"
            spec.append((variant, [] if kind == "none" else planted(AB, 1 if kind == "pl" else 4)))
    make_population(spec)
    cset = build_calibration_set("synthetic only", 1, per_dimension=20, dimensions=(AB,))
    assert kit.items_of(cset).count() == 20


def test_planted_phrases_of_another_dimension_do_not_fill_the_planted_stratum():
    """Only factual_accuracy phrases are planted, so an abusiveness set has no planted item to draw."""
    make_population([("left", [])] * 200 + [("left", planted(FA, 1))] * 30 + [("right", planted(FA, 4))] * 30)
    refuses(name="wrong dimension", seed=1, per_dimension=20, dimensions=(AB,))


def test_the_presentation_order_depends_on_the_seed_even_when_the_same_messages_are_drawn():
    """A corpus so small that every message is drawn whatever the seed: only the order can differ."""
    make_population([("left", [])] * 6 + [("right", [])] * 4 + [("left", planted(AB, 1))] * 3 + [("right", planted(AB, 4))] * 3)
    sets = [build_calibration_set(f"small{seed}", seed, per_dimension=100, dimensions=(AB,), allow_short=True) for seed in (1, 2, 3, 4)]
    rows = [item_rows(one) for one in sets]
    assert len({frozenset(r[1] for r in one) for one in rows}) == 1
    assert len({tuple(r[1] for r in one) for one in rows}) > 1


def test_the_shortfall_error_is_a_value_error_and_carries_the_report():
    make_population([("left", [])] * 6)
    with pytest.raises(ValueError) as caught:
        build_calibration_set("carry", 1, per_dimension=100, dimensions=(AB,))
    assert isinstance(caught.value, CalibrationShortfall)
    assert isinstance(caught.value.strata, dict) and "short" in json.dumps(caught.value.strata).lower()


def test_the_default_shares_are_forty_thirty_thirty_and_nothing_for_flagged_messages():
    from django.conf import settings

    assert settings.CALIBRATION_STRATUM_SHARES == {"no_issue": 0.4, "planted_low": 0.3, "planted_high": 0.3, "flagged_low": 0.0, "flagged_high": 0.0}


def test_a_set_of_twenty_is_eight_no_issue_six_planted_low_and_six_planted_high_and_reports_no_zero_quota_strata():
    make_population(rich_spec())
    cset = build_calibration_set("shares", 2, per_dimension=20, dimensions=(AB,))
    counts = {}
    for label in kit.items_of(cset).values_list("stratum", flat=True):
        counts[tail(label)] = counts.get(tail(label), 0) + 1
    assert counts == {"no_issue": 8, "planted_low": 6, "planted_high": 6}
    assert "flagged" not in json.dumps(cset.strata)


def test_no_master_run_is_needed_to_build_a_set():
    """Flagged strata have no quota by default, so a corpus no Master ever saw fills the set."""
    from moderation.models import Issue

    spec = [("left", []) if i % 3 else ("left", planted(AB, 1 + 3 * (i % 2))) for i in range(200)]
    make_population(spec + [("right", planted(AB, 1))] * 20 + [("right", planted(AB, 4))] * 20)
    assert Issue.objects.count() == 0
    assert kit.items_of(build_calibration_set("no master", 1, per_dimension=20, dimensions=(AB,))).count() == 20
