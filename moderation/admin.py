"""Minimal, read-only admin for the moderation tables (plan section 12, step 9c).

Nothing here can be added, changed or deleted through the admin, and no admin action exists, so the admin can never
spend money or call the model. The breaker is reset with ``manage.py reset_breaker``.

Privacy: ``LLMCall.request``, ``raw_response`` and ``parsed`` are never shown in a list. The detail page shows each of
them cut to ``ADMIN_RAW_DISPLAY_CHARS`` characters (``settings.ADMIN_RAW_DISPLAY_CHARS``, 5,000 by default).
"""

import json

from django.conf import settings
from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html, format_html_join

from .models import (
    GuardState,
    InterventionAct,
    Issue,
    IssueDisposition,
    LLMCall,
    ModerationRun,
    PreviewCheck,
    PreviewMode,
)


def _truncated(value):
    """Text for a detail page: at most settings.ADMIN_RAW_DISPLAY_CHARS characters of ``value``, then a one-line note."""
    if value is None or value == "" or value == {}:
        return "-"
    limit = settings.ADMIN_RAW_DISPLAY_CHARS
    text = value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return format_html("<pre style='white-space: pre-wrap'>{}</pre>", text)
    return format_html(
        "<pre style='white-space: pre-wrap'>{}</pre><p><em>Truncated: showing the first {} of {} characters.</em></p>",
        text[:limit],
        limit,
        len(text),
    )


