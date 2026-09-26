"""moderation/quotes.py: locate_quote finds the model's quote in the message; offsets ALWAYS index the ORIGINAL text."""
import pytest

LEFT_CURLY_D, RIGHT_CURLY_D = "“", "”"
RIGHT_CURLY_S, LEFT_CURLY_S = "’", "‘"
EN, EM, NBSP = "–", "—", " "


def locate(text, quote):
    from moderation.quotes import locate_quote

    return locate_quote(text, quote)


def span(text, result):
    assert result.start is not None and result.end is not None
    return text[result.start:result.end]


# --- exact ---------------------------------------------------------------------------------------------------------

def test_exact_match_reports_offsets_and_match_type():
    text = "Rent control always reduces the housing supply, and everyone knows it."
    result = locate(text, "always reduces the housing supply")
    assert result.match == "exact"
    assert result.start == text.index("always")
    assert result.end == result.start + len("always reduces the housing supply")
    assert span(text, result) == "always reduces the housing supply"
    assert result.occurrences == 1


def test_first_occurrence_wins_and_all_occurrences_are_counted():
    text = "cheap rent is good, cheap rent is fair, cheap rent for all"
    result = locate(text, "cheap rent")
    assert (result.start, result.end, result.match, result.occurrences) == (0, 10, "exact", 3)


def test_exact_is_preferred_over_an_earlier_normalized_match():
    text = "RENT CONTROL FAILS. Then, rent control fails."
    result = locate(text, "rent control fails")
    assert result.match == "exact"
    assert result.start == text.index("rent control fails")
    assert result.start > 0
    assert result.occurrences == 1


def test_whole_message_quote():
    text = "You clearly have no idea what you are talking about."
    result = locate(text, text)
    assert (result.start, result.end, result.match) == (0, len(text), "exact")
    assert result.occurrences == 1


def test_whole_message_quote_with_normalization():
    text = "You clearly   have no idea\nwhat you’re talking about."
    result = locate(text, "you clearly have no idea what you're talking about.")
    assert result.match == "normalized"
    assert (result.start, result.end) == (0, len(text))


def test_single_character_message_and_quote():
    result = locate("x", "x")
    assert (result.start, result.end, result.match) == (0, 1, "exact")


# --- not found -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("quote", ["", " ", "   ", "\n", "\t \n", NBSP, " "])
def test_empty_and_whitespace_only_quotes_are_not_found(quote):
    result = locate("Some text with   spaces\nand lines.", quote)
    assert result.match == "not_found"
    assert result.occurrences == 0


def test_empty_quote_in_empty_text_is_not_found():
    assert locate("", "").match == "not_found"


def test_quote_in_empty_text_is_not_found():
    assert locate("", "anything").match == "not_found"


def test_quote_longer_than_the_text_is_not_found():
    assert locate("short text", "short text and a great deal more of it").match == "not_found"


def test_paraphrase_is_not_found():
    text = "Rent control has always increased the housing supply."
    for quote in ("Rent controls increase housing supply", "rent control raised supply", "has always decreased"):
        result = locate(text, quote)
        assert result.match == "not_found", quote
        assert result.occurrences == 0


def test_a_word_order_change_is_not_found():
    assert locate("one two three four", "three two").match == "not_found"


def test_inserted_word_is_not_found():
    assert locate("rent control fails", "rent control always fails").match == "not_found"


# --- normalized ----------------------------------------------------------------------------------------------------

def test_case_insensitive():
    text = "Everyone KNOWS that Rent Control Fails."
    result = locate(text, "rent control fails")
    assert result.match == "normalized"
    assert span(text, result) == "Rent Control Fails"
    result = locate(text, "EVERYONE knows")
    assert result.match == "normalized" and span(text, result) == "Everyone KNOWS"


def test_normalized_occurrences_count_all_case_variants():
    text = "Rent Control is bad. rent control is worse. RENT CONTROL is the worst."
    result = locate(text, "Rent control")
    assert result.match == "normalized"
    assert result.start == 0
    assert result.occurrences == 3


