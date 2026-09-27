import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from moderation import clock, taxonomy


def _now():
    # Looked up at call time (not bound at import), so tests that patch moderation.clock.now also move created_at.
    return clock.now()


class LLMCall(models.Model):
    """One row per attempted call to a language model, including refusals. This table is the spend ledger.

    `conversation_id` and `run_id` are plain integers, not foreign keys, on purpose: deleting a conversation or a run
    must never delete its cost records, or the budget could shrink.
    """

    PURPOSE_CHOICES = [
        ("moderation", "moderation"),
        ("spike", "spike"),
        ("golden", "golden"),
        ("replay", "replay"),
        ("judge", "judge"),
    ]
    STATUS_CHOICES = [
        ("pending", "pending"),
        ("ok", "ok"),
        ("error", "error"),
        ("refused_budget", "refused_budget"),
        ("refused_breaker", "refused_breaker"),
        ("refused_disabled", "refused_disabled"),
        ("refused_model", "refused_model"),
    ]

    purpose = models.CharField(max_length=20, choices=PURPOSE_CHOICES)
    run_id = models.IntegerField(null=True, blank=True, db_index=True)
    conversation_id = models.IntegerField(null=True, blank=True, db_index=True)
    agent = models.CharField(max_length=50, blank=True, default="")
    attempt = models.PositiveIntegerField(default=1)
    provider = models.CharField(max_length=30, default="anthropic")
    model = models.CharField(max_length=100)
    prompt_version = models.CharField(max_length=100, blank=True, default="")
    prompt_sha256 = models.CharField(max_length=64, blank=True, default="")
    temperature = models.FloatField(null=True, blank=True)
    max_tokens = models.PositiveIntegerField()
    request = models.JSONField(default=dict, blank=True)
    raw_response = models.TextField(blank=True, default="")
    parsed = models.JSONField(null=True, blank=True)
    tokens_in = models.PositiveIntegerField(default=0)
    tokens_out = models.PositiveIntegerField(default=0)
    cache_write_tokens = models.PositiveIntegerField(default=0)
    cache_read_tokens = models.PositiveIntegerField(default=0)
    reserved_usd = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    cost_usd = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    latency_ms = models.PositiveIntegerField(default=0)
    stop_reason = models.CharField(max_length=50, blank=True, default="")
    provider_request_id = models.CharField(max_length=100, blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    error = models.TextField(blank=True, default="")
    error_code = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(default=_now, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["purpose", "status", "created_at"]),
        ]

    def __str__(self):
        return f"LLMCall {self.pk} {self.purpose}/{self.agent} {self.model} {self.status}"


class GuardState(models.Model):
    """The circuit breaker's state (two tiers: see moderation/breaker.py). Exactly one row, with pk=1 (save() forces it; a constraint forbids any other)."""

    REASON_CHOICES = [
        ("spend_limit", "spend_limit"),
        ("consecutive_errors", "consecutive_errors"),
        ("auth_error", "auth_error"),
        ("billing_error", "billing_error"),
        ("manual", "manual"),
    ]
    KIND_CHOICES = [("hard", "hard"), ("soft", "soft")]

    breaker_tripped = models.BooleanField(default=False)
    tripped_at = models.DateTimeField(null=True, blank=True)
    trip_reason = models.CharField(max_length=30, choices=REASON_CHOICES, blank=True, default="")
    trip_detail = models.TextField(blank=True, default="")
    consecutive_errors = models.PositiveIntegerField(default=0)
    last_error_at = models.DateTimeField(null=True, blank=True)
    # Two-tier breaker: "hard" needs `manage.py reset_breaker`; "soft" recovers by itself after `cooldown_until`
    # through one half-open probe call (probe_in_flight / probe_started_at).
    trip_kind = models.CharField(max_length=10, choices=KIND_CHOICES, blank=True, default="")
    cooldown_until = models.DateTimeField(null=True, blank=True)
    cooldown_seconds = models.PositiveIntegerField(default=0)
    probe_in_flight = models.BooleanField(default=False)
    probe_started_at = models.DateTimeField(null=True, blank=True)
    # Email alerts: when the last one was sent, and the error text if the last attempt failed.
    last_alert_at = models.DateTimeField(null=True, blank=True)
    last_alert_error = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(pk=1), name="guardstate_single_row"),
        ]

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


# ---------------------------------------------------------------------------------------------------------------------
# Step 4b: the moderation run and what it produced (plan section 8). Rules are enforced twice: in save() (friendly
# ValidationError) and, where a rule crosses tables, by SQLite triggers created in migration 0004 (IntegrityError).
# ---------------------------------------------------------------------------------------------------------------------

