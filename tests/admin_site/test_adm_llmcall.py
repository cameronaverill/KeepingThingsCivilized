"""LLMCall privacy: the list view never contains request, raw_response or parsed text (with or without a search or a
filter, on the run page and on every other page); the detail view shows each cut to 5,000 characters."""
import pytest

import adm_kit as K

CHANGELIST = K.url("moderation", "llmcall", "changelist")
LIMIT = 5000
RAW_PAGES = ("REQHEADMARK", "REQTAILMARK", "RAWHEADMARK", "RAWTAILMARK", "PARSEDHEADMARK", "PARSEDTAILMARK")


def detail(client, call):
    return K.page(client.get(K.url("moderation", "llmcall", "change", call.pk)))


def make_call(**fields):
    from moderation.models import LLMCall

    values = dict(purpose="moderation", agent="agent-t", model="model-t", max_tokens=100, status="ok")
    values.update(fields)
    return LLMCall.objects.create(**values)


# --- the list never shows the raw fields ---------------------------------------------------------------------------------

def test_the_list_view_contains_no_request_response_or_parsed_text(root, world):
    html = K.page(root.get(CHANGELIST))
    assert [m for m in RAW_PAGES if m in html] == []


def test_the_list_view_contains_no_body_text_at_all(root, world):
    html = K.page(root.get(CHANGELIST))
    assert K.longest_run(html, "x") < 50
    assert K.longest_run(html, "q") < 50
    assert K.longest_run(html, "y") < 50


def test_the_list_has_no_request_raw_response_or_parsed_column(root, world):
    columns = K.column_classes(K.page(root.get(CHANGELIST)))
    assert columns & {"request", "raw_response", "parsed", "request_display", "raw_response_display", "parsed_display"} == set()


@pytest.mark.parametrize(
    "params",
    [{"q": "1"}, {"q": "agent-zz1"}, {"status": "ok"}, {"status__exact": "ok"}, {"o": "1"}, {"o": "-1"}, {"_popup": "1"}, {"p": "1"}],
    ids=str,
)
def test_the_list_view_never_shows_the_raw_fields_however_it_is_filtered_or_sorted(root, world, params):
    html = K.page(root.get(CHANGELIST, params))
    assert [m for m in RAW_PAGES if m in html] == []
    assert K.longest_run(html, "q") < 50


def test_a_search_for_text_inside_the_raw_fields_finds_nothing(root, world):
    """If the raw text were searchable, a hit would leak that a call contains it; the contract's search is by ids."""
    for needle in (K.MARK_REQUEST_HEAD, K.MARK_RAW_TAIL, K.MARK_PARSED_HEAD, "CALL-ERROR-MARK"):
        assert K.listed_pks(root.get(CHANGELIST, {"q": needle}), "moderation", "llmcall") == [], needle


def test_the_run_page_does_not_show_the_raw_fields_of_its_calls(root, world):
    html = K.page(root.get(K.url("moderation", "moderationrun", "change", world.run_done.pk)))
    assert [m for m in RAW_PAGES if m in html] == []
    assert K.longest_run(html, "q") < 50 and K.longest_run(html, "x") < 50 and K.longest_run(html, "y") < 50


def test_the_conversation_and_message_pages_do_not_show_the_raw_fields(root, world):
    for html in (
        K.page(root.get(K.url("forum", "conversation", "change", world.conv.pk))),
        K.page(root.get(K.url("forum", "message", "change", world.m1.pk))),
        K.page(root.get("/admin/")),
    ):
        assert [m for m in RAW_PAGES if m in html] == []


# --- the detail view truncates -------------------------------------------------------------------------------------------

def test_the_detail_view_shows_the_start_of_each_raw_field(root, world):
    html = detail(root, world.call1)
    assert K.MARK_REQUEST_HEAD in html and K.MARK_PARSED_HEAD in html
    assert K.longest_run(html, "q") > 0


def test_the_detail_view_cuts_the_raw_response_to_exactly_5000_characters(root, world):
    html = detail(root, world.call1)
    assert K.longest_run(html, "q") == LIMIT
    assert K.MARK_RAW_TAIL not in html


