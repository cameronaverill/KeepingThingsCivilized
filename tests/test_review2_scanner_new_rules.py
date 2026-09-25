"""Architect's extended scanner rules: OpenAI and Stripe keys, URL credentials, bearer tokens, npm auth tokens, more
secret file names, and secrets that exist only in a merge commit.

Every secret-looking literal is assembled at runtime. The specific key rules have no placeholder exemption.
"""
import pytest

# --- fake secrets ------------------------------------------------------------------------------------------------------
BODY = "Ab1_-" * 5  # 25 characters from [A-Za-z0-9_-]
OPENAI = {kind: "sk-" + kind + "-" + BODY for kind in ("proj", "svcacct", "admin")}
STRIPE_SK = "sk_" + "live_" + "A1b2C3d4E5f6G7h8"  # 16 alphanumerics after the prefix
STRIPE_RK = "rk_" + "live_" + "Z9y8X7w6V5u4T3s2R1"
URL_PASSWORD = "Tr0ub4dor" + "3xyz"
URL = "postgres://appuser:" + URL_PASSWORD + "@db.internal:5432/app"
BEARER_VALUE = "abcDEF123._~+/=-" + "ghiJKL456mno"
BEARER = "Bearer " + BEARER_VALUE
NPM_VALUE = "Ab1Cd2Ef3Gh"
NPM = "//registry.npmjs.org/:_auth" + "Token=" + NPM_VALUE
SECRET_FILE_NAMES = [".netrc", "_netrc", ".pgpass", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "cert.p12", "cert.pfx", "release.jks", "app.keystore"]


def scan(scanner, text, path="notes.txt"):
    return scanner.find_secrets(text, path)


def rules(findings):
    return [f.rule for f in findings]


# --- OpenAI-style project keys ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["proj", "svcacct", "admin"])
def test_openai_project_keys_are_findings_under_their_own_rule(scanner, kind):
    findings = scan(scanner, f"the key is {OPENAI[kind]} for now")
    assert rules(findings) == ["openai-project-key"], findings


@pytest.mark.parametrize("path", ["notes.txt", "config.yaml", "app.py", "Makefile"])
def test_openai_keys_have_no_placeholder_or_python_exemption(scanner, path):
    for body in ("xxxx" * 6, "example" + "a" * 20, "changeme" + "b" * 20, "a" * 25):
        text = "sk-" + "proj-" + body
        assert rules(scan(scanner, f"x = {text}", path)) == ["openai-project-key"], (path, body)


def test_openai_key_length_threshold_is_20_characters_after_the_prefix(scanner):
    assert scan(scanner, "sk-" + "proj-" + "a" * 19) == []
    assert rules(scan(scanner, "sk-" + "proj-" + "a" * 20)) == ["openai-project-key"]
    assert scan(scanner, "sk-" + "other-" + "a" * 30) == []  # only proj, svcacct and admin are covered by this rule


# --- Stripe live keys ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", [STRIPE_SK, STRIPE_RK], ids=["sk_live", "rk_live"])
def test_stripe_live_secret_keys_are_findings(scanner, key):
    assert rules(scan(scanner, f"STRIPE = '{key}'", "app.py")) == ["stripe-live-secret"]


def test_stripe_keys_have_no_placeholder_exemption_and_a_16_character_minimum(scanner):
    assert rules(scan(scanner, "sk_" + "live_" + "x" * 16)) == ["stripe-live-secret"]
    assert rules(scan(scanner, "rk_" + "live_" + "example" + "A" * 10)) == ["stripe-live-secret"]
    assert scan(scanner, "sk_" + "live_" + "A" * 15) == []


def test_stripe_test_and_publishable_keys_are_not_secrets(scanner):
    assert scan(scanner, "sk_" + "test_" + "A1b2C3d4E5f6G7h8I9") == []
    assert scan(scanner, "pk_" + "live_" + "A1b2C3d4E5f6G7h8I9") == []


# --- URL credentials -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        URL,
        "https://admin:" + URL_PASSWORD + "@example-host.internal/path",
        "mysql://root:" + "Sup3rS3cret!" + "@10.0.0.5/db",
        "ftp://deploy:" + "abcdefgh" + "@files.internal",  # exactly 8 characters
    ],
)
def test_credentials_embedded_in_a_url_are_findings(scanner, url):
    for path in ("notes.txt", "settings.py", "docker-compose.yml"):
        found = scan(scanner, f'DATABASE_URL = "{url}"' if path == "settings.py" else f"url: {url}", path)
        assert "url-credentials" in rules(found), (path, url, found)


