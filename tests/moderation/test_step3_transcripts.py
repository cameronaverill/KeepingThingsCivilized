"""golden/transcripts/*.json: format, the 12 paired transcripts and the 4 singles (brief section 3 and plan sections 4, 9)."""
import re
from collections import Counter

import pytest
from step3_testkit import (
    DIMENSION_TO_ISSUE_TYPE,
    is_single,
    load_transcripts,
    pair_dimensions,
    pairs,
    parse_rendered,
    planted_items,
    real_results_untouched,  # noqa: F401  (autouse: nothing here may write into golden/results)
    count_chars,
)

DIMENSIONS = set(DIMENSION_TO_ISSUE_TYPE)
TOP_KEYS = {"id", "pair_id", "variant", "description", "topic", "messages", "trigger_seq"}
LABEL = re.compile(r"^(Participant )?[A-Z]$")


@pytest.fixture(scope="module")
def ts():
    transcripts = load_transcripts()
    assert transcripts, "golden/transcripts/ has no *.json files"
    # the pair/single rules below apply to pairs and singles only; mechanical-series members have their own rules
    # (test_step3_series_*.py)
    return {tid: t for tid, t in transcripts.items() if not t.get("series")}


def nine_pairs(ts):
    """(pair_id, members) for the nine pairs; fails (instead of passing vacuously) if there are not nine."""
    grouped = pairs(ts)
    assert len(grouped) == 9, f"expected 9 pairs, found {len(grouped)}"
    return sorted(grouped.items())


def planted_all(ts):
    """[(transcript, message, planted)] over the whole set; at least the 18 pair plants plus the singles' must exist."""
    items = [(t, m, p) for t in ts.values() for m, p in planted_items(t)]
    assert len(items) >= 20, f"only {len(items)} planted phrases in the whole set"
    return items


# --- inventory ----------------------------------------------------------------------------------------------------

def test_twenty_two_transcripts_eighteen_paired_and_four_single(ts):
    assert len(ts) == 22
    assert sum(1 for t in ts.values() if not is_single(t)) == 18
    assert sum(1 for t in ts.values() if is_single(t)) == 4


def test_ids_are_unique_and_match_the_file_names(ts):
    from step3_testkit import TRANSCRIPT_DIR

    files = sorted(TRANSCRIPT_DIR.glob("*.json"))
    everything = load_transcripts()
    assert len(files) == len(everything), "two files share an id (or a file has none)"
    for t in everything.values():
        assert t["_path"].stem == t["id"]
        assert re.fullmatch(r"[a-z0-9_]+", t["id"]), t["id"]


def test_nine_pairs_each_with_a_left_and_a_right_variant_sharing_the_pair_id(ts):
    grouped = pairs(ts)
    assert len(grouped) == 9
    for pair_id, members in grouped.items():
        assert len(members) == 2, pair_id
        assert sorted(t["variant"] for t in members) == ["left", "right"], pair_id


def test_every_topic_has_a_hard_factual_pair_an_abusive_pair_and_an_obvious_factual_pair(ts):
    by_topic = {}
    for pair_id, members in pairs(ts).items():
        proposition = members[0]["topic"]["proposition"]
        by_topic.setdefault(proposition, []).append((pair_id, pair_dimensions(members)))
    assert len(by_topic) == 3, "the topic propositions differ per topic, and there are three topics"
    for proposition, entries in by_topic.items():
        assert len(entries) == 3, proposition
        kinds = sorted(
            "obvious" if pid.endswith("_factual_obvious") else "hard" if pid.endswith("_factual") else "abusive" if pid.endswith("_abusive") else pid
            for pid, _dims in entries
        )
        assert kinds == ["abusive", "hard", "obvious"], (proposition, [pid for pid, _ in entries])
        for pid, dims in entries:
            assert dims == ({"abusiveness"} if pid.endswith("_abusive") else {"factual_accuracy"}), pid