def test_the_detail_view_cuts_the_request_to_5000_characters(root, world):
    html = detail(root, world.call1)
    longest = K.longest_run(html, "x")
    assert 4900 <= longest <= LIMIT
    assert K.MARK_REQUEST_TAIL not in html


def test_the_detail_view_cuts_parsed_to_5000_characters(root, world):
    html = detail(root, world.call1)
    longest = K.longest_run(html, "y")
    assert 4900 <= longest <= LIMIT
    assert K.MARK_PARSED_TAIL not in html


def test_the_detail_view_shows_a_raw_response_of_exactly_5000_characters_in_full(root, world):
    call = make_call(raw_response="m" * LIMIT)
    assert K.longest_run(detail(root, call), "m") == LIMIT


@pytest.mark.parametrize("size", [LIMIT - 1, LIMIT], ids=str)
def test_the_detail_view_of_a_raw_response_that_fits_says_no_more_about_truncation_than_a_tiny_one(root, world, size):
    """Whatever wording the page uses for a cut (field captions included), a response that fits must not add any."""
    baseline = detail(root, make_call(raw_response="m")).lower().count("truncat")
    assert detail(root, make_call(raw_response="m" * size)).lower().count("truncat") == baseline


def test_the_detail_view_shows_a_raw_response_of_4999_characters_in_full(root, world):
    call = make_call(raw_response="m" * (LIMIT - 1))
    assert K.longest_run(detail(root, call), "m") == LIMIT - 1


def test_the_detail_view_cuts_a_raw_response_of_5001_characters_by_exactly_one(root, world):
    call = make_call(raw_response="m" * LIMIT + "Ω")
    html = detail(root, call)
    assert K.longest_run(html, "m") == LIMIT
    assert "Ω" not in html


def test_the_detail_view_counts_characters_not_bytes(root, world):
    call = make_call(raw_response="é" * 6000)
    assert K.longest_run(detail(root, call), "é") == LIMIT


def test_the_detail_view_of_a_short_call_shows_it_in_full(root, world):
    call = make_call(raw_response="a short answer SHORT-MARK", request={"only": "SHORT-REQUEST-MARK"}, parsed={"k": "SHORT-PARSED-MARK"})
    html = detail(root, call)
    for needle in ("SHORT-MARK", "SHORT-REQUEST-MARK", "SHORT-PARSED-MARK"):
        assert needle in html, needle


def test_the_detail_view_of_a_call_with_no_raw_text_renders(root, world):
    call = make_call(raw_response="", request={}, parsed=None)
    assert root.get(K.url("moderation", "llmcall", "change", call.pk)).status_code == 200


def test_the_detail_view_shows_the_cost_tokens_status_and_error(root, world):
    ok = detail(root, world.call1)
    err = detail(root, world.call2)
    assert K.readonly_text(ok, "cost_usd") == "0.012345"
    assert K.readonly_text(ok, "tokens_in") == "12345" and K.readonly_text(ok, "tokens_out") == "6789"
    assert K.readonly_text(err, "status") == "error"
    assert "CALL-ERROR-MARK" in err


def test_the_detail_view_never_writes_to_the_call(root, world):
    from moderation.models import LLMCall

    before = list(LLMCall.objects.order_by("pk").values())
    detail(root, world.call1)
    assert list(LLMCall.objects.order_by("pk").values()) == before


# --- the limit is the tunable ---------------------------------------------------------------------------------------------

def test_the_limit_is_the_admin_raw_display_chars_setting_and_tunable_and_it_is_5000():
    from django.conf import settings

    from config import tunables

    assert tunables.ADMIN_RAW_DISPLAY_CHARS == 5000
    assert settings.ADMIN_RAW_DISPLAY_CHARS == 5000


def test_changing_the_setting_changes_the_cut_at_call_time(root, world, settings):
    settings.ADMIN_RAW_DISPLAY_CHARS = 100
    call = make_call(raw_response="m" * 1000)
    assert K.longest_run(detail(root, call), "m") == 100


def test_a_larger_setting_shows_more(root, world, settings):
    settings.ADMIN_RAW_DISPLAY_CHARS = 7000
    call = make_call(raw_response="m" * 8000)
    assert K.longest_run(detail(root, call), "m") == 7000
