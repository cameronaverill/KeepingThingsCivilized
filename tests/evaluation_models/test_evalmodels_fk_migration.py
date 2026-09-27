"""evaluation migration 0002 (foreign-key cleanup, docs/fk_cleanup_brief.md): Rating.llm_call becomes a real foreign key
(same column, existing values stay) and Annotation.source text becomes Annotation.rater ("self" -> null, "rater:<name>" ->
that rater, a clear error when the rater does not exist), forward, backward and forward again with rows in the tables."""
import evalmodels_testkit as kit
import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db(transaction=True)

TEXT = "The city has 40 parks and all of them are closed. Nobody could go."


def names():
    executor = MigrationExecutor(connection)
    return sorted(n for (app, n) in executor.loader.disk_migrations if app == "evaluation")


def name_0002():
    found = [n for n in names() if n.startswith("0002")]
    assert len(found) == 1, found
    return found[0]


def migrate_to(target):
    MigrationExecutor(connection).migrate([("evaluation", target)])


def apps_at(target):
    return MigrationExecutor(connection).loader.project_state([("evaluation", target)]).apps


def restore():
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


def annotation_texts():
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, source FROM evaluation_annotation ORDER BY id")
        return cursor.fetchall()


class Seed:
    """Rows created while evaluation is at 0001 (historical models, so no save() rules run; the triggers still do)."""


def seed_at_0001(with_ghost=False):
    old = apps_at("0001_initial")
    Rater, Rating, Annotation = old.get_model("evaluation", "Rater"), old.get_model("evaluation", "Rating"), old.get_model("evaluation", "Annotation")
    message, _ = kit.message_with_text(TEXT)
    call = kit.make_llm_call()
    s = Seed()
    s.message, s.call = message, call
    s.judge = Rater.objects.create(name="judge-one", kind="llm", provider="anthropic", model="m1", temperature=0.0)
    s.colon = Rater.objects.create(name="a:b", kind="llm", provider="anthropic", model="m2", temperature=None)
    s.human = Rater.objects.create(name="human-one", kind="human", user_id=kit.make_user().pk)
    base = dict(target_type="message", target_id=message.pk, dimensions=["factual_accuracy"])
    s.llm_rating = Rating.objects.create(rater=s.judge, llm_call_id=call.pk, status="done", **base)
    s.plain_llm_rating = Rating.objects.create(rater=s.colon, **base)
    s.human_rating = Rating.objects.create(rater=s.human, **base)
    ann = dict(target_type="message", target_id=message.pk, dimension="stance", value="pro")
    s.own = Annotation.objects.create(source="self", **ann)
    s.by_judge = Annotation.objects.create(source="rater:judge-one", rating_id=s.llm_rating.pk, **ann)
    s.by_colon = Annotation.objects.create(source="rater:a:b", value="con", **{k: v for k, v in ann.items() if k != "value"})
    s.by_human = Annotation.objects.create(source="rater:human-one", **ann)
    if with_ghost:
        s.ghost = Annotation.objects.create(source="rater:ghost-rater", **ann)
    return s


def check_new_shape(s):
    new = apps_at(name_0002())
    Rating, Annotation = new.get_model("evaluation", "Rating"), new.get_model("evaluation", "Annotation")
    assert {r.pk: r.llm_call_id for r in Rating.objects.all()} == {s.llm_rating.pk: s.call.pk, s.plain_llm_rating.pk: None, s.human_rating.pk: None}
    assert {a.pk: a.rater_id for a in Annotation.objects.all()} == {
        s.own.pk: None, s.by_judge.pk: s.judge.pk, s.by_colon.pk: s.colon.pk, s.by_human.pk: s.human.pk,
    }  # fmt: skip
    assert "source" not in {f.name for f in Annotation._meta.get_fields()}
    assert Annotation.objects.get(pk=s.by_judge.pk).rating_id == s.llm_rating.pk
    assert Annotation.objects.get(pk=s.by_colon.pk).value == "con"


