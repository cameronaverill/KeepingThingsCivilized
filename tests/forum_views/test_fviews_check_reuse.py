"""Step 19: a message posted unchanged after a concern gets the moderator reply from the previewed outputs with NO second
model call; an edited text, or a changed conversation, does a fresh call. Through the real pipeline in sync mode with a
scripted fake LLM."""
import json

import pytest

import fviews_kit as K
import fviews_llm as L

pytestmark = pytest.mark.django_db(transaction=True)


def setup_duo():
    duo = K.Duo("Cities should cap how much landlords can raise rents each year.")
    duo.seed(duo.pa, "I think rent control reduces the supply of housing, which is a well known fact.", 50)
    duo.seed(duo.pb, "The supply of homes barely changes when rents are capped, and tenants gain a lot from that.", 40)
    return duo


def do_check(duo, text=L.DRAFT):
    response = duo.ca.post(f"/c/{duo.conv.pk}/check/", {"text": text})
    return json.loads(response.content)


def post(duo, field_names, text=L.DRAFT, check_id=None):
    data = {field_names["message"]: text}
    if check_id is not None:
        data["check_id"] = check_id
    return duo.ca.post(duo.post_url, data)


def moderator_texts(duo):
    return list(duo.conv.messages.filter(author_type="moderator").order_by("seq_no").values_list("content", flat=True))


def run_of(duo, text):
    from moderation.models import ModerationRun

    message = duo.conv.messages.filter(author_type="user", content=text).order_by("-seq_no").first()
    return ModerationRun.objects.get(trigger_message=message, kind="live")


def check_row(cid):
    from moderation.models import PreviewCheck

    return PreviewCheck.objects.get(pk=cid)


def test_posted_unchanged_after_a_concern_reuses_the_outputs_with_no_second_model_call(fake, field_names, settings):
    settings.MODERATION_RUN_MODE = "sync"
    duo = setup_duo()
    client = fake(*L.concern())  # exactly two answers: any third model call raises
    data = do_check(duo)
    assert data["status"] == "concern" and len(client.calls) == 2
    response = post(duo, field_names, check_id=data["check_id"])
    assert response.status_code == 302
    assert len(client.calls) == 2, "no second model call for the message the author was shown a preview of"
    assert L.NOTE in " ".join(moderator_texts(duo)), "the reply is the one previewed"
    run = run_of(duo, L.DRAFT)
    assert run.status == "done" and run.config_snapshot.get("preview_check_id") == data["check_id"]
    row = check_row(data["check_id"])
    assert row.reused_by_run_id == run.pk and row.action == "posted_as_written"


def test_the_previewed_note_is_exactly_what_the_live_run_posts(fake, field_names, settings):
    settings.MODERATION_RUN_MODE = "sync"
    duo = setup_duo()
    fake(*L.concern(texts=(L.NOTE, L.NOTE_2)))
    data = do_check(duo)
    post(duo, field_names, check_id=data["check_id"])
    posted = " ".join(moderator_texts(duo))
    assert all(note in posted for note in data["notes"])


def test_posted_unchanged_after_no_concern_also_reuses(fake, field_names, settings):
    settings.MODERATION_RUN_MODE = "sync"
    duo = setup_duo()
    client = fake(*L.no_concern())
    data = do_check(duo)
    assert data["status"] == "no_concern" and len(client.calls) == 1
    assert post(duo, field_names, check_id=data["check_id"]).status_code == 302
    assert len(client.calls) == 1 and moderator_texts(duo) == []
    assert check_row(data["check_id"]).reused_by_run_id == run_of(duo, L.DRAFT).pk


def test_an_edited_text_does_a_fresh_model_call_and_the_old_check_is_not_used(fake, field_names, settings):
    settings.MODERATION_RUN_MODE = "sync"
    duo = setup_duo()
    edited = L.DRAFT + " Also, here is my source."
    client = fake(*L.concern(), *L.live_concern(texts=(L.NOTE_2,)))
    data = do_check(duo)
    assert len(client.calls) == 2
    post(duo, field_names, text=edited, check_id=data["check_id"])
    assert len(client.calls) == 4, "the edited text is moderated afresh"
    assert L.NOTE_2 in " ".join(moderator_texts(duo)) and L.NOTE not in " ".join(moderator_texts(duo))
    row = check_row(data["check_id"])
    assert row.reused_by_run_id is None and row.action == ""
    assert "preview_check_id" not in run_of(duo, edited).config_snapshot


