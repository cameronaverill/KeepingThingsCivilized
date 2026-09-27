"""scripts/dev.sh: static checks on the file, then a functional run against stub interpreters in a scratch tree (the real
servers are never started, no network, no .env). The stub `.venv/bin/python` behaves as a web server or as a worker
depending on the manage.py command it is given; every stub records its pid so the tests can prove that the script left no
process behind."""
import os
import re
import signal
import stat
import subprocess
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DEV_SH = REPO / "scripts" / "dev.sh"
REMINDER = "LLM_ENABLED is False in config/tunables.py: moderation runs will be recorded as skipped_disabled until it is turned on."

STUB = r"""#!/usr/bin/env bash
mode=""
for arg in "$@"; do
    case "$arg" in
        runserver) mode=web ;;
        run_moderator) mode=worker ;;
    esac
done
dir="$STUB_DIR"
echo "$$" > "$dir/$mode.pid"
echo "hello-$mode"
echo "problem-$mode" >&2
case "$(cat "$dir/$mode.behaviour" 2>/dev/null)" in
    exit-soon) sleep 1; exit 3 ;;
esac
if [ "$mode" = web ]; then
    # like runserver's autoreloader: a child process that must go down with its parent
    sleep 60 &
    echo "$!" > "$dir/web-child.pid"
    wait
else
    exec sleep 60
fi
"""


def text():
    return DEV_SH.read_text(encoding="utf-8")


def code_lines():
    return [line for line in text().splitlines() if not line.lstrip().startswith("#")]


class TestTheFile:
    def test_it_exists_and_is_executable(self):
        assert DEV_SH.is_file()
        assert DEV_SH.stat().st_mode & stat.S_IXUSR

    def test_it_is_a_bash_script(self):
        assert re.match(r"#!(/usr/bin/env bash|/bin/bash)\s*$", text().splitlines()[0])

    def test_the_syntax_is_valid(self):
        proc = subprocess.run(["bash", "-n", str(DEV_SH)], capture_output=True, text=True, timeout=30)
        assert (proc.returncode, proc.stderr) == (0, "")

    def test_it_stops_on_errors_and_unset_variables(self):
        assert re.search(r"^set -euo pipefail\s*$", text(), re.MULTILINE)

    def test_it_makes_no_reference_to_the_env_file(self):
        assert re.search(r"\.env\b", text()) is None

    def test_it_never_sources_anything_or_names_a_key(self):
        assert [l for l in code_lines() if re.match(r"\s*(source|\.)\s", l)] == []
        assert "ANTHROPIC" not in text()
        assert "API_KEY" not in text()

    def test_it_never_dumps_the_environment(self):
        assert [l for l in code_lines() if re.search(r"\b(printenv|env)\s*(\||>|$)|\bexport\s+-p\b|\bset\s+-x\b|xtrace", l)] == []

    def test_it_uses_the_projects_venv_interpreter(self):
        assert ".venv/bin/python" in text()

    def test_it_starts_the_web_server_and_the_worker_through_manage_py(self):
        assert re.search(r"manage\.py\s+runserver", text())
        assert re.search(r"manage\.py\s+run_moderator", text())

    def test_it_prefixes_web_and_worker_output(self):
        assert "web" in text() and "worker" in text()
        assert re.search(r"\[%s\]|\[web\]", text())
        assert re.search(r"\[worker\]|\[%s\]", text())

    def test_it_traps_signals_and_stops_the_children(self):
        assert re.search(r"^\s*trap\b", text(), re.MULTILINE)
        assert re.search(r"\bkill\b", text())
        assert re.search(r"\bwait\b", text())

    def test_it_prints_the_kill_switch_reminder(self):
        assert REMINDER in text()

    def test_the_reminder_is_a_single_line(self):
        assert len([l for l in text().splitlines() if "LLM_ENABLED" in l and "skipped_disabled" in l]) == 1


# --- Functional, against stubs --------------------------------------------------------------------------------------------