def check_old_shape(s):
    assert dict(annotation_texts()) == {
        s.own.pk: "self", s.by_judge.pk: "rater:judge-one", s.by_colon.pk: "rater:a:b", s.by_human.pk: "rater:human-one",
    }  # fmt: skip
    old = apps_at("0001_initial")
    Rating = old.get_model("evaluation", "Rating")
    assert {r.pk: r.llm_call_id for r in Rating.objects.all()} == {s.llm_rating.pk: s.call.pk, s.plain_llm_rating.pk: None, s.human_rating.pk: None}


# --- shape of the migration ----------------------------------------------------------------------------------------------
def test_there_is_exactly_one_0002_and_it_builds_on_0001():
    executor = MigrationExecutor(connection)
    migration = executor.loader.disk_migrations[("evaluation", name_0002())]
    assert ("evaluation", "0001_initial") in migration.dependencies
    assert len([n for n in names() if n.startswith("0002")]) == 1


def test_makemigrations_check_is_clean_after_the_foreign_key_cleanup():
    import io

    from django.core.management import call_command

    call_command("makemigrations", "--check", "--dry-run", stdout=io.StringIO(), stderr=io.StringIO())


# --- forward, backward, forward again with rows -----------------------------------------------------------------------------
def test_existing_ratings_and_annotations_convert_forward_and_the_old_form_comes_back_backward():
    try:
        migrate_to("0001_initial")
        s = seed_at_0001()
        triggers_before = sorted(t[0] for t in kit.triggers_matching("evaluation_"))
        assert triggers_before

        migrate_to(name_0002())
        check_new_shape(s)
        assert sorted(t[0] for t in kit.triggers_matching("evaluation_")) == triggers_before

        migrate_to("0001_initial")
        check_old_shape(s)
        assert sorted(t[0] for t in kit.triggers_matching("evaluation_")) == triggers_before

        migrate_to(name_0002())
        check_new_shape(s)
        assert sorted(t[0] for t in kit.triggers_matching("evaluation_")) == triggers_before
    finally:
        restore()


def test_the_new_foreign_keys_are_enforced_after_the_migration_ran_over_data():
    from django.db import IntegrityError, transaction

    try:
        migrate_to("0001_initial")
        s = seed_at_0001()
        migrate_to(name_0002())
    finally:
        restore()
    from evaluation.models import Annotation, Rating

    assert Rating.objects.get(pk=s.llm_rating.pk).llm_call.pk == s.call.pk
    assert Annotation.objects.get(pk=s.by_judge.pk).rater.name == "judge-one"
    assert Annotation.objects.get(pk=s.own.pk).rater is None
    with pytest.raises(IntegrityError), transaction.atomic():
        Rating.objects.filter(pk=s.llm_rating.pk).update(llm_call_id=s.call.pk + 5000)
        connection.check_constraints()
    with pytest.raises(IntegrityError), transaction.atomic():
        Annotation.objects.filter(pk=s.by_judge.pk).update(rater_id=s.judge.pk + 5000)
        connection.check_constraints()


def test_the_human_rating_trigger_still_refuses_a_call_id_after_the_migration():
    from django.db import IntegrityError, transaction

    try:
        migrate_to("0001_initial")
        s = seed_at_0001()
        migrate_to(name_0002())
    finally:
        restore()
    from evaluation.models import Rating

    with pytest.raises(IntegrityError), transaction.atomic():
        Rating.objects.filter(pk=s.human_rating.pk).update(llm_call_id=s.call.pk)


def test_a_backward_migration_keeps_annotations_made_after_the_forward_one():
    """Rows created under the new shape (a researcher's own label and a rater's) reverse to 'self' and 'rater:<name>'."""
    try:
        migrate_to("0001_initial")
        seed_at_0001()
        migrate_to(name_0002())
        new = apps_at(name_0002())
        Rater, Annotation = new.get_model("evaluation", "Rater"), new.get_model("evaluation", "Annotation")
        message, _ = kit.message_with_text("Another message for the new rows.")
        late = Rater.objects.create(name="late-rater", kind="llm", provider="p", model="m", temperature=None)
        base = dict(target_type="message", target_id=message.pk, dimension="tone", value="firm")
        mine = Annotation.objects.create(rater=None, **base)
        theirs = Annotation.objects.create(rater=late, **base)
        migrate_to("0001_initial")
        assert dict(annotation_texts())[mine.pk] == "self"
        assert dict(annotation_texts())[theirs.pk] == "rater:late-rater"
    finally:
        restore()


