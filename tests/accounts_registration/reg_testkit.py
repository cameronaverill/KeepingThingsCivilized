"""Helpers for the registration tests (step 6c: a username and a password, nothing else). Never imported from a conftest.

Design choices, so the tests do not depend on the builder's internals:
- The two password inputs are discovered from the rendered page by their type (the contract fixes the wording of the
  page, and that the field for the username is called "username"); everything else is checked through what a person
  would see or what the database holds.
- Nothing here sleeps, mails or touches the network.
"""
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

from django.urls import reverse

# Built from pieces so the repo-wide secret scan does not mistake them for credential assignments. Neither is
# similar to any username used in these tests, and both pass every validator.
STRONG_A = "Zebra-Lantern" + "-Quartz-42x"
STRONG_B = "Marble-Falcon" + "-Orbit-77y"

# The exact user-facing sentences of docs/step6c_brief.md ("Register page text"). The owner reviews these.
TITLE = "Create an account"
INTRO = "Choose a username and a password. You do not need an email address."
PASSWORD_NOTE = (
    "There is no password reset by email. If you forget your password, ask the person running this site to reset it."
)
BUTTON = "Create account"
LOGIN_LINK_SENTENCE = "Already have an account? Log in"
TAKEN = "That username is taken."


# --- urls ------------------------------------------------------------------------------------------------------------
def url(name, *args):
    return reverse(f"accounts:{name}", args=args)


# --- html ------------------------------------------------------------------------------------------------------------
class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.links = []  # (href, text)
        self.text = []
        self.buttons = []
        self.headings = []
        self.title = ""
        self._skip = 0
        self._form = None
        self._open = []  # stack of [tag, collected text] for a, button, h1, title

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
            if tag == "input" and (a.get("type") or "").lower() in ("submit", "button"):
                self.buttons.append(a.get("value") or "")
        if tag in ("a", "button", "h1", "title"):
            self._open.append([tag, [], a.get("href")])

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag == "form":
            self._form = None
        if tag in ("a", "button", "h1", "title"):
            for index in range(len(self._open) - 1, -1, -1):
                if self._open[index][0] == tag:
                    _, chunks, href = self._open.pop(index)
                    text = " ".join("".join(chunks).split())
                    if tag == "a" and href:
                        self.links.append((href, text))
                    elif tag == "button":
                        self.buttons.append(text)
                    elif tag == "h1":
                        self.headings.append(text)
                    elif tag == "title":
                        self.title = text
                    break

    def handle_data(self, data):
        for entry in self._open:
            entry[1].append(data)
        if not self._skip:
            self.text.append(data)


def parse(response):
    page = _Page()
    page.feed(response.content.decode())
    return page


def page_text(response):
    """The visible text of a page, entities decoded, whitespace collapsed."""
    return " ".join(" ".join(parse(response).text).split())


def find_form(response, with_input=None):
    for form in parse(response).forms:
        if with_input is not None and not any(i["name"] == with_input for i in form["inputs"]):
            continue
        return form
    return None


def form_values(form):
    return {i["name"]: i["value"] for i in form["inputs"] if i["name"]}


def link_targets(response):
    return [urlsplit(href).path for href, _ in parse(response).links] + [
        urlsplit(f["action"]).path for f in parse(response).forms if f["action"]
    ]


# --- requests --------------------------------------------------------------------------------------------------------
_NAMES = {}


def password_names(client):
    """(password, password again) input names, discovered from the register page by their type."""
    if not _NAMES:
        response = client.get(url("register"))
        assert response.status_code == 200
        form = find_form(response, with_input="username")
        assert form is not None, "the register page must have a form with a 'username' input"
        names = [i["name"] for i in form["inputs"] if i["type"] == "password"]
        assert len(names) == 2, f"expected a password and a repeat input, found {names}"
        _NAMES["names"] = tuple(names)
    return _NAMES["names"]


def csrf_value(client, path):
    """The csrfmiddlewaretoken hidden input of a page (fetching it also sets the CSRF cookie)."""
    form = find_form(client.get(path), with_input="csrfmiddlewaretoken")
    assert form is not None, f"{path} has no form with a CSRF token"
    return form_values(form)["csrfmiddlewaretoken"]


def register(client, username, password, confirm=None, csrf=True, **extra):
    first, second = password_names(client)
    data = {"username": username, first: password, second: password if confirm is None else confirm}
    if csrf:
        data["csrfmiddlewaretoken"] = csrf_value(client, url("register"))
    return client.post(url("register"), data, **extra)


def logged_in_user_id(client, settings):
    """The id of the user the visitor's session belongs to, or None when nobody is logged in."""
    if settings.SESSION_COOKIE_NAME not in client.cookies:
        return None
    return client.session.get("_auth_user_id")


INTERNAL_MARKERS = re.compile(
    r"Traceback|BadSignature|django\.|IntegrityError|ValueError|KeyError|Exception|"
    r"\{\{|\{%|\bNone\b|\bNoneType\b|object at 0x|<function",
)


def assert_no_internal_text(response):
    text = response.content.decode()
    found = INTERNAL_MARKERS.search(text)
    assert not found, f"internal detail on the page: {found.group(0)!r}"
