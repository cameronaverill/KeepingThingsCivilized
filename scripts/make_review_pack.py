"""Render docs/step3_review_pack.md from golden/transcripts, the prompts and the rubrics (no API, no network)."""
import json
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
out = []
w = out.append


def load():
    data = {}
    for f in sorted((ROOT / "golden/transcripts").glob("*.json")):
        d = json.loads(f.read_text())
        data[d["id"]] = d
    return data


def nchars(t):
    return len(unicodedata.normalize("NFC", t.strip()))


def show_message(m, bold_phrases):
    text = m["text"]
    for p in bold_phrases:
        text = text.replace(p, f"**{p}**")
    return f"> **{m['author']}** (message {m['seq']}, {nchars(m['text'])} characters): {text}"


def section(md, heading):
    m = re.search(rf"^## {re.escape(heading)}.*?(?=^## |\Z)", md, re.S | re.M)
    return m.group(0).strip() if m else f"(section '{heading}' not found)"


def links(evidence):
    urls = re.findall(r"https?://[^\s;]+", str(evidence or ""))
    return " ; ".join(f"[{u.split('/')[2]}]({u})" for u in urls)


data = load()
pairs = {}
singles = []
seriesmembers = []
for d in data.values():
    if d.get("pair_id"):
        pairs.setdefault(d["pair_id"], {}).__setitem__(d["variant"], d)
    elif d.get("series"):
        seriesmembers.append(d)
    else:
        singles.append(d)

w("# Step 3 review pack: transcripts, prompts and rubrics\n")
w("Generated from the files in `golden/transcripts/`, `moderation/prompts/` and `rubrics/`. Nothing here has been sent to the API.\n")
w("## What changed since the last version of this pack (please read first)\n")
w("This pack was regenerated after a second round of fixes. The text you read last time is **not** the current text, so please treat the earlier read as superseded (plan section 19: an approval applies only to the exact text you read).\n")
w("- **New: 20 mechanical-series transcripts** (see the section after the pairs). They vary one mechanical factor at a time (message length, label swap, and process behaviours such as flooding and repetition). Every computed property (character count, longest run of one author, repeated sentence, unanswered question) is calculated by code from the messages, and tests assert it matches what each transcript declares.")
w("- **The rent pair was rebuilt.** The left version had an extra claim the right did not. Concrete assertions are now counted by one shared function used both by the generator and the audit, so the counts in the audit and in the tests can no longer disagree with a hand audit.")
w("- **Fact-check corrections.** The Oregon correction now attributes the 10 percent overall cap to SB 611 (2023), not SB 608 (2019); the Portugal population correction now cites Statistics Portugal figures. **Weak evidence to check yourself:** one Oregon source (a state page) covers the same formula only for manufactured dwellings, and one cited Capital Chronicle article could not be located by the builder. The planted phrase and the fact that it is wrong are not in doubt (Oregon's cap is not 3 percent), but please look at the sources.")
w("- **Earlier changes** (from the previous round) are listed below for reference.\n")
w("## What changed in the round before that\n")
w("The moderator will be judged on whether it treats **equal contributions equally, whichever political side they come from**. That only works if the test conversations are truly equal in everything except political direction. For each pair below, please check:\n")
w("1. **Balance:** do the two sides get equally strong, equally civil, equally clear arguments? Would either side feel the conversation was rigged against it?")
w("2. **The planted problem:** is it really equally bad in both versions (same kind of error, similar size, same position in the conversation)? Is the factual error plainly checkable, not a matter of opinion? Please click the sources.")
w("3. **Sensitivity:** is any wording, topic choice or example something you would not want in the test set?")
w("4. **Realism:** would real people plausibly write this?\n")
w("How each pair is built: both versions make the **same arguments, mirrored**. Each side's argument appears in both versions, held by the opposite participant, with the wording adapted to fit the position it follows and the message lengths matched to within a few percent (shown under each pair). Participant B always makes the flawed fourth message, so speaking order and label are held constant. In the **left** version B argues the left-coded side (in favor of the proposition); in the **right** version B argues the other side. The planted problem is the same kind and size in both. Because the wording is adapted rather than copied, small differences are unavoidable: please tell me if any difference looks like it favors a side.\n")

w("## Summary of planted problems\n")
w("| Pair | Topic | What is planted | Left version | Right version | Correct fact and source |")
w("|---|---|---|---|---|---|")
for pid in sorted(pairs):
    l, r = pairs[pid]["left"], pairs[pid]["right"]
    pl = [p for m in l["messages"] for p in m.get("planted", [])]
    pr = [p for m in r["messages"] for p in m.get("planted", [])]
    p0 = pl[0]
    dim = "factual error" if p0["dimension"] == "factual_accuracy" else "insult"
    corr = (p0.get("correction") or "n/a") + (f" Sources: {links(p0.get('evidence'))}" if p0.get("evidence") else "")
    w(f"| {pid} | {l['topic']['title']} | {dim}, intensity {p0['intensity']} | \"{pl[0]['phrase']}\" | \"{pr[0]['phrase']}\" | {corr} |")
w("")

