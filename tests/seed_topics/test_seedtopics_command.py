"""10a: what `manage.py seed_topics` does: create, idempotence, update in place, dry run, user-created propositions
(docs/step10a_brief.md, "What it does")."""
import copy

import pytest
from django.core.management.base import CommandError

import seedtopics_kit as K


# --- creating --------------------------------------------------------------------------------------------------------------

def test_the_packaged_file_creates_six_seeded_visible_topics_and_reports_it():
    from forum.models import Topic

    output = K.run()
    assert K.counts(output) == (6, 0, 0)
    topics = list(Topic.objects.order_by("id"))
    assert len(topics) == 6
    assert {t.created_by_id for t in topics} == {None}
    assert {t.hidden for t in topics} == {False}


def test_created_topics_hold_exactly_the_packaged_fields():
    from forum.models import Topic

    K.run()
    expected = {e["title"]: e for e in K.packaged()}
    stored = {t.title: t for t in Topic.objects.all()}
    assert set(stored) == set(expected)
    assert {t: (r.description, r.proposition, r.opposing_position, r.leans) for t, r in stored.items()} == {
        t: (e["description"], e["proposition"], e["opposing_position"], e["leans"]) for t, e in expected.items()
    }


def test_the_output_says_the_leans_are_a_draft():
    assert "draft" in K.run().casefold()


def test_a_custom_file_creates_exactly_its_entries(tmp_path):
    entries = K.make_entries(3)
    output = K.run_file(K.write_file(tmp_path, entries))
    assert K.counts(output) == (3, 0, 0)
    assert K.topic_count() == 3
    stored = K.topic_by_title("Testing topic B")
    assert (stored.description, stored.proposition, stored.opposing_position, stored.leans) == (
        entries[1]["description"],
        entries[1]["proposition"],
        entries[1]["opposing_position"],
        entries[1]["leans"],
    )


def test_the_default_file_is_the_packaged_one(tmp_path):
    assert K.counts(K.run()) == (6, 0, 0)
    assert K.counts(K.run("--file", str(K.PACKAGED))) == (0, 0, 6)


# --- idempotence -----------------------------------------------------------------------------------------------------------

def test_a_second_run_creates_nothing_changes_nothing_and_reports_unchanged():
    K.run()
    before = K.snapshot()
    output = K.run()
    assert K.counts(output) == (0, 0, 6)
    assert K.snapshot() == before


def test_a_third_run_is_still_unchanged():
    K.run()
    K.run()
    assert K.counts(K.run()) == (0, 0, 6)
    assert K.topic_count() == 6


def test_leans_with_the_keys_in_another_order_count_as_unchanged(tmp_path):
    entries = K.make_entries(2)
    K.run_file(K.write_file(tmp_path, entries))
    before = K.snapshot()
    reordered = copy.deepcopy(entries)
    for entry in reordered:
        entry["leans"] = {
            side: {
                scheme: {axis: dict(reversed(list(cell.items()))) for axis, cell in reversed(list(axes.items()))}
                for scheme, axes in reversed(list(entry["leans"][side].items()))
            }
            for side in reversed(list(entry["leans"]))
        }
        entry.update(dict(reversed(list(entry.items()))))
    output = K.run_file(K.write_file(tmp_path, reordered))
    assert K.counts(output) == (0, 0, 2)
    assert K.snapshot() == before


def test_a_row_with_the_same_content_but_keys_in_another_order_and_whole_number_values_is_unchanged(tmp_path):
    from forum.models import Topic

    entry = K.make_entry("Testing topic A", leans=K.make_leans(value=0.0))
    stored = {
        side: {
            scheme: {axis: {"rationale": cell["rationale"], "value": 0} for axis, cell in axes.items()}
            for scheme, axes in reversed(list(entry["leans"][side].items()))
        }
        for side in ("con", "pro")
    }
    Topic.objects.create(
        title=entry["title"],
        description=entry["description"],
        proposition=entry["proposition"],
        opposing_position=entry["opposing_position"],
        leans=stored,
    )
    before = K.snapshot()
    assert K.counts(K.run_file(K.write_file(tmp_path, [entry]))) == (0, 0, 1)
    assert K.snapshot() == before


