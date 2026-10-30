"""The documented setup is enough to run the tests: every third-party module the Python tests import is
listed in requirements-dev.txt (a fresh venv built from it can collect the corpus)."""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Import name -> distribution name, where they differ.
DIST = {"yaml": "pyyaml"}


def test_every_imported_package_is_in_requirements():
    local = {"conftest"} | {p.stem for p in ROOT.glob("cases/*.py")} | {p.stem for p in ROOT.glob("tests/*.py")}
    imported = set()
    for p in list(ROOT.glob("cases/*.py")) + list(ROOT.glob("tests/*.py")):
        for node in ast.walk(ast.parse(p.read_text())):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
    third_party = {m for m in imported if m not in sys.stdlib_module_names and m not in local}
    listed = {re.split(r"[<>=!~\s]", l.strip())[0].lower()
              for l in (ROOT / "requirements-dev.txt").read_text().splitlines() if l.strip() and not l.startswith("#")}
    missing = sorted(m for m in third_party if DIST.get(m, m).lower() not in listed)
    assert not missing, f"imported but not in requirements-dev.txt: {missing}"