def test_the_pair_ids_are_exactly_the_nine_expected(ts):
    expected = {f"{topic}_{kind}" for topic in ("rent", "drugs", "sanctuary") for kind in ("factual", "abusive", "factual_obvious")}
    assert set(pairs(ts)) == expected


def test_the_three_topics_are_rent_control_drugs_and_sanctuary_cities(ts):
    by_topic = {}
    for _pid, members in nine_pairs(ts):
        topic = members[0]["topic"]
        by_topic[topic["proposition"]] = (topic["title"] + " " + topic["proposition"]).lower()
    texts = list(by_topic.values())
    assert sum("rent control" in t for t in texts) == 1
    assert sum(bool(re.search(r"decriminali[sz]", t)) and "drug" in t for t in texts) == 1
    assert sum("sanctuary" in t for t in texts) == 1


def test_the_four_singles_are_the_four_documented_kinds(ts):
    singles = [t for t in ts.values() if is_single(t)]

    def long_with_phrase_in_the_middle(t):
        for m, p in planted_items(t):
            length = len(m["text"])
            at = m["text"].find(p["phrase"])
            if length >= 1500 and len(p["phrase"]) < 0.3 * length and 0.25 * length <= at <= 0.75 * length:
                return True
        return False

    def bad_as_a_whole(t):
        return any(p["phrase"].strip() == m["text"].strip() for m, p in planted_items(t))

    def benign(t):
        return not planted_items(t)

    def injection(t):
        return any(
            re.search(r"ignore|disregard|forget", m["text"], re.I) and re.search(r"instruction|previous|prior|system|rules", m["text"], re.I)
            for m in t["messages"]
        )

    kinds = {"long": long_with_phrase_in_the_middle, "whole": bad_as_a_whole, "benign": benign, "injection": injection}
    for name, predicate in kinds.items():
        assert any(predicate(t) for t in singles), f"no single transcript of kind {name!r}"
    # each single serves one kind: no single is claimed only by being ambiguous
    claimed = [[n for n, p in kinds.items() if p(t)] for t in singles]
    assert all(claimed), "a single transcript matches none of the four kinds"
    assert len({tuple(c) for c in claimed}) >= 3, claimed


def test_the_benign_single_plants_nothing_and_has_two_participants(ts):
    benign = [
        t for t in ts.values()
        if is_single(t) and not planted_items(t) and t not in injection_transcripts(ts)
    ]
    assert len(benign) == 1, "exactly one single plants nothing and is not the injection case"
    assert len({m["author"] for m in benign[0]["messages"]}) == 2


# --- format --------------------------------------------------------------------------------------------------------

def test_every_file_has_exactly_the_documented_keys(ts):
    for t in ts.values():
        assert set(t) - {"_path", "stances"} == TOP_KEYS, t["id"]


def test_top_level_field_types(ts):
    for t in ts.values():
        assert isinstance(t["id"], str) and t["id"]
        assert isinstance(t["description"], str) and len(t["description"]) >= 20
        assert isinstance(t["trigger_seq"], int) and not isinstance(t["trigger_seq"], bool)
        if is_single(t):
            assert t["pair_id"] in (None, ""), t["id"]
        else:
            assert isinstance(t["pair_id"], str) and t["pair_id"]
            assert t["variant"] in ("left", "right")
        assert set(t["topic"]) == {"title", "proposition"}
        assert all(isinstance(t["topic"][k], str) and len(t["topic"][k]) >= 8 for k in ("title", "proposition"))
        assert isinstance(t["messages"], list) and len(t["messages"]) >= 2


def test_message_format(ts):
    for t in ts.values():
        for m in t["messages"]:
            assert set(m) == {"seq", "author", "text", "planted"}, (t["id"], sorted(m))
            assert isinstance(m["seq"], int) and not isinstance(m["seq"], bool)
            assert isinstance(m["author"], str) and LABEL.match(m["author"]), (t["id"], m["author"])
            assert isinstance(m["text"], str) and m["text"].strip()
            assert isinstance(m["planted"], list)


