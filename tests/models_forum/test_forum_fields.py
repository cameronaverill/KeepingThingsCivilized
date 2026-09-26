"""Topic, Experiment, Conversation: fields, defaults, choices and constraints (brief 4a)."""
import pytest
from django.core.exceptions import ValidationError
from django.db import models

from forum_testkit import make_conversation, make_experiment, make_topic, refused, refused_by_database

pytestmark = pytest.mark.django_db


def field_names(model):
    return {f.name for f in model._meta.get_fields() if not f.auto_created or f.concrete}


def choice_values(model, name):
    return [value for value, _label in model._meta.get_field(name).choices]


def test_the_forum_app_is_installed_with_the_label_forum():
    from django.apps import apps
    from django.conf import settings

    assert "forum" in settings.INSTALLED_APPS
    assert apps.get_app_config("forum").label == "forum"


def test_topic_has_every_contract_field():
    from forum.models import Topic

    assert {"title", "description", "proposition", "leans", "created_at"} <= field_names(Topic)


def test_experiment_has_every_contract_field():
    from forum.models import Experiment

    assert {"name", "kind", "description", "config", "created_at"} <= field_names(Experiment)


def test_conversation_has_every_contract_field():
    from forum.models import Conversation

    expected = {
        "topic", "status", "source", "experiment", "pair_id", "variant", "transcript_id", "label_seed", "created_at",
    }  # fmt: skip
    assert expected <= field_names(Conversation)


# --- Topic ---------------------------------------------------------------------------------------------------------


def test_topic_leans_defaults_to_an_empty_dict_that_is_not_shared():
    from forum.models import Topic

    first = make_topic()
    second = make_topic()
    assert first.leans == {} and second.leans == {}
    assert Topic._meta.get_field("leans").default is dict
    first.leans["x"] = 1
    assert second.leans == {}


def test_topic_leans_round_trips_nested_json():
    from forum.models import Topic

    leans = {"side_a": {"compass": {"economic": -0.5, "social": 0.25, "rationale": "why"}}, "side_b": {}}
    topic = Topic.objects.create(title="t", description="d", proposition="p", leans=leans)
    assert Topic.objects.get(pk=topic.pk).leans == leans


def test_topic_created_at_is_set_automatically():
    assert make_topic().created_at is not None


def test_topic_text_fields_round_trip_and_leans_is_a_json_field():
    from forum.models import Topic

    assert isinstance(Topic._meta.get_field("leans"), models.JSONField)
    topic = Topic.objects.create(title="T\u00e9st title", description="line1\nline2", proposition="Yes or no?")
    fresh = Topic.objects.get(pk=topic.pk)
    assert (fresh.title, fresh.description, fresh.proposition) == ("T\u00e9st title", "line1\nline2", "Yes or no?")


def test_topic_title_is_unique_at_model_level():
    from forum.models import Topic

    make_topic("same")
    with pytest.raises(ValidationError) as excinfo:
        Topic(title="same", description="", proposition="").full_clean()
    assert "title" in excinfo.value.message_dict


def test_topic_title_is_unique_in_the_database_even_with_bulk_create():
    from forum.models import Topic

    make_topic("same")
    refused_by_database(lambda: Topic.objects.bulk_create([Topic(title="same", description="", proposition="")]))


def test_saving_a_duplicate_topic_title_is_refused():
    from forum.models import Topic

    make_topic("same")
    with refused():
        Topic.objects.create(title="same", description="", proposition="")


# --- Experiment ----------------------------------------------------------------------------------------------------


def test_experiment_kind_choices_are_exactly_the_contract():
    from forum.models import Experiment

    assert sorted(choice_values(Experiment, "kind")) == ["observational", "paired", "replay", "series", "warmup"]


@pytest.mark.parametrize("kind", ["paired", "series", "replay", "warmup", "observational"])
def test_every_experiment_kind_is_valid(kind):
    exp = make_experiment(kind=kind)
    exp.full_clean()
    assert exp.kind == kind