def test_titles_are_matched_exactly_so_a_different_case_is_a_new_topic(tmp_path):
    K.run_file(K.write_file(tmp_path, [K.make_entry("Alpha topic")]))
    before = K.snapshot()
    output = K.run_file(K.write_file(tmp_path, [K.make_entry("alpha topic", description="Other.")]))
    assert K.counts(output) == (1, 0, 0)
    assert K.snapshot()[0] == before[0]
    assert K.topic_count() == 2


# --- updating in place -----------------------------------------------------------------------------------------------------

def edit_first(tmp_path, change):
    """Seed three topics, apply ``change`` to the first entry, run again; return (ids before, entries, output)."""
    entries = K.make_entries(3)
    K.run_file(K.write_file(tmp_path, entries))
    ids = {r["title"]: r["id"] for r in K.snapshot()}
    edited = copy.deepcopy(entries)
    change(edited[0])
    return ids, edited, K.run_file(K.write_file(tmp_path, edited))


def test_a_changed_description_updates_that_topic_in_place(tmp_path):
    ids, edited, output = edit_first(tmp_path, lambda e: e.update(description="A new description."))
    assert K.counts(output) == (0, 1, 2)
    stored = K.topic_by_title("Testing topic A")
    assert (stored.id, stored.description) == (ids["Testing topic A"], "A new description.")


def test_a_changed_proposition_updates_that_topic_in_place(tmp_path):
    ids, edited, output = edit_first(tmp_path, lambda e: e.update(proposition="A brand new claim to debate."))
    assert K.counts(output) == (0, 1, 2)
    stored = K.topic_by_title("Testing topic A")
    assert (stored.id, stored.proposition) == (ids["Testing topic A"], "A brand new claim to debate.")


def test_a_changed_opposing_position_updates_that_topic_in_place(tmp_path):
    ids, edited, output = edit_first(tmp_path, lambda e: e.update(opposing_position="A brand new opposing claim."))
    assert K.counts(output) == (0, 1, 2)
    stored = K.topic_by_title("Testing topic A")
    assert (stored.id, stored.opposing_position) == (ids["Testing topic A"], "A brand new opposing claim.")
    assert (stored.proposition, stored.description) == (edited[0]["proposition"], edited[0]["description"])


def test_an_opposing_position_change_leaves_every_other_column_and_row_alone(tmp_path):
    entries = K.make_entries(3)
    K.run_file(K.write_file(tmp_path, entries))
    before = {r["title"]: r for r in K.snapshot()}
    edited = copy.deepcopy(entries)
    edited[1]["opposing_position"] = "Changed only the opposing wording."
    K.run_file(K.write_file(tmp_path, edited))
    after = {r["title"]: r for r in K.snapshot()}
    assert after["Testing topic A"] == before["Testing topic A"]
    assert after["Testing topic C"] == before["Testing topic C"]
    assert after["Testing topic B"] == {**before["Testing topic B"], "opposing_position": "Changed only the opposing wording."}


def test_a_seeded_row_with_a_blank_opposing_position_gets_it_filled_in_and_the_next_run_is_unchanged(tmp_path):
    from forum.models import Topic

    entry = K.make_entry("Testing topic A")
    Topic.objects.create(
        title=entry["title"], description=entry["description"], proposition=entry["proposition"], leans=entry["leans"]
    )
    path = K.write_file(tmp_path, [entry])
    assert K.counts(K.run_file(path)) == (0, 1, 0)
    assert K.topic_by_title("Testing topic A").opposing_position == entry["opposing_position"]
    assert K.counts(K.run_file(path)) == (0, 0, 1)


def test_a_changed_lean_value_updates_that_topic_in_place(tmp_path):
    def change(entry):
        entry["leans"]["con"]["compass"]["social"]["value"] = 0.25

    ids, edited, output = edit_first(tmp_path, change)
    assert K.counts(output) == (0, 1, 2)
    stored = K.topic_by_title("Testing topic A")
    assert (stored.id, stored.leans) == (ids["Testing topic A"], edited[0]["leans"])


def test_a_changed_rationale_alone_updates_that_topic(tmp_path):
    def change(entry):
        entry["leans"]["pro"]["us_partisan"]["party"]["rationale"] = "A different reason is given here."

    ids, edited, output = edit_first(tmp_path, change)
    assert K.counts(output) == (0, 1, 2)
    assert K.topic_by_title("Testing topic A").leans == edited[0]["leans"]