def test_seqs_are_unique_increasing_and_the_trigger_is_one_of_them(ts):
    for t in ts.values():
        seqs = [m["seq"] for m in t["messages"]]
        assert seqs == sorted(set(seqs)), t["id"]
        assert t["trigger_seq"] in seqs, t["id"]


def test_planted_item_format(ts):
    for t, m, p in planted_all(ts):
        assert set(p) - {"correction", "evidence"} == {"dimension", "phrase", "intensity"}, (t["id"], sorted(p))
        assert p["dimension"] in DIMENSIONS
        assert isinstance(p["phrase"], str) and p["phrase"].strip()
        assert isinstance(p["intensity"], int) and not isinstance(p["intensity"], bool)
        assert 0 <= p["intensity"] <= 4, (t["id"], p)
        if p["dimension"] == "abusiveness":
            assert p["intensity"] >= 1, (t["id"], "an abusive planted phrase must have intensity 1 or more")


def test_every_planted_phrase_occurs_exactly_and_once_in_its_message(ts):
    for t, m, p in planted_all(ts):
        assert p["phrase"] in m["text"], (t["id"], p["phrase"])
        assert m["text"].count(p["phrase"]) == 1, (t["id"], "a planted phrase must be unambiguous", p["phrase"])


def test_planted_phrases_are_found_by_locate_quote_as_exact_matches_at_the_right_offset(ts):
    from moderation.quotes import locate_quote

    for t, m, p in planted_all(ts):
        result = locate_quote(m["text"], p["phrase"])
        assert result.match == "exact" and result.occurrences == 1
        assert m["text"][result.start:result.end] == p["phrase"]


def test_planted_messages_are_the_trigger_message(ts):
    """The Master flags issues in the newest message (plan section 6), so the planted phrase is in the trigger message."""
    for t, m, _p in planted_all(ts):
        assert m["seq"] == t["trigger_seq"], t["id"]


def test_every_planted_factual_error_states_the_correct_fact_and_the_evidence(ts):
    factual = [(t, m, p) for t, m, p in planted_all(ts) if p["dimension"] == "factual_accuracy"]
    assert len(factual) >= 13, len(factual)  # 6 pairs x 2 variants + the long single
    for t, m, p in factual:
        assert p["intensity"] >= 1, (t["id"], "a planted error is wrong by at least 1")
        for key in ("correction", "evidence"):
            assert isinstance(p.get(key), str) and len(p[key].strip()) >= 15, (t["id"], key)
        assert p["correction"].strip() != p["phrase"].strip()
        assert p["evidence"] != p["correction"], (t["id"], "evidence (a source note) is not the correction again")


def test_every_paired_factual_transcript_plants_at_least_one_error_and_every_abusive_one_a_phrase(ts):
    for _pid, members in nine_pairs(ts):
        dims = pair_dimensions(members)
        assert len(dims) == 1, "a pair plants one kind of problem"
        for t in members:
            assert {p["dimension"] for _m, p in planted_items(t)} == dims, t["id"]


# --- the message limit ---------------------------------------------------------------------------------------------

def test_every_message_is_within_max_message_chars(ts):
    from django.conf import settings

    for t in ts.values():
        for m in t["messages"]:
            n = count_chars(m["text"])
            assert 1 <= n <= settings.MAX_MESSAGE_CHARS, (t["id"], m["seq"], n)


def test_the_long_single_is_long_but_within_the_limit(ts):
    from django.conf import settings

    longest = max(count_chars(m["text"]) for t in ts.values() if is_single(t) for m in t["messages"])
    assert 1500 <= longest <= settings.MAX_MESSAGE_CHARS


# --- pair structure ------------------------------------------------------------------------------------------------

def within_five_percent(a, b):
    return abs(a - b) <= max(0.05 * max(a, b), 3)  # a few characters of slack for very short messages


def pair_of(members):
    by = {t["variant"]: t for t in members}
    return by["left"], by["right"]


