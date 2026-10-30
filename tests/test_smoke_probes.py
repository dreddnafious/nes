"""The smoke run's probes (smoke/probes.py) judge Reset for real: on a mapper covered only by a probe,
a Reset button that does nothing fails the `reset` check (a probe's screen of play must differ from its
title, or a dead Reset would still match it). Run the way a developer runs it while working on one
board: `smoke/smoke.py --mapper <n>` in a copy of the checkout whose Reset clicks are swallowed."""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON = ROOT / ".venv" / "bin" / "python"

DEAD_RESET = """<script>window.addEventListener('click', e => {
  if (e.target && e.target.closest && e.target.closest('#reset')) { e.stopImmediatePropagation(); e.preventDefault(); }
}, true);</script>"""


def test_a_dead_reset_fails_the_reset_check_on_every_probe():
    with tempfile.TemporaryDirectory(prefix="nes-smoke-probe-") as tmp:
        copy = Path(tmp) / "checkout"
        for part in ("src", "smoke", "roms/games"):
            shutil.copytree(ROOT / part, copy / part, ignore=shutil.ignore_patterns("__pycache__"))
        page = copy / "src" / "web" / "index.html"
        page.write_text(page.read_text().replace("<head>", "<head>\n" + DEAD_RESET, 1))
        r = subprocess.run([str(PYTHON), "-B", str(copy / "smoke" / "smoke.py"), "--mapper", "1", "--mapper", "3",
                            "--mapper", "7"], cwd=copy, capture_output=True, text=True, timeout=240)
        assert r.returncode != 0, f"a dead Reset passed the smoke run: {r.stdout[-1500:]}"
        report = json.loads((copy / "out" / "smoke" / "latest" / "report.json").read_text())
        resets = {row["subject"]: row["result"] for row in report["checks"] if row["check"] == "reset"}
        assert resets and all(v == "FAILED" for v in resets.values()), resets
        others = [(row["subject"], row["check"]) for row in report["checks"]
                  if row["result"] == "FAILED" and row["check"] != "reset"]
        assert not others, f"checks FAILED besides reset: {others}"