class ReadOnlyAdmin(admin.ModelAdmin):
    """No add, change or delete for anyone (superusers included). Viewing follows Django's view permission."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ReadOnlyTabularInline(admin.TabularInline):
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class IssueInline(ReadOnlyTabularInline):
    model = Issue
    fields = ("local_id", "issue_type", "dimension", "intensity", "validity", "quote_match", "confidence", "message")
    readonly_fields = fields


class InterventionActInline(ReadOnlyTabularInline):
    model = InterventionAct
    fields = ("order", "act_type", "tone", "validity", "addressee", "subject", "char_len", "word_count")
    readonly_fields = fields


@admin.register(ModerationRun)
class ModerationRunAdmin(ReadOnlyAdmin):
    list_display = (
        "id",
        "conversation",
        "kind",
        "status",
        "failure_reason",
        "attempts",
        "is_stale",
        "decision",
        "created_at",
    )
    list_filter = ("status", "kind", "decision")
    search_fields = ("=conversation__id",)
    list_select_related = ("conversation",)
    ordering = ("-id",)
    inlines = (IssueInline, InterventionActInline)
    fields = (
        "conversation",
        "trigger_message",
        "snapshot_seq",
        "kind",
        "replay_of",
        "replicate",
        "status",
        "attempts",
        "is_stale",
        "decision",
        "rationale",
        "posted_message",
        "failure_reason",
        "error",
        "config_snapshot",
        "discussion_map",
        "claimed_at",
        "started_at",
        "finished_at",
        "created_at",
        "llm_call_rows",
    )
    readonly_fields = fields

    # LLMCall keeps a plain integer run_id (so cost records outlive their run), so it cannot be an inline (an inline
    # needs a foreign key). This read-only table, with links to each call's own page, takes its place.
    @admin.display(description="LLM calls")
    def llm_call_rows(self, obj):
        calls = list(
            LLMCall.objects.filter(run_id=obj.pk)
            .order_by("id")
            .only("id", "agent", "attempt", "model", "status", "tokens_in", "tokens_out", "cost_usd")
        )
        if not calls:
            return "-"
        rows = format_html_join(
            "",
            "<tr><td><a href='{}'>{}</a></td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>",
            (
                (
                    reverse("admin:moderation_llmcall_change", args=[c.pk]),
                    c.pk,
                    c.agent,
                    c.attempt,
                    c.model,
                    c.status,
                    f"{c.tokens_in} in / {c.tokens_out} out",
                    "" if c.cost_usd is None else c.cost_usd,
                )
                for c in calls
            ),
        )
        return format_html(
            "<table><thead><tr><th>Call</th><th>Agent</th><th>Attempt</th><th>Model</th><th>Status</th>"
            "<th>Tokens</th><th>Cost (USD)</th></tr></thead><tbody>{}</tbody></table>",
            rows,
        )


@admin.register(LLMCall)
class LLMCallAdmin(ReadOnlyAdmin):
    list_display = ("id", "run", "agent", "model", "status", "tokens_in", "tokens_out", "cost_usd", "created_at")
    list_filter = ("status", "purpose", "agent", "model")
    search_fields = ("=run_id", "=conversation_id")
    ordering = ("-id",)
    # The three raw fields are deliberately absent from `fields`: only the truncated displays below appear.
    fields = (
        "purpose",
        "run_id",
        "conversation_id",
        "agent",
        "attempt",
        "provider",
        "model",
        "prompt_version",
        "prompt_sha256",
        "temperature",
        "max_tokens",
        "tokens_in",
        "tokens_out",
        "cache_write_tokens",
        "cache_read_tokens",
        "reserved_usd",
        "cost_usd",
        "latency_ms",
        "stop_reason",
        "provider_request_id",
        "status",
        "error_code",
        "error",
        "created_at",
        "finished_at",
        "request_display",
        "raw_response_display",
        "parsed_display",
    )
    readonly_fields = fields

    # LLMCall.run_id is a plain integer (no foreign key), so "run" is a display column showing that number.
    @admin.display(description="Run", ordering="run_id")
    def run(self, obj):
        return obj.run_id

    @admin.display(description="Request (truncated)")
    def request_display(self, obj):
        return _truncated(obj.request)

    @admin.display(description="Raw response (truncated)")
    def raw_response_display(self, obj):
        return _truncated(obj.raw_response)

    @admin.display(description="Parsed (truncated)")
    def parsed_display(self, obj):
        return _truncated(obj.parsed)


class IssueDispositionInline(ReadOnlyTabularInline):
    model = IssueDisposition
    fields = ("disposition", "reason")
    readonly_fields = fields
    show_change_link = False


@admin.register(Issue)
class IssueAdmin(ReadOnlyAdmin):
    list_display = ("id", "run", "local_id", "issue_type", "dimension", "intensity", "validity", "quote_match", "message")
    list_filter = ("validity", "dimension", "issue_type", "quote_match")
    search_fields = ("=run__id", "=run__conversation__id")
    list_select_related = ("run", "message")
    ordering = ("-id",)
    inlines = (IssueDispositionInline,)
    fields = (
        "run",
        "local_id",
        "message",
        "issue_type",
        "dimension",
        "quote",
        "quote_start",
        "quote_end",
        "quote_match",
        "explanation",
        "confidence",
        "intensity",
        "validity",
        "rejection_reason",
    )
    readonly_fields = fields


@admin.register(IssueDisposition)
class IssueDispositionAdmin(ReadOnlyAdmin):
    list_display = ("id", "issue", "disposition")
    list_filter = ("disposition",)
    search_fields = ("=issue__run__id",)
    list_select_related = ("issue",)
    ordering = ("-id",)
    fields = ("issue", "disposition", "reason")
    readonly_fields = fields


@admin.register(InterventionAct)
class InterventionActAdmin(ReadOnlyAdmin):
    list_display = (
        "id",
        "run",
        "order",
        "act_type",
        "tone",
        "validity",
        "char_len",
        "word_count",
        "is_question",
        "quotes_participant",
    )
    list_filter = ("validity", "act_type", "tone", "is_question", "quotes_participant")
    search_fields = ("=run__id", "=run__conversation__id")
    list_select_related = ("run",)
    ordering = ("-id",)
    fields = (
        "run",
        "order",
        "act_type",
        "tone",
        "text",
        "addressee",
        "subject",
        "validity",
        "rejection_reason",
        "char_len",
        "word_count",
        "is_question",
        "quotes_participant",
        "source_issues",
        "source_messages",
    )
    readonly_fields = fields


@admin.register(GuardState)
class GuardStateAdmin(ReadOnlyAdmin):
    list_display = ("id", "breaker_tripped", "trip_kind", "trip_reason", "tripped_at", "consecutive_errors")
    fields = (
        "breaker_tripped",
        "trip_kind",
        "trip_reason",
        "trip_detail",
        "tripped_at",
        "consecutive_errors",
        "last_error_at",
        "cooldown_until",
        "cooldown_seconds",
        "probe_in_flight",
        "probe_started_at",
        "last_alert_at",
        "last_alert_error",
    )
    readonly_fields = fields


@admin.register(PreviewMode)
class PreviewModeAdmin(ReadOnlyAdmin):
    list_display = ("id", "conversation", "mode", "assigned_at")
    list_filter = ("mode",)
    list_select_related = ("conversation",)
    search_fields = ("=conversation__id",)
    ordering = ("-id",)
    fields = ("conversation", "mode", "assigned_at")
    readonly_fields = fields


@admin.register(PreviewCheck)
class PreviewCheckAdmin(ReadOnlyAdmin):
    """Draft checks (step 19). The draft text and the model outputs appear on the detail page only, never in the list, and
    the model outputs are cut like the LLM call displays."""

    list_display = (
        "id",
        "conversation",
        "participant",
        "mode",
        "outcome",
        "unavailable_reason",
        "action",
        "char_count",
        "reused_by_run_id",
        "created_at",
    )
    list_filter = ("outcome", "mode", "action", "unavailable_reason")
    list_select_related = ("conversation", "participant")
    search_fields = ("=conversation__id", "=participant__id")
    ordering = ("-id",)
    fields = (
        "conversation",
        "participant",
        "mode",
        "outcome",
        "unavailable_reason",
        "snapshot_seq",
        "char_count",
        "draft_sha256",
        "draft_text",
        "note_texts_display",
        "master_output_display",
        "intervenor_output_display",
        "llm_call_ids",
        "action",
        "resulting_message",
        "reused_by_run",
        "created_at",
        "resolved_at",
    )
    readonly_fields = fields

    @admin.display(description="Notes shown to the author")
    def note_texts_display(self, obj):
        return _truncated(obj.note_texts)

    @admin.display(description="Master output (truncated)")
    def master_output_display(self, obj):
        return _truncated(obj.master_output)

    @admin.display(description="Intervenor output (truncated)")
    def intervenor_output_display(self, obj):
        return _truncated(obj.intervenor_output)
