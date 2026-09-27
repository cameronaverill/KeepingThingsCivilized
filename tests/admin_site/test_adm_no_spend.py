"""No admin page, action or URL spends money or calls the LLM. The folder's autouse fixture makes every attempt to build a
client, call the model or run the pipeline raise and record itself; here each test also checks the database: no LLMCall,
ModerationRun or Message row is created, and the spy heard nothing."""
import pytest
from django.apps import apps
from django.contrib import admin
from django.test import RequestFactory

import adm_kit as K

CONTRACT_ACTIONS = {
    ("forum", "topic"): {"delete_selected", "hide_selected_propositions", "unhide_selected_propositions"},
}


def counts():
    from forum.models import Message
    from moderation.models import LLMCall, ModerationRun

    return {"calls": LLMCall.objects.count(), "runs": ModerationRun.objects.count(), "messages": Message.objects.count()}


def request_for(user):
    request = RequestFactory().get("/admin/")
    request.user = user
    return request


def test_the_spy_really_blocks_the_client_builder_and_the_pipeline(spend_spy):
    """Sanity: the safeguards this file relies on do raise, so a silent pass elsewhere means nothing was called."""
    from moderation import llm, pipeline

    for fn in (llm._build_real_client, llm.call, pipeline.run_moderation):
        with pytest.raises(AssertionError):
            fn()
    assert spend_spy.events == ["build_real_client", "llm.call", "pipeline.run_moderation"]


def test_the_spy_records_a_use_of_the_installed_client(spend_spy):
    from moderation import llm

    with pytest.raises(AssertionError):
        llm.get_client().messages.parse(model="m")
    assert spend_spy.events == ["client.messages.parse"]


@pytest.mark.parametrize("app,name", K.CONTRACT_MODELS, ids=lambda v: v)
def test_every_action_offered_on_a_contract_model_is_one_that_cannot_spend(world, superuser, app, name):
    """Read-only models and users offer no action; Topic offers only hide, unhide and Django's own delete."""
    ma = admin.site._registry[apps.get_model(app, name)]
    offered = set(ma.get_actions(request_for(superuser)))
    allowed = CONTRACT_ACTIONS.get((app, name), set())
    assert offered <= allowed


@pytest.mark.parametrize("app,name", K.CONTRACT_MODELS, ids=lambda v: v)
def test_running_every_offered_action_on_every_row_spends_nothing(root, world, spend_spy, app, name):
    model = apps.get_model(app, name)
    ma = admin.site._registry[model]
    before = counts()
    ids = [r.pk for r in model.objects.all()]
    for action in ma.get_actions(request_for(K.make_superuser())):
        root.post(K.url(app, name, "changelist"), {"action": action, "_selected_action": ids}, follow=True)
    assert spend_spy.events == []
    assert counts() == before


@pytest.mark.parametrize("app,name", K.CONTRACT_MODELS, ids=lambda v: v)
def test_the_model_admin_has_no_url_beyond_the_stock_ones(app, name):
    """A custom admin view (for example a 'rerun this run' button) could spend money; the URL patterns are exactly the
    ones a stock ModelAdmin for the same model serves."""
    model = apps.get_model(app, name)
    ma = admin.site._registry[model]
    stock = admin.ModelAdmin(model, admin.site)
    assert [(str(p.pattern), p.name) for p in ma.get_urls()] == [(str(p.pattern), p.name) for p in stock.get_urls()]


def test_visiting_every_admin_page_of_the_contract_spends_nothing(root, world, spend_spy):
    before = counts()
    root.get("/admin/")
    for app, name in K.CONTRACT_MODELS:
        model = apps.get_model(app, name)
        root.get(K.url(app, name, "changelist"))
        root.get(K.url(app, name, "add"))
        for row in model.objects.all():
            root.get(K.url(app, name, "change", row.pk))
            root.get(K.url(app, name, "history", row.pk))
            root.get(K.url(app, name, "delete", row.pk))
    assert spend_spy.events == []
    assert counts() == before


def test_posting_to_every_admin_url_of_the_contract_spends_nothing(root, world, spend_spy):
    before = counts()
    for app, name in K.CONTRACT_MODELS:
        model = apps.get_model(app, name)
        root.post(K.url(app, name, "add"), {"_save": "Save"})
        for row in model.objects.all():
            root.post(K.url(app, name, "change", row.pk), {"_save": "Save", "status": "pending"})
    assert spend_spy.events == []
    assert counts() == before


def test_the_breaker_state_is_untouched_by_visiting_its_page(root, world):
    from moderation.models import GuardState

    before = list(GuardState.objects.values())
    root.get(K.url("moderation", "guardstate", "change", 1))
    root.get(K.url("moderation", "guardstate", "changelist"))
    assert list(GuardState.objects.values()) == before


def test_no_admin_module_of_the_site_imports_the_anthropic_sdk():
    """Plan section 15: only llm.py imports anthropic, so an admin module could never build a client itself."""
    import inspect

    import accounts.admin
    import forum.admin
    import moderation.admin

    for module in (accounts.admin, forum.admin, moderation.admin):
        source = inspect.getsource(module)
        assert "import anthropic" not in source and "from anthropic" not in source, module.__name__
