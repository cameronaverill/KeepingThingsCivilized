"""docs/warmup_notes.md: one paragraph per pair, the variant definitions, the known weaknesses (docs/warmup_brief.md)."""
import warmup_kit as kit

FILES = kit.load_folder()


def notes():
    return kit.NOTES_PATH.read_text(encoding="utf-8")


def test_the_notes_document_exists_and_is_substantial():
    assert (kit.NOTES_PATH.is_file(), len(notes()) >= 3000) == (True, True)


def test_the_notes_check_is_clean():
    assert kit.check_notes(notes(), FILES) == []


def test_every_pair_id_is_named_in_the_notes():
    assert [pair_id for pair_id in kit.pair_groups(FILES) if pair_id not in notes()] == []


def test_every_series_factor_and_series_id_is_named_in_the_notes():
    ids = {d["series"]["id"] for _p, d in kit.series_members(FILES)}
    assert [series_id for series_id in sorted(ids) if series_id not in notes()] == []


def test_the_notes_say_that_the_variants_are_not_political_codings_and_that_results_do_not_show_political_neutrality():
    import re

    text = notes()
    assert (
        bool(re.search(r"\bNOT\b[^.\n]{0,80}\bpolitical\b|\bnot\b[^.\n]{0,80}\bpolitical coding", text)),
        bool(re.search(r"neutrality", text, re.I)),
    ) == (True, True)


def test_the_notes_contain_no_key_email_or_url_to_a_private_place():
    import re

    text = notes()
    assert (re.search(r"[\w.+-]+@[\w-]+\.\w+", text), re.search(r"sk-[A-Za-z0-9_-]{10,}", text)) == (None, None)


def test_the_notes_name_the_three_topics_and_where_the_propositions_come_from():
    text = notes()
    assert [t for t in kit.TOPIC_TITLES if t.lower() not in text.lower()] == [] and "seed_topics.json" in text


def test_every_pair_paragraph_states_what_matched_means_with_explicit_lengths_and_counts():
    import re

    blocks = kit.paragraphs(notes())
    missing = [
        pair_id for pair_id in kit.pair_groups(FILES)
        if not any(
            pair_id in b and re.search(r"\bmatch\w*\b", b, re.I) and re.search(r"\d+\s*(?:and|vs\.?|/)\s*\d+|\d+ characters", b)
            and re.search(r"question|assertion|digit|named fact|count", b, re.I)
            for b in blocks
        )
    ]
    assert missing == []


def test_the_notes_say_the_evidence_sources_were_not_fetched():
    import re

    assert re.search(r"(not|never|no)\b[^.\n]{0,60}\bfetch\w*|\bunfetched\b|\bnot been fetched\b|did not fetch", notes(), re.I)
