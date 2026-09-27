"""The warm-up folder loads with the replay machinery (both assignments) using only the fake LLM and the test database, and
nothing here changes golden/transcripts or the warm-up files (docs/warmup_brief.md, 'Checker's tests')."""
import json

import pytest
import warmup_kit as kit

NAME = "warmup-check"
NO_ISSUE = {"issues": [], "discussion_map": {"agreements": [], "disagreements": []}}
FILES = kit.load_folder()
N = len(FILES)
KNOWN = kit.known_of(FILES)
GOLDEN_HASH_AT_IMPORT = kit.folder_hash(kit.GOLDEN_DIR)
WARMUP_HASH_AT_IMPORT = kit.folder_hash(kit.WARMUP_DIR)


def table_counts():
    from forum.models import Conversation, Experiment, Message, Participant, Topic
    from moderation.models import Issue, LLMCall, ModerationRun

    return {
        model.__name__: model.objects.count()
        for model in (Experiment, Conversation, Participant, Message, Topic, ModerationRun, Issue, LLMCall)
    }


def load(**kwargs):
    from moderation import replay

    return replay.load_experiment(NAME, FILES, assignments="both", known=KNOWN, **kwargs)


def conversation_of(experiment, data, swapped=False):
    from forum.models import Conversation

    return Conversation.objects.get(experiment=experiment, transcript_id=data["id"] + (":swapped" if swapped else ""))


def labels_of(conversation):
    return [m.participant.label for m in conversation.messages.select_related("participant").order_by("seq_no")]


def test_the_test_database_is_not_the_dev_servers_sqlite_file(replay_environment):
    from django.db import connection

    assert not str(connection.settings_dict["NAME"]).endswith("db.sqlite3")


def test_both_assignments_of_every_transcript_make_two_synthetic_closed_conversations_each(replay_environment):
    from forum.models import Conversation

    load()
    rows = list(Conversation.objects.all())
    assert (len(rows), {c.source for c in rows}, {c.status for c in rows}) == (2 * N, {"synthetic"}, {"closed"})


def test_every_message_and_participant_is_stored_and_no_user_is_created(replay_environment):
    from django.contrib.auth import get_user_model

    from forum.models import Message, Participant

    load()
    assert (Message.objects.count(), Participant.objects.count(), get_user_model().objects.count()) == (
        2 * sum(len(d["messages"]) for _p, d in FILES), 4 * N, 0,
    )


def test_the_three_topics_are_created_once_with_the_seeded_propositions(replay_environment):
    from forum.models import Topic

    load()
    assert sorted((t.title, t.proposition) for t in Topic.objects.all()) == sorted(
        (v["title"], v["proposition"]) for v in kit.seed_topics().values()
    )


def test_as_is_authorship_is_the_files_and_swapped_is_its_mirror_with_identical_text(replay_environment):
    experiment = load().experiment
    stored = lambda c: [m.content.strip() for m in c.messages.order_by("seq_no")]  # noqa: E731
    letters = lambda d: [m["author"].split()[-1] for m in d["messages"]]  # noqa: E731
    texts = lambda d: [m["text"].strip() for m in d["messages"]]  # noqa: E731
    seen = {
        data["id"]: (
            labels_of(conversation_of(experiment, data)), labels_of(conversation_of(experiment, data, swapped=True)),
            stored(conversation_of(experiment, data)), stored(conversation_of(experiment, data, swapped=True)),
        )
        for _path, data in FILES
    }
    expected = {
        data["id"]: (letters(data), [{"A": "B", "B": "A"}[x] for x in letters(data)], texts(data), texts(data)) for _path, data in FILES
    }
    assert seen == expected


def test_planted_items_pair_ids_and_variants_are_stored(replay_environment):
    experiment = load().experiment
    convs = [(data, conversation_of(experiment, data, swapped=swapped)) for _path, data in FILES for swapped in (False, True)]
    stored = [([m.planted for m in c.messages.order_by("seq_no")], c.pair_id, c.variant) for _d, c in convs]
    expected = [([m["planted"] for m in d["messages"]], d["pair_id"] or "", d["variant"] or "") for d, _c in convs]
    assert stored == expected


def test_loading_the_folder_twice_creates_nothing_new(replay_environment):
    load()
    before = table_counts()
    load()
    assert table_counts() == before


def test_the_factors_of_every_stored_conversation_equal_compute_features_on_the_file_and_the_declared_block(replay_environment):
    from moderation import replay
    from moderation.series import compute_features

    experiment = load().experiment
    got, expected, declared = [], [], []
    for _path, data in FILES:
        for swapped in (False, True):
            got.append(replay.factors(conversation_of(experiment, data, swapped=swapped)))
            expected.append(compute_features(data["messages"], data["trigger_seq"]))
    for _path, data in kit.series_members(FILES):
        declared.append((replay.factors(conversation_of(experiment, data)), data["computed"]))
    assert (got == expected, [a == b for a, b in declared]) == (True, [True] * len(declared))