@pytest.fixture
def tree(tmp_path):
    """A scratch project: scripts/dev.sh (a copy), .venv/bin/python (the stub) and an empty manage.py."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "dev.sh").write_text(text(), encoding="utf-8")
    (tmp_path / "scripts" / "dev.sh").chmod(0o755)
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    python = tmp_path / ".venv" / "bin" / "python"
    python.write_text(STUB, encoding="utf-8")
    python.chmod(0o755)
    (tmp_path / "manage.py").write_text("", encoding="utf-8")
    (tmp_path / "state").mkdir()
    yield tmp_path
    for pid_file in (tmp_path / "state").glob("*.pid"):
        try:
            os.kill(int(pid_file.read_text().strip()), signal.SIGKILL)
        except (ProcessLookupError, ValueError):
            pass


def start(tree, behaviours=()):
    for mode, behaviour in behaviours:
        (tree / "state" / f"{mode}.behaviour").write_text(behaviour)
    env = {"PATH": os.environ["PATH"], "HOME": str(tree), "STUB_DIR": str(tree / "state")}
    proc = subprocess.Popen(
        ["bash", str(tree / "scripts" / "dev.sh")], cwd=tree, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, start_new_session=True, preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL),
    )  # SIGINT back to its default: a suite started as a background job inherits "ignored", which bash cannot trap
    proc.seen = []
    proc.reader = threading.Thread(target=lambda: proc.seen.extend(iter(proc.stdout.readline, "")), daemon=True)
    proc.reader.start()
    return proc


def wait_for_output(proc, lines, timeout=20):
    """Wait until dev.sh has printed every one of `lines` (whole lines)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        printed = [l.rstrip("\n") for l in proc.seen]
        if all(line in printed for line in lines):
            return
        time.sleep(0.05)
    raise AssertionError(f"dev.sh never printed {lines}; it printed {proc.seen}")


CHILD_LINES = ["[web] hello-web", "[web] problem-web", "[worker] hello-worker", "[worker] problem-worker"]


def pid_of(tree, mode, timeout=20):
    path = tree / "state" / f"{mode}.pid"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.read_text().strip():
            return int(path.read_text().strip())
        time.sleep(0.05)
    raise AssertionError(f"the stub {mode} never started")


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return not is_zombie(pid)


def is_zombie(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0] == "Z"
    except FileNotFoundError:
        return True


def gone_within(pid, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return not alive(pid)


def finish(proc, timeout=20):
    """Wait for dev.sh to exit; returns everything it printed."""
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        raise AssertionError("dev.sh did not exit in time; output so far: " + "".join(proc.seen))
    proc.reader.join(timeout=10)
    return "".join(proc.seen)


class TestRunningIt:
    def test_both_processes_start_their_output_is_prefixed_and_the_reminder_is_printed(self, tree):
        proc = start(tree)
        wait_for_output(proc, [REMINDER, *CHILD_LINES])
        proc.send_signal(signal.SIGINT)
        lines = finish(proc).splitlines()
        assert REMINDER in lines
        for line in ("[web] hello-web", "[web] problem-web", "[worker] hello-worker", "[worker] problem-worker"):
            assert line in lines

    def test_every_line_of_child_output_carries_a_prefix(self, tree):
        proc = start(tree)
        wait_for_output(proc, [REMINDER, *CHILD_LINES])
        proc.send_signal(signal.SIGINT)
        lines = finish(proc).splitlines()
        assert [l for l in lines if l != REMINDER and not re.match(r"\[(web|worker)\] ", l)] == []

    def test_ctrl_c_stops_both_processes_and_the_script_exits(self, tree):
        proc = start(tree)
        web, worker = pid_of(tree, "web"), pid_of(tree, "worker")
        proc.send_signal(signal.SIGINT)
        finish(proc)
        assert proc.returncode is not None
        assert gone_within(web) and gone_within(worker)

    def test_sigterm_stops_both_processes_and_the_script_exits(self, tree):
        proc = start(tree)
        web, worker = pid_of(tree, "web"), pid_of(tree, "worker")
        proc.send_signal(signal.SIGTERM)
        finish(proc)
        assert gone_within(web) and gone_within(worker)

    def test_the_web_servers_child_process_is_stopped_too(self, tree):
        proc = start(tree)
        pid_of(tree, "web")
        pid_of(tree, "worker")
        child = pid_of(tree, "web-child")
        proc.send_signal(signal.SIGINT)
        finish(proc)
        assert gone_within(child)

    def test_when_the_worker_exits_the_web_server_is_stopped_and_the_script_exits(self, tree):
        proc = start(tree, behaviours=[("worker", "exit-soon")])
        web = pid_of(tree, "web")
        out = finish(proc)
        assert gone_within(web)
        assert "[worker] hello-worker" in out.splitlines()

    def test_when_the_web_server_exits_the_worker_is_stopped_and_the_script_exits(self, tree):
        proc = start(tree, behaviours=[("web", "exit-soon")])
        worker = pid_of(tree, "worker")
        finish(proc)
        assert gone_within(worker)

    def test_the_script_exits_non_zero_when_a_process_died_on_its_own(self, tree):
        proc = start(tree, behaviours=[("worker", "exit-soon")])
        finish(proc)
        assert proc.returncode != 0

    def test_it_does_not_start_when_the_venv_interpreter_is_missing(self, tree):
        (tree / ".venv" / "bin" / "python").unlink()
        proc = start(tree)
        out = finish(proc)
        assert proc.returncode != 0
        assert "[web]" not in out and "[worker]" not in out