def test_an_unknown_experiment_kind_is_rejected_by_validation():
    from forum.models import Experiment

    with pytest.raises(ValidationError) as excinfo:
        Experiment(name="n", kind="nonsense").full_clean()
    assert "kind" in excinfo.value.message_dict


def test_experiment_config_defaults_to_an_empty_dict_and_round_trips():
    from forum.models import Experiment

    exp = make_experiment()
    assert exp.config == {}
    assert Experiment._meta.get_field("config").default is dict
    exp2 = Experiment.objects.create(name="with config", kind="series", config={"n": 3, "arms": ["a", "b"]})
    assert Experiment.objects.get(pk=exp2.pk).config == {"n": 3, "arms": ["a", "b"]}


def test_experiment_created_at_is_set_automatically_and_description_may_be_blank():
    exp = make_experiment()
    assert exp.created_at is not None
    exp.description = ""
    exp.full_clean()


def test_experiment_name_is_unique_at_model_level():
    from forum.models import Experiment

    make_experiment("dup")
    with pytest.raises(ValidationError) as excinfo:
        Experiment(name="dup", kind="paired").full_clean()
    assert "name" in excinfo.value.message_dict


def test_experiment_name_is_unique_in_the_database_even_with_bulk_create():
    from forum.models import Experiment

    make_experiment("dup")
    refused_by_database(lambda: Experiment.objects.bulk_create([Experiment(name="dup", kind="replay")]))


def test_saving_a_duplicate_experiment_name_is_refused():
    from forum.models import Experiment

    make_experiment("dup")
    with refused():
        Experiment.objects.create(name="dup", kind="replay")


# --- Conversation --------------------------------------------------------------------------------------------------


def test_conversation_defaults():
    conv = make_conversation()
    conv.refresh_from_db()
    assert conv.status == "open"
    assert conv.source == "human"
    assert conv.experiment is None
    assert conv.pair_id == ""
    assert conv.variant == ""
    assert conv.transcript_id == ""
    assert conv.label_seed is None
    assert conv.created_at is not None


def test_conversation_status_choices_are_exactly_the_contract():
    from forum.models import Conversation

    assert sorted(choice_values(Conversation, "status")) == ["active", "closed", "open"]
    assert Conversation._meta.get_field("status").default == "open"


def test_conversation_source_choices_are_exactly_the_contract():
    from forum.models import Conversation

    assert sorted(choice_values(Conversation, "source")) == ["human", "synthetic"]
    assert Conversation._meta.get_field("source").default == "human"


@pytest.mark.parametrize("status", ["open", "active", "closed"])
def test_every_status_is_valid(status):
    conv = make_conversation(status=status)
    conv.full_clean()


@pytest.mark.parametrize("source", ["human", "synthetic"])
def test_every_source_is_valid(source):
    conv = make_conversation(source)
    conv.full_clean()


@pytest.mark.parametrize("field, bad", [("status", "archived"), ("source", "robot")])
def test_an_unknown_status_or_source_is_rejected_by_validation(field, bad):
    conv = make_conversation()
    setattr(conv, field, bad)
    with pytest.raises(ValidationError) as excinfo:
        conv.full_clean()
    assert field in excinfo.value.message_dict


def test_blank_string_fields_are_allowed_and_filled_fields_round_trip():
    from forum.models import Conversation

    conv = make_conversation(experiment=make_experiment(), pair_id="pair-1", variant="left", transcript_id="t01")
    conv.full_clean()
    fresh = Conversation.objects.get(pk=conv.pk)
    assert (fresh.pair_id, fresh.variant, fresh.transcript_id) == ("pair-1", "left", "t01")
    for name in ("pair_id", "variant", "transcript_id"):
        assert Conversation._meta.get_field(name).blank is True


def test_label_seed_is_a_nullable_big_integer_and_holds_64_bit_values():
    from forum.models import Conversation

    field = Conversation._meta.get_field("label_seed")
    assert isinstance(field, models.BigIntegerField)
    assert field.null is True
    big = 2**62 + 12345
    conv = make_conversation(label_seed=big)
    assert Conversation.objects.get(pk=conv.pk).label_seed == big
    neg = make_conversation(label_seed=-(2**62))
    assert Conversation.objects.get(pk=neg.pk).label_seed == -(2**62)