@pytest.mark.parametrize(
    "text, quote, expected",
    [
        ("rent  control    fails", "rent control fails", "rent  control    fails"),
        ("rent\ncontrol fails", "rent control fails", "rent\ncontrol fails"),
        ("rent\r\ncontrol\tfails", "rent control fails", "rent\r\ncontrol\tfails"),
        (f"rent{NBSP}control fails", "rent control fails", f"rent{NBSP}control fails"),
        ("rent control fails", "rent   control\n\nfails", "rent control fails"),
        (f"rent   control fails", "rent control fails", f"rent   control fails"),
        ("a rent  \n  control b", "rent control", "rent  \n  control"),
    ],
)
def test_whitespace_runs_collapse(text, quote, expected):
    result = locate(text, quote)
    assert result.match == "normalized"
    assert span(text, result) == expected


@pytest.mark.parametrize(
    "text, quote, expected",
    [
        (f"He said {LEFT_CURLY_D}rent control works{RIGHT_CURLY_D} again", 'He said "rent control works" again', f"He said {LEFT_CURLY_D}rent control works{RIGHT_CURLY_D} again"),
        (f"That{RIGHT_CURLY_S}s not what I said", "That's not what I said", f"That{RIGHT_CURLY_S}s not what I said"),
        ("That's not what I said", f"That{RIGHT_CURLY_S}s not what I said", "That's not what I said"),
        ('He said "no way" today', f"He said {LEFT_CURLY_D}no way{RIGHT_CURLY_D} today", 'He said "no way" today'),
        (f"the {LEFT_CURLY_S}war on drugs{RIGHT_CURLY_S} failed", "the 'war on drugs' failed", f"the {LEFT_CURLY_S}war on drugs{RIGHT_CURLY_S} failed"),
    ],
)
def test_curly_and_straight_quotes_and_apostrophes_are_equal(text, quote, expected):
    result = locate(text, quote)
    assert result.match == "normalized"
    assert span(text, result) == expected


@pytest.mark.parametrize(
    "text, quote, expected",
    [
        (f"between 2010{EN}2020 rents rose", "between 2010-2020 rents", f"between 2010{EN}2020 rents"),
        (f"wait{EM}what did you say", "wait-what did you", f"wait{EM}what did you"),
        ("well-known fact", f"well{EN}known fact", "well-known fact"),
        ("that - and only that - matters", f"that {EM} and only that {EN} matters", "that - and only that - matters"),
        (f"a{EM}b", f"a{EN}b", f"a{EM}b"),
    ],
)
def test_en_dash_em_dash_and_hyphen_are_equal(text, quote, expected):
    result = locate(text, quote)
    assert result.match == "normalized"
    assert span(text, result) == expected


def test_combined_normalizations_at_once():
    text = f"Then he said:\n  {LEFT_CURLY_D}It{RIGHT_CURLY_S}s the 2010{EN}2020 data,{NBSP}stupid!{RIGHT_CURLY_D}  End."
    quote = 'IT\'S THE 2010-2020   DATA, STUPID!"'
    result = locate(text, quote)
    assert result.match == "normalized"
    assert span(text, result) == f"It{RIGHT_CURLY_S}s the 2010{EN}2020 data,{NBSP}stupid!{RIGHT_CURLY_D}"


def test_a_normalized_match_can_cover_a_multi_line_span():
    text = "First line.\nSecond   line\ncontinues here.\nLast."
    result = locate(text, "second line continues")
    assert result.match == "normalized"
    assert span(text, result) == "Second   line\ncontinues"


def test_quote_padded_with_whitespace_still_matches():
    text = "rent control fails badly"
    result = locate(text, "  rent control fails \n")
    assert result.match in ("exact", "normalized")
    assert span(text, result).strip().lower().startswith("rent control")


# --- offsets index the ORIGINAL text -------------------------------------------------------------------------------

