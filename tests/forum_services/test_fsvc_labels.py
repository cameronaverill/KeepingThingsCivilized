"""Labels: assigned at random when a conversation fills, the seed stored and able to reproduce the assignment, join order
recorded separately from the label (plan section 2, neutrality inputs)."""
import random

import pytest
from fsvc_testkit import enter  # noqa: E402

from fsvc_testkit import make_prop, make_user, svc

pytestmark = pytest.mark.django_db

N = 40


def fill_many(settings, n=N):
    """n conversations, each filled by the same two people on n different propositions. Returns a list of
    (conversation, participant with join_order 1, participant with join_order 2)."""
    settings.MAX_OPEN_CONVERSATIONS = 10_000
    first, second = make_user(), make_user()
    out = []
    for _ in range(n):
        topic = make_prop()
        conv = enter(first, topic)
        enter(second, topic)
        conv.refresh_from_db()
        p1, p2 = conv.participants.order_by("join_order")
        out.append((conv, p1, p2))
    return out


@pytest.fixture
def filled(settings):
    return fill_many(settings)


def test_every_filled_conversation_has_exactly_the_labels_a_and_b(filled):
    for conv, p1, p2 in filled:
        assert conv.status == "active"
        assert {p1.label, p2.label} == {"A", "B"}


def test_both_assignments_occur_and_neither_dominates(filled):
    first_is_a = sum(1 for _, p1, _ in filled if p1.label == "A")
    assert 8 <= first_is_a <= N - 8, first_is_a


def test_the_person_who_entered_first_always_has_join_order_one_whatever_their_label(filled):
    first_user_ids = {p1.user_id for _, p1, _ in filled}
    assert len(first_user_ids) == 1
    for _, p1, p2 in filled:
        assert (p1.join_order, p2.join_order) == (1, 2)
    labels_of_first = {p1.label for _, p1, _ in filled}
    assert labels_of_first == {"A", "B"}


def test_the_seed_is_stored_as_an_integer_and_is_fresh_each_time(filled):
    seeds = [conv.label_seed for conv, _, _ in filled]
    assert all(isinstance(seed, int) for seed in seeds)
    assert len(set(seeds)) > N // 2  # fresh randomness, not a constant or a tiny counter


# Ways a builder might turn random.Random(seed) into "which of the two gets A". The contract fixes that the assignment
# is reproducible from the stored seed with random.Random(seed), not which draw is used, so the test accepts the
# common ones and demands that ONE of them explains every stored conversation.
def _candidates():
    def shuffle_first(seed):
        labels = ["A", "B"]
        random.Random(seed).shuffle(labels)
        return labels[0]

    def shuffle_participants(seed):
        order = [1, 2]
        random.Random(seed).shuffle(order)
        return "A" if order[0] == 1 else "B"

    def choice_first(seed):
        return random.Random(seed).choice(["A", "B"])

    def random_below_half(seed):
        return "A" if random.Random(seed).random() < 0.5 else "B"

    def randint_zero(seed):
        return "A" if random.Random(seed).randint(0, 1) == 0 else "B"

    def randrange_zero(seed):
        return "A" if random.Random(seed).randrange(2) == 0 else "B"

    def getrandbits_zero(seed):
        return "A" if random.Random(seed).getrandbits(1) == 0 else "B"

    def sample_first(seed):
        return random.Random(seed).sample(["A", "B"], 2)[0]

    base = [shuffle_first, shuffle_participants, choice_first, random_below_half, randint_zero, randrange_zero,
            getrandbits_zero, sample_first]  # fmt: skip
    flipped = [(lambda f: (lambda seed: "B" if f(seed) == "A" else "A"))(f) for f in base]
    return base + flipped


def test_the_assignment_can_be_reproduced_from_the_stored_seed(filled):
    explains_all = [
        candidate
        for candidate in _candidates()
        if all(candidate(conv.label_seed) == p1.label for conv, p1, _ in filled)
    ]
    assert explains_all, (
        "no random.Random(label_seed) recipe among the common ones reproduces the stored labels; "
        "either the seed is not what decides the labels or the recipe is unusual (contract ambiguity)"
    )


def test_a_waiting_conversation_has_a_single_participant_and_nobody_is_told_a_label():
    """While waiting there is nothing to reproduce yet; the conversation just must not claim a full assignment."""
    conv = enter(make_user(), make_prop())
    conv.refresh_from_db()
    assert conv.participants.count() == 1
    assert conv.status == "open"


def test_labels_are_independent_of_who_the_users_are(settings):
    """Across many conversations between the SAME two people, each person is A about half the time."""
    settings.MAX_OPEN_CONVERSATIONS = 10_000
    first, second = make_user(), make_user()
    first_as_a = 0
    for _ in range(N):
        topic = make_prop()
        conv = enter(first, topic)
        enter(second, topic)
        if conv.participants.get(user=first).label == "A":
            first_as_a += 1
    assert 8 <= first_as_a <= N - 8, first_as_a


def test_labels_do_not_depend_on_the_order_of_entering_when_the_other_person_starts(settings):
    settings.MAX_OPEN_CONVERSATIONS = 10_000
    first, second = make_user(), make_user()
    second_as_a = 0
    for _ in range(N):
        topic = make_prop()
        enter(second, topic)
        conv = enter(first, topic)
        if conv.participants.get(user=second).label == "A":
            second_as_a += 1
        assert conv.participants.get(user=second).join_order == 1
        assert conv.participants.get(user=first).join_order == 2
    assert 8 <= second_as_a <= N - 8, second_as_a