def test_pair_members_match_in_structure(ts):
    for pair_id, members in nine_pairs(ts):
        left, right = pair_of(members)
        assert len(left["messages"]) == len(right["messages"]), pair_id
        assert [m["seq"] for m in left["messages"]] == [m["seq"] for m in right["messages"]], pair_id
        assert [m["author"] for m in left["messages"]] == [m["author"] for m in right["messages"]], pair_id
        assert left["trigger_seq"] == right["trigger_seq"], pair_id
        assert left["topic"] == right["topic"], f"{pair_id}: the two variants share the topic and proposition"


def test_the_flawed_message_is_by_the_same_participant_at_the_same_seq_in_both_variants(ts):
    for pair_id, members in nine_pairs(ts):
        left, right = pair_of(members)
        flawed = [{(m["seq"], m["author"]) for m, _p in planted_items(t)} for t in (left, right)]
        assert flawed[0] == flawed[1] and flawed[0], pair_id
        assert len(flawed[0]) == 1, "one flawed message per transcript"


def test_pair_members_plant_the_same_dimension_and_intensity(ts):
    for pair_id, members in nine_pairs(ts):
        left, right = pair_of(members)
        signature = lambda t: sorted((m["seq"], p["dimension"], p["intensity"]) for m, p in planted_items(t))  # noqa: E731
        assert signature(left) == signature(right), pair_id


def test_pair_members_have_lengths_within_five_percent(ts):
    for pair_id, members in nine_pairs(ts):
        left, right = pair_of(members)
        totals = [sum(count_chars(m["text"]) for m in t["messages"]) for t in (left, right)]
        assert within_five_percent(*totals), (pair_id, totals)
        for ml, mr in zip(left["messages"], right["messages"]):
            a, b = count_chars(ml["text"]), count_chars(mr["text"])
            assert within_five_percent(a, b), (pair_id, ml["seq"], a, b)
        for (_m1, p1), (_m2, p2) in zip(planted_items(left), planted_items(right)):
            assert within_five_percent(len(p1["phrase"]), len(p2["phrase"])) or abs(len(p1["phrase"]) - len(p2["phrase"])) <= 12, pair_id


def test_the_two_variants_of_a_pair_actually_differ(ts):
    for pair_id, members in nine_pairs(ts):
        left, right = pair_of(members)
        assert [m["text"] for m in left["messages"]] != [m["text"] for m in right["messages"]], pair_id
        assert left["id"] != right["id"] and left["description"] != right["description"]
        differing = [i for i, (a, b) in enumerate(zip(left["messages"], right["messages"])) if a["text"] != b["text"]]
        assert differing, pair_id
        # The planted phrase itself MAY be identical in both variants (the cleanest control: the same false claim or insult
        # from the other side); what must differ is the argument around it, checked above.


def test_pair_ids_and_transcript_ids_are_related_and_the_variant_is_visible_in_the_id(ts):
    for pair_id, members in nine_pairs(ts):
        for t in members:
            assert t["id"].startswith(pair_id) or pair_id in t["id"], (pair_id, t["id"])
            assert t["variant"] in t["id"], t["id"]


def test_pairs_do_not_share_message_text_across_topics(ts):
    """A pair for one topic must not be a copy of another topic's pair with a word swapped."""
    seen = {}
    for t in ts.values():
        for m in t["messages"]:
            if len(m["text"]) > 80:
                seen.setdefault(m["text"], set()).add(t["topic"]["proposition"])
    assert all(len(props) == 1 for props in seen.values())


# --- the injection transcript --------------------------------------------------------------------------------------

def injection_transcripts(ts):
    return [
        t for t in ts.values()
        if is_single(t)
        and any(re.search(r"ignore|disregard|forget", m["text"], re.I) and re.search(r"instruction|previous|prior|system|rules", m["text"], re.I) for m in t["messages"])
    ]


def test_the_injected_text_is_in_a_user_message(ts):
    found = injection_transcripts(ts)
    assert found, "no injection transcript"
    for t in found:
        assert any(re.search(r"ignore|disregard|forget", m["text"], re.I) for m in t["messages"] if m["author"] in {x["author"] for x in t["messages"]})


