"""7b: the static files exist and follow the design rules (44px targets, visible focus, 4.5:1 contrast, no left/right
alignment for the two participants, no maxlength in compose.js, and a button disabled only while a check runs)."""
import re

import pytest
from django.contrib.staticfiles import finders
from django.templatetags.static import static

import fviews_js as J

NAMES = ["site.css", "compose.js", "poll.js"]


def read(name):
    return (J.STATIC_DIR / name).read_text(encoding="utf-8")


def code(name):
    """The script without its comments, so a comment that says 'there is no maxlength' is not mistaken for code."""
    text = read(name)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", text)


@pytest.mark.parametrize("name", NAMES)
def test_static_file_exists_and_is_served_by_staticfiles(name):
    assert (J.STATIC_DIR / name).is_file() and read(name).strip()
    assert finders.find(f"forum/{name}"), f"forum/{name} must be found by the static file finders"
    assert static(f"forum/{name}").endswith(f"forum/{name}")


# --- the stylesheet -------------------------------------------------------------------------------------------------

def rules(css):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [(m.group(1).strip(), m.group(2)) for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css)]


def variables(css):
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"(--[\w-]+)\s*:\s*([^;}]+)", css)}


def resolve(value, vars_):
    value = value.strip()
    for _ in range(5):
        found = re.fullmatch(r"var\((--[\w-]+)(?:\s*,\s*([^)]+))?\)", value)
        if not found:
            break
        value = vars_.get(found.group(1), found.group(2) or "").strip()
    return value


def hex_rgb(value):
    value = value.strip().lower()
    named = {"white": "#ffffff", "black": "#000000"}
    value = named.get(value, value)
    found = re.fullmatch(r"#([0-9a-f]{3}|[0-9a-f]{6})", value)
    if not found:
        return None
    digits = found.group(1)
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    return tuple(int(digits[i:i + 2], 16) for i in (0, 2, 4))


def luminance(rgb):
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def declared(body, prop):
    found = re.search(rf"(?:^|;|\s){prop}\s*:\s*([^;]+)", body)
    return found.group(1).strip() if found else None


def test_the_stylesheet_has_44px_touch_targets_and_a_narrow_screen_layout():
    css = read("site.css")
    assert re.search(r"min-height\s*:\s*44px", css) or re.search(r"min-height\s*:\s*2\.75rem", css)
    assert re.search(r"@media[^{]*max-width|min-width", css) or "flex-wrap" in css or "grid" in css
    assert re.search(r"overflow-wrap\s*:\s*(anywhere|break-word)|word-break\s*:\s*break-word|word-wrap\s*:\s*break-word", css), \
        "a long unbroken word in a message must wrap, not scroll the page sideways"
    assert not re.search(r"overflow-x\s*:\s*scroll", css)


def test_buttons_are_at_least_44px_high():
    css = read("site.css")
    hits = [sel for sel, body in rules(css)
            if re.search(r"\bmin-height\s*:\s*(44px|2\.75rem)|\bheight\s*:\s*(44px|2\.75rem)", body)]
    assert any(re.search(r"button|\.btn|\[type", sel) for sel in hits), f"no 44px rule for buttons: {hits}"


def test_focus_is_visible_and_never_removed():
    css = read("site.css")
    assert ":focus-visible" in css or ":focus" in css
    for selector, body in rules(css):
        if re.search(r"outline\s*:\s*(none|0)\b", body) and ":not(:focus-visible)" not in selector:
            pytest.fail(f"outline removed without a replacement for {selector!r}")
    assert re.search(r"outline\s*:\s*[^;]*\d+px|outline-width\s*:|box-shadow\s*:[^;]*\d+px", css)


def test_text_and_background_colour_pairs_reach_4_5_to_1():
    css = read("site.css")
    vars_ = variables(css)
    pairs = 0
    for selector, body in rules(css):
        fg = declared(body, "color")
        bg = declared(body, "background-color") or declared(body, "background")
        if not fg or not bg:
            continue
        fg_rgb, bg_rgb = hex_rgb(resolve(fg, vars_)), hex_rgb(resolve(bg, vars_))
        if fg_rgb is None or bg_rgb is None:
            continue
        pairs += 1
        ratio = contrast(fg_rgb, bg_rgb)
        assert ratio >= 4.5, f"{selector!r}: {fg} on {bg} is only {ratio:.2f}:1"
    assert pairs >= 1, "declare at least the body text and background colours as hex (or variables holding hex) in one rule"


