"""A scripted stand-in for the Anthropic client, used by every test. It never imports `anthropic`.

    fake = FakeLLM([{"verdict": "ok"}, FakeProviderError(500, "api_error", "boom"), lambda kwargs: make_message(...)])
    llm.set_client(fake)

Each `client.messages.parse(**kwargs)` pops the next scripted item and records `kwargs` in `.calls`. An item is:
- a dict: validated into `kwargs["output_format"]` (a Pydantic model class) and returned in a message;
- a `FakeProviderError`: raised (it carries the same attributes `llm.py` reads from an SDK error);
- a callable `(kwargs) -> message`: its return value is returned (use `make_message` to build one).
A dict that does not fit the schema comes back as `parsed_output=None` (see `_FakeMessages.parse`).
"""
from types import SimpleNamespace

import json

from pydantic import BaseModel, ValidationError


class FakeProviderError(Exception):
    """Same attributes as the ones `llm.py` reads from an SDK APIStatusError."""

    def __init__(self, status_code, error_type, message, error_code=None):
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type
        self.message = message
        self.error_code = error_code


def make_message(
    parsed,
    *,
    input_tokens=100,
    output_tokens=50,
    cache_creation_input_tokens=0,
    cache_read_input_tokens=0,
    stop_reason="end_turn",
    id="msg_fake_0001",
    text=None,
):
    """A fake response. `parsed` is a dict or a Pydantic instance (or None, as when the model refused). `.parsed_output`
    starts as `parsed`; FakeLLM validates a dict into the requested schema before returning it."""
    if text is None:
        if isinstance(parsed, BaseModel):
            text = parsed.model_dump_json()
        elif parsed is None:
            text = ""
        else:
            text = json.dumps(parsed)
    return SimpleNamespace(
        id=id,
        parsed_output=parsed,
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=cache_creation_input_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
        ),
    )


class _FakeMessages:
    def __init__(self, owner):
        self._owner = owner

    def parse(self, **kwargs):
        owner = self._owner
        owner.calls.append(kwargs)
        if not owner.script:
            raise AssertionError("FakeLLM script is exhausted: the code made more calls than were scripted")
        item = owner.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        message = item(kwargs) if callable(item) else make_message(item)
        schema = kwargs.get("output_format")
        if isinstance(message.parsed_output, dict) and schema is not None:
            try:
                message.parsed_output = schema.model_validate(message.parsed_output)
            except ValidationError:
                # The real SDK raises here and loses the usage; the fake keeps the usage and reports "no parsed
                # output", so tests can check the billing of an unusable answer. To test the raising path, script a
                # callable that raises pydantic.ValidationError.
                message.parsed_output = None
        return message


class FakeLLM:
    def __init__(self, script=()):
        self.script = list(script)
        self.calls = []
        self.messages = _FakeMessages(self)
