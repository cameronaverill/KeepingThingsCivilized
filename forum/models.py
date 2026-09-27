"""The forum's data: topics, conversations, participants and messages (plan section 8).

Rules that matter are enforced twice: in ``save()`` (friendly ``ValidationError``) and by database constraints
(``IntegrityError``), so ``bulk_create``, queryset updates and raw SQL cannot bypass them where SQLite can express them.
Nothing cascades: every relation is PROTECT.
"""

import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import IntegrityError, models, transaction
from django.db.models import F, Max, Q
from django.db.models.functions import Length
from django.db.models.lookups import Exact

from .limits import count_message_chars

_LABEL_RE = re.compile(r"[A-Z]")

KIND_CHOICES = [
    ("paired", "paired"),
    ("series", "series"),
    ("replay", "replay"),
    ("warmup", "warmup"),
    ("observational", "observational"),
]
STATUS_CHOICES = [("open", "open"), ("active", "active"), ("closed", "closed")]
SOURCE_CHOICES = [("human", "human"), ("synthetic", "synthetic")]
AUTHOR_CHOICES = [("user", "user"), ("moderator", "moderator")]
SIDE_CHOICES = [("pro", "pro"), ("con", "con")]


class Topic(models.Model):
    # Optional since step 7: a user-created proposition is displayed by its proposition text alone. When a title is set
    # it is unique (a conditional constraint, so any number of topics may have a blank title).
    title = models.CharField(max_length=200, blank=True, default="")
    description = models.TextField(blank=True, default="")
    proposition = models.TextField(blank=True, default="")
    # The wording of the opposing position ("con"); blank for user-created propositions, whose "con" is the plain
    # choice "I disagree with this position" (step 7c, two-position model).
    opposing_position = models.TextField(blank=True, default="")
    # Per side, per scheme and axis, with rationale (plan section 2).
    leans = models.JSONField(default=dict, blank=True)
    # Null for seeded topics; the user who created a proposition otherwise (plan section 2, step 7).
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="created_topics"
    )
    # Set by an admin: a hidden proposition leaves the home page and cannot be entered; its conversations are kept.
    hidden = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["title"], condition=~Q(title=""), name="forum_topic_title_unique_if_set"),
            models.CheckConstraint(condition=~Q(proposition=""), name="forum_topic_proposition_not_empty"),
        ]

    def __str__(self):
        return self.title or (self.proposition[:60] + ("..." if len(self.proposition) > 60 else ""))

    def validate_unique(self, exclude=None):
        """Keep the friendly field error for a duplicate non-blank title (a conditional constraint alone would report
        it as a non-field error)."""
        errors = {}
        try:
            super().validate_unique(exclude=exclude)
        except ValidationError as error:
            errors = error.update_error_dict(errors)
        if self.title and (exclude is None or "title" not in exclude):
            if Topic.objects.filter(title=self.title).exclude(pk=self.pk).exists():
                errors.setdefault("title", []).append(
                    ValidationError("Topic with this Title already exists.", code="unique")
                )
        if errors:
            raise ValidationError(errors)


class Experiment(models.Model):
    KIND_CHOICES = KIND_CHOICES

    name = models.CharField(max_length=200, unique=True)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    description = models.TextField(blank=True, default="")
    config = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(kind__in=[value for value, _ in KIND_CHOICES]), name="forum_experiment_kind_valid"
            ),
        ]

    def __str__(self):
        return self.name


