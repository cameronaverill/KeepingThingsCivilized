from django.db import models
from django.db.models import Q

from moderation import clock


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