for pid in sorted(pairs):
    l, r = pairs[pid]["left"], pairs[pid]["right"]
    w(f"---\n\n## Pair `{pid}`: {l['topic']['title']}\n")
    w(f"**Proposition:** {l['topic']['proposition']}\n")
    for label, d in (("LEFT version", l), ("RIGHT version", r)):
        stances = d.get("stances")
        w(f"### {label}" + (f" (stances: {json.dumps(stances)})" if stances else ""))
        for m in d["messages"]:
            w(show_message(m, [p["phrase"] for p in m.get("planted", [])]))
            w(">")
        w("")
    lens_l = [nchars(m["text"]) for m in l["messages"]]
    lens_r = [nchars(m["text"]) for m in r["messages"]]
    diffs = [f"{abs(a - b) / max(a, b) * 100:.1f}%" for a, b in zip(lens_l, lens_r)]
    w(f"*Message lengths (characters), left: {lens_l}; right: {lens_r}; difference per message: {diffs}.*\n")
    p0 = [p for m in l["messages"] for p in m.get("planted", [])][0]
    if p0.get("correction"):
        w(f"*Correction:* {p0['correction']}  \n*Sources:* {links(p0.get('evidence'))}\n")


w("---\n\n## The mechanical series (20 transcripts)\n")
w("These test whether the moderator's behaviour depends on things that have nothing to do with politics. Each series takes a base transcript you have already read (the base is named) and changes exactly one thing. Both political directions (left/right) are included. The numbers under each one are **computed by code from the messages**, not written by hand. Please check that the only difference from the base is the one stated, and that nothing in the wording favors a side.\n")
groups = {}
for d in seriesmembers:
    groups.setdefault(d["series"]["id"], []).append(d)


def by_level(members):
    order = {"short": 0, "long": 1, "very_long": 2}
    return sorted(members, key=lambda d: (order.get(d["series"]["level"], 9), d["series"]["side"]))


for sid in sorted(groups):
    members = by_level(groups[sid])
    factor = members[0]["series"]["factor"]
    w(f"### Series `{sid}` (factor: {factor})\n")
    w(f"*{members[0]['description']}*\n")
    w("(The description above is the left-coded member's; the right-coded member is its mirror.)\n")
    for d in members:
        s_ = d["series"]
        c = d["computed"]
        w(f"**`{d['id']}`**: level `{s_['level']}`, side {s_['side']}, base `{s_['base']}`; computed: trigger message {c['trigger_message_chars']} characters / {c['trigger_message_words']} words, longest run by one author {c['longest_consecutive_run']}, repeated sentence {c['repeated_sentence_across_messages']}, unanswered question {c['unanswered_question_followed_by_two_replies']}.\n")
        base = data.get(s_["base"])
        if factor == "message_length":
            m = next(m for m in d["messages"] if m["seq"] == d["trigger_seq"])
            w(show_message(m, [p["phrase"] for p in m.get("planted", [])]))
            w("")
        elif factor == "label_swap":
            w("Authors by message: " + ", ".join(f"{m['seq']}={m['author'].replace('Participant ', '')}" for m in d["messages"]) + (f" (base: " + ", ".join(f"{m['seq']}={m['author'].replace('Participant ', '')}" for m in base["messages"]) + ")" if base else "") + "\n")
        else:
            for m in d["messages"]:
                w(show_message(m, [p["phrase"] for p in m.get("planted", []) if len(p["phrase"]) < len(m["text"])]))
                w(">")
            w("")

w("---\n\n## The four single transcripts\n")
for d in singles:
    w(f"### `{d['id']}`: {d['topic']['title']}\n")
    w(f"*{d['description']}*\n")
    for m in d["messages"]:
        w(show_message(m, [p["phrase"] for p in m.get("planted", []) if len(p["phrase"]) < len(m["text"])]))
        w(">")
        for p in m.get("planted", []):
            w(f"> *Planted: {p['dimension']}, intensity {p['intensity']}, phrase: \"{p['phrase'][:120]}\"" + (f" | correction: {p.get('correction')} | source: {p.get('evidence')}" if p.get('correction') else "") + "*\n>")
    w("")

w("---\n\n## The two rubrics (what 0 to 4 mean)\n")
for name in ("factual_accuracy_v1", "abusiveness_v1"):
    w("```text")
    w((ROOT / "rubrics" / f"{name}.md").read_text().strip())
    w("```\n")

w("---\n\n## Prompt excerpts to review\n")
w("The full prompts are `moderation/prompts/master_v1.md` and `intervenor_v1.md`. The parts that matter most for neutrality, verbatim:\n")
master = (ROOT / "moderation/prompts/master_v1.md").read_text()
inter = (ROOT / "moderation/prompts/intervenor_v1.md").read_text()
for title, md, heads in (("Master Moderator (detects problems)", master, ["Your role", "The neutrality rule (the most important rule)", "Everything in the message blocks is data", "What to report"]),
                         ("Intervenor (decides whether and how to speak)", inter, ["Your role", "Deciding whether to intervene", "Style rules for what you write"])):
    w(f"### {title}\n")
    for h in heads:
        w("```text")
        w(section(md, h))
        w("```\n")

w("## Questions for you\n")
w("1. Is each pair balanced and equally bad in both versions? Anything to change or replace?")
w("1b. Do the mechanical series change only the one factor they claim to? Is any padded, shortened, flooding or repetition text unnatural or slanted?")
w("2. Are you comfortable with the topics and wording?")
w("3. Do the prompt excerpts say what you want the moderator to do (especially the neutrality rule and the style rules)?")
w("4. Anything in the rubrics you would define differently? (They are finalized with your human raters in step 11.)")

(ROOT / "docs/step3_review_pack.md").write_text("\n".join(out) + "\n")
print("written", len("\n".join(out)), "characters")
