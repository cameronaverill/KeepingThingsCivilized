"""Admin for propositions (step 7a). Step 9 owns the rest of the admin."""

from django.contrib import admin

from .models import Topic


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
