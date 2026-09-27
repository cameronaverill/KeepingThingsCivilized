"""Step 7c revision 5: the read-only admin for forum.Block (list: blocker, blocked, created; no add, change or delete)."""
import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.urls import reverse

from fsvc_testkit import make_user, svc, uniq

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff(client):
    name = uniq("adminquill")
    user = get_user_model().objects.create_superuser(username=name, email=f"{name}@mailbox.example", password=None)
    client.force_login(user)
    return user


@pytest.fixture
def one_block():
    from forum.models import Block

    a, b = make_user(), make_user()
    svc().block_user(a, b)
    return a, b, Block.objects.get()


def test_block_is_registered_in_the_admin():
    from forum.models import Block

    assert Block in admin.site._registry


def test_the_admin_offers_no_add_change_or_delete_permission(rf, staff):
    from forum.models import Block

    modeladmin = admin.site._registry[Block]
    request = rf.get("/admin/")
    request.user = staff
    assert modeladmin.has_add_permission(request) is False
    assert modeladmin.has_change_permission(request) is False
    assert modeladmin.has_delete_permission(request) is False


def test_the_changelist_lists_blocker_blocked_and_created(client, staff, one_block):
    a, b, row = one_block
    response = client.get(reverse("admin:forum_block_changelist"))
    assert response.status_code == 200
    html = response.content.decode()
    assert a.username in html and b.username in html
    modeladmin = admin.site._registry[type(row)]
    for column in ("blocker", "blocked", "created_at"):
        assert column in modeladmin.list_display


def test_the_add_page_is_forbidden(client, staff):
    assert client.get(reverse("admin:forum_block_add")).status_code == 403
    assert client.post(reverse("admin:forum_block_add"), {"blocker": 1, "blocked": 2}).status_code == 403


def test_the_change_page_cannot_change_anything(client, staff, one_block):
    a, b, row = one_block
    url = reverse("admin:forum_block_change", args=[row.pk])
    get = client.get(url)
    assert get.status_code in (200, 403)
    if get.status_code == 200:
        html = get.content.decode()
        assert "_save" not in html and 'name="blocked"' not in html  # a read-only page has no inputs to change
    post = client.post(url, {"blocker": b.pk, "blocked": a.pk, "_save": "Save"})
    assert post.status_code in (403, 200, 302)
    from forum.models import Block

    row_after = Block.objects.get(pk=row.pk)
    assert (row_after.blocker_id, row_after.blocked_id) == (a.pk, b.pk)


def test_the_delete_page_and_action_are_forbidden(client, staff, one_block):
    _, _, row = one_block
    assert client.get(reverse("admin:forum_block_delete", args=[row.pk])).status_code == 403
    assert client.post(reverse("admin:forum_block_delete", args=[row.pk]), {"post": "yes"}).status_code == 403
    response = client.post(
        reverse("admin:forum_block_changelist"),
        {"action": "delete_selected", "_selected_action": [row.pk], "post": "yes"},
        follow=True,
    )
    assert response.status_code in (200, 403)
    from forum.models import Block

    assert Block.objects.filter(pk=row.pk).exists()


def test_no_delete_selected_action_is_offered(client, staff, one_block):
    html = client.get(reverse("admin:forum_block_changelist")).content.decode()
    assert "delete_selected" not in html


def test_the_admin_is_closed_to_anonymous_visitors(client, one_block):
    response = client.get(reverse("admin:forum_block_changelist"))
    assert response.status_code in (302, 403)
    if response.status_code == 302:
        assert "login" in response["Location"]


def test_an_ordinary_logged_in_user_cannot_open_the_block_list(client, one_block):
    a, _, _ = one_block
    client.force_login(a)
    assert client.get(reverse("admin:forum_block_changelist")).status_code in (302, 403)
