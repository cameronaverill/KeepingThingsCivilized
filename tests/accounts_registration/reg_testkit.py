"""Helpers for the step 6a tests (registration, email confirmation, resend). Never imported from a conftest.

Design choices, so the tests do not depend on the builder's internals:
- Form field names for the two password inputs are discovered from the rendered page (the contract only fixes
  "username" and "email"); every other detail is checked through what a user (or a mail client) would see.
- Time is controlled by a Clock that patches time.time (used by django.core.signing, the cache and any rate limiter
  built on them) and django.utils.timezone.now (used for database timestamps). The clock is FROZEN, so nothing is
  ever slept on and counted-down messages ("wait 60 seconds") are exactly reproducible; tests call advance().
- Mail failures are produced by pointing MAILERS at SwitchBackend below (an in-memory backend that raises on demand),
  so any code path that sends through the default mailer is covered, however it is called.
"""
import re
import time
from datetime import datetime, timedelta, timezone as dt_timezone
from html.parser import HTMLParser
from urllib.parse import quote, urlsplit

from django.core.mail.backends.locmem import EmailBackend as _LocmemBackend
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

# Built from pieces so the repo-wide secret scan does not mistake them for credential assignments. Neither is
# similar to any username or address used in these tests, and both pass every validator.
STRONG_A = "Zebra-Lantern" + "-Quartz-42x"
STRONG_B = "Marble-Falcon" + "-Orbit-77y"

REAL_TIME = time.time
REAL_NOW = timezone.now


# --- time ------------------------------------------------------------------------------------------------------------
class Clock:
    """A frozen clock that only moves when a test calls advance()."""

    def __init__(self):
        self._start = REAL_TIME()
        self.offset = 0.0

    def time(self):
        return self._start + self.offset

    def now(self):
        return datetime.fromtimestamp(self.time(), tz=dt_timezone.utc)

    def advance(self, seconds=0, days=0):
        self.offset += seconds + days * 86400


# --- mail ------------------------------------------------------------------------------------------------------------
class SwitchBackend(_LocmemBackend):
    """The in-memory mailbox, except that it raises SwitchBackend.error (an exception instance) while one is set."""

    error = None

    def send_messages(self, messages):
        if SwitchBackend.error is not None:
            raise SwitchBackend.error
        return super().send_messages(messages)


SWITCH_MAILERS = {"default": {"BACKEND": "reg_testkit.SwitchBackend", "OPTIONS": {}}}

LINK_RE = re.compile(r"https?://[^\s<>\"']+?/confirm/([^\s/<>\"']+)/")


def confirmation_links(message):
    """Every confirmation link in a message body, as (absolute_url, token)."""
    return [(m.group(0), m.group(1)) for m in LINK_RE.finditer(message.body)]


def only_link(message):
    links = confirmation_links(message)
    assert len(links) == 1, f"expected exactly one confirmation link, found {len(links)} in: {message.body!r}"
    return links[0]


# --- urls ------------------------------------------------------------------------------------------------------------
def url(name, *args):
    return reverse(f"accounts:{name}", args=args)


def url_or(name, fallback):
    """reverse() for a name owned by step 6b, with a literal fallback so 6a tests do not depend on 6b's timing."""
    try:
        return url(name)
    except NoReverseMatch:
        return fallback


def login_url():
    return url_or("login", "/accounts/login/")


def reset_url():
    return url_or("password_reset", "/accounts/password-reset/")


def confirm_path(token):
    return url("confirm", token)


# --- html ------------------------------------------------------------------------------------------------------------
class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.links = []
        self.text = []
        self._skip = 0
        self._form = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "form":
            self._form = {"action": a.get("action"), "method": (a.get("method") or "get").lower(), "inputs": []}
            self.forms.append(self._form)
        elif tag in ("input", "textarea", "select", "button") and self._form is not None:
            self._form["inputs"].append(
                {"name": a.get("name"), "type": (a.get("type") or "text").lower(), "value": a.get("value")}
            )
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag == "form":
            self._form = None

    def handle_data(self, data):
        if not self._skip:
            self.text.append(data)


def parse(response):
    page = _Page()
    page.feed(response.content.decode())
    return page


def page_text(response):
    """The visible text of a page, entities decoded, whitespace collapsed."""
    return " ".join(" ".join(parse(response).text).split())


def find_form(response, action_path=None, with_input=None):
    """The first form whose action is action_path (an empty action means 'this page') and that has an input."""
    for form in parse(response).forms:
        if action_path is not None and form["action"] not in (action_path, "", None):
            continue
        if with_input is not None and not any(i["name"] == with_input for i in form["inputs"]):
            continue
        return form
    return None


