"""The LLM gateway: EVERY call to Claude goes through `call()`, which refuses anything unsafe or over budget, logs
everything to the `LLMCall` ledger and prices every call. This is the ONLY module that imports `anthropic`.

SDK and API facts this module relies on. Verified 2026-09-25 against the installed SDK source (anthropic 1.8.0,
.venv/lib/python3.13/site-packages/anthropic) and Anthropic's docs (platform.claude.com/docs/en/...). If the docs or the
SDK change, re-verify before the first real call (step 3).

1. Structured output: `client.messages.parse(model=, max_tokens=, system=, messages=, output_format=<PydanticModel>)`
   (resources/messages/messages.py `Messages.parse`; docs build-with-claude/structured-outputs). The SDK turns the
   model into `output_config={"format": {"type": "json_schema", "schema": ...}}` (constrained decoding) and returns a
   `ParsedMessage` whose `.parsed_output` is a model instance, `.stop_reason`, `.usage`, `.id`, `._request_id`.
   It parses each text block with `TypeAdapter.validate_json`, so INVALID or TRUNCATED JSON RAISES
   `pydantic.ValidationError` from inside `parse()` and the response (with its usage) is lost. `.parsed_output` is
   None when there is no text block (for example `stop_reason == "refusal"`). `stop_reason` "max_tokens" means truncated.
2. Prompt caching: automatic caching (a TOP-LEVEL `cache_control={"type": "ephemeral"}`, sent through `extra_body`
   because `Messages.parse()` in SDK 1.8.0 has no such keyword) was tried on the first real run (claude-haiku-4-5,
   2026-09-25) and MEASURED as harmful here: it wrote the entire prompt to the cache on every call (cache_creation
   4,206-5,000 tokens, input_tokens 3, cache reads 0) because each request differs at the end, so it cost +25% on
   input with no benefit. Instead an explicit breakpoint marks only the fixed instructions: `system` is sent as
   `[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]` (`cache_system=True`, the default). The
   schema is part of the cached prefix. Minimum cacheable prefix: 4,096 tokens on Haiku 4.5 (a shorter prefix is
   silently not cached and costs nothing extra) and 1,024 on Sonnet 5. Changing the schema, the thinking config or
   effort starts a new cache prefix. Budgets still assume no cache hits.
3. `temperature`: claude-sonnet-5 returns HTTP 400 for any non-default temperature/top_p/top_k (docs
   models/sonnet-5/whats-new-sonnet-5, "Sampling parameters not accepted"). claude-haiku-4-5 accepts it. So
   `temperature=None` (the default) sends nothing; a non-None temperature for a model in
   `pricing.ACCEPTS_TEMPERATURE` that maps to False raises ValueError before any row, reservation or call. For
   Haiku it is recorded and sent via `extra_body` (the SDK has no `temperature` keyword at all).
4. Thinking: Sonnet 5 runs ADAPTIVE THINKING BY DEFAULT when `thinking` is omitted (thinking bills as output tokens
   and counts toward max_tokens). Haiku 4.5 uses manual extended thinking, off unless `thinking.type == "enabled"`.
   Both accept `thinking={"type": "disabled"}` (docs build-with-claude/thinking-troubleshooting table: only
   "enabled" is rejected on Sonnet 5, only "adaptive" on Haiku 4.5), so every call sends it explicitly.
5. Usage (types/usage.py): `input_tokens` counts ONLY tokens after the last cache breakpoint (uncached input);
   `cache_creation_input_tokens` and `cache_read_input_tokens` are separate and can be None; total input is the sum of
   the three. Errors: `anthropic.APIStatusError` has `.status_code`, `.message`, `.body` (decoded JSON), `.request_id`,
   `.type`; the body is `{"type": "error", "error": {"type": ..., "message": ..., "details": {...}}, "request_id": ...}`.
   Spend limit set in the Console: HTTP 400, error type `invalid_request_error`, message starting "You have reached
   your specified API usage limits" (or "... specified workspace API usage limits"). Tier monthly cap: HTTP 429,
   type `rate_limit_error`, and `error.details.error_code == "enforced_spend_limit_reached"` (the SDK has no
   `error_code` attribute, so it is read from the body). Connection and timeout failures are `anthropic.APIConnectionError`
   / `APITimeoutError` with no status code.
"""
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from decimal import Decimal

import anthropic
from django.conf import settings
from django.db import connection, transaction
from pydantic import ValidationError

from moderation import breaker, budget, clock, pricing
from moderation.errors import (
    BreakerOpen,
    BudgetExceeded,
    BudgetUnavailable,
    LLMAPIError,
    LLMDisabled,
    LLMOutputError,
    LLMRefused,
    ModelNotAllowed,
)
from moderation.fake_llm import FakeProviderError
from moderation.models import LLMCall
from moderation.scrub import scrub

