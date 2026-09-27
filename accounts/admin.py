"""Admin for users (plan section 12, step 9c).

A plain ModelAdmin, not Django's UserAdmin: the password hash is never rendered (not even masked), users cannot be
added here (they register through the site, step 6), and nobody can delete a user (other tables reference users with
PROTECT). Staff can switch ``is_active`` (deactivating an abusive account is a moderation tool); everything else is
read-only. Privilege changes (``is_staff``, ``is_superuser``) go through the shell or ``createsuperuser``.
"""

from django.contrib import admin
from django.contrib.auth import get_user_model

User = get_user_model()


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("username", "email", "is_active", "email_verified_at", "date_joined")
    list_filter = ("is_active", "is_staff", "is_superuser")
    search_fields = ("username", "email")
    ordering = ("-date_joined", "-id")
    fields = (
        "username",
        "email",
        "email_verified_at",
        "is_active",
        "is_staff",
        "is_superuser",
        "date_joined",
        "last_login",
    )
    readonly_fields = (
        "username",
        "email",
        "email_verified_at",
        "is_staff",
        "is_superuser",
        "date_joined",
        "last_login",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
