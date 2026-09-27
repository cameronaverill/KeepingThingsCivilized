"""What the README says about accounts, the kill switch, secrets and the dev script; and what it must not claim."""
import re

import pytest

import readme_kit
from readme_kit import REPO_ROOT


@pytest.fixture(scope="module")
def scanner_module():
    import repo_helpers

    return repo_helpers.load_scanner()


# --- private data ---------------------------------------------------------------------------------------------

EMAIL_ADDRESS = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
# The owner's personal details, assembled from pieces so this test file does not itself spell them out.
OWNER_DETAILS = ["cam" + "eron", "aver" + "ill", "gma" + "il"]


def test_readme_contains_no_email_address(readme):
    assert EMAIL_ADDRESS.findall(readme.text) == []


def test_readme_contains_nothing_the_secret_scanner_flags(readme, scanner_module):
    assert scanner_module.find_secrets(readme.text, "README.md") == []


@pytest.mark.parametrize("detail", OWNER_DETAILS)
def test_readme_does_not_mention_the_owners_personal_details(readme, detail):
    assert detail not in readme.text.lower()


def test_readme_contains_no_home_directory_or_container_path(readme):
    assert readme_kit.lines_matching(readme.text, r"/home/\w+|/Users/\w+|/workspace\b") == []


# --- what it must say -----------------------------------------------------------------------------------------


NO_EMAIL = (
    r"(?is)(\bno e-?mail|without (an |any )?e-?mail|e-?mail (address )?(is |are )?(not|never) (needed|required|asked|collected)"
    r"|(does not|do not|doesn't|don't|never) (ask for|need|require|collect|store)[^.\n]{0,30}e-?mail)"
)
ABOUT_ACCOUNTS = r"(?i)account|regist|sign.?up|username"


def test_readme_says_accounts_need_no_email(readme):
    """The denial must be in a sentence about accounts / registration (not, say, the alert address being blank)."""
    about_accounts = readme_kit.sentences_matching(readme.text, ABOUT_ACCOUNTS)
    assert [s for s in about_accounts if re.search(NO_EMAIL, s)] != []


def test_readme_says_llm_enabled_is_off_by_default(readme):
    pattern = r"(?is)LLM_ENABLED[^\n]{0,200}?\b(off|false|disabled)\b|\b(off|false|disabled)\b[^\n]{0,200}?LLM_ENABLED"
    assert re.search(pattern, readme.text)


def test_every_sentence_giving_the_llm_enabled_default_says_off(readme):
    """Any sentence that mentions LLM_ENABLED and "default" must say off / False / disabled."""
    about_default = readme_kit.sentences_matching(readme.text, r"LLM_ENABLED[^\n]*default|default[^\n]*LLM_ENABLED")
    assert [s for s in about_default if not re.search(r"(?i)\b(off|false|disabled)\b", s)] == []


def test_no_sentence_calls_llm_enabled_true_by_default(readme):
    claims = readme_kit.sentences_matching(readme.text, r"LLM_ENABLED[^\n]{0,60}\b(True|on|enabled)\b[^\n]{0,40}default")
    assert [s for s in claims if not readme_kit.NEGATION.search(s)] == []


def test_readme_says_the_api_key_goes_in_dot_env(readme):
    assert re.search(r"(?is)ANTHROPIC_API_KEY[^\n]{0,200}\.env\b|\.env\b[^\n]{0,200}ANTHROPIC_API_KEY", readme.text)


def test_readme_says_dot_env_is_git_ignored_or_never_committed(readme):
    assert re.search(r"(?i)\.env[^\n]{0,120}(never committed|not committed|git-?ignored|ignored by git|not tracked|untracked)"
                     r"|(never commit|do not commit|don't commit|not commit)[^\n]{0,80}\.env\b", readme.text)


def test_readme_mentions_the_secret_scanner_hooks(readme):
    assert re.search(r"(?i)hook", readme.text)
    assert "scripts/install_hooks.sh" in readme.text


# --- what it must not tell the reader to do -------------------------------------------------------------------


def test_readme_never_tells_the_reader_to_commit_dot_env(readme):
    pattern = r"(?i)(\bcommit|git add|git push)[^.]*\.env\b(?!\.example)"
    assert readme_kit.sentences_matching_without_negation(readme.text, pattern) == []


def test_readme_never_uses_git_add_of_everything(readme):
    assert readme_kit.lines_matching(readme.text, r"git add (-A|--all|-f|--force|\.)(\s|$)") == []


