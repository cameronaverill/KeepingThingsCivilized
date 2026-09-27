"""`manage.py spike`: run the two moderator prompts over the golden transcripts and write a readable report.

    manage.py spike --max-usd 0.30 --dry-run            # no API call: prints the number of calls and a worst-case cost
    manage.py spike --max-usd 0.10 --only rent_factual_left rent_factual_right
    manage.py spike --max-usd 0.50                      # the whole set (needs LLM_ENABLED and a key)

Each transcript goes through the Master Moderator and then the Intervenor, both through `moderation.llm.call` with
`purpose="spike"` (so the budget guard, the kill switch, the circuit breaker and the ledger all apply) and a
`SessionBudget(--max-usd)`. Results are written to golden/results/<timestamp>/ (git-ignored) or --out:
one JSON file per transcript and a report.md meant to be read by a person.

A real run is done deliberately, after the Console spend limit and the key are confirmed; tests use FakeLLM.
"""
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from forum.limits import count_message_chars
from moderation import budget, clock, llm, pricing, prompting, quotes, taxonomy
from moderation.series import COMPUTED_KEYS, FACTORS, SIDES, compute_features
from moderation.errors import (
    BudgetExceeded,
    BudgetUnavailable,
    LLMAPIError,
    LLMOutputError,
    LLMRefused,
)
from moderation.label_check import NAMES_LABEL_RE, names_a_label  # noqa: F401  (NAMES_LABEL_RE re-exported: step 3 tests import it from here)
from moderation.models import LLMCall
from moderation.schemas import IntervenorOutput, MasterOutput

TRANSCRIPTS_DIR = Path(settings.BASE_DIR) / "golden" / "transcripts"
RESULTS_DIR = Path(settings.BASE_DIR) / "golden" / "results"
MASTER_PROMPT = "master_v1"
INTERVENOR_PROMPT = "intervenor_v1"
_ZERO = Decimal("0")


def usd(value):
    return f"${Decimal(value):.4f}"


# --- Loading -------------------------------------------------------------------------------------------------------

def load_transcript_files(directory=None):
    """Read every *.json file in the folder: a list of (path, data). A file that is not valid JSON, is not an object, has
    no `id`, or repeats another file's id is a CommandError naming the file(s), whatever --only selects."""
    directory = Path(directory) if directory else TRANSCRIPTS_DIR
    files = []
    seen = {}
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:  # JSONDecodeError and UnicodeDecodeError are ValueErrors
            raise CommandError(f"{path.name}: cannot read this transcript file ({type(exc).__name__}: {exc})") from exc
        if not isinstance(data, dict):
            raise CommandError(f"{path.name}: a transcript file must contain a JSON object")
        if not isinstance(data.get("id"), str) or not data["id"]:
            raise CommandError(f"{path.name}: missing required key 'id' (a non-empty string)")
        if not re.fullmatch(r"[a-z0-9_]+", data["id"]):
            raise CommandError(
                f"{path.name}: transcript id {data['id']!r} is not allowed; an id may contain only a-z, 0-9 and _"
            )
        if data["id"] in seen:
            raise CommandError(f"duplicate transcript id {data['id']!r} in {seen[data['id']].name} and {path.name}")
        seen[data["id"]] = path
        files.append((path, data))
    return files


def load_transcripts(directory=None):
    """All golden transcripts as {id: dict}, sorted by file name (see load_transcript_files for the errors)."""
    return {data["id"]: data for _path, data in load_transcript_files(directory)}


def _safe_path(directory, name):
    """directory/name, refusing anything that resolves outside the results directory (defense in depth)."""
    root = Path(directory).resolve()
    path = (root / name).resolve()
    if root != path.parent:
        raise CommandError(f"refusing to write outside the results directory: {name!r}")
    return path


def _is_moderator(author):
    return "moderator" in str(author).lower()


def validate_transcript(path, data, known_ids=None):
    """Raise CommandError (naming the file and the key or message) unless the transcript is usable and within limits."""
    name = path.name

    def need(container, key, where=""):
        if not isinstance(container, dict) or key not in container:
            raise CommandError(f"{name}: missing required key '{where}{key}'")
        return container[key]

    topic = need(data, "topic")
    need(topic, "title", "topic.")
    need(topic, "proposition", "topic.")
    messages = need(data, "messages")
    trigger = need(data, "trigger_seq")
    if not isinstance(messages, list) or not messages:
        raise CommandError(f"{name}: 'messages' must be a non-empty list")
    seqs = []
    for position, message in enumerate(messages, start=1):
        label = f"messages[{position}]."
        seq = need(message, "seq", label)
        author = need(message, "author", label)
        text = need(message, "text", label)
        if isinstance(seq, bool) or not isinstance(seq, int):
            raise CommandError(f"{name}: message {position} has a seq that is not an integer")
        if not isinstance(author, str) or not isinstance(text, str):
            raise CommandError(f"{name}: message seq {seq} needs a string 'author' and a string 'text'")
        counted = count_message_chars(text)  # the rule real users are held to (CRLF as one, NFC, stripped, code points)
        if counted > settings.MAX_MESSAGE_CHARS:
            raise CommandError(
                f"{name}: message seq {seq} is {counted} characters, over MAX_MESSAGE_CHARS "
                f"({settings.MAX_MESSAGE_CHARS}); synthetic transcripts obey the same limit as real users"
            )
        planted = message.get("planted", [])
        if not isinstance(planted, list):
            raise CommandError(f"{name}: message seq {seq}: 'planted' must be a list")
        for item in planted:
            for key in ("dimension", "phrase", "intensity"):
                need(item, key, f"messages[{position}].planted[].")
        seqs.append(seq)
    if len(set(seqs)) != len(seqs):
        raise CommandError(f"{name}: message seq numbers are not unique")
    if seqs != sorted(seqs):
        raise CommandError(f"{name}: message seq numbers must be in ascending order")
    if isinstance(trigger, bool) or trigger not in seqs:
        raise CommandError(f"{name}: trigger_seq {trigger!r} is not the seq of any message")
    for message in messages:
        if message["seq"] == trigger and _is_moderator(message["author"]):
            raise CommandError(
                f"{name}: trigger_seq {trigger} is a Moderator message; the newest message the Master sees must be "
                "a participant message (the moderator never replies to itself)"
            )
    if data.get("series") is not None:
        _validate_series(name, data, messages, trigger, known_ids)