def test_the_dry_run_plan_counts_conversations_and_runs_and_writes_nothing(replay_environment):
    from moderation import replay

    plan = replay.dry_run_plan(NAME, FILES, assignments="both", replicates=1, known=KNOWN)
    assert (
        plan.conversations_to_create, plan.runs_to_do, len(plan.conversations), plan.assignments, plan.transcript_ids,
        {c["assignment"] for c in plan.conversations}, plan.worst_case_usd > 0,
        sum(c["worst_case_usd"] for c in plan.conversations) == plan.worst_case_usd, set(table_counts().values()),
    ) == (2 * N, 2 * N, 2 * N, ("as-is", "swapped"), sorted(d["id"] for _p, d in FILES), {"as-is", "swapped"}, True, True, {0})


def test_the_dry_run_plan_scales_with_replicates(replay_environment):
    from moderation import replay

    plan = replay.dry_run_plan(NAME, FILES, assignments="both", replicates=3, known=KNOWN)
    assert (plan.runs_to_do, plan.conversations_to_create) == (6 * N, 2 * N)


def test_a_single_assignment_plan_has_one_conversation_per_transcript(replay_environment):
    from moderation import replay

    plan = replay.dry_run_plan(NAME, FILES, assignments="as-is", replicates=1, known=KNOWN)
    assert (plan.conversations_to_create, plan.runs_to_do) == (N, N)


def test_the_plan_has_one_run_per_conversation_with_the_files_trigger(replay_environment):
    from moderation import replay

    specs = replay.plan_runs(load(), replicates=1)
    triggers = sorted((s.transcript_id.split(":")[0], s.trigger_message.seq_no) for s in specs)
    assert (len(specs), triggers) == (2 * N, sorted((d["id"], d["trigger_seq"]) for _p, d in FILES for _ in range(2)))


def test_every_run_finishes_with_a_fake_no_issue_answer_and_no_real_client(replay_environment, fake):
    from forum.models import Message
    from moderation import replay
    from moderation.models import LLMCall, ModerationRun

    specs = replay.plan_runs(load(), replicates=1)
    client = fake(*[dict(NO_ISSUE) for _ in specs])
    report = replay.execute_runs(specs, max_usd=50)
    runs = list(ModerationRun.objects.filter(kind="replay"))
    assert (
        dict(report.counts), len(client.calls), len(runs), {r.decision for r in runs},
        LLMCall.objects.count(), {row.purpose for row in LLMCall.objects.all()},
        Message.objects.filter(author_type="moderator").count(),
    ) == ({"done": 2 * N}, 2 * N, 2 * N, {"no_intervention"}, 2 * N, {"replay"}, 0)


def test_the_golden_folder_is_unchanged_by_loading_planning_and_running_the_warmup(replay_environment, fake):
    from moderation import replay

    before = kit.folder_hash(kit.GOLDEN_DIR)
    replay.dry_run_plan(NAME, FILES, assignments="both", known=KNOWN)
    specs = replay.plan_runs(load(), replicates=1)
    fake(*[dict(NO_ISSUE) for _ in specs])
    replay.execute_runs(specs, max_usd=50)
    assert (kit.folder_hash(kit.GOLDEN_DIR), before) == (GOLDEN_HASH_AT_IMPORT, GOLDEN_HASH_AT_IMPORT)


def test_the_warmup_files_are_unchanged_by_the_replay_machinery(replay_environment):
    from moderation import replay

    replay.dry_run_plan(NAME, FILES, assignments="both", known=KNOWN)
    load()
    assert kit.folder_hash(kit.WARMUP_DIR) == WARMUP_HASH_AT_IMPORT


def test_the_golden_folder_still_holds_its_forty_two_transcripts_and_no_warmup_file_leaked_into_it():
    names = sorted(p.name for p in kit.GOLDEN_DIR.glob("*.json"))
    assert (len(names), [n for n in names if n in {p.name for p, _d in FILES}]) == (42, [])


def test_the_folder_hash_reacts_to_a_changed_added_or_removed_file(tmp_path):
    import shutil

    copy = tmp_path / "golden_copy"
    shutil.copytree(kit.GOLDEN_DIR, copy)
    base = kit.folder_hash(copy)
    target = next(iter(sorted(copy.glob("*.json"))))
    original = target.read_bytes()
    target.write_bytes(original + b" ")
    changed = kit.folder_hash(copy)
    target.write_bytes(original)
    (copy / "extra.json").write_text("{}", encoding="utf-8")
    added = kit.folder_hash(copy)
    (copy / "extra.json").unlink()
    target.unlink()
    removed = kit.folder_hash(copy)
    assert len({base, changed, added, removed}) == 4


def test_the_warmup_and_the_golden_folders_load_into_one_experiment_together(replay_environment):
    """The ids do not collide, the topics coexist (same title never has two propositions), and the counts add up."""
    from forum.models import Conversation, Topic
    from moderation import replay

    golden = kit.load_folder(kit.GOLDEN_DIR)
    everything = golden + FILES
    known = kit.known_of(everything)
    plan = replay.load_experiment("warmup-and-golden", everything, assignments="both", known=known)
    dry = replay.dry_run_plan("another-experiment", everything, assignments="both", known=known)
    assert (
        len(known), len(plan.conversations), Conversation.objects.count(), Topic.objects.count(),
        dry.conversations_to_create, dry.runs_to_do,
    ) == (len(golden) + N, 2 * (len(golden) + N), 2 * (len(golden) + N), 6, 2 * (len(golden) + N), 2 * (len(golden) + N))