def test_topic_is_required_and_experiment_is_optional():
    from forum.models import Conversation

    assert Conversation._meta.get_field("topic").null is False
    assert Conversation._meta.get_field("experiment").null is True
    with refused():
        Conversation.objects.create()


def test_foreign_keys_use_protect():
    from forum.models import Conversation, Message, Participant

    for model, name in [
        (Conversation, "topic"), (Conversation, "experiment"), (Participant, "conversation"), (Participant, "user"),
        (Message, "conversation"), (Message, "participant"), (Message, "in_reply_to"),
    ]:  # fmt: skip
        assert model._meta.get_field(name).remote_field.on_delete is models.PROTECT, (model.__name__, name)


def test_the_user_foreign_key_points_at_the_configured_user_model_and_is_nullable():
    from django.conf import settings

    from forum.models import Participant

    field = Participant._meta.get_field("user")
    assert field.null is True
    assert field.remote_field.model._meta.label == settings.AUTH_USER_MODEL


# --- transcript_id unique per experiment when not blank -------------------------------------------------------------


def test_transcript_id_repeats_within_an_experiment_are_refused_at_model_level():
    from forum.models import Conversation

    exp = make_experiment()
    make_conversation("synthetic", experiment=exp, transcript_id="t1")
    dup = Conversation(topic=make_topic(), source="synthetic", experiment=exp, transcript_id="t1")
    with pytest.raises(ValidationError):
        dup.full_clean()


def test_transcript_id_repeats_within_an_experiment_are_refused_on_save():
    from forum.models import Conversation

    exp = make_experiment()
    make_conversation("synthetic", experiment=exp, transcript_id="t1")
    with refused():
        Conversation.objects.create(topic=make_topic(), source="synthetic", experiment=exp, transcript_id="t1")


def test_transcript_id_repeats_within_an_experiment_are_refused_by_the_database():
    from forum.models import Conversation

    exp = make_experiment()
    topic = make_topic()
    make_conversation("synthetic", topic=topic, experiment=exp, transcript_id="t1")
    refused_by_database(
        lambda: Conversation.objects.bulk_create(
            [Conversation(topic=topic, source="synthetic", experiment=exp, transcript_id="t1")]
        )
    )


def test_transcript_id_update_to_a_taken_value_is_refused_by_the_database():
    from forum.models import Conversation

    exp = make_experiment()
    topic = make_topic()
    make_conversation("synthetic", topic=topic, experiment=exp, transcript_id="t1")
    other = make_conversation("synthetic", topic=topic, experiment=exp, transcript_id="t2")
    refused_by_database(lambda: Conversation.objects.filter(pk=other.pk).update(transcript_id="t1"))


def test_the_same_transcript_id_may_be_used_in_different_experiments():
    topic = make_topic()
    make_conversation("synthetic", topic=topic, experiment=make_experiment(), transcript_id="t1")
    other = make_conversation("synthetic", topic=topic, experiment=make_experiment(), transcript_id="t1")
    other.full_clean()


def test_blank_transcript_ids_may_repeat_within_an_experiment():
    from forum.models import Conversation

    exp = make_experiment()
    topic = make_topic()
    make_conversation(topic=topic, experiment=exp)
    make_conversation(topic=topic, experiment=exp)
    Conversation.objects.bulk_create(
        [Conversation(topic=topic, experiment=exp), Conversation(topic=topic, experiment=exp)]
    )
    assert Conversation.objects.filter(experiment=exp, transcript_id="").count() == 4


def test_a_conversation_can_be_resaved_without_tripping_its_own_transcript_constraint():
    conv = make_conversation("synthetic", experiment=make_experiment(), transcript_id="t1")
    conv.status = "closed"
    conv.save()
    conv.full_clean()