class Conversation(models.Model):
    STATUS_CHOICES = STATUS_CHOICES
    SOURCE_CHOICES = SOURCE_CHOICES

    topic = models.ForeignKey(Topic, on_delete=models.PROTECT, related_name="conversations")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="open")
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default="human")
    experiment = models.ForeignKey(
        Experiment, null=True, blank=True, on_delete=models.PROTECT, related_name="conversations"
    )
    pair_id = models.CharField(max_length=100, blank=True, default="")
    variant = models.CharField(max_length=100, blank=True, default="")
    # The golden transcript this synthetic conversation came from.
    transcript_id = models.CharField(max_length=100, blank=True, default="")
    label_seed = models.BigIntegerField(null=True, blank=True)
    # Set by forum.services.end_conversation when a participant closes the conversation (a conversation closed by the
    # message limit has neither).
    ended_by = models.ForeignKey(
        "Participant", null=True, blank=True, on_delete=models.PROTECT, related_name="ended_conversations"
    )
    ended_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["experiment", "transcript_id"],
                condition=~Q(transcript_id=""),
                name="forum_conversation_transcript_unique_per_experiment",
            ),
            models.CheckConstraint(
                condition=Q(status__in=[value for value, _ in STATUS_CHOICES]), name="forum_conversation_status_valid"
            ),
            models.CheckConstraint(
                condition=Q(source__in=[value for value, _ in SOURCE_CHOICES]), name="forum_conversation_source_valid"
            ),
        ]

    def __str__(self):
        return f"Conversation {self.pk} ({self.topic_id})"

    def next_seq(self):
        """The seq_no the next message of this conversation gets (1 for the first)."""
        current = Message.objects.filter(conversation_id=self.pk).aggregate(m=Max("seq_no"))["m"]
        return 1 if current is None else current + 1

    def messages_up_to(self, seq_no):
        """Messages with seq_no <= the given one, oldest first."""
        return Message.objects.filter(conversation_id=self.pk, seq_no__lte=seq_no).order_by("seq_no")


class Participant(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.PROTECT, related_name="participants")
    # Null for the participants of synthetic conversations.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="participations"
    )
    label = models.CharField(
        max_length=1,
        validators=[RegexValidator(r"\A[A-Z]\Z", "The label must be one uppercase letter, A to Z.")],
    )
    join_order = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    # The position this person holds: "pro" (Topic.proposition) or "con" (the opposing position). Blank for synthetic
    # participants and for rows that pre-date step 7c (a blank side counts as "pro" when pairing).
    side = models.CharField(max_length=3, blank=True, default="", choices=SIDE_CHOICES)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["conversation", "label"], name="forum_participant_label_unique"),
            models.UniqueConstraint(
                fields=["conversation", "side"],
                condition=~Q(side=""),
                name="forum_participant_side_unique_per_conversation",
            ),
            models.CheckConstraint(condition=Q(side__in=["", "pro", "con"]), name="forum_participant_side_valid"),
            models.UniqueConstraint(fields=["conversation", "join_order"], name="forum_participant_join_order_unique"),
            models.UniqueConstraint(
                fields=["conversation", "user"],
                condition=Q(user__isnull=False),
                name="forum_participant_user_unique",
            ),
            models.CheckConstraint(
                condition=Q(label__gte="A", label__lte="Z") & Q(Exact(Length("label"), 1)),
                name="forum_participant_label_a_to_z",
            ),
            models.CheckConstraint(condition=Q(join_order__gte=1), name="forum_participant_join_order_positive"),
        ]

    def __str__(self):
        return f"Participant {self.label} of conversation {self.conversation_id}"

    def clean_rules(self):
        """Raise ValidationError if this participant breaks a rule that needs more than one field or table."""
        if not isinstance(self.label, str) or not _LABEL_RE.fullmatch(self.label):
            raise ValidationError({"label": "The label must be one uppercase letter, A to Z."})
        if self.join_order is None or self.join_order < 1:
            raise ValidationError({"join_order": "The join order must be a positive number."})
        if self.side not in ("", "pro", "con"):
            raise ValidationError({"side": "The side must be blank, 'pro' or 'con'."})
        conversation = self.conversation
        if conversation.source == "human" and self.user_id is None:
            raise ValidationError({"user": "A participant of a human conversation must have a user."})
        if conversation.source == "synthetic" and self.user_id is not None:
            raise ValidationError({"user": "A participant of a synthetic conversation must not have a user."})
        # Only a new participant can push the count over the limit; updating an existing row never does.
        if self._state.adding and (
            Participant.objects.filter(conversation_id=self.conversation_id).count() >= settings.MAX_PARTICIPANTS
        ):
            raise ValidationError(
                f"A conversation can have at most {settings.MAX_PARTICIPANTS} participants."
            )

    def save(self, *args, **kwargs):
        with transaction.atomic(using=kwargs.get("using")):
            self.clean_rules()
            super().save(*args, **kwargs)