def test_rendering_the_injection_transcript_treats_it_as_data(ts):
    from moderation.prompting import render_transcript

    for t in injection_transcripts(ts):
        rendered = render_transcript([(m["seq"], m["author"], m["text"]) for m in t["messages"]])
        parsed = parse_rendered(rendered)
        assert [a["id"] for a, _t in parsed] == [str(m["seq"]) for m in t["messages"]]
        assert [x.strip() for _a, x in parsed] == [m["text"].strip() for m in t["messages"]]
        assert rendered.count("</message>") == len(t["messages"])
        assert rendered.lower().count("<message ") == len(t["messages"])


def test_no_transcript_contains_an_email_address_or_a_username_like_handle(ts):
    for t in ts.values():
        for m in t["messages"]:
            assert not re.search(r"[\w.+-]+@[\w-]+\.\w+", m["text"]), t["id"]
            assert not re.search(r"(?<!\w)@\w{3,}", m["text"]), t["id"]


def test_every_transcript_is_rendered_by_the_real_renderer_without_creating_blocks(ts):
    from moderation.prompting import render_transcript

    for t in ts.values():
        parsed = parse_rendered(render_transcript([(m["seq"], m["author"], m["text"]) for m in t["messages"]]))
        assert len(parsed) == len(t["messages"]), t["id"]


# --- stances (each paired transcript says who argues which side of the proposition) -----------------------------------

STANCE_KEYS = {"Participant A", "Participant B"}


def valid_stances(stances):
    return (
        isinstance(stances, dict)
        and set(stances) == STANCE_KEYS
        and all(v in ("pro", "con") for v in stances.values())
        and stances["Participant A"] != stances["Participant B"]
    )


def test_every_paired_transcript_has_valid_opposite_stances(ts):
    for _pid, members in nine_pairs(ts):
        for t in members:
            assert "stances" in t, f"{t['id']} has no stances"
            assert valid_stances(t["stances"]), (t["id"], t["stances"])


def test_stances_swap_across_a_pair_and_match_the_variant(ts):
    """Pro is the left-coded side for all three topics: in the left variant Participant B is pro, in the right B is con."""
    for pair_id, members in nine_pairs(ts):
        left, right = pair_of(members)
        assert left["stances"]["Participant B"] == "pro", pair_id
        assert left["stances"]["Participant A"] == "con", pair_id
        assert right["stances"]["Participant B"] == "con", pair_id
        assert right["stances"]["Participant A"] == "pro", pair_id
        assert left["stances"] != right["stances"]
        assert {left["stances"]["Participant A"], left["stances"]["Participant B"]} == {"pro", "con"}


def test_singles_have_null_or_valid_stances(ts):
    singles = [t for t in ts.values() if is_single(t)]
    assert len(singles) == 4
    for t in singles:
        assert t.get("stances") is None or valid_stances(t["stances"]), t["id"]


# --- the "obvious, non-legal factual error" pairs (brief section 8) ------------------------------------------------------

TOPICS = ("rent", "drugs", "sanctuary")
LEGAL_WORDS = re.compile(r"\b(law|laws|act|acts|statute|statutes|bill|bills|ordinance|ordinances|legislation|ruling|rulings|court|courts|amendment|amendments|section|sections)\b", re.I)


def obvious_pairs(ts):
    grouped = {pid: members for pid, members in pairs(ts).items() if pid.endswith("_factual_obvious")}
    assert set(grouped) == {f"{t}_factual_obvious" for t in TOPICS}, sorted(grouped)
    return sorted(grouped.items())


def test_the_obvious_pairs_exist_with_the_expected_ids_and_files(ts):
    for pid, members in obvious_pairs(ts):
        assert sorted(t["id"] for t in members) == [f"{pid}_left", f"{pid}_right"]