def _validate_series(name, data, messages, trigger, known_ids):
    """Rules for a member of a mechanical series (docs/step3_brief.md section 9): a well-formed `series` object, a base
    transcript that exists, and a `computed` block equal to what the messages really are."""
    series = data["series"]
    if not isinstance(series, dict):
        raise CommandError(f"{name}: 'series' must be an object")
    for key in ("id", "factor", "level", "side", "base"):
        if not isinstance(series.get(key), str) or not series[key]:
            raise CommandError(f"{name}: missing required key 'series.{key}' (a non-empty string)")
    if series["factor"] not in FACTORS:
        raise CommandError(f"{name}: series.factor {series['factor']!r} is not one of {', '.join(FACTORS)}")
    if series["side"] not in SIDES:
        raise CommandError(f"{name}: series.side {series['side']!r} is not 'left' or 'right'")
    if known_ids is not None and series["base"] not in known_ids:
        raise CommandError(f"{name}: series.base {series['base']!r} is not a transcript in the folder")
    computed = data.get("computed")
    if not isinstance(computed, dict):
        raise CommandError(f"{name}: missing required key 'computed' (an object) for a series member")
    for key in COMPUTED_KEYS:
        if key not in computed:
            raise CommandError(f"{name}: missing required key 'computed.{key}'")
    actual = compute_features(messages, trigger)
    for key in COMPUTED_KEYS:
        if computed[key] != actual[key]:
            raise CommandError(
                f"{name}: computed.{key} is {computed[key]!r} but the messages give {actual[key]!r}; regenerate the transcript"
            )
    base = known_ids.get(series["base"]) if isinstance(known_ids, dict) else None
    if base is not None and series["factor"] in ("message_length", "label_swap"):
        _check_against_base(name, data, base, series)


def _planted_phrases(transcript):
    return sorted(
        (m["seq"], p.get("phrase")) for m in transcript["messages"] for p in (m.get("planted") or []) if isinstance(p, dict)
    )


def _check_against_base(name, data, base, series):
    """A message-length member equals its base except message 4 (messages 1 to 3 identical, same planted phrase); a
    label-swap member has identical text with every label swapped."""
    swap = {"Participant A": "Participant B", "Participant B": "Participant A"}
    mine, theirs = data["messages"], base["messages"]
    if len(mine) != len(theirs):
        raise CommandError(f"{name}: has {len(mine)} messages but its base {series['base']} has {len(theirs)}")
    if data["trigger_seq"] != base["trigger_seq"]:
        raise CommandError(f"{name}: trigger_seq differs from the base {series['base']}")
    if series["factor"] == "message_length":
        for own, ref in zip(mine[: data["trigger_seq"] - 1], theirs[: data["trigger_seq"] - 1]):
            if (own["seq"], own["author"], own["text"]) != (ref["seq"], ref["author"], ref["text"]):
                raise CommandError(f"{name}: message seq {own['seq']} must be identical to the base {series['base']}")
        if _planted_phrases(data) != _planted_phrases(base):
            raise CommandError(f"{name}: the planted phrase must be identical to the base {series['base']}'s")
    else:
        for own, ref in zip(mine, theirs):
            if own["text"] != ref["text"] or own["author"] != swap.get(ref["author"], ref["author"]):
                raise CommandError(
                    f"{name}: message seq {own['seq']} must be the base {series['base']}'s text with the label swapped"
                )


def visible_messages(transcript):
    """(message_id, label, text) for every message up to and including the trigger message; message_id is the seq."""
    trigger = transcript["trigger_seq"]
    return [(m["seq"], m["author"], m["text"]) for m in transcript["messages"] if m["seq"] <= trigger]


# --- Input rendering and cost estimates --------------------------------------------------------------------------------

def master_request(transcript, master_prompt):
    user = prompting.render_master_input(
        visible_messages(transcript),
        topic_title=transcript["topic"]["title"],
        proposition=transcript["topic"]["proposition"],
    )
    return {"system": master_prompt.text, "messages": [{"role": "user", "content": user}], "user_text": user}


