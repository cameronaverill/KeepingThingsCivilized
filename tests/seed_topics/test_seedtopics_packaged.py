"""10a: the packaged data file forum/seed_topics.json (docs/step10a_brief.md, "The data file")."""
import json
import re

import pytest
from django.conf import settings

import seedtopics_kit as K

ENTRIES = K.packaged() if K.PACKAGED.exists() else []
POLITICAL = [e for e in ENTRIES if not K.is_warmup(e)]
WARMUP = [e for e in ENTRIES if K.is_warmup(e)]

INSULTS = (
    "idiot", "stupid", "moron", "dumb", "evil", "bigot", "fascist", "commie", "libtard", "scum", "traitor", "sheep",
    "brainwash", "ignorant", "hate", "lunatic", "crazy", "deplorable",
)  # fmt: skip
PERSONAL_NAMES = (
    "trump", "biden", "obama", "harris", "sanders", "clinton", "reagan", "bush", "pelosi", "mcconnell", "newsom", "desantis",
    "warren", "musk", "adams", "de blasio", "mamdani", "mr.", "mrs.", "ms.", "dr.",
)  # fmt: skip


def all_strings(node):
    """Every string in a nested structure, keys included."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for k, v in node.items() for s in [k, *all_strings(v)]]
    if isinstance(node, list):
        return [s for v in node for s in all_strings(v)]
    return []


def rationales(entry):
    return [
        cell["rationale"]
        for side in K.SIDES
        for scheme, axes in K.SCHEMES.items()
        for cell in (entry["leans"][side][scheme][axis] for axis in axes)
    ]


def values(entry):
    return [
        entry["leans"][side][scheme][axis]["value"] for side in K.SIDES for scheme, axes in K.SCHEMES.items() for axis in axes
    ]


def test_the_packaged_file_exists_and_is_a_json_list_of_six():
    assert K.PACKAGED.is_file()
    assert isinstance(json.loads(K.PACKAGED.read_text(encoding="utf-8")), list)
    assert len(ENTRIES) == 6


def test_every_entry_has_exactly_the_five_keys():
    assert [sorted(e) for e in ENTRIES] == [sorted(K.ENTRY_KEYS)] * 6


def test_titles_are_the_three_golden_topics_and_the_three_warmup_topics():
    titles = [e["title"] for e in ENTRIES]
    assert len(set(titles)) == 6
    assert {t for t in titles if t in K.GOLDEN_TITLES} == set(K.GOLDEN_TITLES)
    assert sorted(t.casefold() for t in titles if t not in K.GOLDEN_TITLES) == sorted(K.WARMUP_TITLES_FOLDED)


def test_the_rent_control_proposition_is_the_one_in_the_brief():
    assert K.by_title(ENTRIES, "Rent control")["proposition"] == K.RENT_PROPOSITION


@pytest.mark.parametrize("entry", ENTRIES, ids=[e["title"] for e in ENTRIES])
def test_every_packaged_proposition_fits_the_limit_and_is_a_nonempty_string(entry):
    assert isinstance(entry["proposition"], str)
    assert entry["proposition"].strip() == entry["proposition"]
    assert 20 <= len(entry["proposition"]) <= settings.MAX_PROPOSITION_CHARS


@pytest.mark.parametrize("entry", ENTRIES, ids=[e["title"] for e in ENTRIES])
def test_every_packaged_title_and_description_is_a_nonempty_string(entry):
    assert isinstance(entry["title"], str) and entry["title"].strip() == entry["title"] and entry["title"] != ""
    assert isinstance(entry["description"], str) and entry["description"] != ""


COST_WORDS = ("$", "cost", "dollar", "usd", "price")
STOP = {"should", "their", "that", "with", "than", "from", "have", "each", "which", "there", "would", "these"}


def content_words(text):
    """Lower-case words of four or more letters, possessive 's dropped, plain stop words removed."""
    words = re.findall(r"[a-z]+", re.sub(r"'s\b", "", text.casefold()))
    return {w for w in words if len(w) >= 4 and w not in STOP}


@pytest.mark.parametrize("entry", ENTRIES, ids=[e["title"] for e in ENTRIES])
def test_every_entry_has_an_opposing_position_that_is_a_plain_claim_within_the_limit(entry):
    text = entry["opposing_position"]
    assert isinstance(text, str) and text.strip() == text
    assert 20 <= len(text) <= settings.MAX_PROPOSITION_CHARS
    assert text[0].isupper() and text[-1] in ".!?"


@pytest.mark.parametrize("entry", ENTRIES, ids=[e["title"] for e in ENTRIES])
def test_the_opposing_position_differs_from_the_proposition(entry):
    assert entry["opposing_position"] != entry["proposition"]
    assert entry["opposing_position"].casefold() != entry["proposition"].casefold()


@pytest.mark.parametrize("entry", ENTRIES, ids=[e["title"] for e in ENTRIES])
def test_the_opposing_position_is_about_the_same_subject_as_the_proposition(entry):
    shared = content_words(entry["proposition"]) & content_words(entry["opposing_position"])
    assert len(shared) >= 3


