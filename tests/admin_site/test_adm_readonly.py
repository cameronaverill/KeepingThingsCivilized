"""The read-only models cannot be added, changed or deleted by anyone: not through the model admin's permission methods, not
through the admin URLs (GET or POST), not by a superuser, not by staff holding every permission, and not by an admin
action. Users cannot be deleted either."""
import pytest
from django.apps import apps
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory

import adm_kit as K

RO = K.READ_ONLY_MODELS


def model_of(app, name):
    return apps.get_model(app, name)


def fingerprint(model):
    """Every row of the model as (pk, all concrete field values): a change to any field shows up."""
    return sorted(
        (tuple(sorted((f.attname, repr(getattr(row, f.attname))) for f in model._meta.concrete_fields)))
        for row in model.objects.all()
    )


def request_for(user):
    request = RequestFactory().get("/admin/")
    request.user = user
    return request


def post_data(model, row):
    """A plausible edit form for the row: every concrete field echoed back with one text field changed."""
    data = {}
    for f in model._meta.concrete_fields:
        value = getattr(row, f.attname)
        data[f.attname] = "" if value is None else value
    return data


@pytest.mark.parametrize("app,name", RO, ids=lambda v: v)
def test_the_model_admin_grants_no_add_change_or_delete_even_to_a_superuser(world, superuser, app, name):
    model = model_of(app, name)
    ma = admin.site._registry[model]
    request = request_for(superuser)
    assert (ma.has_add_permission(request), ma.has_change_permission(request), ma.has_delete_permission(request)) == (False, False, False)
    row = model.objects.first()
    assert (ma.has_change_permission(request, row), ma.has_delete_permission(request, row)) == (False, False)


@pytest.mark.parametrize("app,name", RO, ids=lambda v: v)
def test_the_model_admin_keeps_the_view_permission(world, superuser, app, name):
    ma = admin.site._registry[model_of(app, name)]
    assert ma.has_view_permission(request_for(superuser)) is True


@pytest.mark.parametrize("app,name", RO, ids=lambda v: v)
def test_the_add_page_is_forbidden_to_a_superuser(root, world, app, name):
    assert root.get(K.url(app, name, "add")).status_code == 403


@pytest.mark.parametrize("app,name", RO, ids=lambda v: v)
def test_posting_to_the_add_url_creates_nothing(root, world, app, name):
    model = model_of(app, name)
    before = fingerprint(model)
    response = root.post(K.url(app, name, "add"), post_data(model, model.objects.first()))
    assert response.status_code == 403
    assert fingerprint(model) == before


@pytest.mark.parametrize("app,name", RO, ids=lambda v: v)
def test_posting_to_the_change_url_changes_nothing(root, world, app, name):
    model = model_of(app, name)
    row = model.objects.first()
    before = fingerprint(model)
    data = post_data(model, row)
    data.update({"_save": "Save", "status": "done", "content": "edited", "text": "edited", "rationale": "edited", "reason": "edited"})
    response = root.post(K.url(app, name, "change", row.pk), data)
    assert response.status_code == 403
    assert fingerprint(model) == before


@pytest.mark.parametrize("app,name", RO, ids=lambda v: v)
def test_the_change_page_offers_no_save_button(root, world, app, name):
    row = model_of(app, name).objects.first()
    html = K.page(root.get(K.url(app, name, "change", row.pk)))
    for control in ('name="_save"', 'name="_continue"', 'name="_addanother"'):
        assert control not in html, control


@pytest.mark.parametrize("app,name", RO, ids=lambda v: v)
def test_the_change_page_has_no_editable_input_for_a_model_field(root, world, app, name):
    model = model_of(app, name)
    row = model.objects.first()
    html = K.page(root.get(K.url(app, name, "change", row.pk)))
    editable = [f.name for f in model._meta.concrete_fields if f'name="{f.name}"' in html]
    assert editable == []


@pytest.mark.parametrize("app,name", RO, ids=lambda v: v)
def test_the_delete_page_is_forbidden_and_a_post_deletes_nothing(root, world, app, name):
    model = model_of(app, name)
    row = model.objects.first()
    before = fingerprint(model)
    assert root.get(K.url(app, name, "delete", row.pk)).status_code == 403
    assert root.post(K.url(app, name, "delete", row.pk), {"post": "yes"}).status_code == 403
    assert fingerprint(model) == before


@pytest.mark.parametrize("app,name", RO, ids=lambda v: v)
def test_the_changelist_offers_no_delete_selected_action(root, world, app, name):
    ma = admin.site._registry[model_of(app, name)]
    request = request_for(K.make_superuser())
    assert "delete_selected" not in ma.get_actions(request)


@pytest.mark.parametrize("app,name", RO, ids=lambda v: v)
def test_a_bulk_delete_post_from_the_changelist_deletes_nothing(root, world, app, name):
    model = model_of(app, name)
    before = fingerprint(model)
    ids = [r.pk for r in model.objects.all()]
    root.post(K.url(app, name, "changelist"), {"action": "delete_selected", "_selected_action": ids})
    root.post(K.url(app, name, "changelist"), {"action": "delete_selected", "_selected_action": ids, "post": "yes"})
    assert fingerprint(model) == before