class Message(models.Model):
    AUTHOR_CHOICES = AUTHOR_CHOICES

    conversation = models.ForeignKey(Conversation, on_delete=models.PROTECT, related_name="messages")
    seq_no = models.PositiveIntegerField(blank=True, validators=[MinValueValidator(1)])  # save() assigns it when left empty (None)
    author_type = models.CharField(max_length=10, choices=AUTHOR_CHOICES)
    participant = models.ForeignKey(
        Participant, null=True, blank=True, on_delete=models.PROTECT, related_name="messages"
    )
    in_reply_to = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="replies"
    )
    content = models.TextField()
    char_count = models.PositiveIntegerField(editable=False, validators=[MinValueValidator(1)])  # always computed by save()
    # For synthetic messages: the planted phrases with dimension and intended intensity.
    planted = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["conversation", "seq_no"], name="forum_message_seq_unique"),
            models.CheckConstraint(condition=Q(seq_no__gte=1), name="forum_message_seq_positive"),
            models.CheckConstraint(
                condition=Q(author_type__in=[value for value, _ in AUTHOR_CHOICES]),
                name="forum_message_author_type_valid",
            ),
            models.CheckConstraint(condition=Q(char_count__gte=1), name="forum_message_char_count_positive"),
            models.CheckConstraint(
                condition=~Q(author_type="moderator") | Q(participant__isnull=True),
                name="forum_message_moderator_has_no_participant",
            ),
            models.CheckConstraint(
                condition=~Q(author_type="user") | Q(participant__isnull=False),
                name="forum_message_user_has_participant",
            ),
        ]

    def __str__(self):
        return f"Message {self.seq_no} of conversation {self.conversation_id}"

    def _validate_rules(self):
        """Everything except seq_no assignment. Raises ValidationError."""
        if self.author_type not in ("user", "moderator"):
            raise ValidationError({"author_type": "The author type must be 'user' or 'moderator'."})
        if self.content is None:
            raise ValidationError({"content": "The message is empty."})
        self.char_count = count_message_chars(self.content)
        if self.char_count < 1:
            raise ValidationError({"content": "The message is empty."})
        if self.author_type == "user":
            if self.participant_id is None:
                raise ValidationError({"participant": "A user message needs a participant."})
            if self.participant.conversation_id != self.conversation_id:
                raise ValidationError({"participant": "The participant belongs to a different conversation."})
            if self.char_count > settings.MAX_MESSAGE_CHARS:
                raise ValidationError(
                    {
                        "content": f"The message is {self.char_count} characters; the limit is "
                        f"{settings.MAX_MESSAGE_CHARS}."
                    }
                )
        elif self.participant_id is not None:
            raise ValidationError({"participant": "A moderator message has no participant."})
        if self.in_reply_to_id is not None:
            parent = self.in_reply_to
            if parent.conversation_id != self.conversation_id:
                raise ValidationError({"in_reply_to": "The message replied to is in a different conversation."})
            if self.seq_no is not None and parent.seq_no >= self.seq_no:
                raise ValidationError({"in_reply_to": "A message can only reply to an earlier message."})

    def save(self, *args, **kwargs):
        using = kwargs.get("using")
        with transaction.atomic(using=using):
            creating = self._state.adding
            if creating:
                current = (
                    Message.objects.filter(conversation_id=self.conversation_id).aggregate(m=Max("seq_no"))["m"] or 0
                )
                if self.seq_no is None:
                    self.seq_no = current + 1
                elif self.seq_no < 1:
                    raise ValidationError({"seq_no": "The sequence number must be at least 1."})
                elif self.seq_no <= current:
                    raise ValidationError(
                        {"seq_no": f"The sequence number must be greater than {current}, the current maximum."}
                    )
            elif self.seq_no is None or self.seq_no < 1:
                raise ValidationError({"seq_no": "The sequence number must be at least 1."})
            self._validate_rules()
            super().save(*args, **kwargs)


class Block(models.Model):
    """One person blocking another (step 7c, revision 5). Blocks apply in both directions when people look at or join
    each other's waiting positions; ``forum.services.block_user`` also ends the conversations the two share."""

    blocker = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="blocks_made")
    blocked = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="blocks_received")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["blocker", "blocked"], name="forum_block_pair_unique"),
            models.CheckConstraint(condition=~Q(blocker=F("blocked")), name="forum_block_not_self"),
        ]

    def __str__(self):
        return f"Block {self.blocker_id} -> {self.blocked_id}"

    def save(self, *args, **kwargs):
        if self.blocker_id is not None and self.blocker_id == self.blocked_id:
            raise ValidationError({"blocked": "A person cannot block themselves."})
        super().save(*args, **kwargs)
