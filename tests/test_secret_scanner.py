"""scripts/check_secrets.py, in-process: rules, thresholds, placeholders, redaction, allow marker, filename rule.

(Step 1 fixes, section D.2. The command-line modes and the git hooks are tested in test_secret_cli_and_hooks.py.)
"""
import ast
import sys

import pytest

# Every secret is assembled at runtime from pieces, so this file itself contains no real-looking key.
ANTHROPIC = "sk-ant-" + "Ab1_-" * 8
GENERIC_SK = "sk-" + "A1b2C3d4" * 4
AWS = "AKIA" + "ABCDEF0123456789"
GITHUB_GHP = "ghp_" + "A1b2" * 9
GITHUB_PAT = "github_pat_" + "A1_b2" * 10
SLACK = "xoxb-" + "1234567890"
GOOGLE = "AIza" + "B" * 35
PEM = "-----BEGIN " + "RSA PRIVATE KEY-----"
TOKEN = "Zx9Qw7" * 4  # 24 alphanumerics, used as the value in assignments

BARE_SECRETS = {
    "anthropic": ANTHROPIC,
    "generic_sk": GENERIC_SK,
    "aws": AWS,
    "github_token": GITHUB_GHP,
    "github_pat": GITHUB_PAT,
    "slack": SLACK,
    "google": GOOGLE,
    "pem": PEM,
}


def scan(scanner, text, path="src/app.py"):
    return scanner.find_secrets(text, path)


# --- the shape of the module ---------------------------------------------------------------------------------------


def test_finding_has_path_line_rule_redacted(scanner):
    findings = scan(scanner, "one\ntwo\nkey is " + ANTHROPIC + "\n", "notes/a.txt")
    assert len(findings) == 1
    finding = findings[0]
    assert finding.path == "notes/a.txt"
    assert finding.line == 3  # 1-based
    assert isinstance(finding.rule, str) and finding.rule
    assert isinstance(finding.redacted, str) and finding.redacted


def test_scanner_uses_only_the_standard_library():
    # It must run on the minimal system Python, which has no third-party packages.
    from repo_helpers import SCANNER_PATH

    tree = ast.parse(SCANNER_PATH.read_text())
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules.add((node.module or "").split(".")[0])
    assert modules <= set(sys.stdlib_module_names), sorted(modules - set(sys.stdlib_module_names))


def test_scanner_source_does_not_trigger_itself(scanner):
    from repo_helpers import SCANNER_PATH

    assert scan(scanner, SCANNER_PATH.read_text(), "scripts/check_secrets.py") == []


# --- every rule fires ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(BARE_SECRETS))
def test_each_key_rule_fires_exactly_once_on_its_own_sample(scanner, kind):
    secret = BARE_SECRETS[kind]
    findings = scan(scanner, f"the value is {secret} for now\n")
    assert len(findings) == 1, findings
    assert findings[0].line == 1


def test_different_kinds_of_secret_are_reported_under_different_rule_names(scanner):
    names = {}
    for kind in ("anthropic", "generic_sk", "aws", "github_token", "slack", "google", "pem"):
        (finding,) = scan(scanner, f"the value is {BARE_SECRETS[kind]}\n")
        names[kind] = finding.rule
    assert len(set(names.values())) == len(names), names


@pytest.mark.parametrize("letter", list("pousr"))
def test_every_github_token_prefix_is_covered(scanner, letter):
    assert len(scan(scanner, f"token value gh{letter}_" + "A1b2" * 9)) == 1


@pytest.mark.parametrize("letter", list("abprs"))
def test_every_slack_token_prefix_is_covered(scanner, letter):
    assert len(scan(scanner, f"see xox{letter}-" + "1234567890abc")) == 1


@pytest.mark.parametrize(
    "header",
    ["RSA PRIVATE KEY", "PRIVATE KEY", "EC PRIVATE KEY", "OPENSSH PRIVATE KEY", "DSA PRIVATE KEY"],
)
def test_pem_private_key_headers_are_found(scanner, header):
    assert len(scan(scanner, "-----BEGIN " + header + "-----\nMIIB\n-----END " + header + "-----\n")) >= 1


