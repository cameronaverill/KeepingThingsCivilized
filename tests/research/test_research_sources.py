"""Item 4 (docs/step20b_brief.md): the source list is computed in code from `WebSearchResult.tool_blocks`, never
asked of the model. Pure-function tests of `moderation.research.extract_sources(tool_blocks, *, cap)`: dedupe by
domain, cap after dedup (not before), empty input gives no error, a malformed result is skipped, not fatal.
"""
import research_kit as kit


def test_multiple_results_of_the_same_domain_dedupe_to_one():
    block = kit.tool_result_block(
        [("First result", "https://news.example.com/a"), ("Second result", "https://news.example.com/b")]
    )
    sources = kit.extract_sources([block], cap=10)
    assert sources == [{"title": "First result", "url": "https://news.example.com/a"}]


def test_different_domains_are_all_kept():
    block = kit.tool_result_block(
        [("A", "https://a.example.com/1"), ("B", "https://b.example.com/1"), ("C", "https://c.example.com/1")]
    )
    sources = kit.extract_sources([block], cap=10)
    assert [s["title"] for s in sources] == ["A", "B", "C"]


def test_dedup_happens_across_separate_tool_blocks_not_just_within_one():
    first = kit.tool_result_block([("A1", "https://a.example.com/1")])
    second = kit.tool_result_block([("A2", "https://a.example.com/2"), ("B1", "https://b.example.com/1")])
    sources = kit.extract_sources([first, second], cap=10)
    assert sources == [
        {"title": "A1", "url": "https://a.example.com/1"},
        {"title": "B1", "url": "https://b.example.com/1"},
    ]


def test_first_occurrence_of_a_domain_wins_not_the_last():
    block = kit.tool_result_block(
        [("Original title", "https://x.example.com/1"), ("A later, different title", "https://x.example.com/2")]
    )
    sources = kit.extract_sources([block], cap=10)
    assert sources == [{"title": "Original title", "url": "https://x.example.com/1"}]


def test_the_cap_is_applied_after_dedup_not_before():
    """Five raw results but only two distinct domains, cap=2: truncating the raw list to 2 BEFORE dedup would give
    just one source (two duplicates of the first domain); truncating after dedup gives both real sources."""
    block = kit.tool_result_block(
        [
            ("A1", "https://a.example.com/1"),
            ("A2", "https://a.example.com/2"),
            ("A3", "https://a.example.com/3"),
            ("B1", "https://b.example.com/1"),
            ("C1", "https://c.example.com/1"),
        ]
    )
    sources = kit.extract_sources([block], cap=2)
    assert sources == [
        {"title": "A1", "url": "https://a.example.com/1"},
        {"title": "B1", "url": "https://b.example.com/1"},
    ]


def test_more_distinct_domains_than_the_cap_are_truncated():
    block = kit.tool_result_block([(chr(65 + i), f"https://site{i}.example.com/1") for i in range(6)])
    sources = kit.extract_sources([block], cap=3)
    assert len(sources) == 3
    assert [s["title"] for s in sources] == ["A", "B", "C"]


def test_zero_tool_blocks_gives_zero_sources_without_erroring():
    assert kit.extract_sources([], cap=3) == []


def test_none_tool_blocks_gives_zero_sources_without_erroring():
    assert kit.extract_sources(None, cap=3) == []


def test_a_tool_block_with_an_empty_content_list_gives_zero_sources():
    block = kit.tool_result_block([])
    assert kit.extract_sources([block], cap=3) == []


def test_a_result_missing_title_is_skipped_not_fatal():
    good = kit.tool_result_block([("Has both", "https://good.example.com/1")])
    bad = kit.tool_result_block([])
    bad.content = [kit.malformed_result(url="https://missing-title.example.com/1")]
    sources = kit.extract_sources([bad, good], cap=10)
    assert sources == [{"title": "Has both", "url": "https://good.example.com/1"}]


def test_a_result_missing_url_is_skipped_not_fatal():
    good = kit.tool_result_block([("Has both", "https://good.example.com/1")])
    bad = kit.tool_result_block([])
    bad.content = [kit.malformed_result(title="Missing URL")]
    sources = kit.extract_sources([bad, good], cap=10)
    assert sources == [{"title": "Has both", "url": "https://good.example.com/1"}]


def test_a_result_with_blank_title_or_url_is_skipped_not_fatal():
    block = kit.tool_result_block([])
    block.content = [
        kit.malformed_result(title="", url="https://blank-title.example.com/1"),
        kit.malformed_result(title="Blank URL", url=""),
        kit.malformed_result(title="Fine", url="https://fine.example.com/1"),
    ]
    sources = kit.extract_sources([block], cap=10)
    assert sources == [{"title": "Fine", "url": "https://fine.example.com/1"}]


def test_a_malformed_result_next_to_good_ones_does_not_stop_the_good_ones_from_being_collected():
    block = kit.tool_result_block([])
    block.content = [
        kit.malformed_result(url="https://only-url.example.com/1"),
        kit.malformed_result(title="Good one", url="https://good-one.example.com/1"),
        kit.malformed_result(title="only-title-no-url"),
        kit.malformed_result(title="Another good one", url="https://another.example.com/1"),
    ]
    sources = kit.extract_sources([block], cap=10)
    assert sources == [
        {"title": "Good one", "url": "https://good-one.example.com/1"},
        {"title": "Another good one", "url": "https://another.example.com/1"},
    ]


def test_a_non_web_search_result_block_type_is_ignored_if_present_in_the_list():
    """`WebSearchResult.tool_blocks` (moderation/llm.py) is already filtered to `web_search_tool_result` blocks
    before extract_sources ever sees it, but a block with no usable `.content` must not blow up either way."""
    from types import SimpleNamespace

    weird = SimpleNamespace(type="text")  # no .content at all
    good = kit.tool_result_block([("Fine", "https://fine.example.com/1")])
    sources = kit.extract_sources([weird, good], cap=10)
    assert sources == [{"title": "Fine", "url": "https://fine.example.com/1"}]


def test_returned_sources_are_plain_dicts_with_exactly_title_and_url_keys():
    block = kit.tool_result_block([("T", "https://example.com/1")])
    (source,) = kit.extract_sources([block], cap=10)
    assert type(source) is dict
    assert set(source) == {"title", "url"}


def test_the_cap_of_one_returns_exactly_one_source():
    block = kit.tool_result_block([("A", "https://a.example.com/1"), ("B", "https://b.example.com/1")])
    assert len(kit.extract_sources([block], cap=1)) == 1


def test_the_cap_of_zero_returns_no_sources():
    block = kit.tool_result_block([("A", "https://a.example.com/1")])
    assert kit.extract_sources([block], cap=0) == []