def intervenor_request(transcript, intervenor_prompt, valid_issues, discussion_map):
    user = prompting.render_intervenor_input(
        visible_messages(transcript),
        valid_issues,
        topic_title=transcript["topic"]["title"],
        proposition=transcript["topic"]["proposition"],
        discussion_map=discussion_map,
    )
    return {"system": intervenor_prompt.text, "messages": [{"role": "user", "content": user}], "user_text": user}


def estimate_worst_case(transcript, model, master_prompt, intervenor_prompt):
    """Reservation-basis worst-case cost of the two calls for one transcript (what the guard would reserve)."""
    master = master_request(transcript, master_prompt)
    master_tokens = budget.estimate_input_tokens(
        system=master["system"], messages=master["messages"], schema=MasterOutput
    )
    master_cost = budget.reservation_usd(model, estimated_input_tokens=master_tokens, max_tokens=settings.MASTER_MAX_TOKENS)
    # The Intervenor also receives the Master's issues; assume they fill the whole Master output allowance.
    intervenor = intervenor_request(transcript, intervenor_prompt, [], None)
    extra_chars = int(settings.MASTER_MAX_TOKENS) * 4
    intervenor_tokens = budget.estimate_input_tokens(
        system=intervenor["system"],
        messages=intervenor["messages"] + [{"role": "user", "content": "x" * extra_chars}],
        schema=IntervenorOutput,
    )
    intervenor_cost = budget.reservation_usd(
        model, estimated_input_tokens=intervenor_tokens, max_tokens=settings.INTERVENOR_MAX_TOKENS
    )
    return master_cost, intervenor_cost


# --- Running one call ----------------------------------------------------------------------------------------------

def _call_record(agent, exc_or_result, prompt):
    """A JSON-friendly record of one call, from an LLMResult or from an exception that carries a call_id."""
    record = {"agent": agent, "prompt": {"name": prompt.name, "version": prompt.version, "sha256": prompt.sha256}}
    call_id = getattr(exc_or_result, "call_id", None)
    record["call_id"] = call_id
    row = LLMCall.objects.filter(pk=call_id).first() if call_id else None
    if row is not None:
        record.update(
            status=row.status,
            raw_response=row.raw_response,
            input_tokens=row.tokens_in,
            output_tokens=row.tokens_out,
            cache_write_tokens=row.cache_write_tokens,
            cache_read_tokens=row.cache_read_tokens,
            cost_usd=str(row.cost_usd if row.cost_usd is not None else row.reserved_usd),
            stop_reason=row.stop_reason,
        )
    else:
        record.update(cost_usd="0")
    return record


def run_agent(*, agent, prompt, request, output_schema, max_tokens, model, session):
    """One guarded call. Returns (record, parsed_or_None, stop). `stop` is True when the whole run must stop
    (the guard refused: budget cap, kill switch, breaker or model)."""
    try:
        result = llm.call(
            purpose="spike",
            agent=agent,
            model=model,
            system=request["system"],
            messages=request["messages"],
            output_schema=output_schema,
            max_tokens=max_tokens,
            prompt_version=prompt.name,
            session=session,
        )
    except LLMRefused as exc:
        record = _call_record(agent, exc, prompt)
        record.update(status=getattr(exc, "status", "refused"), error=str(exc), cost_usd="0")
        if isinstance(exc, BudgetExceeded):
            record["cap_name"] = exc.cap_name
        return record, None, True
    except BudgetUnavailable as exc:
        record = _call_record(agent, exc, prompt)
        record.update(status="budget_unavailable", error=str(exc), cost_usd="0")
        return record, None, True
    except (LLMAPIError, LLMOutputError) as exc:
        record = _call_record(agent, exc, prompt)
        record["error"] = str(exc)
        record["error_kind"] = getattr(exc, "reason", "") or getattr(exc, "error_type", "") or type(exc).__name__
        return record, None, False
    record = _call_record(agent, result, prompt)
    record.update(
        status="ok",
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cache_write_tokens=result.cache_write_tokens,
        cache_read_tokens=result.cache_read_tokens,
        cost_usd=str(result.cost_usd),
        stop_reason=result.stop_reason,
        latency_ms=result.latency_ms,
    )
    return record, result.parsed, False


# --- Checking the Master's issues (a small version of what step 5's pipeline will do) -----------------------------------

def check_issues(transcript, master_output):
    """Locate each issue's quote and apply the only-new-issues rule. Returns a list of dicts (issue + location + valid)."""
    texts = {m["seq"]: m["text"] for m in transcript["messages"] if m["seq"] <= transcript["trigger_seq"]}
    moderator_seqs = {m["seq"] for m in transcript["messages"] if _is_moderator(m["author"])}
    newest = transcript["trigger_seq"]
    checked = []
    for issue in master_output.issues:
        entry = issue.model_dump(mode="json")
        reason = None
        text = texts.get(issue.message_id)
        if issue.message_id in moderator_seqs:
            reason = "the message is a moderator message (the moderator's own posts are never flagged)"
            location = {"start": None, "end": None, "match": "not_found", "occurrences": 0}
        elif text is None:
            reason = "the message id is not in the transcript"
            location = {"start": None, "end": None, "match": "not_found", "occurrences": 0}
        else:
            found = quotes.locate_quote(text, issue.quote)
            location = {"start": found.start, "end": found.end, "match": found.match, "occurrences": found.occurrences}
            if found.match == quotes.NOT_FOUND:
                reason = "the quoted phrase was not found in the message"
        if reason is None and issue.issue_type not in taxonomy.CROSS_MESSAGE_ISSUE_TYPES and issue.message_id != newest:
            reason = "an issue of this type on an older message (only-new-issues rule)"
        entry["location"] = location
        entry["valid"] = reason is None
        entry["rejection_reason"] = reason
        checked.append(entry)
    return checked


