"""Generic references to what a rater looks at: a forum `Message` or a moderator `InterventionAct` (plan section 8).

A target is stored as two plain columns, `target_type` (`message` | `intervention_act`) and `target_id`, so the
evaluation tables never depend on a foreign key into the site's tables. The helpers here turn a model instance into
that pair, a pair back into an instance, and give the text that spans (`start`, `end`) index into.
"""

from django.core.exceptions import ValidationError

from forum.models import Message
from moderation.models import InterventionAct

TARGET_MESSAGE = "message"
TARGET_ACT = "intervention_act"
TARGET_TYPES = (TARGET_MESSAGE, TARGET_ACT)
TARGET_TYPE_CHOICES = [(TARGET_MESSAGE, "message"), (TARGET_ACT, "intervention_act")]

_MODELS = {TARGET_MESSAGE: Message, TARGET_ACT: InterventionAct}


def target_ref(target):
    """(target_type, target_id) of a Message or InterventionAct instance."""
    if isinstance(target, Message):
        return TARGET_MESSAGE, target.pk
    if isinstance(target, InterventionAct):
        return TARGET_ACT, target.pk
    raise TypeError(f"A target must be a Message or an InterventionAct, not {type(target).__name__}.")


def resolve_target(target_type, target_id):
    """The Message or InterventionAct for a reference, or None if the type is unknown or the row does not exist."""
    model = _MODELS.get(target_type)
    if model is None or target_id is None:
        return None
    return model.objects.filter(pk=target_id).first()


def target_text(target):
    """The text the spans of findings index into: a message's content or an act's text."""
    if isinstance(target, Message):
        return target.content
    if isinstance(target, InterventionAct):
        return target.text
    raise TypeError(f"A target must be a Message or an InterventionAct, not {type(target).__name__}.")


def validate_target(target_type, target_id):
    """Return the target instance, or raise ValidationError when the type is unknown or the row does not exist."""
    if target_type not in _MODELS:
        raise ValidationError({"target_type": f"The target type must be one of {', '.join(TARGET_TYPES)}."})
    target = resolve_target(target_type, target_id)
    if target is None:
        raise ValidationError({"target_id": f"There is no {target_type} with id {target_id}."})
    return target
