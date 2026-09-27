"""The admin of PreviewMode and PreviewCheck is read-only for everyone: no add, change or delete, by permission method or by URL,
and the draft text is shown on the detail page only."""
import preview_kit as pk
import pytest
from django.apps import apps
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

pytestmark = pytest.mark.django_db

MODEL_BACKEND = "django.contrib.auth.backends.ModelBackend"
NAMES = ["PreviewMode", "PreviewCheck"]


@pytest.fixture(autouse=True)
def _admin_environment(settings):
    settings.MODERATION_RUN_MODE = "worker"


@pytest.fixture
def superuser():
    return get_user_model().objects.create_superuser(username="previewroot", email="previewroot@mailbox.example", password=None)


@pytest.fixture
def root(superuser):
    client = Client()
    client.force_login(superuser, backend=MODEL_BACKEND)
    return client


@pytest.fixture
def rows():
    from moderation.models import PreviewCheck, PreviewMode

    w = pk.world([("A", "One thing."), ("B", "Another thing.")])
    mode = PreviewMode.objects.create(conversation_id=w.conv.pk, mode="on", assigned_at=timezone.now())
    check = PreviewCheck.objects.create(
        conversation_id=w.conv.pk, participant_id=w.parts["B"].pk, draft_text=f"A private draft {pk.MARKER}.", char_count=30, draft_sha256=pk.sha("x"),
        snapshot_seq=2, mode="on", outcome="concern", unavailable_reason="", note_texts=["A note."], master_output=None,
        intervenor_output=None, llm_call_ids=[], action="",
    )  # fmt: skip
    return {"PreviewMode": mode, "PreviewCheck": check}


def url(name, kind, pk_=None):
    return reverse(f"admin:moderation_{name.lower()}_{kind}", args=[] if pk_ is None else [pk_])


def request_for(user):
    request = RequestFactory().get("/admin/")
    request.user = user
    return request


@pytest.mark.parametrize("name", NAMES)
class TestReadOnly:
    def test_both_models_are_registered(self, name):
        assert apps.get_model("moderation", name) in admin.site._registry

    def test_no_add_change_or_delete_permission_even_for_a_superuser(self, name, superuser, rows):
        ma = admin.site._registry[apps.get_model("moderation", name)]
        request = request_for(superuser)
        row = rows[name]
        assert (ma.has_add_permission(request), ma.has_change_permission(request), ma.has_delete_permission(request)) == (False, False, False)
        assert (ma.has_change_permission(request, row), ma.has_delete_permission(request, row)) == (False, False)
        assert ma.has_view_permission(request) is True

    def test_the_add_page_is_forbidden(self, name, root, rows):
        assert root.get(url(name, "add")).status_code == 403

    def test_posting_to_the_add_page_is_forbidden_and_adds_nothing(self, name, root, rows):
        model = apps.get_model("moderation", name)
        before = model.objects.count()
        assert root.post(url(name, "add"), {"conversation": 99, "mode": "off"}).status_code == 403
        assert model.objects.count() == before

    def test_the_delete_page_is_forbidden_and_deletes_nothing(self, name, root, rows):
        model = apps.get_model("moderation", name)
        target = rows[name].pk
        assert root.get(url(name, "delete", target)).status_code == 403
        assert root.post(url(name, "delete", target), {"post": "yes"}).status_code == 403
        assert model.objects.filter(pk=target).exists()

    def test_posting_a_change_is_forbidden_and_changes_nothing(self, name, root, rows):
        model = apps.get_model("moderation", name)
        target = rows[name]
        before = {f.attname: getattr(target, f.attname) for f in model._meta.concrete_fields}
        data = {k: ("" if v is None else v) for k, v in before.items()}
        data["mode"] = "off"
        assert root.post(url(name, "change", target.pk), data).status_code == 403
        after = model.objects.get(pk=target.pk)
        assert {f.attname: getattr(after, f.attname) for f in model._meta.concrete_fields} == before

    def test_the_delete_selected_action_deletes_nothing(self, name, root, rows):
        model = apps.get_model("moderation", name)
        root.post(url(name, "changelist"), {"action": "delete_selected", "_selected_action": [rows[name].pk], "post": "yes"})
        assert model.objects.filter(pk=rows[name].pk).count() == 1

    def test_the_changelist_and_the_detail_page_can_be_viewed(self, name, root, rows):
        assert root.get(url(name, "changelist")).status_code == 200
        assert root.get(url(name, "change", rows[name].pk)).status_code == 200

    def test_a_visit_makes_no_model_call(self, name, root, rows, fake):
        client = fake()
        root.get(url(name, "changelist"))
        root.get(url(name, "change", rows[name].pk))
        assert client.calls == []
        assert pk.total_calls() == 0


class TestTheDraftText:
    def test_the_changelist_does_not_show_the_draft_text(self, root, rows):
        page = root.get(url("PreviewCheck", "changelist")).content.decode()
        assert pk.MARKER not in page

    def test_the_detail_page_shows_the_draft_text(self, root, rows):
        page = root.get(url("PreviewCheck", "change", rows["PreviewCheck"].pk)).content.decode()
        assert pk.MARKER in page

    def test_the_list_has_no_draft_column(self):
        model = apps.get_model("moderation", "PreviewCheck")
        assert "draft_text" not in admin.site._registry[model].list_display
