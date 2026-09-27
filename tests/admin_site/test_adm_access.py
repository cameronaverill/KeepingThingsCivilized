"""Who reaches the admin: anonymous and non-staff users go to the admin login; staff without permissions see nothing;
staff with only a view permission see only that model; superusers see every contract model."""
import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

import adm_kit as K

LOGIN = reverse("admin:login")
ALL_CHANGELISTS = [K.url(a, m, "changelist") for a, m in K.CONTRACT_MODELS]


def is_login_redirect(response, target):
    return response.status_code == 302 and response["Location"] == f"{LOGIN}?next={target}"


@pytest.mark.parametrize("target", ["/admin/"] + ALL_CHANGELISTS, ids=lambda t: t)
def test_an_anonymous_visitor_is_sent_to_the_admin_login(world, target):
    response = Client().get(target)
    assert is_login_redirect(response, target), response.get("Location")


@pytest.mark.parametrize("target", ["/admin/"] + ALL_CHANGELISTS, ids=lambda t: t)
def test_a_signed_in_user_who_is_not_staff_is_sent_to_the_admin_login(world, target):
    response = K.client_for(world.user_a).get(target)
    assert is_login_redirect(response, target), response.get("Location")


def test_a_non_staff_user_cannot_open_an_admin_change_page(world):
    client = K.client_for(world.user_b)
    for target in (
        K.url("moderation", "moderationrun", "change", world.run_done.pk),
        K.url("moderation", "llmcall", "change", world.call1.pk),
        K.url("accounts", "user", "change", world.user_a.pk),
    ):
        assert is_login_redirect(client.get(target), target)


def test_a_non_staff_user_posting_to_the_admin_is_sent_to_the_login_and_changes_nothing(world):
    target = K.url("forum", "topic", "changelist")
    response = K.client_for(world.user_a).post(
        target, {"action": "hide_selected_propositions", "_selected_action": [world.topic.pk]}
    )
    assert is_login_redirect(response, target)
    world.topic.refresh_from_db()
    assert world.topic.hidden is False


def test_a_superuser_who_is_not_marked_staff_is_sent_to_the_admin_login(world):
    user = K.make_superuser()
    get_user_model().objects.filter(pk=user.pk).update(is_staff=False)
    user.refresh_from_db()
    assert user.is_superuser and not user.is_staff
    assert is_login_redirect(K.client_for(user).get("/admin/"), "/admin/")


def test_an_inactive_staff_user_is_sent_to_the_admin_login(world):
    user = K.make_staff(perms=K.all_perms_of_apps("moderation"))
    get_user_model().objects.filter(pk=user.pk).update(is_active=False)
    user.refresh_from_db()
    assert is_login_redirect(K.client_for(user).get("/admin/"), "/admin/")


def test_the_admin_login_page_itself_is_reachable_by_anyone(world):
    assert Client().get(LOGIN).status_code == 200


def test_a_staff_user_with_no_permissions_can_open_the_index_but_sees_no_model(world):
    response = K.client_for(K.make_staff()).get("/admin/")
    assert response.status_code == 200
    html = K.page(response)
    assert "permission to view or edit anything" in html
    for app, model in K.CONTRACT_MODELS:
        assert K.url(app, model, "changelist") not in html


@pytest.mark.parametrize("app,model", K.CONTRACT_MODELS, ids=lambda v: v)
def test_a_staff_user_with_no_permissions_gets_403_on_every_changelist(world, app, model):
    assert K.client_for(K.make_staff()).get(K.url(app, model, "changelist")).status_code == 403


@pytest.mark.parametrize("app,model", K.CONTRACT_MODELS, ids=lambda v: v)
def test_a_staff_user_with_no_permissions_gets_403_on_every_change_page(world, app, model):
    row = world.rows(app, model)[0]
    assert K.client_for(K.make_staff()).get(K.url(app, model, "change", row.pk)).status_code == 403


def test_a_staff_user_with_only_the_run_view_permission_sees_runs_and_nothing_else(world):
    client = K.client_for(K.make_staff(perms=["moderation.view_moderationrun"]))
    assert client.get(K.url("moderation", "moderationrun", "changelist")).status_code == 200
    assert client.get(K.url("moderation", "moderationrun", "change", world.run_done.pk)).status_code == 200
    others = [c for c in K.CONTRACT_MODELS if c != ("moderation", "moderationrun")]
    codes = {f"{a}_{m}": client.get(K.url(a, m, "changelist")).status_code for a, m in others}
    assert codes == {f"{a}_{m}": 403 for a, m in others}


def test_a_staff_user_with_only_the_call_view_permission_cannot_see_runs(world):
    client = K.client_for(K.make_staff(perms=["moderation.view_llmcall"]))
    assert client.get(K.url("moderation", "llmcall", "changelist")).status_code == 200
    assert client.get(K.url("moderation", "moderationrun", "changelist")).status_code == 403


def test_a_staff_user_holding_every_moderation_permission_can_still_only_view(world):
    client = K.client_for(K.make_staff(perms=K.all_perms_of_apps("moderation")))
    for model in ("moderationrun", "llmcall", "issue", "issuedisposition", "interventionact", "guardstate"):
        assert client.get(K.url("moderation", model, "changelist")).status_code == 200, model
        assert client.get(K.url("moderation", model, "add")).status_code == 403, model


def test_a_superuser_sees_every_contract_model_on_the_index(root):
    html = K.page(root.get("/admin/"))
    missing = [f"{a}.{m}" for a, m in K.CONTRACT_MODELS if K.url(a, m, "changelist") not in html]
    assert missing == []


@pytest.mark.parametrize("app,model", K.CONTRACT_MODELS, ids=lambda v: v)
def test_a_superuser_reaches_every_contract_changelist(root, app, model):
    assert root.get(K.url(app, model, "changelist")).status_code == 200