def test_a_public_key_or_certificate_header_is_not_a_secret(scanner):
    assert scan(scanner, "-----BEGIN " + "PUBLIC KEY-----\nMIIB\n") == []
    assert scan(scanner, "-----BEGIN " + "CERTIFICATE-----\nMIIB\n") == []


# --- thresholds: one character short is not a match, the exact length is ----------------------------------------------


@pytest.mark.parametrize(
    "short, exact",
    [
        ("sk-ant-" + "a" * 19, "sk-ant-" + "a" * 20),
        ("sk-" + "a1" * 15 + "a", "sk-" + "a1" * 16),  # 31 versus 32
        ("AKIA" + "A" * 15, "AKIA" + "A" * 16),
        ("ghp_" + "a" * 35, "ghp_" + "a" * 36),
        ("github_pat_" + "a" * 49, "github_pat_" + "a" * 50),
        ("xoxb-" + "1" * 9, "xoxb-" + "1" * 10),
        ("AIza" + "a" * 34, "AIza" + "a" * 35),
    ],
    ids=["anthropic", "generic-sk", "aws", "github-token", "github-pat", "slack", "google"],
)
def test_length_thresholds(scanner, short, exact):
    assert scan(scanner, f"value {short} here") == []
    assert len(scan(scanner, f"value {exact} here")) == 1


def test_aws_key_must_be_uppercase_letters_and_digits(scanner):
    assert scan(scanner, "id " + "AKIA" + "a" * 16) == []


def test_ordinary_text_is_clean(scanner):
    text = "\n".join(
        [
            "def add(a, b):",
            "    return a + b  # sk- is a prefix, ghp_ another, AKIA yet another",
            "The AKIA prefix and xoxb- and AIza are only names here.",
            "url = 'https://example.com/path?x=1'",
            "print('password must be 12 characters or more')",
            "def get_token(request):",
            "token = None",
        ]
    )
    assert scan(scanner, text) == []


# --- generic assignments ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        'api_key = "{t}"',
        "api_key='{t}'",
        "API_KEY={t}",
        "ANTHROPIC_API_KEY={t}",
        "secret: {t}",
        "secret = {t}",
        "DJANGO_SECRET_KEY={t}",
        "token = '{t}'",
        "access_token: {t}",
        "passwd={t}",
        "password = \"{t}\"",
        "DB_PASSWORD: {t}",
    ],
)
def test_generic_assignment_of_a_long_value_is_a_finding(scanner, line):
    findings = scan(scanner, line.format(t=TOKEN))
    assert len(findings) >= 1, line


def test_generic_assignment_needs_at_least_20_characters(scanner):
    assert len(scan(scanner, "password = " + "Zx9Qw7Pl" * 2 + "Ab12")) == 1  # 20
    assert scan(scanner, "password = " + "Zx9Qw7Pl" * 2 + "Ab1") == []  # 19
    assert scan(scanner, "token: short") == []


@pytest.mark.parametrize(
    "line",
    [
        "client = Anthropic(api_key=settings.ANTHROPIC_API_KEY, max_retries=1)",
        "api_key=settings.ANTHROPIC_API_KEY",
        "password = self.cleaned_data.get_the_password_field_value",
        "secret = os.environ.get_the_secret_from_the_environment",
    ],
)
def test_code_that_reads_a_secret_from_a_variable_is_not_a_finding(scanner, line):
    # Step 2 onwards contains lines like this; a scanner that flags them would be switched off within a day.
    assert scan(scanner, line) == [], line


@pytest.mark.parametrize(
    "line",
    [
        "api_key=",
        'api_key = ""',
        "SECRET = ''",
        "ANTHROPIC_API_KEY=",
        "password = 'your-password-example-goes-here'",
        "token = 'changeme-changeme-changeme'",
        "secret: xxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "api_key = 'Example-Example-Example-1234'",  # 'example' in any case is a placeholder
        "password: CHANGEME_CHANGEME_CHANGEME",
        "password: enter your password here at the prompt",
        "# the token: a long explanation that keeps going on and on",
    ],
)
def test_placeholders_and_empty_values_are_not_findings(scanner, line):
    assert scan(scanner, line) == [], line