def test_a_check_for_the_edited_text_then_posting_that_text_reuses_the_second_check(fake, field_names, settings):
    settings.MODERATION_RUN_MODE = "sync"
    duo = setup_duo()
    edited = L.DRAFT + " Also, here is my source."
    client = fake(*L.concern(), *L.concern(texts=(L.NOTE_2,)))
    first = do_check(duo)
    duo.ca.post(f"/c/{duo.conv.pk}/check/{first['check_id']}/edit/")
    second = do_check(duo, edited)
    assert second["status"] == "concern" and second["check_id"] != first["check_id"] and len(client.calls) == 4
    post(duo, field_names, text=edited, check_id=second["check_id"])
    assert len(client.calls) == 4
    assert check_row(first["check_id"]).action == "edited" and check_row(second["check_id"]).action == "posted_as_written"
    assert L.NOTE_2 in " ".join(moderator_texts(duo))


def test_a_message_from_the_other_person_in_between_means_a_fresh_call(fake, field_names, settings):
    settings.MODERATION_RUN_MODE = "sync"
    duo = setup_duo()
    client = fake(*L.concern(), *L.live_concern(texts=(L.NOTE_2,)))
    data = do_check(duo)
    duo.seed(duo.pb, "While you were writing, I posted this.", 1)
    post(duo, field_names, check_id=data["check_id"])
    assert len(client.calls) == 4
    assert check_row(data["check_id"]).reused_by_run_id is None


def test_reuse_does_not_depend_on_the_check_id_being_sent(fake, field_names, settings):
    """The pipeline finds the check by conversation, author, text and snapshot (docs/step19_backend_brief.md); the id on the
    post only records what the author did. So a post whose script lost the id is still not moderated twice."""
    settings.MODERATION_RUN_MODE = "sync"
    duo = setup_duo()
    client = fake(*L.concern())
    data = do_check(duo)
    post(duo, field_names)
    assert len(client.calls) == 2 and L.NOTE in " ".join(moderator_texts(duo))
    row = check_row(data["check_id"])
    assert row.reused_by_run_id == run_of(duo, L.DRAFT).pk and row.action == "", "reused, but not recorded as resolved"


def test_someone_elses_check_is_never_reused_by_the_other_participant(fake, field_names, settings, clock):
    settings.MODERATION_RUN_MODE = "sync"
    duo = setup_duo()
    client = fake(*L.concern(), *L.live_concern(texts=(L.NOTE_2,)))
    data = do_check(duo)  # A's check
    response = duo.cb.post(duo.post_url, {field_names["message"]: L.DRAFT, "check_id": data["check_id"]})
    assert response.status_code == 302
    assert len(client.calls) == 4, "B's identical text is moderated on its own"
    assert check_row(data["check_id"]).reused_by_run_id is None and check_row(data["check_id"]).action == ""


def test_an_unavailable_check_means_the_post_is_moderated_as_today(fake, field_names, settings):
    settings.MODERATION_RUN_MODE = "sync"
    duo = setup_duo()
    settings.LLM_ENABLED = False
    client = fake()
    data = do_check(duo)
    assert data["status"] == "unavailable"
    settings.LLM_ENABLED = True
    client = fake(*L.live_concern())
    post(duo, field_names, check_id=data.get("check_id"))
    assert len(client.calls) == 2 and L.NOTE in " ".join(moderator_texts(duo))


def test_the_moderator_posts_appear_for_both_people_and_show_no_check_information(fake, field_names, settings):
    settings.MODERATION_RUN_MODE = "sync"
    duo = setup_duo()
    fake(*L.concern())
    data = do_check(duo)
    post(duo, field_names, check_id=data["check_id"])
    for client in (duo.ca, duo.cb):
        page = client.get(duo.url).content.decode()
        _, poll = K.poll_json(client, duo.conv)
        blob = page + json.dumps(poll)
        assert L.NOTE in blob
        for leak in ("preview_check", "check_id", "posted_as_written", "draft_sha"):
            assert leak not in blob
