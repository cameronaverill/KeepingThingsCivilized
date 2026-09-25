"""scripts/check_secrets.py command line, the git hooks and scripts/install_hooks.sh (step 1 fixes, sections D.2-D.4).

Everything runs in throwaway git repositories under pytest's tmp path; the real /workspace repository is never
modified. Fake secrets are built at runtime.
"""
import os
import shutil
import stat
import subprocess
import sys

import pytest

from repo_helpers import REPO_ROOT, SCANNER_PATH, GitRepo, git_env

FAKE_KEY = "sk-ant-" + "Ab1_-" * 8
FAKE_AWS = "AKIA" + "ABCDEF0123456789"
LEAKY = f'client = Anthropic(api_key="{FAKE_KEY}")\n'
CLEAN = "def add(a, b):\n    return a + b\n"


def output(result):
    return result.stdout + result.stderr


def assert_no_full_secret(result, *secrets):
    text = output(result)
    for secret in secrets or (FAKE_KEY,):
        assert secret not in text, "the full secret was printed"


# =====================================================================================================================
# command line
# =====================================================================================================================


def test_staged_mode_flags_a_staged_secret_with_file_line_rule_and_redaction(repo):
    repo.write("app/client.py", "import anthropic\n\n" + LEAKY)
    repo.add("app/client.py")
    result = repo.run_scanner("--staged")
    assert result.returncode == 1, output(result)
    assert "app/client.py:3" in output(result)  # file:line
    assert FAKE_KEY[:6] + "\u2026" in output(result)  # the redacted snippet
    assert_no_full_secret(result)


def test_staged_mode_is_clean_for_clean_files(repo):
    repo.write("app/util.py", CLEAN)
    repo.add("app/util.py")
    result = repo.run_scanner("--staged")
    assert result.returncode == 0, output(result)


def test_staged_mode_with_nothing_staged_is_clean(repo):
    assert repo.run_scanner("--staged").returncode == 0


def test_staged_mode_reads_the_index_not_the_working_tree(repo):
    repo.write("a.py", LEAKY)
    repo.add("a.py")
    repo.write("a.py", CLEAN)  # the working copy is cleaned up, but the staged content still holds the key
    assert repo.run_scanner("--staged").returncode == 1

    repo.git("reset", "-q")
    repo.write("b.py", CLEAN)
    repo.add("b.py")
    repo.write("b.py", LEAKY)  # a leaky edit that is NOT staged must not block this commit
    result = repo.run_scanner("--staged")
    assert result.returncode == 0, output(result)


def test_staged_mode_ignores_unstaged_and_untracked_files(repo):
    repo.write("clean.py", CLEAN)
    repo.add("clean.py")
    repo.write("untracked.py", LEAKY)
    assert repo.run_scanner("--staged").returncode == 0


def test_staged_mode_flags_a_staged_env_file_by_name(repo):
    repo.write(".env", "SOMETHING=harmless\n")
    repo.add(".env")
    result = repo.run_scanner("--staged")
    assert result.returncode == 1
    assert ".env" in output(result)


def test_staged_env_example_is_allowed(repo):
    repo.write(".env.example", "ANTHROPIC_API_KEY=\nDJANGO_SECRET_KEY=\n")
    repo.add(".env.example")
    assert repo.run_scanner("--staged").returncode == 0


def test_staged_mode_copes_with_odd_file_names(repo):
    repo.write("my notes/caf\u00e9 file.txt", LEAKY)
    repo.add("my notes/caf\u00e9 file.txt")
    result = repo.run_scanner("--staged")
    assert result.returncode == 1, output(result)
    assert "file.txt" in output(result)


def test_staged_mode_survives_binary_and_non_utf8_files(repo):
    repo.write("image.bin", bytes(range(256)) * 20)
    repo.write("latin1.txt", "caf\xe9 au lait\n".encode("latin-1"))
    repo.add("image.bin", "latin1.txt")
    result = repo.run_scanner("--staged")
    assert result.returncode == 0, output(result)
    assert "Traceback" not in output(result)


