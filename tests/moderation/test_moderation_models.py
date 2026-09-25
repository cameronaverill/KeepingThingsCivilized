"""moderation/models.py and the app itself: LLMCall and GuardState shape, migrations, indexes."""
import io
from decimal import Decimal

import pytest
from django.apps import apps
from django.core.management import call_command
from django.db import connection, models

LLMCALL_FIELDS = [
    "purpose", "run_id", "conversation_id", "agent", "attempt", "provider", "model", "prompt_version", "prompt_sha256",
    "temperature", "max_tokens", "request", "raw_response", "parsed", "tokens_in", "tokens_out", "cache_write_tokens",
    "cache_read_tokens", "reserved_usd", "cost_usd", "latency_ms", "stop_reason", "provider_request_id", "status",
    "error", "error_code", "created_at", "finished_at",
]  # fmt: skip
GUARDSTATE_FIELDS = ["breaker_tripped", "tripped_at", "trip_reason", "trip_detail", "consecutive_errors", "last_error_at"]


def test_the_moderation_app_is_installed():
    from django.conf import settings

    assert "moderation" in settings.INSTALLED_APPS
    assert apps.get_app_config("moderation").label == "moderation"


def test_migrations_exist_and_are_up_to_date():
    from pathlib import Path

    config = apps.get_app_config("moderation")
    assert list((Path(config.path) / "migrations").glob("0001_*.py"))
    call_command("makemigrations", "moderation", "--check", "--dry-run", stdout=io.StringIO())  # exits 1 on drift


def test_llmcall_has_every_contract_field():
    from moderation.models import LLMCall

    names = {f.name for f in LLMCall._meta.get_fields()}
    missing = [n for n in LLMCALL_FIELDS if n not in names]
    assert missing == []


def test_guardstate_has_every_contract_field():
    from moderation.models import GuardState

    names = {f.name for f in GuardState._meta.get_fields()}
    assert [n for n in GUARDSTATE_FIELDS if n not in names] == []


@pytest.mark.parametrize("name", ["reserved_usd", "cost_usd"])
def test_money_fields_are_decimals_with_six_places(name):
    from moderation.models import LLMCall

    field = LLMCall._meta.get_field(name)
    assert isinstance(field, models.DecimalField)
    assert (field.max_digits, field.decimal_places) == (12, 6)


def test_cost_is_null_until_the_call_finishes_and_a_few_more_fields_are_nullable():
    from moderation.models import LLMCall

    for name in ("cost_usd", "temperature", "parsed", "run_id", "conversation_id"):
        assert LLMCall._meta.get_field(name).null is True, name
    assert LLMCall._meta.get_field("reserved_usd").null is False


@pytest.mark.parametrize("name", ["conversation_id", "run_id"])
def test_context_ids_are_plain_integers_not_foreign_keys(name):
    """Spend history must outlive the conversation it was for (brief section 2, decision 1)."""
    from moderation.models import LLMCall

    field = LLMCall._meta.get_field(name)
    assert isinstance(field, models.IntegerField)
    assert not field.is_relation


@pytest.mark.parametrize("name", ["conversation_id", "run_id"])
def test_context_ids_are_indexed(name):
    from moderation.models import LLMCall

    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, LLMCall._meta.db_table)
    indexed_columns = [tuple(c["columns"]) for c in constraints.values() if c["index"] and not c["primary_key"]]
    assert any(cols and cols[0] == name for cols in indexed_columns), indexed_columns


def test_status_and_purpose_choices_are_exactly_the_contract_values():
    from moderation.models import LLMCall

    statuses = {value for value, _ in LLMCall._meta.get_field("status").choices}
    purposes = {value for value, _ in LLMCall._meta.get_field("purpose").choices}
    assert statuses == {"pending", "ok", "error", "refused_budget", "refused_breaker", "refused_disabled", "refused_model"}
    assert purposes == {"moderation", "spike", "golden", "replay", "judge"}


def test_trip_reason_choices():
    from moderation.models import GuardState

    reasons = {value for value, _ in GuardState._meta.get_field("trip_reason").choices}
    assert {"spend_limit", "consecutive_errors", "manual"} <= reasons


def test_llmcall_defaults():
    from moderation.models import LLMCall

    assert LLMCall._meta.get_field("attempt").default == 1
    assert LLMCall._meta.get_field("provider").default == "anthropic"


def test_a_row_round_trips_with_exact_decimals_and_json():
    from moderation_testkit import seed_call

    row = seed_call("ok", cost="0.123457", reserved="0.5")
    row.request = {"model": "x", "messages": [{"role": "user", "content": "héllo"}]}
    row.parsed = {"a": [1, 2]}
    row.save()
    fresh = type(row).objects.get(pk=row.pk)
    assert fresh.cost_usd == Decimal("0.123457")
    assert fresh.reserved_usd == Decimal("0.500000")
    assert fresh.request["messages"][0]["content"] == "héllo"
    assert fresh.parsed == {"a": [1, 2]}
