"""Step 7c revision 5: forum.Block (migration 0004): fields, the unique pair and the no-self-block rule, at model level
and at database level (bulk_create and queryset update skip save())."""
import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction

from forum_testkit import make_user, refused_by_database

pytestmark = pytest.mark.django_db


def block(a, b):
    from forum.models import Block

    return Block(blocker=a, blocked=b)


def test_field_definitions():
    from django.conf import settings

    from forum.models import Block

    blocker = Block._meta.get_field("blocker")
    blocked = Block._meta.get_field("blocked")
    for field, related in ((blocker, "blocks_made"), (blocked, "blocks_received")):
        assert isinstance(field, models.ForeignKey)
        assert field.remote_field.model._meta.label == settings.AUTH_USER_MODEL
        assert field.remote_field.on_delete is models.PROTECT
        assert field.remote_field.related_name == related
        assert field.null is False
    created = Block._meta.get_field("created_at")
    assert isinstance(created, models.DateTimeField) and created.auto_now_add is True


def test_related_names_work_on_users():
    a, b = make_user(), make_user()
    block(a, b).save()
    assert [x.blocked_id for x in a.blocks_made.all()] == [b.pk]
    assert [x.blocker_id for x in b.blocks_received.all()] == [a.pk]
    assert not a.blocks_received.exists() and not b.blocks_made.exists()


def test_created_at_is_set_on_save():
    a, b = make_user(), make_user()
    saved = block(a, b)
    saved.save()
    saved.refresh_from_db()
    assert saved.created_at is not None


def test_the_constraints_are_declared():
    from forum.models import Block

    names = {c.name for c in Block._meta.constraints}
    assert len(names) == 2 and all("block" in n for n in names)


# --- model level ------------------------------------------------------------------------------------------------------


def test_a_person_cannot_block_themselves_at_model_level():
    a = make_user()
    with pytest.raises((ValidationError, IntegrityError)), transaction.atomic():
        block(a, a).save()
    from forum.models import Block

    assert Block.objects.count() == 0


def test_save_refuses_a_self_block_with_a_friendly_validation_error_not_a_database_error():
    """Like every other forum model, save() speaks first (ValidationError); the database check is the backstop."""
    a = make_user()
    with pytest.raises(ValidationError) as excinfo, transaction.atomic():
        block(a, a).save()
    assert "blocked" in excinfo.value.message_dict


def test_full_clean_reports_a_self_block_and_a_duplicate_pair():
    a, b = make_user(), make_user()
    with pytest.raises(ValidationError):
        block(a, a).full_clean()
    block(a, b).save()
    with pytest.raises(ValidationError):
        block(a, b).full_clean()


def test_a_duplicate_pair_is_refused_on_save():
    a, b = make_user(), make_user()
    block(a, b).save()
    with pytest.raises((ValidationError, IntegrityError)), transaction.atomic():
        block(a, b).save()
    from forum.models import Block

    assert Block.objects.count() == 1


def test_each_direction_is_its_own_block():
    a, b = make_user(), make_user()
    block(a, b).save()
    block(b, a).save()
    from forum.models import Block

    assert Block.objects.count() == 2


def test_one_person_can_block_many_and_be_blocked_by_many():
    a, b, c, d = (make_user() for _ in range(4))
    for x, y in ((a, b), (a, c), (a, d), (b, d), (c, d)):
        block(x, y).save()
    from forum.models import Block

    assert Block.objects.count() == 5


# --- database level ---------------------------------------------------------------------------------------------------


def test_the_database_refuses_a_self_block_through_bulk_create():
    from forum.models import Block

    a = make_user()
    refused_by_database(lambda: Block.objects.bulk_create([block(a, a)]))


def test_the_database_refuses_a_self_block_through_a_queryset_update():
    from forum.models import Block

    a, b = make_user(), make_user()
    block(a, b).save()
    refused_by_database(lambda: Block.objects.filter(blocker=a, blocked=b).update(blocked=a))
    assert Block.objects.filter(blocker=a, blocked=b).exists()


def test_the_database_refuses_a_duplicate_pair_through_bulk_create():
    from forum.models import Block

    a, b = make_user(), make_user()
    refused_by_database(lambda: Block.objects.bulk_create([block(a, b), block(a, b)]))
    assert Block.objects.count() == 0


def test_the_database_refuses_a_duplicate_pair_through_a_queryset_update():
    from forum.models import Block

    a, b, c = make_user(), make_user(), make_user()
    block(a, b).save()
    block(a, c).save()
    refused_by_database(lambda: Block.objects.filter(blocker=a, blocked=c).update(blocked=b))


def test_the_database_accepts_both_directions_through_bulk_create():
    from forum.models import Block

    a, b = make_user(), make_user()
    Block.objects.bulk_create([block(a, b), block(b, a)])
    assert Block.objects.count() == 2


# --- protect ----------------------------------------------------------------------------------------------------------


def test_a_user_who_blocked_someone_cannot_be_deleted():
    from django.db.models import ProtectedError

    a, b = make_user(), make_user()
    block(a, b).save()
    with pytest.raises(ProtectedError):
        a.delete()


def test_a_user_who_was_blocked_cannot_be_deleted():
    from django.db.models import ProtectedError

    a, b = make_user(), make_user()
    block(a, b).save()
    with pytest.raises(ProtectedError):
        b.delete()


def test_the_block_row_itself_can_be_deleted():
    from forum.models import Block

    a, b = make_user(), make_user()
    saved = block(a, b)
    saved.save()
    saved.delete()
    assert Block.objects.count() == 0
    a.delete()  # nothing protects a user without blocks
