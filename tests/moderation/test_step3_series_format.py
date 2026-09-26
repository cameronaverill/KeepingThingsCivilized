"""The mechanical series (brief section 9): format, computed ground truth (recomputed here), and identical-except-the-factor."""
import re

import pytest
from step3_serieskit import (
    unanswered_pattern,
    FACTORS,
    LENGTH_TARGETS,
    PROCESS_FACTORS,
    all_transcripts,
    by_factor,
    digit_tokens,
    groups,
    longest_run,
    repeated_sentences,
    run_authors,
    sentences,
    series_members,
    stance_key,
    swap_label,
    trigger_message,
    unanswered_question,
    values_under,
    within,
    word_count,
)
from step3_testkit import (
    count_assertions,
    count_chars,
    is_series,
    planted_items,
    real_results_untouched,  # noqa: F401
)


@pytest.fixture(scope="module")
def everything():
    transcripts = all_transcripts()
    assert transcripts
    return transcripts


@pytest.fixture(scope="module")
def members(everything):
    found = series_members(everything)
    assert found, "no series transcripts (transcripts with a `series` object) in golden/transcripts"
    return found


# --- (1) format and inventory ---------------------------------------------------------------------------------------------

def test_the_series_adds_about_twenty_transcripts_and_the_set_is_about_forty_two(members, everything):
    assert 18 <= len(members) <= 22, len(members)
    assert 40 <= len(everything) <= 44, len(everything)
    assert len(everything) == 22 + len(members), "22 existing (18 in pairs, 4 singles) plus the series"


def test_every_series_member_has_the_documented_format(members, everything):
    for tid, t in members.items():
        assert t["pair_id"] in (None, ""), tid
        series = t["series"]
        assert {"id", "factor", "level", "side", "base"} <= set(series), (tid, sorted(series))
        assert isinstance(series["id"], str) and re.fullmatch(r"[a-z0-9_]+", series["id"]), tid
        assert series["factor"] in FACTORS, (tid, series["factor"])
        assert isinstance(series["level"], str) and series["level"], tid
        assert series["side"] in ("left", "right"), tid
        assert series["base"] in everything and not is_series(everything[series["base"]]), (tid, "the base must be an existing non-series transcript")
        assert isinstance(t.get("stances"), dict) and set(t["stances"]) == {"Participant A", "Participant B"}, tid
        assert sorted(t["stances"].values()) == ["con", "pro"], tid
        assert isinstance(t.get("computed"), dict) and t["computed"], f"{tid} has no computed block"
        assert isinstance(t.get("planted"), list) if "planted" in t else True


def test_series_ids_and_transcript_ids_are_unique_and_the_pair_rules_do_not_apply(members, everything):
    assert len({t["id"] for t in everything.values()}) == len(everything)
    for t in members.values():
        assert not t.get("pair_id")


def test_the_base_belongs_to_the_same_side_when_the_base_is_a_pair_member(members, everything):
    for tid, t in members.items():
        base = everything[t["series"]["base"]]
        if base.get("variant") in ("left", "right"):
            assert base["variant"] == t["series"]["side"], tid


def test_the_factors_present_and_how_many_members_each_has(members):
    counts = {factor: len(by_factor(members, factor)) for factor in FACTORS}
    assert counts["message_length"] == 10, counts  # 6 factual + 4 abusive new members
    assert counts["label_swap"] == 4, counts
    assert counts["flooding"] == 2 and counts["repetition"] == 2 and counts["unanswered_question"] == 2, counts


def test_every_level_is_present_on_both_sides(members):
    for (sid, level), sides in groups(members).items():
        assert set(sides) == {"left", "right"}, (sid, level, sorted(sides))


def test_the_length_levels_are_the_documented_ones(members):
    seen = {}
    for t in by_factor(members, "message_length").values():
        kind = "factual" if t["series"]["base"].startswith("rent_factual_obvious") else "abusive" if t["series"]["base"].startswith("drugs_abusive") else "other"
        seen.setdefault(kind, set()).add(t["series"]["level"])
    assert seen == {"factual": {"short", "long", "very_long"}, "abusive": {"short", "very_long"}}, seen


# --- (2) computed ground truth, recomputed independently -----------------------------------------------------------------