def test_staged_mode_allow_marker_is_honoured(repo):
    repo.write("fixture.py", f'KEY = "{FAKE_KEY}"  # secret-scan: allow\n')
    repo.add("fixture.py")
    assert repo.run_scanner("--staged").returncode == 0


def test_all_mode_scans_tracked_files_only(repo):
    repo.write("ok.py", CLEAN)
    repo.write("leaky.py", LEAKY)
    repo.add("ok.py", "leaky.py")
    repo.commit_plain()
    result = repo.run_scanner("--all")
    assert result.returncode == 1
    assert "leaky.py:1" in output(result)
    assert "ok.py:" not in output(result).replace("leaky.py", "")
    assert_no_full_secret(result)

    repo.git("rm", "-q", "--cached", "leaky.py")  # now untracked (the file is still on disk)
    assert repo.run_scanner("--all").returncode == 0


def test_all_mode_is_clean_for_a_clean_repository(repo):
    repo.write("ok.py", CLEAN)
    repo.add("ok.py")
    repo.commit_plain()
    assert repo.run_scanner("--all").returncode == 0


def test_all_mode_flags_a_tracked_env_file(repo):
    repo.write(".env", "X=1\n")
    repo.add(".env")
    repo.commit_plain()
    assert repo.run_scanner("--all").returncode == 1


def test_history_mode_finds_a_secret_that_was_added_and_later_deleted(repo):
    repo.write("config.py", LEAKY)
    repo.add("config.py")
    repo.commit_plain("oops")
    repo.write("config.py", CLEAN)
    repo.add("config.py")
    repo.commit_plain("remove the key")
    assert repo.run_scanner("--all").returncode == 0  # the current tree is clean...
    result = repo.run_scanner("--history")  # ...but git still remembers the key
    assert result.returncode == 1, output(result)
    assert FAKE_KEY[:6] + "\u2026" in output(result)
    assert "config.py" in output(result)
    assert_no_full_secret(result)


def test_history_mode_looks_at_every_branch(repo):
    repo.write("base.py", CLEAN)
    repo.add("base.py")
    repo.commit_plain("base")
    repo.git("checkout", "-q", "-b", "side")
    repo.write("side.py", LEAKY)
    repo.add("side.py")
    repo.commit_plain("side work")
    repo.git("checkout", "-q", "main")
    result = repo.run_scanner("--history")
    assert result.returncode == 1, output(result)
    assert "side.py" in output(result)


def test_history_mode_is_clean_for_clean_history(repo):
    repo.write("a.py", CLEAN)
    repo.add("a.py")
    repo.commit_plain()
    repo.write("a.py", CLEAN + "\n# more\n")
    repo.add("a.py")
    repo.commit_plain("second")
    assert repo.run_scanner("--history").returncode == 0


def test_history_mode_honours_the_allow_marker(repo):
    repo.write("a.py", f'KEY = "{FAKE_KEY}"  # secret-scan: allow\n')
    repo.add("a.py")
    repo.commit_plain("allowed fixture")
    repo.write("a.py", CLEAN)
    repo.add("a.py")
    repo.commit_plain("remove it")
    assert repo.run_scanner("--history").returncode == 0


def test_a_mode_is_required(repo):
    result = repo.run_scanner()
    assert result.returncode != 0
    assert "Traceback" not in output(result)


# =====================================================================================================================
# the hooks
# =====================================================================================================================

HOOKS = REPO_ROOT / ".githooks"


@pytest.mark.parametrize("name, mode", [("pre-commit", "--staged"), ("pre-push", "--history")])
def test_hook_files_exist_are_executable_and_call_the_scanner(name, mode):
    hook = HOOKS / name
    assert hook.is_file(), f".githooks/{name} is missing"
    assert hook.stat().st_mode & stat.S_IXUSR, f".githooks/{name} is not executable"
    text = hook.read_text()
    assert text.startswith("#!"), "needs a shebang line"
    assert "check_secrets.py" in text and mode in text
    assert ".venv/bin/python" in text and "python3" in text  # prefers the venv, falls back to the system Python