def planted_spans(transcript):
    """For each planted phrase: {message_id, dimension, intensity, phrase, start, end}."""
    spans = []
    for message in transcript["messages"]:
        for planted in message.get("planted", []):
            found = quotes.locate_quote(message["text"], planted["phrase"])
            spans.append(
                {
                    "message_id": message["seq"],
                    "dimension": planted["dimension"],
                    "intensity": planted["intensity"],
                    "phrase": planted["phrase"],
                    "start": found.start,
                    "end": found.end,
                }
            )
    return spans


def planted_detection(transcript, checked_issues):
    """For each planted phrase, the issue (if any) whose located quote overlaps it, with the same message id."""
    results = []
    for span in planted_spans(transcript):
        hit = None
        for issue in checked_issues:
            loc = issue["location"]
            if (
                issue["valid"]
                and issue["message_id"] == span["message_id"]
                and loc["start"] is not None
                and span["start"] is not None
                and loc["start"] < span["end"]
                and span["start"] < loc["end"]
            ):
                hit = issue
                break
        results.append({**span, "detected_by": hit["id"] if hit else None, "issue_type": hit["issue_type"] if hit else None,
                        "intensity_found": hit["intensity"] if hit else None})
    return results


# --- The run -------------------------------------------------------------------------------------------------------

def run_transcript(transcript, *, model, master_prompt, intervenor_prompt, session):
    """Master then Intervenor for one transcript. Returns (result_dict, stop)."""
    result = {
        "id": transcript["id"],
        "pair_id": transcript.get("pair_id"),
        "variant": transcript.get("variant"),
        "model": model,
        "topic": transcript["topic"],
        "trigger_seq": transcript["trigger_seq"],
        "planted": planted_spans(transcript),
        "series": transcript.get("series"),
        "computed": transcript.get("computed") or compute_features(transcript["messages"], transcript["trigger_seq"]),
        "status": "ok",
    }
    master_req = master_request(transcript, master_prompt)
    result["input"] = {"master_user_message": master_req["user_text"]}
    record, parsed, stop = run_agent(
        agent="master", prompt=master_prompt, request=master_req, output_schema=MasterOutput,
        max_tokens=settings.MASTER_MAX_TOKENS, model=model, session=session,
    )
    result["master"] = record
    if parsed is None:
        result["status"] = "not_run" if stop else "master_failed"
        result["total_cost_usd"] = record["cost_usd"]
        if stop:
            result["stop_reason"] = record.get("error", "the guard refused a call")
        return result, stop
    checked = check_issues(transcript, parsed)
    result["master"]["output"] = parsed.model_dump(mode="json")
    result["master"]["issues_checked"] = checked
    result["planted_detection"] = planted_detection(transcript, checked)

    valid = [issue for issue in checked if issue["valid"]]
    inter_req = intervenor_request(transcript, intervenor_prompt, valid, parsed.discussion_map)
    result["input"]["intervenor_user_message"] = inter_req["user_text"]
    record2, parsed2, stop = run_agent(
        agent="intervenor", prompt=intervenor_prompt, request=inter_req, output_schema=IntervenorOutput,
        max_tokens=settings.INTERVENOR_MAX_TOKENS, model=model, session=session,
    )
    result["intervenor"] = record2
    if parsed2 is None:
        result["status"] = "intervenor_failed"
        if stop:
            result["stop_reason"] = record2.get("error", "the guard refused a call")
    else:
        result["intervenor"]["output"] = parsed2.model_dump(mode="json")
        result["label_check"] = label_check(result["intervenor"]["output"])
    result["total_cost_usd"] = str(Decimal(record["cost_usd"]) + Decimal(record2["cost_usd"]))
    return result, stop


# --- The report ----------------------------------------------------------------------------------------------------

def label_check(output):
    """{"acts": n, "naming_label": k, "act_numbers": [1-based numbers of the acts that name a label]} for an Intervenor output."""
    acts = (output or {}).get("acts") or []
    named = [number for number, act in enumerate(acts, start=1) if names_a_label(act["text"])]
    return {"acts": len(acts), "naming_label": len(named), "act_numbers": named}


def _act_summary(result):
    out = (result.get("intervenor") or {}).get("output")
    if not out:
        return None
    return out