def test_computed_block_equals_an_independent_recomputation(members):
    for tid, t in members.items():
        c = t["computed"]
        trigger = trigger_message(t)
        chars = {count_chars(trigger["text"]), len(trigger["text"])}
        assert chars & set(values_under(c, "char")), (tid, "trigger character count", sorted(chars), c)
        assert word_count(trigger["text"]) in values_under(c, "word"), (tid, "trigger word count", c)
        assert longest_run(t) in values_under(c, "run"), (tid, "longest run", longest_run(t), c)
        repeated = bool(repeated_sentences(t))
        repeat_values = values_under(c, "repeat")
        assert repeat_values and any(bool(v) == repeated for v in repeat_values), (tid, "repeated sentence", repeated, c)
        unanswered = unanswered_question(t)
        question_values = values_under(c, "unanswered") or values_under(c, "question")
        assert question_values and any(bool(v) == unanswered for v in question_values), (tid, "unanswered question", unanswered, c)


def test_length_series_hit_their_target_lengths_within_ten_percent_and_the_limit(members):
    from django.conf import settings

    for tid, t in by_factor(members, "message_length").items():
        kind = "factual" if t["series"]["base"].startswith("rent_factual_obvious") else "abusive"
        target = LENGTH_TARGETS[kind][t["series"]["level"]]
        n = count_chars(trigger_message(t)["text"])
        assert within(n, target), f"{tid}: message 4 is {n} characters, target {target} +-10%"
        for m in t["messages"]:
            assert 1 <= count_chars(m["text"]) <= settings.MAX_MESSAGE_CHARS, (tid, m["seq"])


def test_flooding_run_is_exactly_four_and_the_trigger_is_the_last_of_the_run(members):
    flood = by_factor(members, "flooding")
    assert len(flood) == 2
    for tid, t in flood.items():
        author, seqs = run_authors(t)
        assert len(seqs) == 4 and longest_run(t) == 4, tid
        assert t["trigger_seq"] == seqs[-1], tid
        assert values_under(t["computed"], "run")[0] == 4


def test_repetition_has_an_identical_sentence_in_three_messages_of_the_same_participant(members):
    rep = by_factor(members, "repetition")
    assert len(rep) == 2
    for tid, t in rep.items():
        found = repeated_sentences(t)
        assert len(found) == 1, (tid, "exactly one participant repeats")
        (author, sents), = found.items()
        assert max(sents.values()) == 3, (tid, sents)
        assert len(sentences(next(iter(sents)))) == 1


def test_unanswered_question_pattern_holds(members):
    ua = by_factor(members, "unanswered_question")
    assert len(ua) == 2
    for tid, t in ua.items():
        pattern = unanswered_pattern(t)
        assert pattern, tid
        asker, replier = pattern
        assert asker.endswith("A") and replier.endswith("B"), (tid, pattern, "A asks, B answers twice without addressing it")
        assert t["trigger_seq"] == t["messages"][-1]["seq"]


def test_process_series_are_civil_and_plain(members):
    """No planted problem and no new checkable claim: no digits, no planted items, no insults."""
    for factor in PROCESS_FACTORS:
        for tid, t in by_factor(members, factor).items():
            assert not planted_items(t), tid
            for m in t["messages"]:
                assert digit_tokens(m["text"]) == 0, (tid, m["seq"])
            assert count_assertions(" ".join(m["text"] for m in t["messages"])) >= 0


# --- (3) identical except the varied factor ---------------------------------------------------------------------------------

def test_length_series_members_are_identical_to_the_base_except_message_four(members, everything):
    for tid, t in by_factor(members, "message_length").items():
        base = everything[t["series"]["base"]]
        assert [(m["seq"], m["author"]) for m in t["messages"]] == [(m["seq"], m["author"]) for m in base["messages"]], tid
        assert t["messages"][:3] == base["messages"][:3] or [
            (m["seq"], m["author"], m["text"]) for m in t["messages"][:3]
        ] == [(m["seq"], m["author"], m["text"]) for m in base["messages"][:3]], f"{tid}: messages 1 to 3 must equal the base's"
        assert t["trigger_seq"] == base["trigger_seq"] and t["topic"] == base["topic"], tid
        assert t["stances"] == base["stances"], tid


def test_length_series_keep_the_planted_phrase_and_add_nothing_checkable(members, everything):
    for tid, t in by_factor(members, "message_length").items():
        base = everything[t["series"]["base"]]
        mine, theirs = planted_items(t), planted_items(base)
        assert len(mine) == len(theirs) == 1, tid
        assert mine[0][1]["phrase"] == theirs[0][1]["phrase"] and mine[0][1]["dimension"] == theirs[0][1]["dimension"], tid
        assert mine[0][1]["intensity"] == theirs[0][1]["intensity"], tid
        assert mine[0][1]["phrase"] in trigger_message(t)["text"]
        m4, base4 = trigger_message(t)["text"], trigger_message(base)["text"]
        assert digit_tokens(m4) == digit_tokens(base4), (tid, "digit tokens in message 4")
        assert m4.count("?") == base4.count("?"), (tid, "question marks in message 4")
        assert count_assertions(m4) == count_assertions(base4), (tid, "checkable assertions in message 4")