# --- redaction -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(BARE_SECRETS))
def test_redaction_never_contains_the_full_secret(scanner, kind):
    secret = BARE_SECRETS[kind]
    (finding,) = scan(scanner, f"the value is {secret}\n")
    assert secret not in finding.redacted
    assert secret not in repr(finding) and secret not in str(finding)
    assert finding.redacted.endswith("\u2026")
    assert len(finding.redacted) <= 7  # at most 6 characters, then the ellipsis
    assert secret.startswith(finding.redacted[:-1])


def test_redaction_of_a_generic_assignment_hides_the_value(scanner):
    (finding,) = scan(scanner, f'api_key = "{TOKEN}"')
    assert TOKEN not in finding.redacted and TOKEN not in repr(finding)
    assert finding.redacted.endswith("\u2026") and len(finding.redacted) <= 7


def test_redaction_of_a_short_match_still_ends_with_the_ellipsis(scanner):
    (finding,) = scan(scanner, "id " + AWS)
    assert finding.redacted == AWS[:6] + "\u2026"


# --- the allow marker ------------------------------------------------------------------------------------------------


def test_allow_marker_skips_only_its_own_line(scanner):
    text = f"a = '{ANTHROPIC}'  # secret-scan: allow\nb = 1\n"
    assert scan(scanner, text) == []
    text = f"a = '{ANTHROPIC}'  # secret-scan: allow\nb = '{ANTHROPIC}'\n"
    findings = scan(scanner, text)
    assert [f.line for f in findings] == [2]


def test_allow_marker_works_for_generic_assignments_and_other_comment_styles(scanner):
    assert scan(scanner, f'api_key = "{TOKEN}"  # secret-scan: allow') == []
    assert scan(scanner, f"secret: {TOKEN} <!-- secret-scan: allow -->") == []
    assert scan(scanner, f"KEY={AWS} # secret-scan: allow ") == []


def test_a_similar_looking_marker_does_not_disable_scanning(scanner):
    assert len(scan(scanner, f"a = '{ANTHROPIC}'  # scan: allow")) == 1
    assert len(scan(scanner, f"a = '{ANTHROPIC}'  # secret-scan: deny")) == 1


# --- several findings and positions ------------------------------------------------------------------------------------


def test_every_secret_in_a_file_is_reported_with_its_own_line_number(scanner):
    text = f"first\n{AWS}\nmiddle\nx = {GITHUB_GHP}\n\n{PEM}\n"
    findings = scan(scanner, text)
    assert sorted(f.line for f in findings) == [2, 4, 6]


def test_two_secrets_on_one_line_are_both_found(scanner):
    assert len(scan(scanner, f"{AWS} and {GOOGLE}")) == 2


def test_empty_and_blank_text_is_clean(scanner):
    assert scan(scanner, "") == []
    assert scan(scanner, "\n\n   \n") == []


# --- the filename rule --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [".env", "sub/dir/.env", ".env.local", ".env.production", "config/.env.staging", "id_rsa.pem", "keys/server.key", "x.PEM"],
)
def test_secret_looking_file_names_are_findings_even_when_the_content_is_harmless(scanner, path):
    findings = scan(scanner, "FOO=bar\n", path)
    assert len(findings) >= 1, path
    assert findings[0].path == path


@pytest.mark.parametrize(
    "path",
    [".env.example", "deploy/.env.example", "environment.py", ".environment", "env", "keys.py", "monkeypatch.py", "notes.pem.txt", "README.md"],
)
def test_ordinary_file_names_are_not_findings(scanner, path):
    assert scan(scanner, "FOO=bar\n", path) == [], path


def test_env_example_with_empty_values_is_clean(scanner):
    text = "ANTHROPIC_API_KEY=\nDJANGO_SECRET_KEY=\nDJANGO_ENV=development\n"
    assert scan(scanner, text, ".env.example") == []


def test_a_real_secret_inside_env_example_is_still_found(scanner):
    assert len(scan(scanner, f"ANTHROPIC_API_KEY={ANTHROPIC}\n", ".env.example")) >= 1