TRACKED_FILES = r"(tunables\.py|settings\.py|\.env\.example|requirements[\w-]*\.txt|README|plan\.md|\.sh\b|\.html\b)"


def test_readme_never_tells_the_reader_to_put_a_key_in_a_tracked_file(readme):
    """A key sentence that names a tracked file right after "in/into/inside" must be a denial ("never in ...")."""
    pattern = r"(?i)(api[ _-]?key|ANTHROPIC_API_KEY|secret[ _-]?key)[^,;.\n]{0,40}\b(in|into|inside|to)\b[ `]{0,3}\S*" + TRACKED_FILES
    assert readme_kit.sentences_matching_without_negation(readme.text, pattern) == []


def test_readme_never_tells_the_reader_to_paste_a_key_into_code(readme):
    pattern = r"(?i)(paste|hard-?code|put|type)[^.]{0,40}\bkey\b[^.]{0,40}(into|in) (the )?(code|source|file|settings)"
    assert readme_kit.sentences_matching_without_negation(readme.text, pattern) == []


# --- claims about features that do not exist ------------------------------------------------------------------

# phrase (regex, case-insensitive) -> why it is wrong
BLOCKLIST = {
    r"email confirmation": "accounts have no email step (plan.md section 1, step 6c)",
    r"confirm(ation)? (of )?(your |the )?e-?mail": "accounts have no email step",
    r"confirmation e-?mail": "accounts have no email step",
    r"verif(y|ication)[^\n.]{0,20}e-?mail": "accounts have no email step",
    r"e-?mail verification": "accounts have no email step",
    r"password[ -]reset[^\n]{0,20}e-?mail": "no password reset by email",
    r"reset[^\n.]{0,30}password[^\n.]{0,30}(by|via|with|through) e-?mail": "no password reset by email",
    r"forgot(ten)? (your )?password": "no forgot-password flow",
    r"\bresend": "no resend-confirmation flow",
    r"MAX_OPEN_CONVERSATIONS\s*=\s*5\b": "MAX_OPEN_CONVERSATIONS is None (no limit)",
    r"sign[ -]?up with (google|github|facebook)|social login|oauth|single sign-on": "no third-party login",
}


@pytest.mark.parametrize("pattern", sorted(BLOCKLIST))
def test_readme_does_not_claim_removed_or_nonexistent_feature(readme, pattern):
    """A line that denies the feature ("there is no password reset by email") is fine; one that asserts it is not."""
    assert readme_kit.sentences_matching_without_negation(readme.text, "(?i)" + pattern) == [], BLOCKLIST[pattern]


# Where the AI moderator legitimately sees "Participant A": lines that talk about the moderator side of the system.
MODERATOR_SIDE = r"(?i)moderator|master|intervenor|\bmodel\b|transcript|prompt|\bLLM\b|\brater"


def test_participant_labels_are_only_described_as_what_the_moderator_sees(readme):
    """Users see usernames (plan.md, owner decision 2026-09-26); "Participant A/B" exist only in what the AI sees."""
    found = readme_kit.sentences_matching(readme.text, r"Participant [AB]\b|Participant A/B")
    offenders = [sentence for sentence in found if not re.search(MODERATOR_SIDE, sentence)]
    assert offenders == []


def test_the_blocked_tunable_value_really_is_not_five():
    from config import tunables

    assert tunables.MAX_OPEN_CONVERSATIONS is None


def test_the_removed_email_flow_is_really_gone_from_the_accounts_app():
    """Sanity check on the blocklist itself: the files the blocklist talks about no longer exist."""
    gone = ["accounts/emails.py", "accounts/tokens.py"]
    assert [p for p in gone if (REPO_ROOT / p).exists()] == []


# --- scripts/dev.sh -------------------------------------------------------------------------------------------


def test_readme_shows_how_to_run_scripts_dev_sh(readme):
    assert "scripts/dev.sh" in readme.text


def test_readme_notes_dev_sh_needs_bash_4_3_or_newer(readme):
    assert re.search(r"(?is)bash[^\n]{0,80}4\.3|4\.3[^\n]{0,80}bash", readme.text)


def test_dev_sh_really_needs_bash_4_3(readme):
    assert "bash 4.3+" in (REPO_ROOT / "scripts" / "dev.sh").read_text()


def test_readme_says_dev_sh_starts_both_the_web_server_and_the_worker(readme):
    assert re.search(r"(?is)dev\.sh[^\n]{0,300}(worker|run_moderator)|(worker|run_moderator)[^\n]{0,300}dev\.sh", readme.text)
