"""A tiny HTML reader for the step 7b tests: builds a tree from a rendered page so tests can ask questions about
forms, labels, alerts and message blocks without depending on the builder's class names. Nothing imports a conftest."""
import html as _html
import re
from html.parser import HTMLParser

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
SKIP_TEXT = {"script", "style", "head", "template"}


class Node:
    def __init__(self, tag, attrs=None, parent=None):
        self.tag = tag
        self.attrs = dict(attrs or {})
        self.parent = parent
        self.children = []  # Node or str

    # -- queries --
    def walk(self):
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.walk()

    def find_all(self, tag=None, **attrs):
        out = []
        for node in self.walk():
            if tag and node.tag != tag:
                continue
            if all(node.attrs.get(k.replace("_", "-")) == v for k, v in attrs.items()):
                out.append(node)
        return out

    def find(self, tag=None, **attrs):
        found = self.find_all(tag, **attrs)
        return found[0] if found else None

    def text(self, skip=SKIP_TEXT):
        """Visible text with whitespace collapsed. Textarea content counts (it is what the user sees)."""
        parts = []

        def go(node):
            for child in node.children:
                if isinstance(child, str):
                    parts.append(child)
                elif child.tag not in skip:
                    go(child)
                    if child.tag in ("p", "div", "li", "br", "h1", "h2", "h3", "h4", "section", "article", "form", "tr"):
                        parts.append(" ")

        go(self)
        return re.sub(r"\s+", " ", "".join(parts)).strip()

    def raw_text(self):
        """Text exactly as stored (no collapsing), used for textarea contents."""
        return "".join(c if isinstance(c, str) else c.raw_text() for c in self.children)

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def get(self, name, default=None):
        return self.attrs.get(name, default)

    def __repr__(self):
        return f"<{self.tag} {self.attrs}>"


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)

    def handle_endtag(self, tag):
        node = self.cur
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self.cur = node.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def parse(page):
    if isinstance(page, bytes):
        page = page.decode()
    builder = _Builder()
    builder.feed(page)
    builder.close()
    return builder.root


def doc(response):
    return parse(response.content.decode())


def visible_text(response):
    return doc(response).text()


def raw(response):
    return response.content.decode()


# --- forms and controls ---------------------------------------------------------------------------------------------

def forms(root):
    return root.find_all("form")


def form_with_action(root, path):
    """The form whose action is `path` (an empty action or one equal to the page's own path is `page_path`)."""
    for form in forms(root):
        if form.get("action") == path:
            return form
    return None


def forms_posting_to(root, path):
    return [f for f in forms(root) if f.get("action") == path]


def controls(root):
    return [n for n in root.walk() if n.tag in ("input", "textarea", "select")]


def text_control(form):
    """The one free-text control of a form (textarea, or a text input), or None."""
    for node in form.walk():
        if node.tag == "textarea":
            return node
    for node in form.walk():
        if node.tag == "input" and node.get("type", "text") in ("text", "search"):
            return node
    return None


def textarea_value(node):
    """What a browser shows in a textarea: its text, minus one leading newline."""
    value = node.raw_text()
    if value.startswith("\r\n"):
        value = value[2:]
    elif value.startswith("\n"):
        value = value[1:]
    return value


def control_value(node):
    return textarea_value(node) if node.tag == "textarea" else node.get("value", "")


def hidden_csrf(form):
    for node in form.walk():
        if node.tag == "input" and node.get("name") == "csrfmiddlewaretoken":
            return node.get("value")
    return None


def has_button(form):
    return any(n.tag == "button" for n in form.walk())


def unlabelled_controls(root):
    """Visible form controls with no accessible name: no <label for>, no wrapping <label>, no aria-label/-labelledby."""
    label_for = {n.get("for") for n in root.find_all("label") if n.get("for")}
    bad = []
    for node in controls(root):
        kind = node.get("type", "text") if node.tag == "input" else node.tag
        if kind in ("hidden", "submit", "button", "image", "reset"):
            continue
        if node.get("id") and node.get("id") in label_for:
            continue
        if node.get("aria-label", "").strip() or node.get("aria-labelledby", "").strip():
            continue
        if any(a.tag == "label" for a in node.ancestors()):
            continue
        bad.append(node)
    return bad


def fake_buttons(root):
    """Controls that look like buttons but are not <button>: <input type=submit|button>, role=button on a non-button."""
    bad = []
    for node in root.walk():
        if node.tag == "input" and node.get("type") in ("submit", "button", "image", "reset"):
            bad.append(node)
        elif node.get("role") == "button" and node.tag not in ("button",):
            bad.append(node)
    return bad


def alerts(root):
    return [n.text() for n in root.walk() if n.get("role") == "alert"]


def alert_text(response):
    return " ".join(alerts(doc(response)))


def links(root):
    return [n.get("href") for n in root.find_all("a") if n.get("href") is not None]


def scripts_src(root):
    return [n.get("src") for n in root.walk() if n.tag == "script" and n.get("src")]


def stylesheets(root):
    return [n.get("href") for n in root.find_all("link") if "stylesheet" in n.get("rel", "")]


def counter_nodes(root):
    """Elements that carry the live counter: anything whose id, class or a data- attribute name/value says 'counter'."""
    found = []
    for node in root.walk():
        if node.tag in ("textarea", "input", "select", "button", "label"):
            continue  # the box that points at its counter is not the counter
        blob = " ".join([node.get("id", ""), node.get("class", "")] + [f"{k} {v}" for k, v in node.attrs.items() if k.startswith("data-")])
        if "counter" in blob.lower() or "counter" in node.get("aria-describedby", "").lower():
            found.append(node)
    return found


def without_header(root_html):
    """The page with its <header>...</header> removed (the signed-in user's own name may appear there)."""
    return re.sub(r"(?is)<header\b.*?</header>", "", root_html)


def normalise_page(response):
    """The page with per-request tokens removed, to compare two pages for equality."""
    page = re.sub(r'name="csrfmiddlewaretoken" value="[^"]*"', "CSRF", response.content.decode())
    return page


def unescape(text):
    return _html.unescape(text)
