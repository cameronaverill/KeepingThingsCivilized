"""Readers for the enter forms on the home page (waiting cards) and on the propose page (seeded topics). Helper only;
nothing imports a conftest."""
import re

import fviews_html as H

ENTER = re.compile(r"/p/(\d+)/enter/")


class Choice:
    def __init__(self, form, button, side):
        self.form, self.button, self.side = form, button, side
        self.text = H.unescape(button.text()).strip()
        self.topic_id = int(ENTER.fullmatch(form.get("action", "")).group(1))


def choices_in(root):
    """{topic id: {side: Choice}} read from the enter forms: a hidden `side` input (or a button named side)."""
    out = {}
    for form in H.forms(root):
        found = ENTER.fullmatch(form.get("action", ""))
        if not found:
            continue
        tid = int(found.group(1))
        buttons = [n for n in form.walk() if n.tag == "button"]
        hidden = [n.get("value") for n in form.walk() if n.tag == "input" and n.get("name") == "side" and n.get("type") == "hidden"]
        if hidden:
            out.setdefault(tid, {})[hidden[0]] = Choice(form, buttons[0], hidden[0])
        else:
            for b in [b for b in buttons if b.get("name") == "side"]:
                out.setdefault(tid, {})[b.get("value")] = Choice(form, b, b.get("value"))
    return out


def enter_forms(root):
    return [f for f in H.forms(root) if ENTER.fullmatch(f.get("action", ""))]


class Card:
    def __init__(self, node, form, username, quote, block_form):
        self.node, self.form, self.username, self.quote, self.block_form = node, form, username, quote, block_form
        self.button = next(n for n in form.walk() if n.tag == "button")
        self.button_text = H.unescape(self.button.text()).strip()
        self.side = next((n.get("value") for n in form.walk() if n.tag == "input" and n.get("name") == "side"), None)
        self.topic_id = int(ENTER.fullmatch(form.get("action")).group(1))


BLOCK = re.compile(r"/users/([^/]+)/block/")
LABEL = re.compile(r"(\S+) is waiting to discuss:")


def waiting_cards(root):
    """One Card per enter form: the smallest ancestor holding the '<username> is waiting to discuss:' label and exactly one
    enter form is the card. The card's block form (POST to /users/<username>/block/) is read too."""
    cards = []
    for form in enter_forms(root):
        node = form.parent
        while node is not None and not (LABEL.search(node.text()) and len(enter_forms(node)) == 1):
            node = node.parent
            if node is not None and len(enter_forms(node)) > 1:
                node = None
        assert node is not None, "an enter form that is not inside a card with the waiting label"
        username = LABEL.search(H.unescape(node.text())).group(1)
        quotes = [n for n in node.walk() if n.tag == "blockquote"] or \
                 [n for n in node.walk() if "quote" in n.get("class", "")]
        assert quotes, "the card has a quote block"
        block = next((f for f in H.forms(node) if BLOCK.fullmatch(f.get("action", ""))), None)
        cards.append(Card(node, form, username, " ".join(H.unescape(quotes[0].text()).split()), block))
    return cards


def form_fields(form):
    """What a browser would send for this form (all named inputs except the CSRF token; the button's own name/value too)."""
    fields = {n.get("name"): n.get("value", "") for n in form.walk()
              if n.tag == "input" and n.get("name") and n.get("name") != "csrfmiddlewaretoken" and n.get("type") != "submit"}
    for b in form.walk():
        if b.tag == "button" and b.get("name"):
            fields[b.get("name")] = b.get("value", "")
    return fields