@pytest.mark.parametrize(
    "line",
    [
        "postgres://user:" + "${DB_PASSWORD}" + "@host/db",
        "postgres://user:" + "$DB_PASSWORD_LONG" + "@host/db",
        "postgres://user:" + "{password_value}" + "@host/db",
        "postgres://user:" + "%(db_password)s" + "@host/db",
        "postgres://user:" + "{{ vault_db_password }}" + "@host/db",
        "postgres://user:short12@host/db",  # 7 characters
        "postgres://user:" + "changeme" + "-please@host/db",
        "postgres://user:" + "example" + "Password1@host/db",
        "postgres://user:" + "xxxx" + "xxxxxxxx@host/db",
        "https://example.com/path",
        "https://example.com:8080/path?next=a@b",
        "git@github.com:org/repo.git",
        "ssh://git@github.com/org/repo.git",
        "https://user@host/path",
        "mail user@host about it",
        "http://localhost:8000/accounts/login/",
    ],
)
def test_urls_without_a_real_embedded_password_are_not_findings(scanner, line):
    assert "url-credentials" not in rules(scan(scanner, line)), line


def test_url_credentials_have_no_python_exemption_for_lowercase_words(scanner):
    line = 'URL = "postgres://user:' + "correcthorsebattery" + '@host/db"'
    assert "url-credentials" in rules(scan(scanner, line, "settings.py"))


# --- bearer tokens ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["notes.txt", "script.sh", "README.md", "client.py"])
def test_a_bearer_token_is_a_finding(scanner, path):
    line = f'curl -H "Authorization: {BEARER}" https://api.internal/x' if path != "client.py" else f'H = "Authorization: {BEARER}"'
    assert "bearer-token" in rules(scan(scanner, line, path)), path


def test_bearer_token_needs_20_characters_from_the_token_alphabet(scanner):
    assert rules(scan(scanner, "Bearer " + "a" * 20)) == ["bearer-token"]
    assert scan(scanner, "Bearer " + "a" * 19) == []
    assert scan(scanner, "Bearer abc") == []


@pytest.mark.parametrize(
    "line",
    [
        "Authorization: Bearer ${TOKEN}",
        "Authorization: Bearer ${API_TOKEN_FOR_THE_SERVICE_HERE}",
        "Authorization: Bearer {token}",
        "headers = {'Authorization': f'Bearer {access_token_value_here}'}",
        "Authorization: Bearer <your-access-token-here>",
        "Authorization: Bearer $TOKEN",
        "the Bearer scheme is described in RFC 6750",
    ],
)
def test_bearer_templates_and_short_values_are_not_findings(scanner, line):
    assert "bearer-token" not in rules(scan(scanner, line)), line


# --- npm auth tokens ---------------------------------------------------------------------------------------------------------


def test_an_npm_auth_token_is_a_finding(scanner):
    assert scan(scanner, NPM, ".npmrc") != []
    assert scan(scanner, NPM, "config.txt") != []
    assert scan(scanner, "_auth" + "Token=" + NPM_VALUE, "x.ini") != []


def test_npm_auth_token_needs_10_characters(scanner):
    assert scan(scanner, "//registry.npmjs.org/:_auth" + "Token=" + "A" * 9, ".npmrc") == []
    assert scan(scanner, "//registry.npmjs.org/:_auth" + "Token=" + "A" * 10, ".npmrc") != []


# --- redaction and the allow marker for every new rule ----------------------------------------------------------------------

NEW_SAMPLES = {
    "openai": (OPENAI["proj"], f"the key is {OPENAI['proj']}"),
    "openai-svcacct": (OPENAI["svcacct"], f"the key is {OPENAI['svcacct']}"),
    "stripe-sk": (STRIPE_SK, f"the key is {STRIPE_SK}"),
    "stripe-rk": (STRIPE_RK, f"the key is {STRIPE_RK}"),
    "url": (URL_PASSWORD, f"url: {URL}"),
    "bearer": (BEARER_VALUE, f"Authorization: {BEARER}"),
    "npm": (NPM_VALUE, NPM),
}


@pytest.mark.parametrize("name", sorted(NEW_SAMPLES))
def test_redaction_never_shows_a_new_rules_secret_in_full(scanner, name):
    secret, line = NEW_SAMPLES[name]
    findings = scan(scanner, line, "notes.txt")
    assert findings, name
    for finding in findings:
        for text in (finding.redacted, repr(finding), str(finding)):
            assert secret not in text, (name, text)
        assert finding.redacted.endswith("…") and len(finding.redacted) <= 7
    whole_line_secret = line.split("url: ")[-1]
    assert all(whole_line_secret not in f.redacted for f in findings)


@pytest.mark.parametrize("name", sorted(NEW_SAMPLES))
def test_the_allow_marker_silences_every_new_rule(scanner, name):
    secret, line = NEW_SAMPLES[name]
    assert scan(scanner, line + "  # secret-scan: allow") == []
    assert len(scan(scanner, line + "  # scan: allow")) >= 1


@pytest.mark.parametrize("name", sorted(NEW_SAMPLES))
def test_the_cli_never_prints_a_new_rules_secret_in_full(repo, name):
    secret, line = NEW_SAMPLES[name]
    repo.write("notes.txt", line + "\n")
    repo.add("notes.txt")
    for mode in ("--staged",):
        result = repo.run_scanner(mode)
        assert result.returncode == 1, (name, result.stdout + result.stderr)
        assert secret not in result.stdout + result.stderr
        assert "notes.txt:1" in result.stdout + result.stderr
    repo.commit_plain()
    for mode in ("--all", "--history"):
        result = repo.run_scanner(mode)
        assert result.returncode == 1, (name, mode)
        assert secret not in result.stdout + result.stderr


