"""Typed exceptions of the LLM gateway. Only `llm.py` imports the `anthropic` package; it normalizes SDK errors into these."""


class LLMRefused(Exception):
    """The guard refused the call; no request was made. `status` is the LLMCall.status the refusal is logged with."""

    status = "refused"

    def __init__(self, message="", *, call_id=None):
        super().__init__(message)
        self.message = message
        self.call_id = call_id


class LLMDisabled(LLMRefused):
    """The kill switch is off, or there is no API key."""

    status = "refused_disabled"


class BreakerOpen(LLMRefused):
    """`kind` is "hard" (needs `manage.py reset_breaker`) or "soft" (retries by itself; `retry_at` says when)."""

    status = "refused_breaker"

    def __init__(self, message="", *, kind="hard", retry_at=None, call_id=None):
        super().__init__(message, call_id=call_id)
        self.kind = kind
        self.retry_at = retry_at


class ModelNotAllowed(LLMRefused):
    status = "refused_model"


class BudgetExceeded(LLMRefused):
    status = "refused_budget"

    def __init__(self, message="", *, cap_name=None, limit=None, spent=None, requested=None, call_id=None):
        if not message:
            message = f"budget cap {cap_name!r} would be exceeded: limit {limit}, spent {spent}, requested {requested}"
        super().__init__(message, call_id=call_id)
        self.cap_name = cap_name
        self.limit = limit
        self.spent = spent
        self.requested = requested


class BudgetUnavailable(Exception):
    """The ledger (or the guard state) could not be read, so the call is refused (fail closed)."""


class LLMAPIError(Exception):
    """The provider call failed."""

    def __init__(self, message="", *, status_code=None, error_type="", error_code="", call_id=None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_type = error_type
        self.error_code = error_code
        self.call_id = call_id


class LLMOutputError(Exception):
    """The call succeeded (and is billed and logged `ok`) but the output is unusable: truncated, refused, or invalid."""

    def __init__(self, message="", *, reason="", stop_reason="", call_id=None):
        super().__init__(message or reason)
        self.reason = reason
        self.stop_reason = stop_reason
        self.call_id = call_id
