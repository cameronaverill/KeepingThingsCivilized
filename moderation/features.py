"""Facts and features computed in code, with no LLM (docs/step5_brief.md).

`process_facts(transcript)` gives the Master conversation-shape facts (who spoke how often in a row, how fast) so it
does not have to count them. `act_text_features(text)` gives the four features stored on an `InterventionAct`.
"""
from moderation.models import InterventionAct


def process_facts(transcript):
    """Facts about the shape of `transcript` (a list of message dicts, oldest first). JSON-serializable.

    Keys:
    - `message_count`: number of messages.
    - `messages_per_label`: {label: count}; the label is the message's own `label` ("Participant A", "Moderator", ...).
    - `latest_author_label`: label of the newest message (None when empty).
    - `current_run_length`: how many consecutive messages, ending at the newest, carry that same label (0 when empty).
    - `longest_run_length` / `longest_run_label`: the longest stretch of consecutive messages with one label (the
      earliest one on a tie). A message with another label, moderator included, ends a run.
    - `seconds_between_last_two_messages`: seconds from the second-newest to the newest message (from `created_at`),
      None with fewer than two messages.
    """
    counts = {}
    longest_len, longest_label = 0, None
    run_len, run_label = 0, None
    for message in transcript:
        label = message["label"]
        counts[label] = counts.get(label, 0) + 1
        if label == run_label:
            run_len += 1
        else:
            run_label, run_len = label, 1
        if run_len > longest_len:
            longest_len, longest_label = run_len, label
    gap = None
    if len(transcript) >= 2:
        gap = (transcript[-1]["created_at"] - transcript[-2]["created_at"]).total_seconds()
    return {
        "message_count": len(transcript),
        "messages_per_label": counts,
        "latest_author_label": run_label,
        "current_run_length": run_len,
        "longest_run_length": longest_len,
        "longest_run_label": longest_label,
        "seconds_between_last_two_messages": gap,
    }


def act_text_features(text):
    """{"char_len", "word_count", "is_question", "quotes_participant"} for `text`, computed by the model's own
    `InterventionAct.compute_features()` on an unsaved instance, so it can never drift from what `save()` stores."""
    act = InterventionAct(text=text)
    act.compute_features()
    return {
        "char_len": act.char_len,
        "word_count": act.word_count,
        "is_question": act.is_question,
        "quotes_participant": act.quotes_participant,
    }
