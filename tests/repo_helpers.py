"""Helper code for the secret-protection tests (import it as `from repo_helpers import ...`, never from conftest): loading scripts/check_secrets.py and building throwaway git repos.

Fake secrets are always built at runtime by concatenation, so no file in this repo contains a real-looking key.
"""
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCANNER_PATH = REPO_ROOT / "scripts" / "check_secrets.py"


def load_scanner():
    """Import scripts/check_secrets.py by path (scripts/ is not a package)."""
    assert SCANNER_PATH.is_file(), "scripts/check_secrets.py does not exist yet"
    spec = importlib.util.spec_from_file_location("check_secrets_under_test", SCANNER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses / typing look the module up by name
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


def git_env(*repo_paths, extra=None):
    """A clean git environment: no global/system config, and each given path marked safe (the container sometimes
    reports 'dubious ownership'). The safe.directory values are exact paths, never '*'."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_TERMINAL_PROMPT="0",
        GIT_CONFIG_COUNT=str(len(repo_paths)),
    )
    for index, path in enumerate(repo_paths):
        env[f"GIT_CONFIG_KEY_{index}"] = "safe.directory"
        env[f"GIT_CONFIG_VALUE_{index}"] = str(path)
    env.update(extra or {})
    return env


class GitRepo:
    """A throwaway repository inside a pytest tmp path."""

    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.mkdir(parents=True, exist_ok=True)
        self.env = git_env(self.path)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test User")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args, check=True, input=None, env=None):
        result = subprocess.run(
            ["git", *args], cwd=self.path, env=env or self.env, capture_output=True, text=True, input=input
        )
        if check and result.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}")
        return result

    def write(self, relative, content, executable=False):
        target = self.path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
        if executable:
            target.chmod(0o755)
        return target

    def add(self, *paths):
        self.git("add", "-f", "--", *paths)

    def commit_plain(self, message="commit"):
        """Commit without any hook, for building history."""
        self.git("commit", "-q", "--no-verify", "-m", message)

    def install_protection(self):
        """Copy the scanner and the hooks from the real repo and point core.hooksPath at them."""
        (self.path / "scripts").mkdir(exist_ok=True)
        shutil.copy2(SCANNER_PATH, self.path / "scripts" / "check_secrets.py")
        shutil.copytree(REPO_ROOT / ".githooks", self.path / ".githooks", dirs_exist_ok=True)  # copy2 keeps the x bit
        self.git("config", "core.hooksPath", ".githooks")

    def run_scanner(self, *args):
        assert SCANNER_PATH.is_file(), "scripts/check_secrets.py does not exist yet"
        return subprocess.run(
            [sys.executable, str(SCANNER_PATH), *args], cwd=self.path, env=self.env, capture_output=True, text=True
        )


class _LazyScanner:
    """Loads the scanner on first attribute access, so a missing script fails the test itself, not fixture setup."""

    _module = None

    def __getattr__(self, name):
        if _LazyScanner._module is None:
            _LazyScanner._module = load_scanner()
        return getattr(_LazyScanner._module, name)