@pytest.mark.parametrize("app,name", RO, ids=lambda v: v)
def test_staff_holding_every_permission_still_cannot_add_change_or_delete(world, app, name):
    model = model_of(app, name)
    row = model.objects.first()
    before = fingerprint(model)
    client = K.client_for(K.make_staff(perms=K.all_perms_of_apps("moderation", "forum", "accounts")))
    assert client.get(K.url(app, name, "add")).status_code == 403
    assert client.post(K.url(app, name, "add"), post_data(model, row)).status_code == 403
    assert client.post(K.url(app, name, "change", row.pk), post_data(model, row)).status_code == 403
    assert client.get(K.url(app, name, "delete", row.pk)).status_code == 403
    assert client.post(K.url(app, name, "delete", row.pk), {"post": "yes"}).status_code == 403
    assert fingerprint(model) == before


def test_the_run_inlines_cannot_add_change_or_delete_rows(root, world):
    """Posting inline formset data on the run's change page (a change POST) changes no issue and no act."""
    from moderation.models import InterventionAct, Issue

    before = (fingerprint(Issue), fingerprint(InterventionAct))
    url = K.url("moderation", "moderationrun", "change", world.run_done.pk)
    data = {
        "issues-TOTAL_FORMS": "3", "issues-INITIAL_FORMS": "2", "issues-MIN_NUM_FORMS": "0", "issues-MAX_NUM_FORMS": "1000",
        "issues-0-id": world.issue_ok.pk, "issues-0-run": world.run_done.pk, "issues-0-DELETE": "on",
        "issues-1-id": world.issue_bad.pk, "issues-1-run": world.run_done.pk, "issues-1-explanation": "edited",
        "acts-TOTAL_FORMS": "1", "acts-INITIAL_FORMS": "1", "acts-MIN_NUM_FORMS": "0", "acts-MAX_NUM_FORMS": "1000",
        "acts-0-id": world.act.pk, "acts-0-run": world.run_done.pk, "acts-0-DELETE": "on",
        "_save": "Save",
    }
    assert root.post(url, data).status_code == 403
    assert (fingerprint(Issue), fingerprint(InterventionAct)) == before


def test_the_guard_state_cannot_be_reset_or_created_through_the_admin(root, world):
    from moderation.models import GuardState

    GuardState.objects.filter(pk=1).update(breaker_tripped=True, trip_reason="manual")
    before = fingerprint(GuardState)
    assert root.post(K.url("moderation", "guardstate", "change", 1), {"breaker_tripped": "", "_save": "Save"}).status_code == 403
    assert root.post(K.url("moderation", "guardstate", "add"), {"breaker_tripped": ""}).status_code == 403
    assert fingerprint(GuardState) == before


# --- users cannot be deleted --------------------------------------------------------------------------------------------

def test_the_user_admin_grants_nobody_the_delete_permission(world, superuser):
    ma = admin.site._registry[get_user_model()]
    request = request_for(superuser)
    assert ma.has_delete_permission(request) is False
    assert ma.has_delete_permission(request, world.user_a) is False


def test_the_user_delete_page_is_forbidden_and_a_post_deletes_nobody(root, world):
    User = get_user_model()
    before = fingerprint(User)
    url = K.url("accounts", "user", "delete", world.user_b.pk)
    assert root.get(url).status_code == 403
    assert root.post(url, {"post": "yes"}).status_code == 403
    assert fingerprint(User) == before


def test_a_user_with_no_participation_cannot_be_deleted_either(root):
    loner = K.make_user("loner_one")
    before = fingerprint(get_user_model())
    assert root.post(K.url("accounts", "user", "delete", loner.pk), {"post": "yes"}).status_code == 403
    assert fingerprint(get_user_model()) == before


def test_staff_holding_the_delete_user_permission_still_cannot_delete_a_user(world):
    client = K.client_for(K.make_staff(perms=["accounts.view_user", "accounts.change_user", "accounts.delete_user"]))
    before = fingerprint(get_user_model())
    assert client.get(K.url("accounts", "user", "delete", world.user_a.pk)).status_code == 403
    assert client.post(K.url("accounts", "user", "delete", world.user_a.pk), {"post": "yes"}).status_code == 403
    assert fingerprint(get_user_model()) == before


def test_the_user_changelist_offers_no_delete_selected_action(world, superuser):
    ma = admin.site._registry[get_user_model()]
    assert "delete_selected" not in ma.get_actions(request_for(superuser))


def test_a_bulk_delete_post_on_the_user_changelist_deletes_nobody(root, world):
    User = get_user_model()
    before = fingerprint(User)
    ids = [u.pk for u in User.objects.all()]
    url = K.url("accounts", "user", "changelist")
    root.post(url, {"action": "delete_selected", "_selected_action": ids})
    root.post(url, {"action": "delete_selected", "_selected_action": ids, "post": "yes"})
    assert fingerprint(User) == before