# --- a missing rater is a clear error, and no data is lost -----------------------------------------------------------------
def test_an_annotation_naming_a_rater_that_does_not_exist_stops_the_migration_with_a_clear_error_and_loses_nothing():
    try:
        migrate_to("0001_initial")
        s = seed_at_0001(with_ghost=True)
        before = annotation_texts()
        with pytest.raises(Exception) as caught:  # noqa: B017,PT011 (the brief does not name the exception)
            migrate_to(name_0002())
        assert not isinstance(caught.value, (AttributeError, TypeError, NameError, ImportError)), repr(caught.value)
        assert "ghost-rater" in str(caught.value)
        applied = {n for (app, n) in MigrationExecutor(connection).loader.applied_migrations if app == "evaluation"}
        assert name_0002() not in applied
        assert annotation_texts() == before
        old = apps_at("0001_initial")
        assert old.get_model("evaluation", "Rating").objects.count() == 3
        assert old.get_model("evaluation", "Rater").objects.count() == 3
        assert s.ghost.pk in dict(before)
        old.get_model("evaluation", "Annotation").objects.filter(pk=s.ghost.pk).delete()
        migrate_to(name_0002())
        check_new_shape(s)
    finally:
        restore()


def test_a_rating_citing_a_ledger_row_that_does_not_exist_stops_the_migration_with_a_clear_error_and_loses_nothing():
    """The brief covers missing raters; the builder extends the same rule to dangling call ids (a foreign key cannot hold them)."""
    try:
        migrate_to("0001_initial")
        s = seed_at_0001()
        old = apps_at("0001_initial")
        made_up = s.call.pk + 5000
        old.get_model("evaluation", "Rating").objects.filter(pk=s.plain_llm_rating.pk).update(llm_call_id=made_up)
        with pytest.raises(Exception) as caught:  # noqa: B017,PT011
            migrate_to(name_0002())
        assert not isinstance(caught.value, (AttributeError, TypeError, NameError, ImportError)), repr(caught.value)
        assert str(made_up) in str(caught.value)
        applied = {n for (app, n) in MigrationExecutor(connection).loader.applied_migrations if app == "evaluation"}
        assert name_0002() not in applied
        assert old.get_model("evaluation", "Rating").objects.get(pk=s.plain_llm_rating.pk).llm_call_id == made_up
        assert dict(annotation_texts())[s.own.pk] == "self"
    finally:
        with connection.cursor() as cursor:  # clear the dangling id so that the teardown migration can run
            cursor.execute("UPDATE evaluation_rating SET llm_call_id = NULL WHERE llm_call_id = %s", [s.call.pk + 5000])
        restore()


def test_rater_names_are_matched_exactly_not_case_insensitively():
    try:
        migrate_to("0001_initial")
        old = apps_at("0001_initial")
        old.get_model("evaluation", "Rater").objects.create(name="Judge-One", kind="llm", provider="p", model="m", temperature=None)
        message, _ = kit.message_with_text("Text for the case test.")
        old.get_model("evaluation", "Annotation").objects.create(
            source="rater:judge-one", target_type="message", target_id=message.pk, dimension="stance", value="pro"
        )
        with pytest.raises(Exception) as caught:  # noqa: B017,PT011
            migrate_to(name_0002())
        assert "judge-one" in str(caught.value)
    finally:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM evaluation_annotation")
        restore()


def test_an_empty_database_migrates_both_ways():
    try:
        migrate_to("0001_initial")
        migrate_to(name_0002())
        migrate_to("0001_initial")
        migrate_to(name_0002())
    finally:
        restore()