def _choices(values):
    return [(v, v) for v in values]


def _invalid(field, message):
    return ValidationError({field: message})


class ModerationRun(models.Model):
    """One pass of the Master Moderator and Intervenor over one user message (live), or a replay of such a pass.

    No-self-reply rule (plan section 2, layer 2): `trigger_message` is always a user-authored message, checked in
    save() and by database triggers. `LLMCall` keeps plain integer `run_id`, so `llm_calls()` filters by number.
    """

    KIND_CHOICES = _choices(("live", "replay"))
    STATUS_CHOICES = _choices(("pending", "running", "done", "failed", "skipped_budget", "skipped_disabled"))
    DECISION_CHOICES = [("", "")] + _choices(taxonomy.DECISIONS)

    conversation = models.ForeignKey("forum.Conversation", on_delete=models.PROTECT, related_name="moderation_runs")
    trigger_message = models.ForeignKey("forum.Message", on_delete=models.PROTECT, related_name="moderation_runs")
    snapshot_seq = models.PositiveIntegerField()
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default="live")
    replay_of = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="replays")
    replicate = models.IntegerField(default=1)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    attempts = models.IntegerField(default=0)
    is_stale = models.BooleanField(default=False)
    decision = models.CharField(max_length=20, choices=DECISION_CHOICES, blank=True, default="")
    rationale = models.TextField(blank=True, default="")
    posted_message = models.OneToOneField(
        "forum.Message", null=True, blank=True, on_delete=models.PROTECT, related_name="posted_by_run"
    )
    failure_reason = models.CharField(max_length=50, blank=True, default="")
    error = models.TextField(blank=True, default="")
    config_snapshot = models.JSONField(default=dict, blank=True)
    discussion_map = models.JSONField(default=dict, blank=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=_now, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["trigger_message"], condition=Q(kind="live"), name="moderationrun_one_live_per_trigger"
            ),
            models.CheckConstraint(
                condition=~Q(kind="replay") | Q(posted_message__isnull=True), name="moderationrun_replay_never_posts"
            ),
            models.CheckConstraint(condition=Q(attempts__gte=0), name="moderationrun_attempts_nonnegative"),
            models.CheckConstraint(condition=Q(replicate__gte=1), name="moderationrun_replicate_positive"),
        ]

    def __str__(self):
        return f"ModerationRun {self.pk} {self.kind} trigger={self.trigger_message_id} {self.status}"

    def llm_calls(self):
        return LLMCall.objects.filter(run_id=self.pk)

    def validate_rules(self):
        trigger = self.trigger_message
        if trigger.author_type != "user":
            raise _invalid("trigger_message", "A moderation run can only be triggered by a user message.")
        if trigger.conversation_id != self.conversation_id:
            raise _invalid("trigger_message", "The trigger message must belong to the run's conversation.")
        if self.snapshot_seq != trigger.seq_no:
            raise _invalid("snapshot_seq", "snapshot_seq must equal the trigger message's seq_no.")
        if self.kind == "live" and self.replay_of_id is not None:
            raise _invalid("replay_of", "A live run cannot be a replay of another run.")
        if self.kind == "replay" and self.posted_message_id is not None:
            raise _invalid("posted_message", "A replay never posts a message.")
        if self.posted_message_id is not None:
            posted = self.posted_message
            if posted.author_type != "moderator":
                raise _invalid("posted_message", "The posted message must be a moderator message.")
            if posted.conversation_id != self.conversation_id:
                raise _invalid("posted_message", "The posted message must belong to the run's conversation.")
            if posted.in_reply_to_id != self.trigger_message_id:
                raise _invalid("posted_message", "The posted message must reply to the trigger message.")

    def save(self, *args, **kwargs):
        self.validate_rules()
        super().save(*args, **kwargs)