def test_text_colours_used_without_their_own_background_reach_4_5_on_the_page_background():
    """A rule that sets `color` but no background sits on the page (body) background or a white surface."""
    css = read("site.css")
    vars_ = variables(css)
    page_bgs = []
    for selector, body in rules(css):
        if selector.strip() in ("body", "html", "html, body", "body, html"):
            bg = declared(body, "background-color") or declared(body, "background")
            if bg:
                page_bgs.append(bg)
    page_bgs = [hex_rgb(resolve(b, vars_)) for b in page_bgs]
    page_bgs = [b for b in page_bgs if b] or [hex_rgb("#ffffff")]
    surfaces = page_bgs + [hex_rgb("#ffffff")]
    checked = 0
    for selector, body in rules(css):
        fg = declared(body, "color")
        has_bg = declared(body, "background-color") or declared(body, "background")
        if not fg or has_bg:
            continue
        fg_rgb = hex_rgb(resolve(fg, vars_))
        if fg_rgb is None:
            continue
        checked += 1
        for bg_rgb in surfaces:
            ratio = contrast(fg_rgb, bg_rgb)
            assert ratio >= 4.5, f"{selector!r}: colour {fg} is only {ratio:.2f}:1 on the page background"
    assert checked >= 1


def test_the_two_participants_are_not_told_apart_by_left_right_alignment_or_partisan_colours():
    css = read("site.css")
    for selector, body in rules(css):
        if not re.search(r"message|msg|bubble|turn|\.you|\.other|mine|theirs|speaker", selector, re.I):
            continue
        assert not re.search(r"float\s*:\s*(left|right)|text-align\s*:\s*right|margin-left\s*:\s*auto|align-self\s*:\s*flex-end", body, re.I), \
            f"{selector!r} aligns a side: {body.strip()}"
        if re.search(r"[.\-_](you|other|mine|theirs)\b", selector, re.I):
            assert not re.search(r"\b(red|blue|crimson|navy|maroon)\b|#(?:f00|00f)\b", body, re.I), f"partisan colour in {selector!r}"


# --- compose.js and poll.js text patterns (the behaviour is tested through Node in test_fviews_js.py) -----------------

def test_compose_js_uses_the_server_counting_rule():
    js = code("compose.js")
    assert re.search(r"\\r\\n|\\r", js), "line endings are normalised"
    assert "normalize(" in js and "NFC" in js, "the count is taken after NFC normalisation"
    assert re.search(r"\.trim\(\)|trimStart|trimEnd|\.replace\([^)]*\^|SPACE|\\s", js), "leading and trailing whitespace is stripped"
    assert re.search(r"Array\.from\(|\[\.\.\.|for\s*\(\s*(const|let|var)\s+\w+\s+of\b|codePointAt|\\u\{?10000|match\(/\[\\ud800", js, re.I), \
        "code points are counted (an emoji is one), not UTF-16 units"


def test_compose_js_only_advises_the_counter_never_truncates_and_disables_the_button_only_while_checking():
    """Step 19: the Post button is disabled while the draft is checked and must be enabled again on every path; the counter
    itself never disables anything (behaviour is tested through Node in test_fviews_check_js.py)."""
    js = code("compose.js")
    assert not re.search(r"maxlength|maxLength", js, re.I), "nothing may be silently truncated"
    assert re.search(r"disabled\s*=\s*false|removeAttribute\(\s*['\"]disabled['\"]", js), "the button is enabled again"
    assert not re.search(r"disabled\s*=\s*(over|n\s*>|count)", js), "the counter never disables the button"
    assert not re.search(r"\.value\s*=\s*[^=]*(slice|substring|substr)\(", js), "the text is never cut"
    assert not re.search(r"\beval\(|document\.write\(|new Function\(", js)


def test_poll_js_reads_the_interval_from_a_data_attribute_and_uses_the_after_parameter():
    js = code("poll.js")
    assert re.search(r"dataset|getAttribute\(\s*['\"]data-", js)
    assert "after=" in js or "after" in js
    assert re.search(r"fetch\(|XMLHttpRequest", js)
    assert re.search(r"setTimeout|setInterval", js)
    assert not re.search(r"\beval\(|document\.write\(|new Function\(", js)
    assert not re.search(r"location\.reload\(\)", js) or "closed" in js, "new messages are appended, not reloaded"
