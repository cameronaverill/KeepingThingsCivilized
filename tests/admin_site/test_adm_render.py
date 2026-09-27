"""Every registered model's changelist and change page render (200) with realistic rows; the list columns are the ones the
contract names; each page shows the rows it should."""
import re

import pytest
from django.apps import apps
from django.contrib import admin

import adm_kit as K

REGISTERED = [
    f"{m._meta.app_label}.{m._meta.model_name}" for m in sorted(admin.site._registry, key=lambda m: str(m._meta))
]

# The columns the contract lists for each changelist.
LIST_COLUMNS = {
    ("moderation", "moderationrun"): ["id", "conversation", "kind", "status", "failure_reason", "attempts", "is_stale", "decision", "created_at"],
    ("moderation", "llmcall"): ["id", "run", "agent", "model", "status", "cost_usd", "created_at"],
    ("accounts", "user"): ["username", "email", "is_active", "email_verified_at", "date_joined"],
    ("forum", "conversation"): ["id", "topic", "status", "source", "experiment", "created_at", "ended_by"],
}


def test_every_contract_model_is_registered_with_the_admin():
    registered = {(m._meta.app_label, m._meta.model_name) for m in admin.site._registry}
    assert set(K.CONTRACT_MODELS) - registered == set()


def test_no_evaluation_model_is_registered_with_the_admin():
    """The contract lists no evaluation model and the site never imports that app (plan section 8)."""
    registered = {m._meta.app_label for m in admin.site._registry}
    assert "evaluation" not in registered


@pytest.mark.parametrize("label", REGISTERED)
def test_every_registered_models_changelist_renders(root, label):
    app, model = label.split(".")
    assert root.get(K.url(app, model, "changelist")).status_code == 200


@pytest.mark.parametrize("app,model", K.CONTRACT_MODELS, ids=lambda v: v)
def test_every_contract_models_change_page_renders_for_every_row(root, world, app, model):
    rows = world.rows(app, model)
    assert len(rows) >= 1
    codes = {row.pk: root.get(K.url(app, model, "change", row.pk)).status_code for row in rows}
    assert codes == {row.pk: 200 for row in rows}


@pytest.mark.parametrize("app,model", K.CONTRACT_MODELS, ids=lambda v: v)
def test_every_contract_models_changelist_lists_every_row(root, world, app, model):
    expected = [r.pk for r in world.rows(app, model)]
    assert sorted(K.listed_pks(root.get(K.url(app, model, "changelist")), app, model)) == expected


@pytest.mark.parametrize("app,model", K.CONTRACT_MODELS, ids=lambda v: v)
def test_every_contract_models_history_page_renders(root, world, app, model):
    row = world.rows(app, model)[0]
    assert root.get(K.url(app, model, "history", row.pk)).status_code == 200


@pytest.mark.parametrize("app,model", K.CONTRACT_MODELS, ids=lambda v: v)
def test_a_changelist_with_no_rows_renders(root, app, model):
    """No world here: the pages must not need a row to exist (the guard state is created on demand)."""
    from moderation.models import GuardState

    GuardState.objects.all().delete()
    assert root.get(K.url(app, model, "changelist")).status_code == 200


@pytest.mark.parametrize("key", list(LIST_COLUMNS), ids=lambda k: "_".join(k))
def test_the_changelist_has_the_columns_the_contract_names(root, world, key):
    columns = K.column_classes(K.page(root.get(K.url(*key, "changelist"))))
    assert set(LIST_COLUMNS[key]) - columns == set()


def test_the_llmcall_list_shows_the_run_number_of_each_call_in_a_run_column(root, world):
    html = K.page(root.get(K.url("moderation", "llmcall", "changelist")))
    cells = re.findall(r'<td class="field-run">(.*?)</td>', html, re.S)
    assert sorted(c.strip() for c in cells) == sorted(
        ["-", str(world.run_done.pk), str(world.run_done.pk), str(world.run_other.pk)]
    )


def test_the_llmcall_list_has_a_tokens_column(root, world):
    html = K.page(root.get(K.url("moderation", "llmcall", "changelist")))
    assert "column-tokens" in html


def test_the_llmcall_list_shows_the_token_counts_and_the_cost(root, world):
    html = K.page(root.get(K.url("moderation", "llmcall", "changelist")))
    assert "12345" in html and "6789" in html
    assert "0.012345" in html


def test_the_llmcall_list_shows_agent_model_and_status(root, world):
    html = K.page(root.get(K.url("moderation", "llmcall", "changelist")))
    for needle in ("agent-zz1", "model-zz1", "agent-zz2", "model-zz2", "agent-other", "agent-norun"):
        assert needle in html, needle


def test_the_run_list_shows_status_kind_decision_reason_and_attempts(root, world):
    html = K.page(root.get(K.url("moderation", "moderationrun", "changelist")))
    for needle in ("failed", "replay", "live", "intervene", "no_intervention", "invalid_output"):
        assert needle in html, needle