@pytest.mark.parametrize("name", ["pre-commit", "pre-push"])
def test_hook_messages_tell_the_user_to_revoke_the_key(name):
    text = (HOOKS / name).read_text().lower()
    assert "revoke" in text
    assert "history" in text  # deleting a key later does not remove it from git history


@pytest.mark.skipif(shutil.which("python3") is None, reason="needs python3 on PATH for the hook's fallback")
class TestHooksInAThrowawayRepo:
    def protected(self, repo):
        repo.install_protection()
        return repo

    def test_commit_with_a_key_is_blocked(self, repo):
        self.protected(repo)
        repo.write("settings_local.py", LEAKY)
        repo.add("settings_local.py")
        result = repo.git("commit", "-m", "add key", check=False)
        assert result.returncode != 0, output(result)
        assert "revoke" in output(result).lower()
        assert "settings_local.py" in output(result)
        assert_no_full_secret(result)
        assert repo.git("rev-parse", "--verify", "HEAD", check=False).returncode != 0, "a commit was created"

    def test_clean_commit_succeeds(self, repo):
        self.protected(repo)
        repo.write("util.py", CLEAN)
        repo.add("util.py")
        result = repo.git("commit", "-m", "add util", check=False)
        assert result.returncode == 0, output(result)
        assert repo.git("rev-parse", "--verify", "HEAD", check=False).returncode == 0

    def test_committing_an_env_file_is_blocked_but_env_example_is_fine(self, repo):
        self.protected(repo)
        repo.write(".env", "FOO=bar\n")
        repo.add(".env")
        assert repo.git("commit", "-m", "env", check=False).returncode != 0
        repo.git("reset", "-q")
        repo.write(".env.example", "ANTHROPIC_API_KEY=\n")
        repo.add(".env.example")
        result = repo.git("commit", "-m", "example", check=False)
        assert result.returncode == 0, output(result)

    def test_an_unstaged_secret_does_not_block_a_clean_commit(self, repo):
        self.protected(repo)
        repo.write("leaky.py", LEAKY)  # never staged
        repo.write("util.py", CLEAN)
        repo.add("util.py")
        assert repo.git("commit", "-m", "clean only", check=False).returncode == 0

    def test_the_allow_marker_lets_a_commit_through(self, repo):
        self.protected(repo)
        repo.write("fixture.py", f'KEY = "{FAKE_KEY}"  # secret-scan: allow\n')
        repo.add("fixture.py")
        assert repo.git("commit", "-m", "fixture", check=False).returncode == 0

    def test_hook_prefers_the_venv_python_when_it_exists(self, repo, tmp_path):
        self.protected(repo)
        marker = tmp_path / "venv_python_used"
        wrapper = repo.write(
            ".venv/bin/python", f'#!/bin/sh\necho used >> "{marker}"\nexec "{sys.executable}" "$@"\n', executable=True
        )
        assert wrapper.exists()
        repo.write("util.py", CLEAN)
        repo.add("util.py")
        result = repo.git("commit", "-m", "with venv", check=False)
        assert result.returncode == 0, output(result)
        assert marker.exists(), "the hook did not use .venv/bin/python"

    def test_hook_falls_back_to_python3_without_a_venv(self, repo):
        self.protected(repo)
        assert not (repo.path / ".venv").exists()
        repo.write("leaky.py", LEAKY)
        repo.add("leaky.py")
        assert repo.git("commit", "-m", "x", check=False).returncode != 0  # the fallback interpreter really ran

    def test_pre_push_hook_blocks_history_that_ever_contained_a_key(self, repo):
        self.protected(repo)
        repo.write("config.py", LEAKY)
        repo.add("config.py")
        repo.commit_plain("oops")
        repo.write("config.py", CLEAN)
        repo.add("config.py")
        repo.commit_plain("remove the key")
        head = repo.git("rev-parse", "HEAD").stdout.strip()
        stdin = f"refs/heads/main {head} refs/heads/main {'0' * 40}\n"
        result = subprocess.run(
            [str(repo.path / ".githooks" / "pre-push"), "origin", "file:///nowhere"],
            cwd=repo.path, env=repo.env, input=stdin, capture_output=True, text=True,
        )
        assert result.returncode != 0, output(result)
        assert "revoke" in output(result).lower()
        assert_no_full_secret(result)

    def test_pre_push_hook_passes_clean_history(self, repo):
        self.protected(repo)
        repo.write("a.py", CLEAN)
        repo.add("a.py")
        repo.commit_plain()
        head = repo.git("rev-parse", "HEAD").stdout.strip()
        result = subprocess.run(
            [str(repo.path / ".githooks" / "pre-push"), "origin", "file:///nowhere"],
            cwd=repo.path, env=repo.env, input=f"refs/heads/main {head} refs/heads/main {'0' * 40}\n",
            capture_output=True, text=True,
        )
        assert result.returncode == 0, output(result)

    def test_a_real_git_push_is_blocked_and_the_remote_stays_empty(self, repo, tmp_path):
        self.protected(repo)
        remote = tmp_path / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True, env=git_env(remote))
        env = git_env(repo.path, remote.resolve())
        repo.env = env
        repo.git("remote", "add", "origin", str(remote))
        repo.write("config.py", LEAKY)
        repo.add("config.py")
        repo.commit_plain("oops")
        result = repo.git("push", "origin", "main", check=False)
        assert result.returncode != 0, output(result)
        assert "revoke" in output(result).lower()
        refs = subprocess.run(["git", "-C", str(remote), "show-ref"], env=env, capture_output=True, text=True)
        assert refs.stdout.strip() == "", "the push reached the remote"

    def test_a_real_git_push_of_clean_history_goes_through(self, repo, tmp_path):
        self.protected(repo)
        remote = tmp_path / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True, env=git_env(remote))
        repo.env = git_env(repo.path, remote.resolve())
        repo.git("remote", "add", "origin", str(remote))
        repo.write("a.py", CLEAN)
        repo.add("a.py")
        repo.commit_plain()
        result = repo.git("push", "origin", "main", check=False)
        assert result.returncode == 0, output(result)


