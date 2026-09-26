"""Runs compose.js / poll.js in Node against a rendered page (helper for the step 7b JS tests; see fviews_jsrun.js)."""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import fviews_html as H

HERE = Path(__file__).resolve().parent
RUNNER = HERE / "fviews_jsrun.js"
NODE = shutil.which("node")
STATIC_DIR = Path(__file__).resolve().parents[2] / "forum" / "static" / "forum"
# Mutation testing points the JS tests at a mutant copy of the scripts without touching the repository.
if os.environ.get("FVIEWS_STATIC_DIR"):
    STATIC_DIR = Path(os.environ["FVIEWS_STATIC_DIR"])


def script_path(name, override_dir=None):
    return str((Path(override_dir) if override_dir else STATIC_DIR) / name)


def number_tree(root):
    """Serialise a parsed page for the runner; every element gets an eid in document order (root = 0)."""
    ids = {}
    counter = [0]

    def go(node):
        ids[id(node)] = counter[0]
        desc = {"tag": node.tag, "attrs": node.attrs, "eid": counter[0], "children": []}
        counter[0] += 1
        for child in node.children:
            desc["children"].append(child if isinstance(child, str) else go(child))
        return desc

    tree = go(root)
    return tree, ids


class Page:
    """A rendered page loaded into the runner. Build ops, call run(), read results back by position."""

    def __init__(self, html, pathname="/c/1/", script=None):
        self.root = H.parse(html)
        self.tree, self.ids = number_tree(self.root)
        self.pathname = pathname
        self.script = script
        self.ops = []

    def eid(self, node):
        return self.ids[id(node)]

    def add(self, **op):
        self.ops.append(op)
        return len(self.ops) - 1

    def run(self):
        assert NODE, "node is required for this test"
        scenario = {"tree": self.tree, "pathname": self.pathname, "ops": self.ops}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scenario.json"
            path.write_text(json.dumps(scenario))
            done = subprocess.run([NODE, str(RUNNER), str(path)], capture_output=True, text=True, timeout=60)
        assert done.returncode == 0, done.stderr[-2000:]
        results = json.loads(done.stdout)
        for i, result in enumerate(results):
            assert not (isinstance(result, dict) and "__error" in result), f"op {i} {self.ops[i]}: {result['__error']}"
        return results