def test_the_run_list_shows_the_conversation_of_each_run(root, world):
    html = K.page(root.get(K.url("moderation", "moderationrun", "changelist")))
    assert "Conversation 7001" in html and "Conversation 8002" in html


def test_the_user_list_shows_username_and_email_but_not_the_password_hash(root, world):
    html = K.page(root.get(K.url("accounts", "user", "changelist")))
    assert "zelda_mox" in html and "zelda_mox@leakcheck.example" in html
    assert K.HASH_FULL not in html and K.HASH_DIGEST not in html and K.HASH_SALT not in html


def test_the_conversation_list_shows_topic_status_source_experiment_and_ended_by(root, world):
    html = K.page(root.get(K.url("forum", "conversation", "changelist")))
    for needle in ("Cats make better pets than dogs.", "Trains", "obs-1", "active", "closed", "human", "synthetic", "Participant B of conversation 8002"):
        assert needle in html, needle


def test_the_message_change_page_shows_the_full_content(root, world):
    html = K.page(root.get(K.url("forum", "message", "change", world.m1.pk)))
    assert "Everyone knows cats are cleaner than dogs, it is a proven fact." in html


def test_the_participant_change_page_shows_the_label_and_the_conversation(root, world):
    html = K.page(root.get(K.url("forum", "participant", "change", world.pb.pk)))
    assert "Conversation 7001" in html and "quincy_ray" in html


def test_the_issue_change_page_shows_quote_explanation_and_rejection_reason(root, world):
    ok = K.page(root.get(K.url("moderation", "issue", "change", world.issue_ok.pk)))
    bad = K.page(root.get(K.url("moderation", "issue", "change", world.issue_bad.pk)))
    assert "it is a proven fact" in ok and "EXPLAIN-MARK no source is given" in ok
    assert "REJECT-MARK vague" in bad


def test_the_disposition_change_page_shows_disposition_and_reason(root, world):
    html = K.page(root.get(K.url("moderation", "issuedisposition", "change", world.disposition.pk)))
    assert "DISPOSITION-MARK asked for a source" in html and "acted" in html


def test_the_act_change_page_shows_the_text_and_the_computed_features(root, world):
    html = K.page(root.get(K.url("moderation", "interventionact", "change", world.act.pk)))
    assert "ACT-TEXT-MARK" in html
    assert K.readonly_text(html, "char_len") == str(len(world.act_text))
    assert K.readonly_text(html, "word_count") == str(len(world.act_text.split()))
    assert 'alt="True"' in K.field_html(html, "is_question")
    assert 'alt="True"' in K.field_html(html, "quotes_participant")


def test_the_act_features_show_true_for_a_question_and_false_for_no_quote(root, world):
    html = K.page(root.get(K.url("moderation", "interventionact", "change", world.act_other.pk)))
    assert 'alt="True"' in K.field_html(html, "is_question")
    assert 'alt="False"' in K.field_html(html, "quotes_participant")


def test_the_act_change_page_shows_its_source_issues_and_messages(root, world):
    html = K.page(root.get(K.url("moderation", "interventionact", "change", world.act.pk)))
    assert "field-source_issues" in html and "field-source_messages" in html
    assert f"Issue {world.issue_ok.pk} possible_factual_error" in html


def test_the_run_change_page_shows_the_rationale_error_and_snapshot_fields(root, world):
    done = K.page(root.get(K.url("moderation", "moderationrun", "change", world.run_done.pk)))
    failed = K.page(root.get(K.url("moderation", "moderationrun", "change", world.run_failed.pk)))
    assert "RATIONALE-MARK done run" in done
    assert "ERROR-MARK boom" in failed


def test_the_guard_state_change_page_shows_the_breaker_state(root, world):
    html = K.page(root.get(K.url("moderation", "guardstate", "change", 1)))
    for name in ("breaker_tripped", "trip_reason", "consecutive_errors"):
        assert f"field-{name}" in html, name


def test_the_user_change_page_shows_the_contract_fields(root, world):
    html = K.page(root.get(K.url("accounts", "user", "change", world.user_a.pk)))
    for name in ("username", "email", "is_active", "email_verified_at", "date_joined"):
        assert f"field-{name}" in html, name


def test_the_message_list_shows_the_participant_label_and_never_the_user(root, world):
    html = K.page(root.get(K.url("forum", "message", "changelist")))
    assert "Participant A of conversation 7001" in html and "Participant B of conversation 7001" in html
    for private in ("zelda_mox", "quincy_ray", "leakcheck.example"):
        assert private not in html, private


def test_the_participant_list_shows_the_user_of_each_human_participant(root, world):
    html = K.page(root.get(K.url("forum", "participant", "changelist")))
    assert "zelda_mox" in html and "quincy_ray" in html


def test_the_user_change_page_shows_no_password_field_at_all(root, world):
    html = K.page(root.get(K.url("accounts", "user", "change", world.user_a.pk)))
    assert "field-password" not in html
    assert "algorithm" not in html.lower()
    for masked in (K.HASH_SALT[:6], K.HASH_DIGEST[:6], "pbkdf2_sha256"):
        assert masked not in html, masked
