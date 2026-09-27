"""The User admin: the list columns, search by username and email, and no password hash anywhere (the field is read-only
in Django's standard pattern, or absent; never editable, never searchable)."""
import re

import pytest
from django.contrib.auth import get_user_model

import adm_kit as K

CHANGELIST = K.url("accounts", "user", "changelist")


def listed_usernames(response):
    """Usernames of the rows on the user changelist, read from the change links' link text."""
    html = K.page(response)
    return re.findall(rf'href="{re.escape(CHANGELIST)}\d+/change/"[^>]*>([^<]+)</a>', html)


def test_the_user_list_has_the_contract_columns(root, world):
    columns = K.column_classes(K.page(root.get(CHANGELIST)))
    assert {"username", "email", "is_active", "email_verified_at", "date_joined"} - columns == set()


def test_the_user_list_has_no_password_column(root, world):
    assert "password" not in K.column_classes(K.page(root.get(CHANGELIST)))


def test_the_user_list_shows_every_user(root, world):
    html = K.page(root.get(CHANGELIST))
    for name in ("zelda_mox", "quincy_ray"):
        assert name in html


def test_searching_by_username_finds_that_user_only(root, world):
    K.make_user("someone_else")
    html = K.page(root.get(CHANGELIST, {"q": "zelda_mox"}))
    assert "zelda_mox" in html
    assert "quincy_ray" not in html and "someone_else" not in html


def test_searching_finds_a_user_by_username_alone_even_when_the_email_does_not_contain_it(root, world):
    other = K.make_user("distinct_handle", "unrelated_mailbox@other.example")
    assert K.listed_pks(root.get(CHANGELIST, {"q": "distinct_handle"}), "accounts", "user") == [other.pk]


def test_searching_finds_a_user_by_email_alone_even_when_the_username_does_not_contain_it(root, world):
    other = K.make_user("distinct_handle", "unrelated_mailbox@other.example")
    assert K.listed_pks(root.get(CHANGELIST, {"q": "unrelated_mailbox"}), "accounts", "user") == [other.pk]


def test_searching_by_a_part_of_a_username_finds_it(root, world):
    html = K.page(root.get(CHANGELIST, {"q": "elda_m"}))
    assert "zelda_mox" in html and "quincy_ray" not in html


def test_searching_by_email_finds_that_user_only(root, world):
    html = K.page(root.get(CHANGELIST, {"q": "quincy_ray@leakcheck.example"}))
    assert "quincy_ray" in html
    assert "zelda_mox" not in html


def test_searching_by_a_part_of_an_email_domain_finds_every_match(root, world):
    K.make_user("outsider_x", "outsider_x@other.example")
    html = K.page(root.get(CHANGELIST, {"q": "leakcheck.example"}))
    assert "zelda_mox" in html and "quincy_ray" in html
    assert "outsider_x" not in html


def test_a_search_with_no_match_lists_nobody(root, world):
    assert K.listed_pks(root.get(CHANGELIST, {"q": "nobody_has_this_name"}), "accounts", "user") == []


def test_the_search_cannot_be_used_to_find_a_user_by_password_hash(root, world):
    for fragment in (K.HASH_DIGEST[:20], K.HASH_SALT, "pbkdf2_sha256"):
        assert K.listed_pks(root.get(CHANGELIST, {"q": fragment}), "accounts", "user") == [], fragment


# --- no password hash -------------------------------------------------------------------------------------------------

def assert_no_hash(html):
    assert K.HASH_FULL not in html
    assert K.HASH_DIGEST not in html
    assert K.HASH_SALT not in html
    assert K.HASH_FULL + "b" not in html


def test_the_user_change_page_does_not_show_the_password_hash(root, world):
    assert_no_hash(K.page(root.get(K.url("accounts", "user", "change", world.user_a.pk))))


def test_the_user_change_page_has_no_password_input(root, world):
    html = K.page(root.get(K.url("accounts", "user", "change", world.user_a.pk)))
    assert 'name="password"' not in html and 'name="password1"' not in html and 'name="password2"' not in html


