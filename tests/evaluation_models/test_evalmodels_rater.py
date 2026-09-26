"""evaluation.Rater (docs/step12_brief.md, plan section 8): fields, defaults, choices and the kind rules, at model
level (friendly ValidationError) and at database level (IntegrityError through bulk_create and queryset update)."""
import evalmodels_testkit as kit
import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models import ProtectedError

pytestmark = pytest.mark.django_db


def llm(**extra):
    from evaluation.models import Rater

    values = dict(name=f"llm{kit.n()}", kind="llm", provider="anthropic", model="claude-sonnet-5", temperature=0.0)
    values.update(extra)
    return Rater(**values)


def human(**extra):
    from evaluation.models import Rater

    values = dict(name=f"human{kit.n()}", kind="human", user=kit.make_user())
    values.update(extra)
    return Rater(**values)


# --- fields, defaults, choices -----------------------------------------------------------------------------------------
def test_the_model_has_the_contract_fields():
    from evaluation.models import Rater

    assert {"name", "kind", "provider", "model", "temperature", "user", "active"} <= kit.field_names(Rater)


def test_kind_choices_are_exactly_human_and_llm():
    from evaluation.models import Rater

    assert kit.choice_values(Rater, "kind") == {"human", "llm"}


def test_active_defaults_to_true():
    from evaluation.models import Rater

    assert Rater._meta.get_field("active").default is True
    rater = kit.make_rater("llm")
    rater.refresh_from_db()
    assert rater.active is True


def test_active_can_be_switched_off_and_stored():
    rater = kit.make_rater("llm", active=False)
    rater.refresh_from_db()
    assert rater.active is False


def test_temperature_is_a_nullable_float():
    from evaluation.models import Rater

    field = Rater._meta.get_field("temperature")
    assert isinstance(field, models.FloatField) and field.null is True


def test_the_user_field_is_a_nullable_foreign_key_to_the_user_model():
    from evaluation.models import Rater

    field = Rater._meta.get_field("user")
    assert field.is_relation and field.null is True
    assert field.related_model._meta.label_lower == settings.AUTH_USER_MODEL.lower()


def test_an_llm_rater_round_trips_provider_model_and_temperature():
    from evaluation.models import Rater

    saved = llm(temperature=0.0)
    saved.save()
    loaded = Rater.objects.get(pk=saved.pk)
    assert (loaded.kind, loaded.provider, loaded.model, loaded.temperature, loaded.user_id) == (
        "llm", "anthropic", "claude-sonnet-5", 0.0, None,
    )  # fmt: skip


def test_an_llm_rater_may_have_no_temperature():
    """Sonnet 5 accepts no temperature setting (plan section 9), so the temperature may stay empty."""
    rater = llm(temperature=None)
    rater.save()
    rater.refresh_from_db()
    assert rater.temperature is None


def test_a_human_rater_round_trips_its_user():
    from evaluation.models import Rater

    person = kit.make_user()
    saved = human(user=person)
    saved.save()
    loaded = Rater.objects.get(pk=saved.pk)
    assert loaded.kind == "human" and loaded.user_id == person.pk and not loaded.model and loaded.temperature is None


# --- uniqueness ---------------------------------------------------------------------------------------------------------
def test_names_are_unique():
    kit.make_rater("llm", name="same-name")
    with pytest.raises(kit.REFUSED), transaction.atomic():
        kit.make_rater("llm", name="same-name")


def test_a_human_and_an_llm_rater_cannot_share_a_name():
    kit.make_rater("llm", name="shared")
    with pytest.raises(kit.REFUSED), transaction.atomic():
        kit.make_rater("human", name="shared")


# --- kind rules at model level -----------------------------------------------------------------------------------------
@pytest.mark.parametrize("empty", ["", None])
def test_an_llm_rater_needs_a_model(empty):
    with pytest.raises(ValidationError):
        llm(model=empty).save()


def test_an_llm_rater_cannot_have_a_user():
    with pytest.raises(ValidationError):
        llm(user=kit.make_user()).save()


def test_a_human_rater_needs_a_user():
    with pytest.raises(ValidationError):
        human(user=None).save()


@pytest.mark.parametrize("extra", [dict(model="claude-sonnet-5"), dict(temperature=0.0), dict(provider="anthropic")])
def test_a_human_rater_carries_no_llm_settings(extra):
    """The plan: provider, model and temperature are for LLM raters only."""
    with pytest.raises(ValidationError):
        human(**extra).save()


def test_an_unknown_kind_is_refused():
    with pytest.raises(kit.REFUSED), transaction.atomic():
        llm(kind="robot").save()


def test_the_kind_rules_apply_on_update_too():
    rater = kit.make_rater("llm")
    rater.model = ""
    with pytest.raises(ValidationError):
        rater.save()
    rater = kit.make_rater("llm")
    rater.user = kit.make_user()
    with pytest.raises(ValidationError):
        rater.save()
    person_rater = kit.make_rater("human")
    person_rater.user = None
    with pytest.raises(ValidationError):
        person_rater.save()


def test_a_refused_save_writes_nothing():
    from evaluation.models import Rater

    with pytest.raises(ValidationError):
        llm(model="").save()
    assert Rater.objects.count() == 0


# --- kind rules at database level --------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "build",
    [
        lambda: llm(model=""),
        lambda: llm(user=kit.make_user()),
        lambda: human(user=None),
        lambda: human(model="claude-sonnet-5"),
        lambda: llm(kind="robot"),
    ],
    ids=["llm-without-model", "llm-with-user", "human-without-user", "human-with-model", "unknown-kind"],
)
def test_bulk_create_cannot_bypass_the_kind_rules(build):
    from evaluation.models import Rater

    with pytest.raises(IntegrityError), transaction.atomic():
        Rater.objects.bulk_create([build()])
    assert Rater.objects.count() == 0


@pytest.mark.parametrize(
    "changes",
    [
        lambda: dict(model=""),
        lambda: dict(user_id=kit.make_user().pk),
        lambda: dict(kind="robot"),
        lambda: dict(kind="human"),
    ],
    ids=["llm-loses-model", "llm-gains-user", "unknown-kind", "llm-turned-human-without-user"],
)
def test_a_queryset_update_cannot_break_the_kind_rules(changes):
    from evaluation.models import Rater

    rater = kit.make_rater("llm")
    with pytest.raises(IntegrityError), transaction.atomic():
        Rater.objects.filter(pk=rater.pk).update(**changes())
    rater.refresh_from_db()
    assert rater.kind == "llm" and rater.model and rater.user_id is None


def test_a_queryset_update_cannot_strip_a_human_rater_of_its_user():
    from evaluation.models import Rater

    rater = kit.make_rater("human")
    with pytest.raises(IntegrityError), transaction.atomic():
        Rater.objects.filter(pk=rater.pk).update(user=None)


def test_names_are_unique_at_database_level():
    from evaluation.models import Rater

    kit.make_rater("llm", name="dup")
    with pytest.raises(IntegrityError), transaction.atomic():
        Rater.objects.bulk_create([llm(name="dup")])


# --- PROTECT -----------------------------------------------------------------------------------------------------------
def test_a_user_with_a_human_rater_cannot_be_deleted():
    rater = kit.make_rater("human")
    with pytest.raises(ProtectedError):
        rater.user.delete()