PREFIXES = [
    "",
    "café über naïve résumé ",
    "\U0001F600\U0001F44D\U0001F1FA\U0001F1F8 ",  # emoji, incl. a flag (two regional indicators)
    "café naïve résumé ",  # combining accents (decomposed)
    "日本語のテキスト ",  # CJK
    "İstanbul ß ﬁ ﬃ ẞ ",  # letters whose case folding changes their length
    "A​‍﻿ invisible ",  # zero-width characters
    "‮ right-to-left ‬ ",
    "\U0001F468‍\U0001F469‍\U0001F467 family ",  # a ZWJ sequence
]


@pytest.mark.parametrize("prefix", PREFIXES, ids=range(len(PREFIXES)))
def test_exact_offsets_index_the_original_after_multibyte_prefixes(prefix):
    text = prefix + "The moon is made of cheese, obviously." + " Trailing \U0001F600."
    result = locate(text, "made of cheese")
    assert result.match == "exact"
    assert span(text, result) == "made of cheese"
    assert result.start == len(prefix) + len("The moon is ")


@pytest.mark.parametrize("prefix", PREFIXES, ids=range(len(PREFIXES)))
def test_normalized_offsets_index_the_original_after_multibyte_prefixes(prefix):
    text = prefix + f"The moon is   made{NBSP}of\ncheese{RIGHT_CURLY_S}s core, obviously." + " Trailing \U0001F600."
    result = locate(text, "MADE OF CHEESE'S CORE")
    assert result.match == "normalized"
    assert span(text, result) == f"made{NBSP}of\ncheese{RIGHT_CURLY_S}s core"


@pytest.mark.parametrize("prefix", PREFIXES, ids=range(len(PREFIXES)))
def test_normalized_offsets_when_the_quote_is_at_the_very_end(prefix):
    text = prefix + "So it goes: NO WAY  OUT"
    result = locate(text, "no way out")
    assert result.match == "normalized"
    assert span(text, result) == "NO WAY  OUT"
    assert result.end == len(text)


def test_quote_containing_multibyte_characters_and_emoji():
    text = "Voilà \U0001F600 the price is 5€, not 6€ \U0001F4B8 at all."
    result = locate(text, "5€, not 6€ \U0001F4B8")
    assert result.match == "exact" and span(text, result) == "5€, not 6€ \U0001F4B8"
    result = locate(text, "5€,  NOT 6€ \U0001F4B8")
    assert result.match == "normalized" and span(text, result) == "5€, not 6€ \U0001F4B8"


def test_case_folding_that_changes_length_does_not_shift_offsets():
    """Lower-casing the whole text and using its indices would put the span in the wrong place here."""
    text = "İİİİ ßßßß ﬃﬃ said that Rent Control fails, and left."
    result = locate(text, "SAID THAT RENT CONTROL FAILS")
    assert result.match == "normalized"
    assert span(text, result) == "said that Rent Control fails"


def test_normalized_match_found_in_a_message_of_many_lines_and_tabs():
    lines = [f"line {i}\t with odd   spacing \U0001F600" for i in range(50)]
    text = "\n".join(lines)
    result = locate(text, "LINE 37 with odd spacing")
    assert result.match == "normalized"
    assert span(text, result) == "line 37\t with odd   spacing"


def test_start_and_end_are_a_valid_slice_for_every_match_type():
    text = f"Alpha {LEFT_CURLY_D}Beta{RIGHT_CURLY_D}  gamma{EM}delta"
    for quote in ("Alpha", 'alpha "beta" gamma-delta', "gamma", "GAMMA-DELTA"):
        result = locate(text, quote)
        assert result.match != "not_found", quote
        assert 0 <= result.start < result.end <= len(text)
        assert text[result.start:result.end].strip() != ""


def test_result_carries_the_documented_fields():
    result = locate("hello world", "world")
    for name in ("start", "end", "match", "occurrences"):
        assert hasattr(result, name), name


# --- html-unescape fallback --------------------------------------------------------------------------------------------
# After the exact and the normal normalized attempts fail, html.unescape(quote) is tried (exact, then normalized). The match
# is reported as "normalized" and the offsets index the ORIGINAL text. An exact match always comes first.