# --- new secret file names -------------------------------------------------------------------------------------------------


def variants(name):
    yield name
    yield name.upper()
    yield "home/user/.ssh/" + name
    yield "deep/dir/" + name.title()


@pytest.mark.parametrize("name", SECRET_FILE_NAMES)
def test_secret_file_names_are_findings_in_any_letter_case_and_any_folder(scanner, name):
    for path in variants(name):
        found = scan(scanner, "harmless=1\n", path)
        assert found, path
        assert found[0].path == path


@pytest.mark.parametrize(
    "path",
    ["id_rsa.pub", "id_ed25519.pub", "netrc.py", "pgpass.md", "my_p12_notes.txt", "keystore.md", "jks", "p12", "id_rsa_notes.txt", "notes.netrc.txt"],
)
def test_lookalike_file_names_are_not_findings(scanner, path):
    assert scan(scanner, "harmless=1\n", path) == [], path


@pytest.mark.parametrize("name", SECRET_FILE_NAMES)
def test_new_secret_file_names_are_blocked_when_staged(repo, name):
    repo.write(name.upper(), "harmless=1\n")
    repo.add(name.upper())
    result = repo.run_scanner("--staged")
    assert result.returncode == 1, result.stdout + result.stderr
    assert name.upper() in result.stdout + result.stderr


@pytest.mark.parametrize("name", SECRET_FILE_NAMES)
def test_new_secret_file_names_are_found_by_all_and_by_history(repo, name):
    repo.write("keys/" + name, "harmless=1\n")
    repo.add("keys/" + name)
    repo.commit_plain("oops")
    assert repo.run_scanner("--all").returncode == 1
    repo.git("rm", "-q", "-f", "keys/" + name)
    repo.commit_plain("removed again")
    assert repo.run_scanner("--all").returncode == 0
    history = repo.run_scanner("--history")
    assert history.returncode == 1 and name in history.stdout + history.stderr


def test_a_binary_keystore_is_blocked_by_name_alone(repo):
    repo.write("release.jks", b"\xfe\xed\xfe\xed\x00\x00\x00\x02" + bytes(range(256)))
    repo.add("release.jks")
    assert repo.run_scanner("--staged").returncode == 1


# --- a secret that exists only in a merge commit -----------------------------------------------------------------------------

FAKE_KEY = "sk-ant-" + "Ab1_-" * 8


def conflicted_merge(repo, resolution):
    repo.write("config.txt", "value = base\n")
    repo.add("config.txt")
    repo.commit_plain("base")
    repo.git("checkout", "-q", "-b", "side")
    repo.write("config.txt", "value = side\n")
    repo.git("commit", "-q", "--no-verify", "-a", "-m", "side")
    repo.git("checkout", "-q", "main")
    repo.write("config.txt", "value = main\n")
    repo.git("commit", "-q", "--no-verify", "-a", "-m", "main")
    merge = repo.git("merge", "side", check=False)
    assert merge.returncode != 0, "the merge was expected to conflict"
    repo.write("config.txt", resolution)
    repo.add("config.txt")
    repo.git("commit", "-q", "--no-verify", "-m", "merge side, resolving the conflict")  # hooks bypassed on purpose


def test_history_finds_a_secret_that_exists_only_in_a_merge_resolution(repo):
    conflicted_merge(repo, f"value = {FAKE_KEY}\n")
    assert repo.git("rev-list", "--merges", "--count", "HEAD").stdout.strip() == "1"
    result = repo.run_scanner("--history")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "config.txt" in result.stdout + result.stderr
    assert FAKE_KEY not in result.stdout + result.stderr


def test_history_finds_a_secret_added_by_an_evil_merge_without_conflict(repo):
    repo.write("a.txt", "a\n")
    repo.add("a.txt")
    repo.commit_plain("a")
    repo.git("checkout", "-q", "-b", "side")
    repo.write("b.txt", "b\n")
    repo.add("b.txt")
    repo.commit_plain("b")
    repo.git("checkout", "-q", "main")
    repo.git("merge", "--no-commit", "--no-ff", "side")
    repo.write("c.txt", f"key = {FAKE_KEY}\n")  # a brand-new file that exists only in the merge commit
    repo.add("c.txt")
    repo.git("commit", "-q", "--no-verify", "-m", "merge with an extra file")
    result = repo.run_scanner("--history")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "c.txt" in result.stdout + result.stderr


def test_a_clean_merge_does_not_upset_history_scanning(repo):
    repo.write("a.txt", "a\n")
    repo.add("a.txt")
    repo.commit_plain("a")
    repo.git("checkout", "-q", "-b", "side")
    repo.write("b.txt", "b\n")
    repo.add("b.txt")
    repo.commit_plain("b")
    repo.git("checkout", "-q", "main")
    repo.write("c.txt", "c\n")
    repo.add("c.txt")
    repo.commit_plain("c")
    repo.git("merge", "--no-ff", "-m", "clean merge", "side")
    assert repo.run_scanner("--history").returncode == 0