@pytest.mark.parametrize("entry", ENTRIES, ids=[e["title"] for e in ENTRIES])
def test_the_opposing_position_carries_no_prefix_debate_or_cost_wording_and_no_name_calling(entry):
    text = entry["opposing_position"].casefold()
    assert not text.startswith("my position")
    assert "disagree" not in text
    assert "debate" not in text
    assert [w for w in COST_WORDS if w in text] == []
    assert [w for w in INSULTS if w in text] == []


def test_the_sanctuary_cities_opposing_position_is_the_one_ruled_by_the_architect():
    assert K.by_title(ENTRIES, "Sanctuary cities")["opposing_position"] == (
        "Cities should not limit their local police's cooperation with federal immigration enforcement."
    )


def test_opposing_positions_are_all_different_from_every_proposition_and_from_each_other():
    opposing = [e["opposing_position"] for e in ENTRIES]
    assert len(set(opposing)) == 6
    assert set(opposing).isdisjoint(e["proposition"] for e in ENTRIES)


def test_no_entry_says_debate_anywhere_in_its_wording():
    text = " ".join(all_strings([{k: v for k, v in e.items() if k != "leans"} for e in ENTRIES])).casefold()
    assert "debate" not in text


def test_propositions_are_all_different():
    assert len({e["proposition"] for e in ENTRIES}) == 6


@pytest.mark.parametrize("entry", ENTRIES, ids=[e["title"] for e in ENTRIES])
def test_leans_have_exactly_both_sides_the_two_schemes_and_their_axes(entry):
    leans = entry["leans"]
    assert sorted(leans) == ["con", "pro"]
    assert [sorted(leans[side]) for side in K.SIDES] == [sorted(K.SCHEMES)] * 2
    assert [sorted(leans[side][scheme]) for side in K.SIDES for scheme in K.SCHEMES] == [
        sorted(axes) for _ in K.SIDES for axes in K.SCHEMES.values()
    ]


@pytest.mark.parametrize("entry", ENTRIES, ids=[e["title"] for e in ENTRIES])
def test_every_leans_cell_has_only_a_float_value_in_range_and_a_rationale(entry):
    cells = [
        entry["leans"][side][scheme][axis] for side in K.SIDES for scheme, axes in K.SCHEMES.items() for axis in axes
    ]
    assert len(cells) == 6
    assert [sorted(c) for c in cells] == [["rationale", "value"]] * 6
    assert all(type(c["value"]) is float for c in cells)
    assert all(-1.0 <= c["value"] <= 1.0 for c in cells)


@pytest.mark.parametrize("entry", ENTRIES, ids=[e["title"] for e in ENTRIES])
def test_every_rationale_is_one_or_two_plain_sentences(entry):
    texts = rationales(entry)
    assert all(isinstance(t, str) and t.strip() == t and len(t) >= 20 for t in texts)
    assert all(t[0].isupper() and t[-1] in ".!?" for t in texts)
    assert all(1 <= len(re.findall(r"[.!?](?:\s|$)", t)) <= 2 for t in texts)


@pytest.mark.parametrize("entry", ENTRIES, ids=[e["title"] for e in ENTRIES])
def test_no_rationale_or_description_name_calls(entry):
    text = " ".join([entry["description"], entry["opposing_position"], *rationales(entry)]).casefold()
    assert [word for word in INSULTS if word in text] == []


@pytest.mark.parametrize("entry", WARMUP, ids=[e["title"] for e in WARMUP])
def test_warmup_topics_have_every_value_zero_and_the_fixed_rationale(entry):
    assert values(entry) == [0.0] * 6
    assert rationales(entry) == [K.WARMUP_LEANS_RATIONALE] * 6


@pytest.mark.parametrize("entry", POLITICAL, ids=[e["title"] for e in POLITICAL])
def test_political_topics_are_coded_and_the_two_sides_point_in_opposite_directions(entry):
    pro_party = entry["leans"]["pro"]["us_partisan"]["party"]["value"]
    con_party = entry["leans"]["con"]["us_partisan"]["party"]["value"]
    assert pro_party * con_party < 0
    assert any(v != 0.0 for v in values(entry))
    assert all(K.WARMUP_LEANS_RATIONALE not in r for r in rationales(entry))


@pytest.mark.parametrize("entry", POLITICAL, ids=[e["title"] for e in POLITICAL])
def test_political_topics_have_a_specific_rationale_for_every_cell(entry):
    texts = rationales(entry)
    assert all(len(t) >= 40 for t in texts)
    assert len(set(texts)) >= 4


def test_the_first_entrys_description_says_plainly_the_leans_are_a_draft():
    description = ENTRIES[0]["description"].casefold()
    assert "draft" in description


def test_the_packaged_file_holds_no_email_address_url_or_personal_name():
    text = " ".join(all_strings(ENTRIES))
    lowered = text.casefold()
    assert re.findall(r"\S+@\S+", text) == []
    assert re.findall(r"https?://|www\.|\.com\b|\.org\b|\.net\b|\.gov\b|\.edu\b", lowered) == []
    assert [name for name in PERSONAL_NAMES if re.search(r"\b" + re.escape(name), lowered)] == []


def test_the_packaged_file_holds_no_secret_or_env_reference():
    raw = K.PACKAGED.read_text(encoding="utf-8")
    assert re.findall(r"sk-ant|\.env|api[_ ]?key|password", raw, flags=re.IGNORECASE) == []
