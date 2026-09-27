"""Admin for propositions (step 7a). Step 9 owns the rest of the admin."""

from django.contrib import admin

from .models import Block, Conversation, Message, Participant, Topic


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    list_display = ("proposition_text", "created_by", "hidden", "created_at")
    list_filter = ("hidden",)
    search_fields = ("proposition", "title")
    raw_id_fields = ("created_by",)
    readonly_fields = ("created_at",)
    ordering = ("-created_at", "-id")
    actions = ["hide_selected_propositions", "unhide_selected_propositions"]

    @admin.display(description="Proposition", ordering="proposition")
    def proposition_text(self, obj):
        return obj.proposition or obj.title

    @admin.action(description="Hide selected propositions")
    def hide_selected_propositions(self, request, queryset):
        count = queryset.update(hidden=True)
        self.message_user(request, f"Hid {count} proposition(s). Their conversations are kept.")

    @admin.action(description="Unhide selected propositions")
    def unhide_selected_propositions(self, request, queryset):
        count = queryset.update(hidden=False)
        self.message_user(request, f"Unhid {count} proposition(s).")


# Step 9c: conversations, messages and participants. The forum services create and change these, so the admin only
# shows them (no add, change or delete, superusers included).
class _ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Conversation)
class ConversationAdmin(_ReadOnlyAdmin):
    list_display = ("id", "topic", "status", "source", "experiment", "created_at", "ended_by")
    list_filter = ("status", "source", "experiment")
    search_fields = ("=id", "topic__proposition", "topic__title")
    list_select_related = ("topic", "experiment", "ended_by")
    ordering = ("-id",)
    fields = (
        "topic",
        "status",
        "source",
        "experiment",
        "pair_id",
        "variant",
        "transcript_id",
        "label_seed",
        "ended_by",
        "ended_at",
        "created_at",
    )
    readonly_fields = fields


@admin.register(Message)
class MessageAdmin(_ReadOnlyAdmin):
    list_display = ("id", "conversation", "seq_no", "author_type", "participant", "char_count", "content_preview", "created_at")
    list_filter = ("author_type",)
    search_fields = ("=conversation__id",)
    list_select_related = ("conversation", "participant")
    ordering = ("-id",)
    fields = (
        "conversation",
        "seq_no",
        "author_type",
        "participant",
        "in_reply_to",
        "content",
        "char_count",
        "planted",
        "created_at",
    )
    readonly_fields = fields

    @admin.display(description="Content")
    def content_preview(self, obj):
        text = " ".join(obj.content.split())
        return text if len(text) <= 80 else text[:79] + "\u2026"


@admin.register(Participant)
class ParticipantAdmin(_ReadOnlyAdmin):
    list_display = ("id", "conversation", "label", "join_order", "user", "joined_at")
    search_fields = ("=conversation__id",)
    list_select_related = ("conversation", "user")
    ordering = ("-id",)
    fields = ("conversation", "label", "join_order", "user", "joined_at")
    readonly_fields = fields


@admin.register(Block)
class BlockAdmin(_ReadOnlyAdmin):
    list_display = ("id", "blocker", "blocked", "created_at")
    list_select_related = ("blocker", "blocked")
    search_fields = ("blocker__username", "blocked__username")
    ordering = ("-id",)
    fields = ("blocker", "blocked", "created_at")
    readonly_fields = fields
