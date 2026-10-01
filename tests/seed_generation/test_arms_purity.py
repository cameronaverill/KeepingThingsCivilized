"""seeding/arms.py stays pure (no Django, no anthropic, no network); seeding/generate.py reaches a model only through the gateway."""
import ast
import subprocess
import sys

import gen_kit as kit

ARMS = kit.ROOT / "seeding" / "arms.py"
GENERATE = kit.ROOT / "seeding" / "generate.py"


def imported_roots(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def imported_modules(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules.add(node.module)
            modules.update(f"{node.module}.{a.name}" for a in node.names)
    return modules


class TestArmsIsPure:
    def test_its_source_imports_neither_django_nor_anthropic_nor_the_gateway(self):
        assert imported_roots(ARMS) & {"django", "anthropic", "moderation", "forum", "evaluation", "requests", "httpx", "socket", "urllib"} == set()

    def test_importing_it_loads_neither_django_nor_anthropic(self):
        code = "import sys, seeding.arms; print(sorted(m for m in ('django', 'anthropic', 'httpx') if m in sys.modules))"
        result = subprocess.run([sys.executable, "-c", code], cwd=kit.ROOT, capture_output=True, text=True, timeout=60)
        assert (result.returncode, result.stdout.strip()) == (0, "[]")


class TestGenerateUsesTheGatewayOnly:
    def test_it_imports_no_provider_library(self):
        assert imported_roots(GENERATE) & {"anthropic", "httpx", "requests", "urllib", "socket"} == set()

    def test_it_imports_the_gateway(self):
        assert "moderation.llm" in imported_modules(GENERATE) or "moderation" in imported_roots(GENERATE)

    def test_the_other_seeding_modules_stay_free_of_django_and_anthropic(self):
        offenders = {
            p.name: imported_roots(p) & {"django", "anthropic"}
            for p in (kit.ROOT / "seeding").glob("*.py")
            if p.name not in ("generate.py", "judge.py", "research_eval.py") and imported_roots(p) & {"django", "anthropic"}
        }
        assert offenders == {}


class TestNoBaseIsLeftInTheRepo:
    def test_the_tests_never_wrote_into_the_repo_generated_folder(self):
        # conftest runs each test in its own empty directory; this guards that promise for the shipped folder.
        assert not (kit.ROOT / "generated" / "bases").exists() or not any((kit.ROOT / "generated" / "bases").glob("range_fact_*"))