logger = logging.getLogger(__name__)

ALL_PURPOSES = budget.SITE_PURPOSES + budget.EVAL_PURPOSES
_ZERO = Decimal("0")

# Sent on every call: see facts 2 and 4 in the module docstring.
_THINKING_OFF = {"type": "disabled"}
_CACHE_BREAKPOINT = {"type": "ephemeral"}

_client = None


@dataclass(frozen=True)
class LLMResult:
    parsed: object
    call_id: int
    stop_reason: str
    input_tokens: int
    output_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    cost_usd: Decimal
    latency_ms: int


# --- Client access ------------------------------------------------------------------------------

def _build_real_client():
    """The only place a real Anthropic client is built. Never called in tests (a fixture makes it raise)."""
    return anthropic.Anthropic(
        api_key=settings.ANTHROPIC_API_KEY,
        max_retries=settings.LLM_MAX_RETRIES,
        timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS,
    )


def get_client():
    global _client
    if _client is None:
        _client = _build_real_client()
    return _client


def set_client(client):
    global _client
    _client = client


def reset_client():
    global _client
    _client = None


# --- Helpers ------------------------------------------------------------------------------------

def _prompt_sha256(system, schema_json):
    return hashlib.sha256((system + "\x00" + json.dumps(schema_json, sort_keys=True)).encode("utf-8")).hexdigest()


def _normalize_provider_error(exc):
    """Reduce an SDK error or a FakeProviderError to (status_code, error_type, error_code, message, request_id)."""
    status_code = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    error = body.get("error") if isinstance(body, dict) else None
    error = error if isinstance(error, dict) else {}
    details = error.get("details")
    details = details if isinstance(details, dict) else {}
    error_type = getattr(exc, "error_type", None) or error.get("type") or getattr(exc, "type", None) or ""
    if not error_type and isinstance(exc, anthropic.APITimeoutError):
        error_type = "timeout_error"
    elif not error_type and isinstance(exc, anthropic.APIConnectionError):
        error_type = "connection_error"
    error_code = getattr(exc, "error_code", None) or details.get("error_code") or error.get("error_code") or ""
    message = getattr(exc, "message", None) or error.get("message") or str(exc)
    # Scrubbed once, here, so no stored or sent copy of provider text (row, exception, breaker detail, alert) can hold a key.
    if status_code is not None and not error_type and not error_code:
        error_type = f"http_{status_code}"  # a non-JSON body (an HTML 502 from a proxy) carries no type or code
    return status_code, scrub(error_type), scrub(error_code), scrub(message), getattr(exc, "request_id", None)


def _message_text(message):
    blocks = getattr(message, "content", None) or []
    return "".join(getattr(block, "text", "") or "" for block in blocks if getattr(block, "type", "text") == "text")


def _breaker_open_error(status):
    if status.kind == "soft":
        when = f"{status.retry_at:%Y-%m-%d %H:%M:%S} UTC" if status.retry_at else "shortly"
        message = f"the AI moderator is temporarily unavailable after repeated API errors; it will retry automatically after {when}"
    else:
        message = (
            f"the AI moderator is paused until the administrator resumes it ({status.reason or 'tripped'}); "
            "run `manage.py reset_breaker` after checking why it tripped"
        )
    return BreakerOpen(message, kind=status.kind, retry_at=status.retry_at)


def _release_probe(probe):
    if probe:
        try:
            breaker.release_probe()
        except Exception:
            logger.exception("could not release the half-open probe")


def _write_refusal(base, exc, text):
    """Log a refusal. Best effort: a failure to write the row must not hide the refusal itself."""
    try:
        row = LLMCall.objects.create(
            **base,
            status=exc.status,
            error=text,
            error_code=exc.status,
            reserved_usd=_ZERO,
            cost_usd=_ZERO,
            created_at=clock.now(),
            finished_at=clock.now(),
        )
        exc.call_id = row.pk
    except Exception:
        logger.exception("could not write the refusal row for %s", exc.status)


def _finish_row(row, session, reserved, cost, **fields):
    for name, value in fields.items():
        setattr(row, name, value)
    row.cost_usd = cost
    row.finished_at = clock.now()
    row.save()
    if session is not None:
        session.spent += cost - reserved