@pytest.mark.parametrize(
    "app,model,attr",
    [
        ("forum", "participant", "pb"),
        ("forum", "topic", "topic"),
        ("forum", "conversation", "conv"),
        ("moderation", "moderationrun", "run_done"),
    ],
    ids=lambda v: v,
)
def test_no_other_admin_page_shows_the_hash_either(root, world, app, model, attr):
    row = getattr(world, attr)
    assert_no_hash(K.page(root.get(K.url(app, model, "change", row.pk))))
    assert_no_hash(K.page(root.get(K.url(app, model, "changelist"))))


def test_the_user_history_page_does_not_show_the_hash(root, world):
    assert_no_hash(K.page(root.get(K.url("accounts", "user", "history", world.user_a.pk))))


def test_saving_the_user_change_form_does_not_alter_the_stored_hash(root, world):
    """Whatever the change form accepts, a POST from the page must leave the password exactly as stored."""
    User = get_user_model()
    root.post(
        K.url("accounts", "user", "change", world.user_a.pk),
        {"username": "zelda_mox", "email": "zelda_mox@leakcheck.example", "is_active": "on", "password": "hacked",
         "password1": "hacked", "password2": "hacked", "_save": "Save"},
    )
    assert User.objects.get(pk=world.user_a.pk).password == K.HASH_FULL


# --- users are neither added nor deleted; only is_active is editable -------------------------------------------------------

def test_the_add_user_page_is_forbidden_and_a_post_creates_nobody(root, world):
    User = get_user_model()
    before = User.objects.count()
    url = K.url("accounts", "user", "add")
    assert root.get(url).status_code == 403
    assert root.post(url, {"username": "sneaky_new", "email": "s@x.example", "password1": "x", "password2": "x"}).status_code == 403
    assert User.objects.count() == before


def test_the_change_page_offers_is_active_as_the_only_editable_field(root, world):
    html = K.page(root.get(K.url("accounts", "user", "change", world.user_b.pk)))
    inputs = set(re.findall(r'<input[^>]*name="([^"]+)"[^>]*>', html)) - {"csrfmiddlewaretoken", "_save", "_continue", "_addanother"}
    assert inputs - {"is_active"} == set(), inputs
    assert 'name="is_active"' in html


def test_posting_the_change_form_can_deactivate_a_user_and_changes_nothing_else(root, world):
    User = get_user_model()
    before = User.objects.get(pk=world.user_b.pk)
    response = root.post(
        K.url("accounts", "user", "change", world.user_b.pk),
        {"username": "hacker_name", "email": "hacker@x.example", "is_staff": "on", "is_superuser": "on",
         "password": "hacked", "email_verified_at_0": "2020-01-01", "email_verified_at_1": "00:00:00", "_save": "Save"},
    )
    assert response.status_code == 302
    after = User.objects.get(pk=world.user_b.pk)
    assert after.is_active is False
    assert (after.username, after.email, after.is_staff, after.is_superuser, after.password, after.email_verified_at, after.date_joined) == (
        before.username, before.email, False, False, K.HASH_FULL + "b", before.email_verified_at, before.date_joined,
    )


def test_posting_the_change_form_with_is_active_reactivates_a_user(root, world):
    User = get_user_model()
    User.objects.filter(pk=world.user_b.pk).update(is_active=False)
    root.post(K.url("accounts", "user", "change", world.user_b.pk), {"is_active": "on", "_save": "Save"})
    assert User.objects.get(pk=world.user_b.pk).is_active is True


def test_a_staff_user_with_only_the_view_permission_cannot_deactivate_a_user(world):
    client = K.client_for(K.make_staff(perms=["accounts.view_user"]))
    resp = client.post(K.url("accounts", "user", "change", world.user_b.pk), {"_save": "Save"})
    assert resp.status_code == 403
    assert get_user_model().objects.get(pk=world.user_b.pk).is_active is True
