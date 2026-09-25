"""Shared helpers for the step 2 (LLM gateway) tests. Not a test module (no test_ prefix), imported by name.

Imports of moderation.* happen inside functions, so a missing module fails the test that needs it, not collection.
"""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from pydantic import BaseModel

SONNET = "claude-sonnet-5"
HAIKU = "claude-haiku-4-5"
FAKE_KEY = "test-key"

# Set by the autouse fixture in conftest.py: the genuine (unpatched) moderation.llm._build_real_client.
ORIGINALS = {}


class Verdict(BaseModel):
    """A small output schema for gateway tests."""

    label: str
    score: int


class OtherSchema(BaseModel):
    """A second schema, to check the prompt hash reacts to the schema."""

    flag: bool


def utc(year, month, day, hour=0, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


def D(value):
    return Decimal(str(value))


_UNSET = object()


def make_msg(
    parsed=_UNSET,
    *,
    input_tokens=100,
    output_tokens=50,
    cache_write=0,
    cache_read=0,
    stop_reason="end_turn",
    msg_id="msg_test_1",
):
    """A message shaped like the contract's fake reply (.parsed_output, .stop_reason, .usage, .id).

    Built here (not with FakeLLM's make_message) so gateway tests do not depend on that helper's exact keywords.
    parsed=None models unusable output (refusal, truncation, invalid).
    """
    if parsed is _UNSET:
        parsed = {"label": "fine", "score": 3}
    if isinstance(parsed, dict):
        parsed = Verdict(**parsed)
    usage = SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_write,
        cache_read_input_tokens=cache_read,
    )
    return SimpleNamespace(parsed_output=parsed, stop_reason=stop_reason, usage=usage, id=msg_id)


def reply(**kwargs):
    """A scripted item (callable) returning make_msg(**kwargs)."""
    return lambda _call_kwargs: make_msg(**kwargs)


def call_kwargs(**overrides):
    kwargs = dict(
        purpose="moderation",
        agent="master",
        model=SONNET,
        system="You are a careful test moderator.",
        messages=[{"role": "user", "content": "Hello there, this is a test message."}],
        output_schema=Verdict,
        max_tokens=500,
    )
    kwargs.update(overrides)
    return kwargs


def run_call(**overrides):
    from moderation import llm

    return llm.call(**call_kwargs(**overrides))


LONG_AGO = utc(2026, 9, 1, 12)


def seed_call(
    status="ok",
    purpose="moderation",
    cost=None,
    reserved=None,
    conversation_id=None,
    run_id=None,
    created_at=LONG_AGO,
    model=SONNET,
):
    """Insert a ledger row directly. Money arguments may be str/int/Decimal.

    ok/error rows default to cost=0 and reserved=cost; pending rows have no cost; refused rows have no cost.
    created_at is forced with a queryset update (works whether or not the field is auto_now_add).
    """
    from moderation.models import LLMCall

    cost = None if cost is None else D(cost)
    reserved = None if reserved is None else D(reserved)
    if status in ("ok", "error"):
        cost = Decimal("0") if cost is None else cost
        reserved = cost if reserved is None else reserved
    elif status == "pending":
        cost = None
        reserved = Decimal("0") if reserved is None else reserved
    else:
        assert status.startswith("refused_"), status
        cost = None
        reserved = Decimal("0") if reserved is None else reserved
    row = LLMCall.objects.create(
        purpose=purpose,
        run_id=run_id,
        conversation_id=conversation_id,
        agent="master",
        attempt=1,
        model=model,
        prompt_version="seed",
        prompt_sha256="0" * 64,
        temperature=None,
        max_tokens=100,
        request={},
        raw_response="",
        parsed=None,
        tokens_in=0,
        tokens_out=0,
        cache_write_tokens=0,
        cache_read_tokens=0,
        reserved_usd=reserved,
        cost_usd=cost,
        latency_ms=0,
        stop_reason="",
        provider_request_id="",
        status=status,
        error="",
        error_code="",
        finished_at=None if status == "pending" else created_at,
    )
    LLMCall.objects.filter(pk=row.pk).update(created_at=created_at)
    row.refresh_from_db()
    return row


SPEND_MSG = "You have reached your specified API usage limits. You will regain access on 2026-10-01 at 00:00 UTC."
SPEND_MSG_WORKSPACE = "You have reached your specified workspace API usage limits. You will regain access on 2026-10-01 at 00:00 UTC."


def sdk_status_error(cls_name, status, error_type, message, details=None):
    """A real anthropic status-error object, built the way the SDK builds them (httpx2 response plus decoded body)."""
    import anthropic
    import httpx2

    error = {"type": error_type, "message": message}
    if details:
        error["details"] = details
    request = httpx2.Request("POST", "https://api.example.test/v1/messages")
    response = httpx2.Response(status, request=request, headers={"request-id": "req_test_123"})
    return getattr(anthropic, cls_name)(message, response=response, body={"type": "error", "error": error})


def raiser(exc):
    """A scripted FakeLLM item that raises `exc`."""

    def item(kwargs):
        raise exc

    return item


def sdk_status_error_raw(cls_name, status, body, message="upstream said something"):
    """Like sdk_status_error but with an arbitrary body (a string, None, ...): what a proxy or gateway may return."""
    import anthropic
    import httpx2

    request = httpx2.Request("POST", "https://api.example.test/v1/messages")
    response = httpx2.Response(status, request=request, headers={"request-id": "req_test_123"})
    return getattr(anthropic, cls_name)(message, response=response, body=body)