class Issue(models.Model):
    """One problem the Master Moderator reported, tied to a phrase of a message (plan sections 5 and 6)."""

    QUOTE_MATCH_CHOICES = _choices(("exact", "normalized", "not_found"))
    VALIDITY_CHOICES = _choices(("valid", "rejected"))
    DIMENSION_CHOICES = [("", "")] + _choices(taxonomy.DIMENSIONS)

    run = models.ForeignKey(ModerationRun, on_delete=models.PROTECT, related_name="issues")
    local_id = models.CharField(max_length=50)
    message = models.ForeignKey("forum.Message", on_delete=models.PROTECT, related_name="issues")
    issue_type = models.CharField(max_length=40, choices=_choices(taxonomy.ISSUE_TYPES))
    dimension = models.CharField(max_length=30, choices=DIMENSION_CHOICES, blank=True, default="")
    quote = models.TextField(blank=True, default="")
    quote_start = models.PositiveIntegerField(null=True, blank=True)
    quote_end = models.PositiveIntegerField(null=True, blank=True)
    quote_match = models.CharField(max_length=12, choices=QUOTE_MATCH_CHOICES, default="not_found")
    explanation = models.TextField(blank=True, default="")
    confidence = models.FloatField(default=0.0)
    intensity = models.PositiveSmallIntegerField(null=True, blank=True)
    validity = models.CharField(max_length=10, choices=VALIDITY_CHOICES, default="valid")
    rejection_reason = models.TextField(blank=True, default="")
    time_sensitive = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["run", "local_id"], name="issue_unique_local_id_per_run"),
            models.CheckConstraint(
                condition=Q(intensity__isnull=True)
                | Q(intensity__gte=taxonomy.INTENSITY_RANGE[0], intensity__lte=taxonomy.INTENSITY_RANGE[1]),
                name="issue_intensity_in_range",
            ),
            models.CheckConstraint(
                condition=(
                    Q(quote_match="not_found", quote_start__isnull=True, quote_end__isnull=True)
                    | (
                        ~Q(quote_match="not_found")
                        & Q(quote_start__isnull=False, quote_end__isnull=False)
                    )
                ),
                name="issue_not_found_iff_no_offsets",
            ),
        ]

    def __str__(self):
        return f"Issue {self.pk} {self.issue_type} run={self.run_id} {self.validity}"

    def validate_rules(self):
        run = self.run
        message = self.message
        if message.conversation_id != run.conversation_id:
            raise _invalid("message", "The message must belong to the run's conversation.")
        if message.seq_no > run.snapshot_seq:
            raise _invalid("message", "The message is later than the run's snapshot.")
        start, end = self.quote_start, self.quote_end
        if (self.quote_match == "not_found") != (start is None and end is None):
            raise _invalid("quote_match", "quote_match is not_found if and only if both offsets are empty.")
        if start is not None or end is not None:
            if start is None or end is None or not (0 <= start < end <= len(message.content)):
                raise _invalid("quote_start", "Offsets must satisfy 0 <= start < end <= len(message content).")
            if self.quote_match == "exact" and message.content[start:end] != self.quote:
                raise _invalid("quote", "For an exact match the quote must equal the message text at the offsets.")
        if self.intensity is not None:
            low, high = taxonomy.INTENSITY_RANGE
            if not low <= self.intensity <= high:
                raise _invalid("intensity", f"intensity must be between {low} and {high}.")
            if not self.dimension:
                raise _invalid("intensity", "An issue without a dimension has no intensity.")
        if message.author_type == "moderator" and self.validity != "rejected":
            raise _invalid("validity", "An issue on a moderator message must be rejected.")
        if self.validity == "rejected" and not self.rejection_reason.strip():
            raise _invalid("rejection_reason", "A rejected issue needs a rejection reason.")

    def save(self, *args, **kwargs):
        self.dimension = taxonomy.dimension_for(self.issue_type) or ""
        self.validate_rules()
        super().save(*args, **kwargs)


class IssueDisposition(models.Model):
    """What the Intervenor did with one valid issue."""

    issue = models.OneToOneField(Issue, on_delete=models.PROTECT, related_name="disposition")
    disposition = models.CharField(max_length=10, choices=_choices(taxonomy.DISPOSITIONS))
    reason = models.TextField(blank=True, default="")

    def __str__(self):
        return f"IssueDisposition issue={self.issue_id} {self.disposition}"

    def save(self, *args, **kwargs):
        if self.issue.validity != "valid":
            raise _invalid("issue", "Only a valid issue can have a disposition.")
        super().save(*args, **kwargs)


_LABEL_RE = re.compile(r"^[A-Z]$")
_QUOTED_SPAN_RE = re.compile(r'"[^"]+"|\u201c[^\u201d]+\u201d')