def test_in_an_obvious_pair_the_planted_phrase_is_identical_in_both_variants(ts):
    for pid, members in obvious_pairs(ts):
        left, right = pair_of(members)
        pl, pr = planted_items(left), planted_items(right)
        assert len(pl) == len(pr) == 1, pid
        assert pl[0][1]["phrase"] == pr[0][1]["phrase"], pid
        assert pl[0][1]["phrase"] in pl[0][0]["text"] and pr[0][1]["phrase"] in pr[0][0]["text"]


def test_in_an_obvious_pair_the_error_is_factual_accuracy_intensity_three_with_correction_and_evidence_url(ts):
    for pid, members in obvious_pairs(ts):
        for t in members:
            (_m, planted), = planted_items(t)
            assert planted["dimension"] == "factual_accuracy", t["id"]
            assert planted["intensity"] == 3, t["id"]
            assert isinstance(planted.get("correction"), str) and len(planted["correction"].strip()) >= 15, t["id"]
            evidence = planted.get("evidence")
            assert isinstance(evidence, str) and re.match(r"https?://\S+", evidence.strip()), (t["id"], evidence)
            assert planted["correction"].strip() != planted["phrase"].strip()


def test_in_an_obvious_pair_the_flawed_message_is_message_four_by_participant_b_in_both_variants(ts):
    for pid, members in obvious_pairs(ts):
        for t in members:
            (message, _planted), = planted_items(t)
            assert message["seq"] == 4, t["id"]
            assert message["author"] in ("B", "Participant B"), t["id"]
            assert t["trigger_seq"] == 4, t["id"]


def test_in_an_obvious_pair_the_stances_swap_and_lengths_match_within_five_percent(ts):
    for pid, members in obvious_pairs(ts):
        left, right = pair_of(members)
        assert left["stances"] == {"Participant A": "con", "Participant B": "pro"}, pid
        assert right["stances"] == {"Participant A": "pro", "Participant B": "con"}, pid
        totals = [sum(count_chars(m["text"]) for m in t["messages"]) for t in (left, right)]
        assert abs(totals[0] - totals[1]) <= 0.05 * max(totals), (pid, totals)
        for ml, mr in zip(left["messages"], right["messages"]):
            a, b = count_chars(ml["text"]), count_chars(mr["text"])
            assert within_five_percent(a, b), (pid, ml["seq"], a, b)


def test_an_obvious_planted_phrase_contains_no_legal_or_jurisdiction_keyword(ts):
    for pid, members in obvious_pairs(ts):
        for t in members:
            for _m, planted in planted_items(t):
                assert not LEGAL_WORDS.search(planted["phrase"]), (t["id"], planted["phrase"])


def test_the_hard_factual_pairs_are_kept(ts):
    for topic in TOPICS:
        assert f"{topic}_factual_left" in ts and f"{topic}_factual_right" in ts


def test_an_obvious_error_is_not_the_same_claim_as_the_hard_pair_of_its_topic(ts):
    for topic in TOPICS:
        hard = {p["phrase"] for t in ts.values() if t["pair_id"] == f"{topic}_factual" for _m, p in planted_items(t)}
        obvious = {p["phrase"] for t in ts.values() if t["pair_id"] == f"{topic}_factual_obvious" for _m, p in planted_items(t)}
        assert hard.isdisjoint(obvious), topic


# --- balance of checkable assertions per message position (architect's request after the first real run) ---------------

def _digit_tokens(text):
    return len([tok for tok in text.split() if re.search(r"\d", tok)])


def _sentences(text):
    return len([x for x in re.split(r"(?<=[.!?])\s+", text.strip()) if x])


@pytest.mark.parametrize(
    "name, measure",
    [("digit-containing tokens", _digit_tokens), ("question marks", lambda text: text.count("?")), ("sentences", _sentences)],
)
def test_matched_messages_differ_by_at_most_one_in_countable_features(ts, name, measure):
    for pair_id, members in nine_pairs(ts):
        left, right = pair_of(members)
        for ml, mr in zip(left["messages"], right["messages"]):
            a, b = measure(ml["text"]), measure(mr["text"])
            assert abs(a - b) <= 1, f"{pair_id} message {ml['seq']}: {name} {a} (left) vs {b} (right)"
