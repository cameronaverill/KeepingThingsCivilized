#!/usr/bin/env python3
"""Scan for API keys and other secrets before they reach git or GitHub. Standard library only.

    check_secrets.py --staged    the staged content (git index); used by the pre-commit hook
    check_secrets.py --all       every tracked file (git ls-files)
    check_secrets.py --history   every line ever added on any branch (git log --all -p); used by the pre-push hook

Exit code 0 when clean, 1 when something was found, 2 when git could not be run. Findings are printed as
`file:line rule redacted`; a secret is never printed in full (at most 6 characters, then an ellipsis).

A line containing the text "secret-scan: allow" (inside any comment style) is skipped: use it for test fixtures.
"""
import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field

ALLOW_MARKER = "secret-scan: allow"

# --- content rules: (name, compiled pattern). A match's text (or group "value" when present) is what gets redacted. ---
_START = r"(?<![A-Za-z0-9])"  # keep the prefixes from matching in the middle of a longer word

_KEY_RULES = [
    ("anthropic-api-key", re.compile(_START + r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("generic-sk-key", re.compile(_START + r"sk-[A-Za-z0-9]{32,}")),
    ("aws-access-key-id", re.compile(_START + r"AKIA[0-9A-Z]{16}")),
    ("github-token", re.compile(_START + r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("github-fine-grained-token", re.compile(_START + r"github_pat_[A-Za-z0-9_]{50,}")),
    ("slack-token", re.compile(_START + r"xox[abprs]-[A-Za-z0-9-]{10,}")),
    ("google-api-key", re.compile(_START + r"AIza[0-9A-Za-z_-]{35}")),
    ("openai-project-key", re.compile(_START + r"sk-(?:proj|svcacct|admin)-[A-Za-z0-9_-]{20,}")),
    ("stripe-live-secret", re.compile(_START + r"(?:sk|rk)_live_[A-Za-z0-9]{16,}")),
    ("npm-auth-token", re.compile(r"_authToken\s{0,5}=\s{0,5}(?P<value>[A-Za-z0-9._~+/=-]{10,})")),
    ("private-key-block", re.compile(r"-----BEGIN (?:[A-Z0-9]+ ){0,4}PRIVATE KEY-----")),
]

# Rules whose match is skipped when it looks like a template or a placeholder: (name, pattern). "value" is the credential.
_TEMPLATE_CHARS = ("$", "{", "%(")
_CREDENTIAL_RULES = [
    # a URL with a user name and password in front of the host name
    ("url-credentials", re.compile(r"://[^\s:/@]{1,100}:(?P<value>[^\s@/]{8,256})@")),
    ("bearer-token", re.compile(r"(?i)\bBearer[ \t]{1,10}(?P<value>[A-Za-z0-9._~+/=-]{20,})")),
]

# api_key = "...", SECRET: ..., DB_PASSWORD=... : a keyword, then = or :, then a 20+ character token.
# The token may not be followed by "." or "(" (that is code reading a value, such as settings.API_KEY or make_key()).
_ASSIGNMENT = re.compile(
    r"(?i)(?:api[_-]?key|secret|token|passwd|password)\w{0,40}[\"']?\s*[=:]\s*(?P<quote>[\"']?)"
    r"(?P<value>[A-Za-z0-9_\-+/]{20,})(?![\w\-+/.(\[])"
)
_PLACEHOLDER_WORDS = ("example", "changeme", "xxxx")
_VARIABLE_NAME = re.compile(r"[a-z][a-z_]*")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    redacted: str
    commit: str = field(default="", compare=False)  # only set by --history

    def __str__(self):
        where = f"{self.path}:{self.line} {self.rule} {self.redacted}"
        return f"{where} (commit {self.commit})" if self.commit else where


def redact(secret: str) -> str:
    """At most the first 6 characters, then an ellipsis. Never the whole secret."""
    return secret[:6] + "…"


def is_python_source(path: str) -> bool:
    return path.lower().endswith(".py")


def _has_placeholder_word(value: str) -> bool:
    return any(word in value.lower() for word in _PLACEHOLDER_WORDS)


def _looks_like_placeholder(value: str, quoted: bool, python_source: bool) -> bool:
    if _has_placeholder_word(value):
        return True
    # In Python source only, an unquoted lowercase_name is code passing a variable along (cache_tokens=cache_tokens).
    # In any other file type (a config file, say) password=correcthorsebatterystaple is a real secret.
    return python_source and not quoted and _VARIABLE_NAME.fullmatch(value) is not None


def _scan_line(line: str, python_source: bool = False) -> list[tuple[str, str]]:
    """(rule, secret text) for everything suspicious on one line."""
    if ALLOW_MARKER in line:
        return []
    found = []
    for name, pattern in _KEY_RULES:
        group = "value" if "value" in pattern.groupindex else 0
        found.extend((name, match.group(group)) for match in pattern.finditer(line))
    for name, pattern in _CREDENTIAL_RULES:
        for match in pattern.finditer(line):
            value = match.group("value")
            if not any(mark in value for mark in _TEMPLATE_CHARS) and not _has_placeholder_word(value):
                found.append((name, value))
    for match in _ASSIGNMENT.finditer(line):
        value = match.group("value")
        already_reported = any(value in secret or secret in value for _, secret in found)
        if not already_reported and not _looks_like_placeholder(value, bool(match.group("quote")), python_source):
            found.append(("secret-assignment", value))
    return found


_SECRET_FILE_NAMES = {".netrc", "_netrc", ".pgpass", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}
_SECRET_FILE_EXTENSIONS = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore")


def is_secret_file_name(path: str) -> bool:
    lowered = path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if lowered == ".env.example":
        return False
    return (
        lowered == ".env"
        or lowered.startswith(".env.")
        or lowered in _SECRET_FILE_NAMES
        or lowered.endswith(_SECRET_FILE_EXTENSIONS)
    )


def find_secrets(text: str, path: str) -> list[Finding]:
    """Everything in `text` (the content of the file called `path`) that looks like a secret."""
    findings = []
    if is_secret_file_name(path):
        findings.append(Finding(path, 1, "secret-file-name", "(file name)"))
    python_source = is_python_source(path)
    for number, line in enumerate(text.splitlines(), start=1):
        for rule, secret in _scan_line(line, python_source):
            findings.append(Finding(path, number, rule, redact(secret)))
    return findings


# --- git access ----------------------------------------------------------------------------------------------------


class GitError(Exception):
    pass


def _git(*args: str) -> bytes:
    try:
        result = subprocess.run(["git", *args], capture_output=True)
    except FileNotFoundError as error:
        raise GitError("git is not installed") from error
    if result.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def _decode(data: bytes) -> str | None:
    """Text of a file, or None for binary content."""
    if b"\0" in data:
        return None
    return data.decode("utf-8", errors="replace")


def _split_z(data: bytes) -> list[str]:
    return [item.decode("utf-8", errors="replace") for item in data.split(b"\0") if item]


def scan_staged() -> list[Finding]:
    _git("rev-parse", "--git-dir")  # outside a repository, `git diff --cached` would quietly do something else
    findings = []
    names = _split_z(_git("-c", "core.quotepath=off", "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR"))
    for name in names:
        text = _decode(_git("show", f":{name}"))
        findings.extend(find_secrets(text or "", name))
    return findings


def scan_all() -> list[Finding]:
    findings = []
    top = _git("rev-parse", "--show-toplevel").decode().strip()
    for name in _split_z(_git("ls-files", "-z", "--full-name")):
        full = os.path.join(top, name)
        text = ""
        if os.path.isfile(full):
            with open(full, "rb") as handle:
                text = _decode(handle.read()) or ""
        findings.extend(find_secrets(text, name))
    return findings


_COMMIT_LINE = re.compile(r"commit [0-9a-f]{40}")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _diff_path(raw: str) -> str:
    """The path from a `+++ b/path` line (git quotes unusual names)."""
    if raw.endswith("\t"):  # git appends a TAB after names that contain a space
        raw = raw[:-1]
    if raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1]
    return raw[2:] if raw.startswith("b/") else raw


def scan_history() -> list[Finding]:
    findings = []
    seen = set()

    def add(finding):
        key = (finding.path, finding.line, finding.rule, finding.redacted, finding.commit)
        if key not in seen:
            seen.add(key)
            findings.append(finding)

    # Names of files that were ever added or changed, for the file name rule.
    listing = _git("-c", "core.quotepath=off", "log", "--all", "-m", "--diff-filter=ACMR", "--name-only", "--format=", "-z")
    for name in sorted(set(_split_z(listing))):
        if is_secret_file_name(name):
            add(Finding(name, 1, "secret-file-name", "(file name)"))

    process = subprocess.Popen(
        ["git", "-c", "core.quotepath=off", "log", "--all", "-m", "-p", "-U0", "--no-color", "--no-ext-diff",
         "--no-textconv", "--format=commit %H"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    commit, path, number, in_header = "", "", 0, False
    for raw in process.stdout:
        line = raw.decode("utf-8", errors="replace").rstrip("\n")
        if _COMMIT_LINE.fullmatch(line):
            commit, path, in_header = line[7:15], "", False
        elif line.startswith("diff --git "):
            path, in_header = "", True
        elif in_header:
            if line.startswith("+++ "):
                path = "" if line[4:] == "/dev/null" else _diff_path(line[4:])
            elif line.startswith("@@"):
                in_header = False
        if not in_header and line.startswith("@@"):
            match = _HUNK.match(line)
            number = int(match.group(1)) if match else 0
        elif not in_header and line.startswith("+") and path:
            for rule, secret in _scan_line(line[1:], is_python_source(path)):
                add(Finding(path, number, rule, redact(secret), commit))
            number += 1
    process.stdout.close()
    error = process.stderr.read().decode(errors="replace").strip()
    if process.wait() != 0:
        raise GitError(f"git log failed: {error}")
    return findings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Find API keys and other secrets before they reach GitHub.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true", help="scan the staged content (pre-commit)")
    mode.add_argument("--all", action="store_true", help="scan every tracked file")
    mode.add_argument("--history", action="store_true", help="scan every line ever added, on all branches (pre-push)")
    args = parser.parse_args(argv)

    try:
        findings = scan_staged() if args.staged else scan_all() if args.all else scan_history()
    except GitError as error:
        print(f"check_secrets: {error}", file=sys.stderr)
        return 2
    if not findings:
        return 0
    print(f"check_secrets: {len(findings)} possible secret(s) found:", file=sys.stderr)
    for finding in findings:
        print(f"  {finding}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
