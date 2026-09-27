"""Wave 16 items 4 & 5 (docs/wave16_brief.md): moderator messages show their real seq_no alongside "automated", and a
moderator message built from several intervention acts (content joined with "\\n\\n") renders each act as its own
<p class="msg-text"> paragraph instead of one run-on sentence. User messages are unaffected either way."""
import re

import fviews_html as H
import fviews_kit as K


def article_for_seq(root, seq_no):
    """The <article class="msg ..."> whose data-seq matches ``seq_no`` (a str or int)."""
    found = [n for n in root.find_all("article") if n.get("data-seq") == str(seq_no)]
    assert len(found) == 1, f"expected exactly one article for seq {seq_no}, found {len(found)}"
    return found[0]


def meta_text(article):
    span = next(n for n in article.walk() if n.tag == "span" and "msg-meta" in n.get("class", ""))
    return H.unescape(span.text())


def paragraphs_of(article):
    return [H.unescape(p.text()) for p in article.walk() if p.tag == "p" and "msg-text" in p.get("class", "")]


# --- item 4: moderator messages show their real sequence number --------------------------------------------------------

def test_a_moderator_message_shows_message_n_alongside_automated():
    duo = K.Duo()
    m1 = duo.seed(duo.pa, "First user message.", minutes_ago=10)
    K.add_moderator_post(duo.conv, m1, "A moderator note.", [(duo.pa.label, duo.pa.label, [m1])])
    root = H.doc(duo.ca.get(duo.url))
    mod_articles = [n for n in root.find_all("article") if "msg-moderator" in n.get("class", "")]
    assert len(mod_articles) == 1
    article = mod_articles[0]
    seq = article.get("data-seq")
    assert seq is not None and seq.isdigit()
    text = meta_text(article)
    assert f"message {seq}" in text
    assert "automated" in text


def test_a_mix_of_user_and_moderator_messages_shows_a_contiguous_ordered_sequence_of_numbers():
    duo = K.Duo()
    m1 = duo.seed(duo.pa, "User message one.", minutes_ago=50)
    m2 = duo.seed(duo.pb, "User message two.", minutes_ago=40)
    K.add_moderator_post(duo.conv, m2, "Moderator note about message two.", [(duo.pb.label, duo.pb.label, [m2])])
    m4 = duo.seed(duo.pa, "User message four.", minutes_ago=20)
    K.add_moderator_post(duo.conv, m4, "Moderator note about message four.", [(duo.pa.label, duo.pa.label, [m4])])
    root = H.doc(duo.ca.get(duo.url))
    articles = [n for n in root.find_all("article") if n.tag == "article" and n.get("data-seq")]
    seqs = sorted(int(n.get("data-seq")) for n in articles)
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs))), f"gaps in the shown sequence: {seqs}"
    for article in articles:
        seq = article.get("data-seq")
        assert f"message {seq}" in meta_text(article), f"article {seq} does not show its own number"


# --- item 5: multiple moderator notes in one message get a visible paragraph break --------------------------------------

def test_a_moderator_message_with_two_joined_notes_renders_as_two_paragraphs():
    duo = K.Duo()
    trigger = duo.seed(duo.pa, "Trigger message for a two-act note.", minutes_ago=30)
    K.add_moderator_post(duo.conv, trigger, "First note.\n\nSecond note.", [(duo.pa.label, duo.pa.label, [trigger])])
    root = H.doc(duo.ca.get(duo.url))
    article = next(n for n in root.find_all("article") if "msg-moderator" in n.get("class", ""))
    paras = paragraphs_of(article)
    assert paras == ["First note.", "Second note."]


def test_a_moderator_message_with_one_note_still_renders_as_exactly_one_paragraph():
    duo = K.Duo()
    trigger = duo.seed(duo.pa, "Trigger message for a single-act note.", minutes_ago=30)
    K.add_moderator_post(duo.conv, trigger, "Only note.", [(duo.pa.label, duo.pa.label, [trigger])])
    root = H.doc(duo.ca.get(duo.url))
    article = next(n for n in root.find_all("article") if "msg-moderator" in n.get("class", ""))
    assert paragraphs_of(article) == ["Only note."]


def test_a_user_message_is_never_split_even_if_it_contains_a_literal_blank_line():
    duo = K.Duo()
    duo.seed(duo.pa, "User line one.\n\nUser line two, still one message.", minutes_ago=5)
    root = H.doc(duo.ca.get(duo.url))
    user_articles = [n for n in root.find_all("article") if "msg-you" in n.get("class", "") or "msg-other" in n.get("class", "")]
    assert user_articles, "no user article found"
    article = user_articles[-1]
    paras = paragraphs_of(article)
    assert len(paras) == 1
    assert re.sub(r"\s+", " ", paras[0]) == "User line one. User line two, still one message."