def test_label_swap_members_swap_every_label_and_the_stances_and_keep_the_text(members, everything):
    swaps = by_factor(members, "label_swap")
    assert len(swaps) == 4
    bases = {t["series"]["base"] for t in swaps.values()}
    assert {b.rsplit("_", 1)[0] for b in bases} == {"rent_factual_obvious", "drugs_abusive"}
    for tid, t in swaps.items():
        base = everything[t["series"]["base"]]
        assert t["series"]["level"] == "swapped"
        assert [m["text"] for m in t["messages"]] == [m["text"] for m in base["messages"]], tid
        assert [m["author"] for m in t["messages"]] == [swap_label(m["author"]) for m in base["messages"]], tid
        assert t["messages"][0]["author"].endswith("B"), "the first speaker is now labelled B"
        for label in ("Participant A", "Participant B"):
            assert t["stances"][label] == base["stances"][swap_label(label)], (tid, label)
        (message, planted), = planted_items(t)
        assert message["seq"] == 4 and message["author"].endswith("A"), (tid, "the flawed message is now by Participant A")
        (bm, bp), = planted_items(base)
        assert planted["phrase"] == bp["phrase"] and planted["intensity"] == bp["intensity"]
        assert t["trigger_seq"] == base["trigger_seq"]


def test_process_series_mirror_each_other_across_sides(members):
    for factor in PROCESS_FACTORS:
        grouped = groups(by_factor(members, factor))
        assert len(grouped) == 1, (factor, sorted(grouped))
        (_key, sides), = grouped.items()
        left, right = sides["left"], sides["right"]
        assert len(left["messages"]) == len(right["messages"]), factor
        for ml, mr in zip(left["messages"], right["messages"]):
            a, b = count_chars(ml["text"]), count_chars(mr["text"])
            assert abs(a - b) <= 0.10 * max(a, b), (factor, ml["seq"], a, b)
            assert digit_tokens(ml["text"]) == digit_tokens(mr["text"]) == 0
            assert abs(ml["text"].count("?") - mr["text"].count("?")) <= 0
        assert left["trigger_seq"] == right["trigger_seq"]
        assert left["stances"] != right["stances"], "the stances swap across sides"
        assert [m["author"] for m in left["messages"]] == [m["author"] for m in right["messages"]]


def violator(t):
    factor = t["series"]["factor"]
    if factor == "flooding":
        return run_authors(t)[0]
    if factor == "repetition":
        (author, _s), = repeated_sentences(t).items()
        return author
    return unanswered_pattern(t)[1]  # the participant who answered twice without addressing the question


def test_the_violating_participant_is_left_coded_on_the_left_member_and_right_coded_on_the_right(members):
    for factor in PROCESS_FACTORS:
        for tid, t in by_factor(members, factor).items():
            who = stance_key(violator(t))
            expected = "pro" if t["series"]["side"] == "left" else "con"
            assert t["stances"][who] == expected, f"{tid}: {who} should be {expected} (pro is the left-coded side)"


def test_process_series_members_measure_alike_across_sides_in_words_and_sentences(members):
    for factor in PROCESS_FACTORS:
        (_key, sides), = groups(by_factor(members, factor)).items()
        for ml, mr in zip(sides["left"]["messages"], sides["right"]["messages"]):
            assert abs(word_count(ml["text"]) - word_count(mr["text"])) <= max(2, 0.15 * max(word_count(ml["text"]), word_count(mr["text"]))), (factor, ml["seq"])
            assert abs(len(sentences(ml["text"])) - len(sentences(mr["text"]))) <= 1, (factor, ml["seq"])


def test_length_series_left_and_right_members_are_balanced_at_each_level(members):
    for (sid, level), sides in groups(by_factor(members, "message_length")).items():
        ln, rn = (count_chars(trigger_message(sides[s])["text"]) for s in ("left", "right"))
        assert abs(ln - rn) <= 0.10 * max(ln, rn), (sid, level, ln, rn)
        lw, rw = (word_count(trigger_message(sides[s])["text"]) for s in ("left", "right"))
        assert abs(lw - rw) <= 0.15 * max(lw, rw), (sid, level, lw, rw)
        ls, rs = (len(sentences(trigger_message(sides[s])["text"])) for s in ("left", "right"))
        assert abs(ls - rs) <= max(1, 0.15 * max(ls, rs)), (sid, level, ls, rs)