# =====================================================================================================================
# scripts/install_hooks.sh
# =====================================================================================================================


def test_install_script_exists_and_is_a_shell_script():
    script = REPO_ROOT / "scripts" / "install_hooks.sh"
    assert script.is_file(), "scripts/install_hooks.sh is missing"
    text = script.read_text()
    assert text.startswith("#!")
    assert "core.hooksPath" in text and ".githooks" in text
    assert "--global" not in text and "--system" not in text, "the setting must be repo-local"


def test_install_script_sets_hooks_path_for_that_clone_only(repo, tmp_path):
    script = REPO_ROOT / "scripts" / "install_hooks.sh"
    assert script.is_file(), "scripts/install_hooks.sh is missing"
    (repo.path / "scripts").mkdir()
    shutil.copy2(script, repo.path / "scripts" / "install_hooks.sh")
    global_config = tmp_path / "global_gitconfig"
    global_config.write_text("")
    env = {**repo.env, "GIT_CONFIG_GLOBAL": str(global_config)}
    result = subprocess.run(
        ["bash", "scripts/install_hooks.sh"], cwd=repo.path, env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, output(result)
    local = repo.git("config", "--local", "--get", "core.hooksPath")
    assert local.stdout.strip() == ".githooks"
    assert global_config.read_text().strip() == "", "the script must not touch the global git config"


def test_install_script_can_be_run_twice(repo):
    script = REPO_ROOT / "scripts" / "install_hooks.sh"
    assert script.is_file(), "scripts/install_hooks.sh is missing"
    (repo.path / "scripts").mkdir()
    shutil.copy2(script, repo.path / "scripts" / "install_hooks.sh")
    for _ in range(2):
        result = subprocess.run(["bash", "scripts/install_hooks.sh"], cwd=repo.path, env=repo.env, capture_output=True, text=True)
        assert result.returncode == 0, output(result)