def _metrics(result):
    """The comparable numbers for one transcript (used in the side-by-side tables)."""
    metrics = {}
    if result["status"] in ("not_run", "master_failed"):
        return None
    issues = (result["master"].get("issues_checked") or [])
    valid = [i for i in issues if i["valid"]]
    metrics["issues"] = ", ".join(f"{i['issue_type']}" + (f" (intensity {i['intensity']})" if i["intensity"] is not None else "") for i in valid) or "none"
    detection = result.get("planted_detection") or []
    if detection:
        metrics["planted"] = "; ".join(
            ("found" + (f" as {d['issue_type']}, intensity {d['intensity_found']}" if d["issue_type"] else "")) if d["detected_by"] else "NOT found"
            for d in detection
        )
    else:
        metrics["planted"] = "(nothing planted)"
    process = sorted({i["issue_type"] for i in valid if i["issue_type"] in ("process_violation", "repetition")})
    metrics["process"] = ("yes: " + ", ".join(process)) if process else "no"
    computed = result.get("computed") or {}
    metrics["computed"] = (
        f"trigger message {computed.get('trigger_message_chars', '?')} characters, {computed.get('trigger_message_words', '?')} words; "
        f"longest run by one author {computed.get('longest_consecutive_run', '?')}; "
        f"repeated sentence: {'yes' if computed.get('repeated_sentence_across_messages') else 'no'}; "
        f"unanswered question: {'yes' if computed.get('unanswered_question_followed_by_two_replies') else 'no'}"
    )
    out = _act_summary(result)
    if out is None:
        metrics.update(decision="(Intervenor did not finish)", acts="", tones="", length=0, label="n/a")
        return metrics
    acts = out["acts"]
    metrics["decision"] = out["decision"]
    metrics["acts"] = ", ".join(a["type"] for a in acts) or "none"
    metrics["tones"] = ", ".join(a["tone"] for a in acts) or "none"
    metrics["length"] = sum(len(a["text"]) for a in acts)
    check = label_check(out)
    metrics["label"] = "no" if not check["naming_label"] else f"YES: {check['naming_label']} of {check['acts']} acts"
    return metrics


_ROWS = [
    ("Planted problem", "planted"),
    ("All problems the Master flagged", "issues"),
    ("Intervenor decision", "decision"),
    ("Kinds of act", "acts"),
    ("Tone of each act", "tones"),
    ("Length of the moderator post (characters)", "length"),
    ("Post text names a participant label", "label"),
]


def _pair_table(left, right):
    lm, rm = _metrics(left), _metrics(right)
    lines = ["| | left-coded variant | right-coded variant | same? |", "|---|---|---|---|"]
    if lm is None or rm is None:
        lines.append("| (one variant did not finish, so no comparison) | | | |")
        return lines
    for label, key in _ROWS:
        a, b = lm[key], rm[key]
        if key == "length":
            if a == b:
                same = "same"
            else:
                same = "same" if min(a, b) and max(a, b) / min(a, b) <= 1.25 else "DIFFERENT"
        else:
            same = "same" if a == b else "DIFFERENT"
        lines.append(f"| {label} | {a} | {b} | {same} |")
    cost_l, cost_r = Decimal(left.get("total_cost_usd", "0")), Decimal(right.get("total_cost_usd", "0"))
    lines.append(f"| Cost of the two calls | {usd(cost_l)} | {usd(cost_r)} | |")
    return lines


_SERIES_ROWS = [
    ("Planted problem found?", "planted"),
    ("All problems the Master flagged (with intensities)", "issues"),
    ("Process problem flagged (process_violation or repetition)", "process"),
    ("Intervenor decision", "decision"),
    ("Kinds of act", "acts"),
    ("Tone of each act", "tones"),
    ("Length of the moderator post (characters)", "length"),
    ("Post text names a participant label", "label"),
    ("Computed features of this transcript", "computed"),
]
_LEVEL_ORDER = {"message_length": ["short", "base", "long", "very_long"], "label_swap": ["base", "swapped"]}


def _same(key, a, b):
    if key == "length":
        if a == b:
            return "same"
        return "same" if min(a, b) and max(a, b) / min(a, b) <= 1.25 else "DIFFERENT"
    if key == "computed":
        return "(differs by design)" if a != b else "same"
    return "same" if a == b else "DIFFERENT"


def _series_sections(results):
    """One section per mechanical series: one block per level (the base transcripts serve as the 'base' level of the
    length and label-swap series), left and right side by side."""
    by_id = {r["id"]: r for r in results}
    series = {}
    for result in results:
        info = result.get("series")
        if info:
            series.setdefault(info["id"], {"factor": info["factor"], "members": {}, "bases": {}})
            series[info["id"]]["members"][(info["level"], info["side"])] = result
            base = by_id.get(info["base"])
            if base is not None and info["factor"] in _LEVEL_ORDER:
                series[info["id"]]["bases"][info["side"]] = base
    if not series:
        return []
    lines = ["## Mechanical series (length, label swap, process behaviors)", ""]
    lines += [
        "Each series changes one factor whose value is computed by code, and is run for a left-coded and a right-coded "
        "variant. Read across a row: did the factor change what the moderator noticed or did, and did it matter more on one side?",
        "",
    ]
    for series_id, info in series.items():
        lines += [f"### Series `{series_id}` (factor: {info['factor'].replace('_', ' ')})", ""]
        levels = []
        for level, _side in info["members"]:
            if level not in levels:
                levels.append(level)
        order = _LEVEL_ORDER.get(info["factor"])
        if order:
            levels = [lv for lv in order if lv in levels or (lv == "base" and info["bases"])]
        lines += ["| level | left: planted found? / decision | right: planted found? / decision | same across sides? |", "|---|---|---|---|"]
        blocks = []
        for level in levels:
            cells = {}
            for side in ("left", "right"):
                result = info["bases"].get(side) if level == "base" else info["members"].get((level, side))
                cells[side] = _metrics(result) if result else None
            label = "base (as written)" if level == "base" else level
            summary = []
            for side in ("left", "right"):
                m = cells[side]
                summary.append("not run or failed" if m is None else f"{m['planted']}; {m['decision']}")
            verdicts = [_same(key, cells["left"][key], cells["right"][key]) for _t, key in _SERIES_ROWS
                        if cells["left"] and cells["right"] and key != "computed"]
            overall = "n/a" if not (cells["left"] and cells["right"]) else ("DIFFERENT" if "DIFFERENT" in verdicts else "same")
            lines.append(f"| {label} | {summary[0]} | {summary[1]} | {overall} |")
            blocks.append((label, cells))
        lines.append("")
        for label, cells in blocks:
            lines += [f"Level: **{label}**", "", "| | left-coded | right-coded | same across sides? |", "|---|---|---|---|"]
            if cells["left"] is None or cells["right"] is None:
                lines += ["| (a variant was not run or did not finish) | | | |", ""]
                continue
            for title, key in _SERIES_ROWS:
                a, b = cells["left"][key], cells["right"][key]
                lines.append(f"| {title} | {a} | {b} | {_same(key, a, b)} |")
            lines.append("")
    return lines


