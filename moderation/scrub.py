"""Remove secrets from provider text before we store or send it. Standard library only; never raises.

Redacts: (a) the exact configured API key, (b) any `sk-ant-` style token, (c) the value after the header-style names
x-api-key, authorization and proxy-authorization (case-insensitive, ':' or '=', optional Bearer/Basic/Token scheme).
Every repetition in the patterns is bounded, so there is no catastrophic backtracking.
"""
import re

REDACTED = "[redacted]"
_MAX_LENGTH = 20000

_TOKEN = re.compile(r"sk-ant-[A-Za-z0-9_-]{10,512}")
_HEADER = re.compile(
    r"""(?P<name>\b(?:x-api-key|proxy-authorization|authorization)\b["']?)"""
    r"""\s{0,8}[:=]\s{0,8}["']?"""
    r"""(?:(?:bearer|basic|token)\s{1,8})?"""
    r"""(?:\[redacted\]|[^\s,;"'}\]]{1,512})""",  # an already-redacted value matches whole, so scrubbing is idempotent
    re.IGNORECASE,
)


def scrub(text):
    """Return `text` (as a string) with secrets replaced by '[redacted]'."""
    try:
        text = "" if text is None else str(text)
        text = text[:_MAX_LENGTH]
        try:
            from django.conf import settings

            key = getattr(settings, "ANTHROPIC_API_KEY", "") or ""
        except Exception:
            key = ""
        if key:
            text = text.replace(key, REDACTED)
        text = _TOKEN.sub(REDACTED, text)
        text = _HEADER.sub(lambda m: f"{m.group('name')}: {REDACTED}", text)
        return text
    except Exception:
        return REDACTED