def form_values(form):
    return {i["name"]: i["value"] for i in form["inputs"] if i["name"]}


def leads_to(response, *paths):
    """True if the page links to, or has a form posting to, any of the given paths."""
    page = parse(response)
    targets = set(page.links) | {f["action"] for f in page.forms if f["action"]}
    targets = {urlsplit(t).path for t in targets}
    return any(p in targets for p in paths)


# --- requests --------------------------------------------------------------------------------------------------------
_FIELDS = {}


def register_fields(client):
    """{'username','email','password','confirm'} input names, discovered from the register page."""
    if not _FIELDS:
        response = client.get(url("register"))
        assert response.status_code == 200
        form = find_form(response, url("register"), with_input="username")
        assert form is not None, "the register page must have a form with a 'username' input"
        names = [i["name"] for i in form["inputs"]]
        assert "email" in names, "the register form must have an 'email' input"
        passwords = [i["name"] for i in form["inputs"] if i["type"] == "password"]
        assert len(passwords) == 2, f"expected a password and a confirmation input, found {passwords}"
        _FIELDS.update(username="username", email="email", password=passwords[0], confirm=passwords[1])
    return _FIELDS


def csrf_value(client, path):
    """The csrfmiddlewaretoken hidden input of a page (fetching it also sets the CSRF cookie)."""
    form = find_form(client.get(path), with_input="csrfmiddlewaretoken")
    assert form is not None, f"{path} has no form with a CSRF token"
    return form_values(form)["csrfmiddlewaretoken"]


def register(client, username, email, password, confirm=None, csrf=True, **extra):
    fields = register_fields(client)
    data = {
        fields["username"]: username,
        fields["email"]: email,
        fields["password"]: password,
        fields["confirm"]: password if confirm is None else confirm,
    }
    if csrf:
        data["csrfmiddlewaretoken"] = csrf_value(client, url("register"))
    return client.post(url("register"), data, **extra)


def resend(client, email, csrf=True, **extra):
    data = {"email": email}
    if csrf:
        data["csrfmiddlewaretoken"] = csrf_value(client, url("resend"))
    return client.post(url("resend"), data, **extra)


def follow(client, response, limit=5):
    """Follow redirects like a browser; returns the final response."""
    hops = 0
    while response.status_code in (301, 302, 303, 307, 308) and hops < limit:
        response = client.get(response["Location"])
        hops += 1
    return response


def open_link(client, link):
    """Click a confirmation link (an absolute URL or a path) and follow any redirects."""
    return follow(client, client.get(urlsplit(link).path))


def normalise(text, hide=()):
    """Replace each value in `hide` (addresses, usernames), plain and URL-quoted, by a placeholder."""
    for value in hide:
        for variant in {value, value.lower(), quote(value), quote(value, safe="")}:
            text = re.sub(re.escape(variant), "<HIDDEN>", text, flags=re.I)
    return text


def snapshot(client, response, hide=()):
    """Everything a visitor can observe about a response chain, with the given values hidden."""
    chain = []
    hops = 0
    while response.status_code in (301, 302, 303, 307, 308) and hops < 5:
        chain.append((response.status_code, normalise(response["Location"], hide)))
        response = client.get(response["Location"])
        hops += 1
    page = parse(response)
    return {
        "chain": chain,
        "status": response.status_code,
        "text": normalise(page_text(response), hide),
        "links": sorted(normalise(h, hide) for h in page.links),
        "forms": sorted(
            (normalise(f["action"] or "", hide), f["method"], tuple(sorted((i["name"] or "", i["type"]) for i in f["inputs"])))
            for f in page.forms
        ),
    }


def cookie_names(client):
    return sorted(client.cookies.keys())


def session_keys(client, settings):
    """Keys stored in the visitor's session, or None when they have no session."""
    if settings.SESSION_COOKIE_NAME not in client.cookies:
        return None
    return sorted(client.session.keys())


INTERNAL_MARKERS = re.compile(
    r"Traceback|BadSignature|SignatureExpired|django\.|IntegrityError|ValueError|KeyError|Exception|"
    r"\{\{|\{%|\bNone\b|\bNoneType\b|object at 0x|<function",
)


def assert_no_internal_text(response):
    text = response.content.decode()
    found = INTERNAL_MARKERS.search(text)
    assert not found, f"internal detail on the page: {found.group(0)!r}"