class InterventionAct(models.Model):
    """One act of a moderator intervention (one to `MAX_ACTS_PER_INTERVENTION` per run), with features computed in code."""

    VALIDITY_CHOICES = Issue.VALIDITY_CHOICES

    run = models.ForeignKey(ModerationRun, on_delete=models.PROTECT, related_name="acts")
    order = models.PositiveIntegerField()
    act_type = models.CharField(max_length=40, choices=_choices(taxonomy.ACT_TYPES))
    tone = models.CharField(max_length=10, choices=_choices(taxonomy.TONES))
    text = models.TextField()
    addressee = models.CharField(max_length=3)
    subject = models.CharField(max_length=4)
    validity = models.CharField(max_length=10, choices=VALIDITY_CHOICES, default="valid")
    rejection_reason = models.TextField(blank=True, default="")
    char_len = models.PositiveIntegerField(default=0)
    word_count = models.PositiveIntegerField(default=0)
    is_question = models.BooleanField(default=False)
    quotes_participant = models.BooleanField(default=False)
    source_issues = models.ManyToManyField(Issue, blank=True, related_name="acts")
    source_messages = models.ManyToManyField("forum.Message", blank=True, related_name="acts_sourced")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["run", "order"], name="interventionact_unique_order_per_run"),
            models.CheckConstraint(condition=Q(order__gte=1), name="interventionact_order_positive"),
            models.CheckConstraint(
                condition=~Q(validity="rejected") | ~Q(rejection_reason=""),
                name="interventionact_rejected_needs_reason",
            ),
        ]

    def __str__(self):
        return f"InterventionAct run={self.run_id} #{self.order} {self.act_type}"

    def compute_features(self):
        self.char_len = len(self.text)
        self.word_count = len(self.text.split())
        self.is_question = "?" in self.text
        self.quotes_participant = bool(_QUOTED_SPAN_RE.search(self.text))

    def validate_rules(self):
        if not self.order or self.order < 1:
            raise _invalid("order", "order must be at least 1.")
        if not (self.addressee == "all" or _LABEL_RE.match(self.addressee)):
            raise _invalid("addressee", "addressee must be a participant label (A to Z) or 'all'.")
        if not (self.subject in ("both", "none") or _LABEL_RE.match(self.subject)):
            raise _invalid("subject", "subject must be a participant label (A to Z), 'both' or 'none'.")
        if self.validity == "rejected" and not self.rejection_reason.strip():
            raise _invalid("rejection_reason", "A rejected act needs a rejection reason.")
        if self.validity == "valid":
            others = InterventionAct.objects.filter(run_id=self.run_id, validity="valid")
            if self.pk is not None:
                others = others.exclude(pk=self.pk)
            if others.count() + 1 > settings.MAX_ACTS_PER_INTERVENTION:
                raise _invalid(
                    "validity", f"A run can have at most {settings.MAX_ACTS_PER_INTERVENTION} valid acts."
                )

    def save(self, *args, **kwargs):
        self.compute_features()
        self.validate_rules()
        super().save(*args, **kwargs)


def _check_sources(instance, action, reverse, model, pk_set, *, issues):
    """Source issues must belong to the act's run and source messages to its conversation."""
    if action != "pre_add" or not pk_set:
        return
    if reverse:
        # `issue.acts.add(act)` / `message.acts_sourced.add(act)`: instance is the issue or message, pk_set holds acts.
        acts = InterventionAct.objects.filter(pk__in=pk_set).select_related("run")
        for act in acts:
            ok = (
                instance.run_id == act.run_id if issues else instance.conversation_id == act.run.conversation_id
            )
            if not ok:
                raise ValidationError("A source must belong to the act's own run (issues) or conversation (messages).")
        return
    if issues:
        if Issue.objects.filter(pk__in=pk_set).exclude(run_id=instance.run_id).exists():
            raise ValidationError("Source issues must belong to the same run as the act.")
    else:
        conversation_id = instance.run.conversation_id
        if model.objects.filter(pk__in=pk_set).exclude(conversation_id=conversation_id).exists():
            raise ValidationError("Source messages must belong to the same conversation as the act.")


@receiver(m2m_changed, sender=InterventionAct.source_issues.through)
def _validate_source_issues(sender, instance, action, reverse, model, pk_set, **kwargs):
    _check_sources(instance, action, reverse, model, pk_set, issues=True)


@receiver(m2m_changed, sender=InterventionAct.source_messages.through)
def _validate_source_messages(sender, instance, action, reverse, model, pk_set, **kwargs):
    _check_sources(instance, action, reverse, model, pk_set, issues=False)


# ---------------------------------------------------------------------------------------------------------------------
# Step 19: the intervention preview (docs/plan.md section 14, step 19; docs/step19_backend_brief.md). Both tables use real
# foreign keys, all PROTECT (so a conversation, participant, message or run that a preview record points at cannot be
# deleted until the record is removed). Only `llm_call_ids` (a JSON list of ledger ids) stays plain: the ledger must stay
# independent of every other table. Everything here is private to the author of a draft.
# ---------------------------------------------------------------------------------------------------------------------

