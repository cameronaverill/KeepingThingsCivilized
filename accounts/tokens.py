"""Signed, expiring, single-use email confirmation links (step 6a).

The token is django.core.signing output (signature plus timestamp) under a salt used for nothing else, so a token
minted for another purpose (a password reset, say) can never be replayed here. It carries the user id and a
fingerprint of the account's confirmation state. The fingerprint changes the moment the account is confirmed, so the
same link stops working after its first use even though the signature itself is still valid.
"""
import hashlib
from dataclasses import dataclass

from django.conf import settings
from django.core import signing

from .models import User

SALT = "accounts.email-confirmation.v1"

OK = "ok"
EXPIRED = "expired"
USED = "used"
INVALID = "invalid"


@dataclass(frozen=True)
class TokenResult:
    status: str
    user: "User | None" = None


def is_pending(user):
    """An unconfirmed account: created by registration and not yet activated by its link."""
    return not user.is_active and user.email_verified_at is None


def _state(user):
    """A short value that changes when the account is confirmed (or its address changes)."""
    raw = f"{user.pk}|{user.email_key}|{user.email_verified_at.isoformat() if user.email_verified_at else ''}"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def make_token(user):
    return signing.dumps({"u": user.pk, "s": _state(user)}, salt=SALT, compress=True)


def _user_for(payload):
    try:
        return User.objects.filter(pk=int(payload["u"])).first()
    except (KeyError, TypeError, ValueError):
        return None


def check_token(token):
    """Classify a link: OK (with the user), EXPIRED, USED (already confirmed) or INVALID (tampered or unknown)."""
    max_age = settings.EMAIL_CONFIRM_MAX_AGE_DAYS * 24 * 60 * 60
    expired = False
    try:
        payload = signing.loads(token, salt=SALT, max_age=max_age)
    except signing.SignatureExpired:
        # The signature is genuine but old. Read it again without the age limit, so an already-confirmed
        # account is told so rather than "expired".
        expired = True
        try:
            payload = signing.loads(token, salt=SALT)
        except signing.BadSignature:
            return TokenResult(INVALID)
    except signing.BadSignature:
        return TokenResult(INVALID)
    if not isinstance(payload, dict):
        return TokenResult(INVALID)
    user = _user_for(payload)
    if user is None:
        return TokenResult(INVALID)
    if not is_pending(user):
        return TokenResult(USED, user)
    if payload.get("s") != _state(user):
        return TokenResult(INVALID)
    return TokenResult(EXPIRED if expired else OK, user)
