"""The run page: issues, acts and calls of that run (and only that run) appear on it, read-only, with links to their own
pages where the contract's admin gives them."""
import re

import adm_kit as K


def run_page(client, run):
    response = client.get(K.url("moderation", "moderationrun", "change", run.pk))
    assert response.status_code == 200
    return K.page(response)


def test_the_run_page_lists_the_issues_of_that_run(root, world):
    html = run_page(root, world.run_done)
    for needle in ("I-QZ71", "I-QZ72", "possible_factual_error", "unclear_statement"):
        assert needle in html, needle


def test_the_run_page_does_not_list_the_issues_of_another_run(root, world):
    html = run_page(root, world.run_done)
    assert "I-OTHER9" not in html


def test_the_run_page_lists_the_acts_of_that_run(root, world):
    html = run_page(root, world.run_done)
    assert "request_information" in html and "neutral" in html
    assert "clarify_argument" not in html


def test_the_other_runs_page_shows_its_own_issue_and_act_only(root, world):
    html = run_page(root, world.run_other)
    assert "I-OTHER9" in html and "clarify_argument" in html
    assert "I-QZ71" not in html and "request_information" not in html


def test_the_run_page_lists_the_calls_of_that_run(root, world):
    html = run_page(root, world.run_done)
    for needle in ("agent-zz1", "agent-zz2", "model-zz1", "model-zz2"):
        assert needle in html, needle


def test_the_run_page_does_not_list_the_calls_of_another_run_or_of_no_run(root, world):
    html = run_page(root, world.run_done)
    for needle in ("agent-other", "agent-norun", "model-other"):
        assert needle not in html, needle


def test_the_other_runs_page_shows_its_own_call_only(root, world):
    html = run_page(root, world.run_other)
    assert "agent-other" in html
    assert "agent-zz1" not in html and "agent-norun" not in html


def call_table_rows(html):
    """The cells of each body row of the run page's LLM calls table, as lists of collapsed text."""
    table = K.field_html(html, "llm_call_rows")
    body = table[table.index("<tbody>"):]
    return [
        [" ".join(re.sub(r"<[^>]+>", " ", cell).split()) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S)
    ]


def test_the_run_page_call_table_shows_id_agent_attempt_model_status_tokens_and_cost(root, world):
    rows = call_table_rows(run_page(root, world.run_done))
    assert len(rows) == 2
    first, second = rows
    assert {str(world.call1.pk), "agent-zz1", "3", "model-zz1", "ok", "0.012345"} <= set(first)
    assert [c for c in first if "12345" in c and "6789" in c and c != "0.012345"] != []
    assert {str(world.call2.pk), "agent-zz2", "4", "model-zz2", "error"} <= set(second)


def test_the_run_page_call_table_lists_calls_in_call_order(root, world):
    rows = call_table_rows(run_page(root, world.run_done))
    assert [r[0] for r in rows] == [str(world.call1.pk), str(world.call2.pk)]


def test_the_run_page_links_each_call_to_its_own_page(root, world):
    html = run_page(root, world.run_done)
    for call in (world.call1, world.call2):
        assert K.url("moderation", "llmcall", "change", call.pk) in html


def test_a_run_with_no_calls_no_issues_and_no_acts_renders(root, world):
    from forum.models import Message
    from moderation.models import ModerationRun

    extra = Message.objects.create(
        conversation=world.conv, author_type="user", participant=world.pa, content="A fresh message with nothing on it."
    )
    run = ModerationRun.objects.create(conversation=world.conv, trigger_message=extra, snapshot_seq=extra.seq_no)
    html = run_page(root, run)
    assert "agent-zz1" not in html and "I-QZ71" not in html and "request_information" not in html


def test_the_run_page_inlines_offer_no_add_or_remove_controls(root, world):
    html = run_page(root, world.run_done)
    assert 'class="add-row"' not in html
    assert 'name="issues-MAX_NUM_FORMS" value="0"' in html
    assert 'name="acts-MAX_NUM_FORMS" value="0"' in html
    assert 'name="_save"' not in html
    assert not re.search(r'name="(issues|acts)-\d+-DELETE"', html)


def test_the_run_page_shows_the_replay_link_and_the_posted_message(root, world):
    replay = run_page(root, world.run_replay)
    done = run_page(root, world.run_done)
    assert f"ModerationRun {world.run_done.pk} live" in replay
    assert f"Message {world.mod.seq_no} of conversation 7001" in K.field_html(done, "posted_message")


def test_the_run_page_shows_the_run_status_decision_and_failure_reason(root, world):
    failed = run_page(root, world.run_failed)
    assert K.readonly_text(failed, "status") == "failed"
    assert K.readonly_text(failed, "failure_reason") == "invalid_output"
    assert K.readonly_text(failed, "attempts") == "3"
    done = run_page(root, world.run_done)
    assert K.readonly_text(done, "decision") == "intervene"