def _quote_line(issue):
    loc = issue["location"]
    if loc["match"] == quotes.NOT_FOUND:
        where = "NOT FOUND in the message"
    else:
        where = f"found ({loc['match']} match) at characters {loc['start']} to {loc['end']}"
        if loc["occurrences"] > 1:
            where += f", appears {loc['occurrences']} times"
    return where


def _transcript_section(result, transcript):
    lines = [f"### {result['id']}", ""]
    lines.append(f"Topic: {transcript['topic']['title']}. Proposition: \"{transcript['topic']['proposition']}\"")
    if transcript.get("description"):
        lines.append("")
        lines.append(f"What this test is: {transcript['description']}")
    lines += ["", "The conversation the moderator saw:", ""]
    for message in transcript["messages"]:
        if message["seq"] > transcript["trigger_seq"]:
            continue
        flag = " (the newest message)" if message["seq"] == transcript["trigger_seq"] else ""
        text = message["text"].replace("\n", "\n> ")
        lines.append(f"> **{message['author']}, message {message['seq']}{flag}:** {text}")
        lines.append(">")
    lines.append("")
    for planted in result["planted"]:
        lines.append(
            f"Planted on purpose in message {planted['message_id']}: \"{planted['phrase']}\" "
            f"({planted['dimension']}, intensity {planted['intensity']})."
        )
    if not result["planted"]:
        lines.append("Nothing was planted in this transcript.")
    lines.append("")
    if result["status"] == "not_run":
        master = result["master"]
        cap = f" (cap: {master['cap_name']})" if master.get("cap_name") else ""
        lines.append(f"NOT RUN: {master.get('error', 'the run was stopped')}{cap}.")
        lines.append("")
        return lines
    if result["status"] == "master_failed":
        lines.append(f"The Master Moderator call FAILED: {result['master'].get('error')}. The Intervenor was not run.")
        lines.append("")
        return lines
    for d in result.get("planted_detection", []):
        if d["detected_by"]:
            lines.append(f"Did the Master find the planted problem? YES (issue {d['detected_by']}, type {d['issue_type']}, intensity {d['intensity_found']}).")
        else:
            lines.append("Did the Master find the planted problem? NO.")
    lines += ["", "**What the Master Moderator found**", ""]
    issues = result["master"].get("issues_checked", [])
    if not issues:
        lines.append("No issues.")
    for issue in issues:
        head = f"- Issue {issue['id']}, message {issue['message_id']}, type `{issue['issue_type']}`"
        if issue["intensity"] is not None:
            head += f", intensity {issue['intensity']} (0 to 4)"
        head += f", confidence {issue['confidence']}"
        if not issue["valid"]:
            head += f"  **REJECTED: {issue['rejection_reason']}**"
        lines.append(head)
        lines.append(f"  - Quote: \"{issue['quote']}\" ({_quote_line(issue)})")
        lines.append(f"  - Why: {issue['explanation']}")
    dmap = result["master"]["output"]["discussion_map"]
    lines.append("")
    lines.append("Discussion map. Agreements: " + ("; ".join(dmap["agreements"]) or "none") + ".")
    for disagreement in dmap["disagreements"]:
        lines.append(f"- Disagreement ({disagreement['kind']}): {disagreement['summary']}")
    lines += ["", "**What the Intervenor decided**", ""]
    inter = result.get("intervenor", {})
    out = inter.get("output")
    if out is None:
        lines.append(f"The Intervenor call FAILED: {inter.get('error')}.")
    else:
        lines.append(f"Decision: `{out['decision']}`. Reason: {out['rationale']}")
        for disp in out["issue_dispositions"]:
            lines.append(f"- Issue {disp['issue_id']}: {disp['disposition']}. {disp['reason']}")
        for number, act in enumerate(out["acts"], start=1):
            if names_a_label(act["text"]):
                lines.append("")
                lines.append(f"**WARNING: the text of act {number} names a participant label.**")
            lines.append("")
            lines.append(
                f"Act {number}: `{act['type']}`, tone {act['tone']}, to {act['addressee']}, about {act['subject']}. "
                f"Would be posted as:"
            )
            lines.append(f"> {act['text']}")
    lines.append("")
    total_in = sum(int(r.get("input_tokens", 0) or 0) for r in (result["master"], inter))
    total_out = sum(int(r.get("output_tokens", 0) or 0) for r in (result["master"], inter))
    lines.append(f"Cost: {usd(result['total_cost_usd'])} ({total_in} input tokens, {total_out} output tokens).")
    lines.append("")
    return lines


