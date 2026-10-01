"""The pipeline on the golden transcripts (docs/plan.md section 14 step 5, "done when"): the conversation is built from
golden/transcripts/*.json, the model's answers are scripted, and the expected rows appear."""
import json
from pathlib import Path

import pipeline_run_kit as kit
import pytest

pytestmark = pytest.mark.django_db

GOLDEN = Path(__file__).resolve().parents[2] / "golden" / "transcripts"
FILES = sorted(GOLDEN.glob("*.json"))
ISSUE_TYPE_FOR = {"factual_accuracy": "possible_factual_error", "abusiveness": "abusive_language"}
DIMENSION_FOR = {"possible_factual_error": "factual_accuracy", "abusive_language": "abusiveness"}


def load(name):
    return json.loads((GOLDEN / f"{name}.json").read_text(encoding="utf-8"))


def build_from(transcript):
    """A synthetic conversation from a golden transcript; returns (world, trigger message, {seq: message})."""
    specs = [(m["author"].split()[-1], m["text"]) for m in transcript["messages"]]
    world = kit.build(specs, proposition=transcript["topic"]["proposition"])
    trigger = world[transcript["trigger_seq"]]
    return world, trigger


def planted_in_trigger(transcript):
    trigger_seq = transcript["trigger_seq"]
    return [p for m in transcript["messages"] if m["seq"] == trigger_seq for p in m.get("planted", [])]


def test_the_golden_folder_is_there():
    assert [p.stem for p in FILES] == ["sanctuary_factual_left", "sanctuary_factual_right", "single_injection"]


class TestOneTranscriptInDetail:
    def test_sanctuary_factual_left_produces_the_expected_rows(self, fake):
        transcript = load("sanctuary_factual_left")
        world, trigger = build_from(transcript)
        phrase = "San Francisco passed its sanctuary ordinance in 1979"
        text = trigger.content
        act_text = "Possible factual error in message 4: the year given for San Francisco's sanctuary ordinance could be checked against the city's records."
        master = kit.master_d(
            kit.issue_d("i1", trigger, "possible_factual_error", phrase, confidence=0.9, intensity=3,
                        explanation="The year is off by a decade."),
            agreements=["Both support building more housing."],
            disagreements=[{"summary": "Whether a cap helps tenants soon enough.", "kind": "normative"}],
        )  # fmt: skip
        interv = kit.interv_d(
            rationale="A plainly wrong date is worth a neutral note.",
            dispositions=[kit.disp_d("i1", "acted", "Checkable and material.")],
            acts=[kit.act_d(act_text, "correct_factual_error", "B", "B", issues=["i1"], messages=[trigger], tone="neutral")],
        )
        client = fake(master, interv)
        run = kit.new_run(trigger)
        _, stored = kit.go(run)

        assert (stored.status, stored.decision, stored.failure_reason, stored.error) == ("done", "intervene", "", "")
        (issue,) = kit.issues_of(stored)
        assert (issue.message_id, issue.validity, issue.quote_match) == (trigger.pk, "valid", "exact")
        assert (issue.quote_start, issue.quote_end) == (text.index(phrase), text.index(phrase) + len(phrase))
        assert (issue.dimension, issue.intensity, issue.confidence) == ("factual_accuracy", 3, 0.9)
        assert (issue.disposition.disposition, issue.disposition.reason) == ("acted", "Checkable and material.")
        (act,) = kit.acts_of(stored)
        assert (act.act_type, act.addressee, act.subject, act.validity, act.text) == ("correct_factual_error", "B", "B", "valid", act_text)
        assert [m.pk for m in act.source_messages.all()] == [trigger.pk]
        assert stored.posted_message.content == act_text
        assert stored.posted_message.in_reply_to_id == trigger.pk
        assert stored.posted_message.seq_no == 5
        assert stored.discussion_map["agreements"] == ["Both support building more housing."]
        assert len(client.calls) == 2
        labels = [label for _, label in kit.rendered_messages(client.calls[0])]
        assert labels == ["Participant A", "Participant B", "Participant A", "Participant B"]

    def test_the_injection_transcript_reaches_the_model_as_data_and_the_run_is_uneventful(self, fake):
        transcript = load("single_injection")
        world, trigger = build_from(transcript)
        client = fake(kit.master_d())
        _, stored = kit.go(kit.new_run(trigger))
        assert (stored.status, stored.decision, stored.rationale) == ("done", "no_intervention", "no valid issues")
        assert len(client.calls) == 1
        assert stored.posted_message is None


@pytest.mark.parametrize("path", FILES, ids=[p.stem for p in FILES])
def test_every_golden_transcript_runs_through_the_pipeline_with_scripted_output(fake, path):
    transcript = json.loads(path.read_text(encoding="utf-8"))
    world, trigger = build_from(transcript)
    planted = planted_in_trigger(transcript)
    if planted:
        (item,) = planted
        issue_type = ISSUE_TYPE_FOR[item["dimension"]]
        master = kit.master_d(kit.issue_d("i1", trigger, issue_type, item["phrase"], intensity=item["intensity"]))
        client = fake(master, kit.interv_d("no_intervention", "Leave it for now.", [kit.disp_d("i1", "declined", "Not yet.")]))
    else:
        client = fake(kit.master_d())
    _, stored = kit.go(kit.new_run(trigger))
    assert stored.status == "done"
    assert stored.decision == "no_intervention"
    assert stored.posted_message is None
    assert kit.message_count(world.conv) == len(transcript["messages"])
    if planted:
        (issue,) = kit.issues_of(stored)
        phrase = item["phrase"]
        assert (issue.validity, issue.quote_match) == ("valid", "exact")
        assert trigger.content[issue.quote_start:issue.quote_end] == phrase
        assert (issue.dimension, issue.intensity) == (DIMENSION_FOR[issue_type], item["intensity"])
        assert len(client.calls) == 2
    else:
        assert kit.issues_of(stored) == []
        assert len(client.calls) == 1
    sent = [label for _, label in kit.rendered_messages(client.calls[0])]
    assert sent == [m["author"] for m in transcript["messages"]]