def test_unescape_ampersand_and_angle_brackets():
    text = "Tom & Jerry said 3 < 5 and 7 > 2 today."
    result = locate(text, "Tom &amp; Jerry said 3 &lt; 5 and 7 &gt; 2")
    assert result.match == "normalized"
    assert span(text, result) == "Tom & Jerry said 3 < 5 and 7 > 2"


def test_unescape_double_quote_entity():
    text = 'He said "rent control works" again.'
    result = locate(text, "He said &quot;rent control works&quot; again")
    assert result.match == "normalized"
    assert span(text, result) == 'He said "rent control works" again'


@pytest.mark.parametrize("entity", ["&#39;", "&#x27;", "&apos;"])
def test_unescape_apostrophe_entities(entity):
    text = "That's not what I said, it's wrong."
    result = locate(text, f"That{entity}s not what I said")
    assert result.match == "normalized"
    assert span(text, result) == "That's not what I said"


def test_all_five_entities_together():
    text = 'A & B said "x < y > z" isn\'t true.'
    result = locate(text, "A &amp; B said &quot;x &lt; y &gt; z&quot; isn&#39;t true")
    assert result.match == "normalized"
    assert span(text, result) == 'A & B said "x < y > z" isn\'t true'


def test_unescape_combined_with_curly_quotes_case_and_whitespace():
    text = f"Then  she said:\n{LEFT_CURLY_D}Cats {chr(38)} dogs{RIGHT_CURLY_D}, isn{RIGHT_CURLY_S}t it?"
    result = locate(text, "she said: &quot;CATS &amp;   dogs&quot;, isn&#39;t it")
    assert result.match == "normalized"
    assert span(text, result) == f"she said:\n{LEFT_CURLY_D}Cats {chr(38)} dogs{RIGHT_CURLY_D}, isn{RIGHT_CURLY_S}t it"


@pytest.mark.parametrize("prefix", PREFIXES, ids=range(len(PREFIXES)))
def test_unescape_offsets_index_the_original_after_multibyte_prefixes(prefix):
    text = prefix + 'Rock & roll <b> "never" dies, isn\'t it? Yes.'
    result = locate(text, "Rock &amp; roll &lt;b&gt; &quot;never&quot; dies, isn&#39;t it")
    assert result.match == "normalized"
    assert span(text, result) == 'Rock & roll <b> "never" dies, isn\'t it'
    assert result.start == len(prefix)


def test_a_message_that_literally_contains_an_entity_is_matched_exactly():
    text = "In HTML you write &amp; to get an ampersand, and &lt; for less-than."
    result = locate(text, "write &amp; to get")
    assert result.match == "exact"
    assert span(text, result) == "write &amp; to get"


def test_exact_wins_over_an_earlier_unescaped_match():
    text = "Fish & chips, then later fish &amp; chips."
    result = locate(text, "fish &amp; chips")
    assert result.match == "exact"
    assert result.start == text.index("fish &amp; chips")


def test_literal_entity_text_with_exact_hit_reports_one_occurrence():
    text = "Use &lt;div&gt; here."
    result = locate(text, "&lt;div&gt;")
    assert (result.match, result.occurrences) == ("exact", 1)
    assert (result.start, result.end) == (4, 15)


def test_a_quote_that_does_not_match_even_after_unescape_is_not_found():
    text = "Tom & Jerry are cartoon characters."
    for quote in ("Tom &amp; Spike", "Tom &lt; Jerry", "&amp;&amp;", "&nosuchentity; Jerry"):
        result = locate(text, quote)
        assert result.match == "not_found", quote
        assert result.occurrences == 0


def test_no_false_positive_on_a_paraphrase_containing_entities():
    text = "Rates rose 5% & 6% in the two cities."
    assert locate(text, "Rates increased 5% &amp; 6% in both cities").match == "not_found"
    assert locate(text, "&amp;").match in ("normalized", "exact")  # a bare ampersand is present, so it is a genuine hit
    assert locate("no ampersand here", "&amp;").match == "not_found"


def test_an_entity_only_quote_that_unescapes_to_whitespace_is_not_found():
    assert locate("some text with   spaces", "&nbsp;").match == "not_found"
    assert locate("abc", "&#32;").match == "not_found"