def _label_header(results):
    checks = [r["label_check"] for r in results if r.get("label_check")]
    named = sum(c["naming_label"] for c in checks)
    total = sum(c["acts"] for c in checks)
    return (
        f"Acts naming a participant label (moderator posts must not name anyone): {named} of {total}."
    )


def build_report(*, results, transcripts, model, master_prompt, intervenor_prompt, started, max_usd, total_cost):
    lines = ["# Prompt spike report", ""]
    lines += [
        f"Run started: {started:%Y-%m-%d %H:%M:%S} UTC. Model: `{model}`.",
        f"Prompts: `{master_prompt.name}` (sha256 {master_prompt.sha256[:12]}) and `{intervenor_prompt.name}` (sha256 {intervenor_prompt.sha256[:12]}).",
        f"Total cost of this run: {usd(total_cost)} (limit for this run: {usd(max_usd)}).",
        _label_header(results),
        "",
        "## How to read this",
        "",
        "Each test conversation was read by two AI agents. The **Master Moderator** lists problems it sees in the newest "
        "message (quoting the exact phrase, and scoring factual errors and abusive phrases from 0 to 4). The **Intervenor** "
        "then decides whether the moderator should say anything, and if so what. Nothing here was posted anywhere.",
        "",
        "The test conversations come in **pairs**: the same conversation twice, with the two sides swapped, so only the "
        "political direction differs. A fair moderator treats both variants the same way. In the comparison tables, "
        "\"DIFFERENT\" marks a row where the two variants were treated differently; look at those first. "
        "Some differences are just chance, so one run proves little; the point of this run is to check the format and the quality.",
        "",
    ]
    by_pair = {}
    for result in results:
        if result.get("pair_id"):
            by_pair.setdefault(result["pair_id"], {})[result["variant"]] = result
    lines += ["## Pairs, side by side", ""]
    if not by_pair:
        lines += ["(No pairs in this run.)", ""]
    for pair_id, variants in by_pair.items():
        lines.append(f"### Pair `{pair_id}`")
        lines.append("")
        if "left" in variants and "right" in variants:
            lines += _pair_table(variants["left"], variants["right"])
        else:
            lines.append("Only one variant was run, so there is nothing to compare.")
        lines.append("")
    lines += _series_sections(results)
    lines += ["## Every transcript in detail", ""]
    for result in results:
        lines += _transcript_section(result, transcripts[result["id"]])
    failures = [r for r in results if r["status"] != "ok"]
    lines += ["## Problems in this run", ""]
    if not failures:
        lines.append("None: every call finished and produced usable output.")
    for result in failures:
        record = result.get("intervenor") if result["status"] == "intervenor_failed" else result["master"]
        lines.append(f"- {result['id']}: {result['status']} ({record.get('error', 'no detail')})")
    lines += ["", "## Tokens and caching", ""]
    lines.append("| transcript | agent | input | output | cache written | cache read | cost |")
    lines.append("|---|---|---|---|---|---|---|")
    for result in results:
        for agent in ("master", "intervenor"):
            record = result.get(agent)
            if record and record.get("status") == "ok":
                lines.append(
                    f"| {result['id']} | {agent} | {record.get('input_tokens', 0)} | {record.get('output_tokens', 0)} | "
                    f"{record.get('cache_write_tokens', 0)} | {record.get('cache_read_tokens', 0)} | {usd(record['cost_usd'])} |"
                )
    lines.append("")
    return "\n".join(lines)


# --- The command ---------------------------------------------------------------------------------------------------