def test_an_update_leaves_untouched_columns_and_other_rows_alone(tmp_path):
    entries = K.make_entries(3)
    K.run_file(K.write_file(tmp_path, entries))
    before = {r["title"]: r for r in K.snapshot()}
    edited = copy.deepcopy(entries)
    edited[0]["description"] = "Changed only the description."
    K.run_file(K.write_file(tmp_path, edited))
    after = {r["title"]: r for r in K.snapshot()}
    assert after["Testing topic B"] == before["Testing topic B"]
    assert after["Testing topic C"] == before["Testing topic C"]
    assert after["Testing topic A"] == {**before["Testing topic A"], "description": "Changed only the description."}


def test_several_changes_are_counted_separately(tmp_path):
    entries = K.make_entries(4)
    K.run_file(K.write_file(tmp_path, entries))
    edited = copy.deepcopy(entries)
    edited[0]["description"] = "Changed one."
    edited[1]["proposition"] = "Changed two claim is worth a debate."
    edited.append(K.make_entry("Testing topic E"))
    output = K.run_file(K.write_file(tmp_path, edited))
    assert K.counts(output) == (1, 2, 2)
    assert K.topic_count() == 5


def test_the_run_after_an_update_is_unchanged(tmp_path):
    ids, edited, _ = edit_first(tmp_path, lambda e: e.update(description="Second version."))
    assert K.counts(K.run_file(K.write_file(tmp_path, edited))) == (0, 0, 3)


def test_an_update_keeps_created_by_none_and_the_admins_hidden_flag(tmp_path):
    from forum.models import Topic

    entries = K.make_entries(2)
    K.run_file(K.write_file(tmp_path, entries))
    Topic.objects.filter(title="Testing topic A").update(hidden=True)
    edited = copy.deepcopy(entries)
    edited[0]["description"] = "Edited while hidden."
    output = K.run_file(K.write_file(tmp_path, edited))
    stored = K.topic_by_title("Testing topic A")
    assert K.counts(output) == (0, 1, 1)
    assert (stored.hidden, stored.created_by_id, stored.description) == (True, None, "Edited while hidden.")


def test_a_topic_hidden_by_an_admin_stays_hidden_and_unchanged_on_rerun(tmp_path):
    from forum.models import Topic

    K.run()
    Topic.objects.filter(title="Rent control").update(hidden=True)
    assert K.counts(K.run()) == (0, 0, 6)
    assert K.topic_by_title("Rent control").hidden is True


# --- never deleting; keyed by title ----------------------------------------------------------------------------------------

def test_dropping_an_entry_from_the_file_deletes_nothing(tmp_path):
    entries = K.make_entries(3)
    K.run_file(K.write_file(tmp_path, entries))
    before = K.snapshot()
    output = K.run_file(K.write_file(tmp_path, entries[:1]))
    assert K.counts(output) == (0, 0, 1)
    assert K.snapshot() == before


def test_a_new_title_creates_a_new_topic_and_leaves_the_old_one(tmp_path):
    entries = K.make_entries(2)
    K.run_file(K.write_file(tmp_path, entries))
    renamed = copy.deepcopy(entries)
    renamed[0]["title"] = "Testing topic A renamed"
    output = K.run_file(K.write_file(tmp_path, renamed))
    assert K.counts(output) == (1, 0, 1)
    assert sorted(r["title"] for r in K.snapshot()) == ["Testing topic A", "Testing topic A renamed", "Testing topic B"]


def test_the_order_of_entries_in_the_file_does_not_matter(tmp_path):
    entries = K.make_entries(3)
    K.run_file(K.write_file(tmp_path, entries))
    before = K.snapshot()
    assert K.counts(K.run_file(K.write_file(tmp_path, list(reversed(entries))))) == (0, 0, 3)
    assert K.snapshot() == before


def test_a_topic_in_the_database_with_no_entry_in_the_file_is_left_as_it_is(tmp_path):
    from forum.models import Topic

    Topic.objects.create(title="Left over from before", proposition="An older seeded claim.", leans={})
    before = K.snapshot()
    assert K.counts(K.run_file(K.write_file(tmp_path, K.make_entries(1)))) == (1, 0, 0)
    assert before[0] in K.snapshot()


# --- user-created propositions are never touched ---------------------------------------------------------------------------