def call(
    *,
    purpose,
    agent,
    model,
    system,
    messages,
    output_schema,
    max_tokens,
    prompt_version="",
    attempt=1,
    temperature=None,
    conversation_id=None,
    run_id=None,
    session=None,
    cache_system=True,
):
    """Make one guarded call and return an LLMResult. Raises ValueError (unknown purpose, bad max_tokens, temperature on a model that rejects it), an
    LLMRefused subclass (no request was made), BudgetUnavailable (no request was made), LLMAPIError (provider failure)
    or LLMOutputError (the call is billed but the output is unusable). One attempt only; retrying is the pipeline's job.

    NEVER call this inside a database transaction (`transaction.atomic`): a rollback in the caller would erase the
    cost record and shrink the budget. The pipeline (step 5) must call it outside any transaction."""
    if purpose not in ALL_PURPOSES:
        raise ValueError(f"unknown purpose {purpose!r}; expected one of {ALL_PURPOSES}")
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
        raise ValueError(f"max_tokens must be a positive integer, not {max_tokens!r}")
    if max_tokens > settings.LLM_MAX_TOKENS_LIMIT:
        raise ValueError(f"max_tokens {max_tokens} is above LLM_MAX_TOKENS_LIMIT ({settings.LLM_MAX_TOKENS_LIMIT})")
    if (
        not isinstance(messages, list)
        or not messages
        or not isinstance(messages[0], dict)
        or messages[0].get("role") != "user"
    ):
        raise ValueError("messages must be a non-empty list whose first element is a dict with role 'user'")
    schema_json = output_schema.model_json_schema()
    estimated_input = budget.estimate_input_tokens(system=system, messages=messages, schema=schema_json)
    if estimated_input > settings.LLM_MAX_INPUT_TOKENS:
        raise ValueError(
            f"estimated input of {estimated_input} tokens is above LLM_MAX_INPUT_TOKENS ({settings.LLM_MAX_INPUT_TOKENS})"
        )
    if settings.LLM_FORBID_ATOMIC_CALLS and connection.in_atomic_block:
        raise RuntimeError(
            "llm.call() was called inside a database transaction (transaction.atomic). If the caller's transaction "
            "rolled back, the ledger row and any breaker trip written here would be erased and the budget would "
            "shrink. LLM calls must run outside any transaction."
        )

    if pricing.ACCEPTS_THINKING_DISABLED.get(model) is False:
        raise ValueError(f"{model} rejects thinking={{'type': 'disabled'}}, which every call sends (HTTP 400)")
    if temperature is not None and pricing.ACCEPTS_TEMPERATURE.get(model) is False:
        raise ValueError(f"{model} does not accept a temperature (the API returns HTTP 400); pass temperature=None")

    request = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
        "output_schema": schema_json,
        "cache_system": bool(cache_system),
        "thinking": _THINKING_OFF,
    }
    if cache_system:
        request["cache_control"] = _CACHE_BREAKPOINT  # logged for readability: it is applied to the system block only
    if temperature is not None:
        request["temperature"] = temperature
    base = dict(
        purpose=purpose,
        run_id=run_id,
        conversation_id=conversation_id,
        agent=agent,
        attempt=attempt,
        model=model,
        prompt_version=prompt_version,
        prompt_sha256=_prompt_sha256(system, schema_json),
        temperature=temperature,
        max_tokens=max_tokens,
        request=request,
    )

    # 1. Kill switch and API key.
    if not settings.LLM_ENABLED:
        exc = LLMDisabled("LLM calls are switched off (LLM_ENABLED is False in config/tunables.py)")
        _write_refusal(base, exc, str(exc))
        raise exc
    if not settings.ANTHROPIC_API_KEY:
        exc = LLMDisabled("no ANTHROPIC_API_KEY is configured")
        _write_refusal(base, exc, str(exc))
        raise exc

    # 2. Circuit breaker (fail closed if its state cannot be read).
    try:
        status = breaker.check()  # may claim the single half-open probe for this call
    except Exception as err:
        raise BudgetUnavailable(f"circuit breaker state could not be read: {err}") from err
    if status.open:
        exc = _breaker_open_error(status)
        _write_refusal(base, exc, str(exc))
        raise exc
    probe = status.probe  # this call is the half-open probe: it must end in record_success/record_error or a release

    # 3. Model allow-list.
    try:
        pricing.get_price(model)
    except ModelNotAllowed as exc:
        _release_probe(probe)
        _write_refusal(base, exc, str(exc))
        raise

    # 4. Ledger, caps and reservation, atomically (SQLite takes the write lock up front, so concurrent callers queue).
    reserved = budget.reservation_usd(model, estimated_input_tokens=estimated_input, max_tokens=max_tokens)
    try:
        with transaction.atomic():
            budget.check_caps(purpose=purpose, conversation_id=conversation_id, amount=reserved, session=session)
            row = LLMCall.objects.create(**base, status="pending", reserved_usd=reserved, created_at=clock.now())
    except BudgetExceeded as exc:
        _release_probe(probe)
        _write_refusal(base, exc, str(exc))
        raise
    except BudgetUnavailable:
        _release_probe(probe)
        raise
    except Exception as err:
        _release_probe(probe)
        raise BudgetUnavailable(f"ledger could not be read or written: {err}") from err
    if session is not None:
        session.spent += reserved

    # 5. The call itself.
    parse_kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "system": [{"type": "text", "text": system, "cache_control": _CACHE_BREAKPOINT}] if cache_system else system,
        "messages": messages,
        "output_format": output_schema,
        "thinking": _THINKING_OFF,
    }
    if temperature is not None:
        parse_kwargs["extra_body"] = {"temperature": temperature}

    try:
        client = get_client()
    except Exception as err:
        # No client, so nothing was sent and nothing is billed: release the reservation. Not fed to the breaker.
        _release_probe(probe)
        _finish_row(
            row, session, reserved, _ZERO,
            status="error", error=f"{type(err).__name__}: {err}", error_code="client_error", latency_ms=0,
        )
        raise

    started = time.monotonic()
    try:
        message = client.messages.parse(**parse_kwargs)
    except (anthropic.APIError, FakeProviderError) as err:
        latency_ms = int((time.monotonic() - started) * 1000)
        status_code, error_type, error_code, text, request_id = _normalize_provider_error(err)
        # An HTTP error response means the request was rejected, so it is not billed. With no status (a dropped
        # connection or a timeout) we cannot know, so the reservation stays counted.
        cost = _ZERO if status_code is not None else reserved
        _finish_row(
            row, session, reserved, cost,
            status="error", error=text, error_code=error_code or error_type,
            latency_ms=latency_ms, provider_request_id=request_id or "",
        )
        breaker.record_error(status_code=status_code, error_type=error_type, error_code=error_code, message=text)
        raise LLMAPIError(
            text, status_code=status_code, error_type=error_type, error_code=error_code, call_id=row.pk
        ) from err
    except ValidationError as err:
        # The SDK could not parse the text (invalid or truncated JSON). The usage is lost, so the call is billed at
        # its reservation, the worst case, and never under-counted.
        latency_ms = int((time.monotonic() - started) * 1000)
        _finish_row(
            row, session, reserved, reserved,
            status="ok", error=f"output could not be parsed (possibly truncated): {err}", error_code="invalid_output",
            latency_ms=latency_ms,
        )
        breaker.record_success()
        raise LLMOutputError(
            "output could not be parsed into the schema (possibly truncated at max_tokens)",
            reason="invalid_output", stop_reason="", call_id=row.pk,
        ) from err
    except Exception as err:
        # An unexpected failure inside parse(): the request may have been sent, so fail closed on money and keep
        # the reservation counted. Not fed to the breaker (a probe is given back so the next caller may try).
        _release_probe(probe)
        _finish_row(
            row, session, reserved, reserved,
            status="error", error=f"{type(err).__name__}: {err}", error_code="client_error",
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise
    latency_ms = int((time.monotonic() - started) * 1000)

    # 6. Success: price the real usage.
    usage = message.usage
    tokens_in = int(getattr(usage, "input_tokens", 0) or 0)
    tokens_out = int(getattr(usage, "output_tokens", 0) or 0)
    cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    cost = pricing.compute_cost(
        model,
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        cache_write_tokens=cache_write,
        cache_read_tokens=cache_read,
    )
    stop_reason = getattr(message, "stop_reason", "") or ""

    parsed = getattr(message, "parsed_output", None)
    failure = None
    if stop_reason == "max_tokens":
        failure = ("truncated", "the output was cut off at max_tokens")
    elif stop_reason == "refusal":
        failure = ("refusal", "the model refused")
    elif parsed is None:
        failure = ("invalid_output", "the response contained no parsed output")
    elif not isinstance(parsed, output_schema):
        try:
            parsed = output_schema.model_validate(parsed)
        except ValidationError as err:
            failure = ("invalid_output", f"the output does not fit the schema: {err}")
    parsed_json = parsed.model_dump(mode="json") if failure is None else None

    _finish_row(
        row, session, reserved, cost,
        status="ok", raw_response=_message_text(message), parsed=parsed_json,
        tokens_in=tokens_in, tokens_out=tokens_out, cache_write_tokens=cache_write, cache_read_tokens=cache_read,
        latency_ms=latency_ms, stop_reason=stop_reason,
        provider_request_id=getattr(message, "_request_id", None) or getattr(message, "id", "") or "",
        error=failure[1] if failure else "", error_code=failure[0] if failure else "",
    )
    breaker.record_success()
    if failure:
        raise LLMOutputError(failure[1], reason=failure[0], stop_reason=stop_reason, call_id=row.pk)
    return LLMResult(
        parsed=parsed,
        call_id=row.pk,
        stop_reason=stop_reason,
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        cache_write_tokens=cache_write,
        cache_read_tokens=cache_read,
        cost_usd=cost,
        latency_ms=latency_ms,
    )