class Command(BaseCommand):
    help = (
        "Run the Master Moderator and the Intervenor over the golden transcripts (real API calls, budgeted) and write "
        "golden/results/<timestamp>/. Use --dry-run first: it makes no call and prints the worst-case cost."
    )

    def add_arguments(self, parser):
        parser.add_argument("--max-usd", required=True, help="Most this run may spend, in US dollars (required).")
        parser.add_argument("--only", nargs="+", metavar="ID", help="Run only these transcript ids.")
        parser.add_argument("--dry-run", action="store_true", help="Make no API call; print the number of calls and the worst-case cost.")
        parser.add_argument("--model", default=None, help="Model to use (default: SPIKE_MODEL in config/tunables.py).")
        parser.add_argument("--out", default=None, help="Directory for the results (default: golden/results/<timestamp>/).")

    def handle(self, *args, **options):
        out = self.stdout.write
        try:
            max_usd = Decimal(str(options["max_usd"]))
        except InvalidOperation:
            raise CommandError(f"--max-usd must be a number of dollars, not {options['max_usd']!r}") from None
        if not max_usd.is_finite() or max_usd <= 0:
            raise CommandError("--max-usd must be greater than zero")
        model = options["model"] or settings.SPIKE_MODEL
        try:
            pricing.get_price(model)
        except LLMRefused as exc:
            raise CommandError(str(exc)) from exc

        files = load_transcript_files()  # a malformed file or a duplicate id anywhere in the folder is an error
        transcripts = {data["id"]: data for _path, data in files}
        if not transcripts:
            raise CommandError(f"no transcripts found in {TRANSCRIPTS_DIR}")
        wanted = options["only"]
        if wanted:
            unknown = [t for t in wanted if t not in transcripts]
            if unknown:
                raise CommandError(f"unknown transcript id(s): {', '.join(unknown)}. Available: {', '.join(transcripts)}")
            transcripts = {t: transcripts[t] for t in transcripts if t in wanted}
        paths = {data["id"]: path for path, data in files}
        for transcript_id, transcript in transcripts.items():
            validate_transcript(paths[transcript_id], transcript, known_ids={t['id']: t for _p, t in files})

        master_prompt = prompting.load_prompt(MASTER_PROMPT)
        intervenor_prompt = prompting.load_prompt(INTERVENOR_PROMPT)

        if options["dry_run"]:
            return self._dry_run(transcripts, model, master_prompt, intervenor_prompt, max_usd)

        if not settings.LLM_ENABLED:
            raise CommandError(
                "LLM_ENABLED is False in config/tunables.py, so no call can be made. A real run is done only after the "
                "Console spend limit and the API key are confirmed; use --dry-run to see the cost."
            )
        if not settings.ANTHROPIC_API_KEY:
            raise CommandError("no ANTHROPIC_API_KEY is configured (it belongs in .env)")

        started = clock.now()
        directory = Path(options["out"]) if options["out"] else RESULTS_DIR / f"{started:%Y%m%d-%H%M%S}"
        directory.mkdir(parents=True, exist_ok=True)
        session = budget.SessionBudget(max_usd)
        out(f"Running {len(transcripts)} transcript(s) with {model}; this run may spend at most {usd(max_usd)}.")

        results = []
        total = _ZERO
        stopped = None
        for transcript in transcripts.values():
            if stopped:
                result = {
                    "id": transcript["id"], "pair_id": transcript.get("pair_id"), "variant": transcript.get("variant"),
                    "model": model, "topic": transcript["topic"], "trigger_seq": transcript["trigger_seq"],
                    "planted": planted_spans(transcript), "status": "not_run", "total_cost_usd": "0",
                    "series": transcript.get("series"),
                    "computed": transcript.get("computed") or compute_features(transcript["messages"], transcript["trigger_seq"]),
                    "master": {"status": "not_run", "error": stopped, "cost_usd": "0"},
                }
                stop = True
            else:
                result, stop = run_transcript(
                    transcript, model=model, master_prompt=master_prompt, intervenor_prompt=intervenor_prompt, session=session
                )
                if stop:
                    stopped = f"the run was stopped: {result['stop_reason']}"
            total += Decimal(result["total_cost_usd"])
            results.append(result)
            _safe_path(directory, f"{result['id']}.json").write_text(
                json.dumps(result, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8"
            )
            out(f"  {result['id']}: {result['status']}, {usd(result['total_cost_usd'])}")

        report = build_report(
            results=results, transcripts=transcripts, model=model, master_prompt=master_prompt,
            intervenor_prompt=intervenor_prompt, started=started, max_usd=max_usd, total_cost=total,
        )
        _safe_path(directory, "report.md").write_text(report, encoding="utf-8")
        if stopped:
            out(f"STOPPED EARLY: {stopped}")
        out(f"Results written to {directory}")
        out(f"Total cost: ${total:.6f}")

    def _dry_run(self, transcripts, model, master_prompt, intervenor_prompt, max_usd):
        out = self.stdout.write
        total = _ZERO
        out(f"DRY RUN: no API call is made. Model: {model}.")
        for transcript in transcripts.values():
            master_cost, intervenor_cost = estimate_worst_case(transcript, model, master_prompt, intervenor_prompt)
            total += master_cost + intervenor_cost
            out(f"  {transcript['id']}: 2 calls, worst case {usd(master_cost + intervenor_cost)}")
        calls = 2 * len(transcripts)
        out(f"Calls that a real run would make: {calls} ({len(transcripts)} transcripts x 2 agents).")
        out(f"Estimated worst-case cost: {usd(total)} (every call priced as if all input were uncached and the whole reply allowance were used; real cost is normally well below this).")
        out(f"This run's limit (--max-usd): {usd(max_usd)}.")
        spike_cap = getattr(settings, "BUDGET_SPIKE_USD_TOTAL", None)
        if spike_cap is not None:
            out(f"Spike cap for all spike calls together (BUDGET_SPIKE_USD_TOTAL): {usd(spike_cap)}.")
        if total > max_usd:
            out(f"WARNING: the estimated worst case ({usd(total)}) is above this run's limit --max-usd ({usd(max_usd)}); a real run may stop early when the guard refuses a call.")
        if spike_cap is not None and total > spike_cap:
            out(f"WARNING: the estimated worst case ({usd(total)}) is above BUDGET_SPIKE_USD_TOTAL ({usd(spike_cap)}); the guard will stop a real run once all spike spending reaches that cap.")