PREVIEW_MODES = ("on", "off")
PREVIEW_OUTCOMES = ("no_concern", "concern", "unavailable")
PREVIEW_UNAVAILABLE_REASONS = (
    "off", "rate_limited", "llm_disabled", "budget", "breaker", "refused", "structural", "api_error", "internal_error",
)
PREVIEW_ACTIONS = ("posted_as_written", "edited", "abandoned")


class PreviewMode(models.Model):
    """Whether a conversation gets previews. Drawn once, the first time the conversation is checked (see
    `moderation/preview.py`), and never redrawn: later changes of `PREVIEW_SHARE` do not touch existing conversations.
    Both participants share it."""

    MODE_CHOICES = _choices(PREVIEW_MODES)

    conversation = models.OneToOneField(
        "forum.Conversation", on_delete=models.PROTECT, related_name="preview_mode_record"
    )
    mode = models.CharField(max_length=3, choices=MODE_CHOICES)
    assigned_at = models.DateTimeField(default=_now)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(mode__in=PREVIEW_MODES), name="previewmode_mode_valid"),
        ]

    def __str__(self):
        return f"PreviewMode conversation={self.conversation} {self.mode}"


class PreviewCheck(models.Model):
    """One check of one draft, before it was posted. The draft text is kept (it is what lets the analysis compare drafts
    with the final text); it is shown in the admin detail page only and never sent to a prompt, an export or a log."""

    OUTCOME_CHOICES = _choices(PREVIEW_OUTCOMES)
    UNAVAILABLE_REASON_CHOICES = [("", "")] + _choices(PREVIEW_UNAVAILABLE_REASONS)
    ACTION_CHOICES = [("", "")] + _choices(PREVIEW_ACTIONS)

    conversation = models.ForeignKey("forum.Conversation", on_delete=models.PROTECT, related_name="preview_checks")
    participant = models.ForeignKey("forum.Participant", on_delete=models.PROTECT, related_name="preview_checks")
    draft_text = models.TextField()
    char_count = models.PositiveIntegerField()
    # SHA-256 (hex) of the draft as forum.limits.count_message_chars normalises it: CRLF/CR to LF, NFC, stripped.
    draft_sha256 = models.CharField(max_length=64)
    # The highest message seq_no in the conversation when the draft was checked.
    snapshot_seq = models.PositiveIntegerField()
    mode = models.CharField(max_length=3, choices=PreviewMode.MODE_CHOICES)
    outcome = models.CharField(max_length=12, choices=OUTCOME_CHOICES)
    unavailable_reason = models.CharField(max_length=20, choices=UNAVAILABLE_REASON_CHOICES, blank=True, default="")
    # The valid act texts shown to the author, in order (empty when there is no concern).
    note_texts = models.JSONField(default=list, blank=True)
    # The validated model outputs, with the draft's placeholder message id (-1); null when no model output exists.
    master_output = models.JSONField(null=True, blank=True)
    intervenor_output = models.JSONField(null=True, blank=True)
    llm_call_ids = models.JSONField(default=list, blank=True)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES, blank=True, default="")
    resulting_message = models.ForeignKey(
        "forum.Message", null=True, blank=True, on_delete=models.PROTECT, related_name="preview_checks_resulting"
    )
    reused_by_run = models.ForeignKey(
        ModerationRun, null=True, blank=True, on_delete=models.PROTECT, related_name="preview_checks_reused"
    )
    created_at = models.DateTimeField(default=_now, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["participant", "created_at"], name="previewcheck_participant_time"),
            models.Index(fields=["conversation", "participant", "draft_sha256"], name="previewcheck_reuse_lookup"),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(outcome__in=PREVIEW_OUTCOMES), name="previewcheck_outcome_valid"),
            models.CheckConstraint(condition=Q(action__in=("",) + PREVIEW_ACTIONS), name="previewcheck_action_valid"),
            models.CheckConstraint(
                condition=Q(action="") | Q(resolved_at__isnull=False), name="previewcheck_resolved_has_time"
            ),
            models.CheckConstraint(condition=Q(mode__in=PREVIEW_MODES), name="previewcheck_mode_valid"),
        ]

    def __str__(self):
        return f"PreviewCheck {self.pk} conversation={self.conversation} {self.outcome}"