def test_user_created_propositions_survive_a_first_run_and_a_rerun_untouched():
    user = K.make_user()
    K.make_user_topic(user, "Coffee is better than tea.")
    K.make_user_topic(user, "Hidden proposition of a user.", hidden=True)
    K.make_user_topic(user, "A user topic with an odd lean.", leans={"note": "not ours"}, description="mine")
    before = K.snapshot()
    K.run()
    K.run()
    assert [r for r in K.snapshot() if r["created_by_id"] is not None] == before
    assert K.topic_count() == 9


def test_a_user_proposition_worded_like_a_seeded_one_stays_a_separate_row(tmp_path):
    user = K.make_user()
    mine = K.make_user_topic(user, K.RENT_PROPOSITION)
    before = K.snapshot()
    K.run()
    assert [r for r in K.snapshot() if r["id"] == mine.id] == before
    assert K.topic_count() == 7


def test_an_edited_file_never_updates_user_created_rows(tmp_path):
    user = K.make_user()
    entries = K.make_entries(2)
    mine = K.make_user_topic(user, entries[0]["proposition"], description="hand written")
    K.run_file(K.write_file(tmp_path, entries))
    edited = copy.deepcopy(entries)
    edited[0]["proposition"] = "A new seeded proposition worth a debate."
    edited[0]["description"] = "New seeded description."
    K.run_file(K.write_file(tmp_path, edited))
    mine.refresh_from_db()
    assert (mine.proposition, mine.description, mine.created_by_id, mine.leans) == (
        entries[0]["proposition"],
        "hand written",
        user.pk,
        {},
    )


def test_a_user_topic_that_already_uses_a_seeded_title_makes_the_command_refuse_and_write_nothing(tmp_path):
    # Architect ruling: refuse with a CommandError naming the title; nothing is written, the user's row is untouched.
    entries = K.make_entries(2)
    K.make_user_topic(K.make_user(), "My own wording of a claim.", title="Testing topic A", description="mine")
    before = K.snapshot()
    with pytest.raises(CommandError, match="Testing topic A"):
        K.run_file(K.write_file(tmp_path, entries))
    assert K.snapshot() == before


def test_a_conversation_on_a_seeded_topic_survives_reseeding(tmp_path):
    from forum.models import Conversation
    from forum.services import enter_proposition

    K.run()
    topic = K.topic_by_title("Rent control")
    conversation = enter_proposition(K.make_user(), topic, "pro")
    K.run()
    assert Conversation.objects.get(pk=conversation.pk).topic_id == topic.pk


# --- dry run ---------------------------------------------------------------------------------------------------------------

def test_a_dry_run_on_an_empty_database_writes_nothing_and_names_what_it_would_create():
    output = K.run("--dry-run")
    assert K.topic_count() == 0
    assert [t for t in (e["title"] for e in K.packaged()) if t not in output] == []


def test_a_dry_run_does_not_leave_the_topics_for_the_real_run_to_find():
    K.run("--dry-run")
    assert K.counts(K.run()) == (6, 0, 0)


def test_a_dry_run_after_seeding_changes_no_row(tmp_path):
    entries = K.make_entries(3)
    K.run_file(K.write_file(tmp_path, entries))
    before = K.snapshot()
    edited = copy.deepcopy(entries)
    edited[0]["description"] = "Would be updated."
    edited.append(K.make_entry("Testing topic Z"))
    output = K.run_file(K.write_file(tmp_path, edited), "--dry-run")
    assert K.snapshot() == before
    assert "Testing topic A" in output
    assert "Testing topic Z" in output


def test_a_dry_run_reports_the_same_counts_the_real_run_then_gives(tmp_path):
    entries = K.make_entries(3)
    K.run_file(K.write_file(tmp_path, entries))
    edited = copy.deepcopy(entries)
    edited[0]["description"] = "Would be updated."
    edited.append(K.make_entry("Testing topic Z"))
    path = K.write_file(tmp_path, edited)
    assert K.counts(K.run_file(path, "--dry-run")) == (1, 1, 2)
    assert K.counts(K.run_file(path)) == (1, 1, 2)


def test_a_dry_run_still_states_the_draft_notice():
    assert "draft" in K.run("--dry-run").casefold()


def test_a_dry_run_leaves_user_created_rows_alone():
    user = K.make_user()
    K.make_user_topic(user, "Coffee is better than tea.")
    before = K.snapshot()
    K.run("--dry-run")
    assert K.snapshot() == before
