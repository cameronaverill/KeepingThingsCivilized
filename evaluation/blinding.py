"""What a rater may see (plan section 9, "Blinding, for everyone").

`blinded_view(target)` returns a plain, JSON-serializable dict with only these fields:

    {
        "proposition": <the topic's proposition; "" when the topic has none>,
        "message": {"text": <the text to rate, or None>, "automated_reply": <bool>},
        "context": [{"label": "earlier message", "text": <text>}, ...],   # oldest first
    }

Nothing else is ever included: no usernames or emails, no `Topic.leans`, title or description, no experiment, variant
or pair labels, no participant labels (context messages are labelled "earlier message" and nothing more), no
identifiers, no moderator output (issues, acts, decisions, moderator messages, runs), and nothing from other raters.

* A user message: its text; `automated_reply` is False.
* A moderator message: the text is withheld (`None`) and `automated_reply` is True, so the view says only that it is an
  automated reply. (Raters rate a moderator's words through its `InterventionAct` targets.)
* An intervention act: its text (the thing being rated) and `automated_reply` True; the context is what the
  moderator was responding to, up to and including the message that triggered the run.

Context is the last `BLINDED_CONTEXT_MESSAGES` earlier USER messages of the conversation. Earlier moderator messages are
left out: they are moderator output.
"""

from django.conf import settings

from forum.models import Message
from moderation.models import InterventionAct

CONTEXT_LABEL = "earlier message"


def _context(conversation_id, up_to_seq, inclusive):
    limit = settings.BLINDED_CONTEXT_MESSAGES
    if limit <= 0:
        return []
    bound = {"seq_no__lte": up_to_seq} if inclusive else {"seq_no__lt": up_to_seq}
    texts = list(
        Message.objects.filter(conversation_id=conversation_id, author_type="user", **bound)
        .order_by("-seq_no")
        .values_list("content", flat=True)[:limit]
    )
    return [{"label": CONTEXT_LABEL, "text": text} for text in reversed(texts)]


def blinded_view(target):
    """The blinded view of a Message or an InterventionAct (see the module docstring). Deterministic."""
    if isinstance(target, Message):
        conversation = target.conversation
        automated = target.author_type == "moderator"
        text = None if automated else target.content
        context = _context(conversation.pk, target.seq_no, inclusive=False)
    elif isinstance(target, InterventionAct):
        run = target.run
        conversation = run.conversation
        automated = True
        text = target.text
        context = _context(conversation.pk, run.snapshot_seq, inclusive=True)
    else:
        raise TypeError(f"A target must be a Message or an InterventionAct, not {type(target).__name__}.")
    return {
        "proposition": conversation.topic.proposition,
        "message": {"text": text, "automated_reply": automated},
        "context": context,
    }
