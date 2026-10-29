"""Shared harness for the corpus: oracle fetching (by hash), and running the build's two consumers,
the CLI and the core API (through the frozen driver, cases/drive.ts). Nothing here grades the build;
each case asserts its own promise."""
import hashlib
import json
import shutil
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "cases"
CACHE = ROOT / "roms" / "cache"
MANIFEST = json.loads((CASES / "oracles.json").read_text())
ORACLES = {o["id"]: o for o in MANIFEST["oracles"]}
NODE = shutil.which("node") or "node"


def oracle(oid: str) -> Path:
    """The path of an oracle file, fetched from its pinned URL on first use and checked by SHA-256."""
    o = ORACLES[oid]
    dest = CACHE / o["path"]
    if not dest.exists() or hashlib.sha256(dest.read_bytes()).hexdigest() != o["sha256"]:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(o["url"], timeout=60) as r:
            data = r.read()
        got = hashlib.sha256(data).hexdigest()
        if got != o["sha256"]:
            raise RuntimeError(f"oracle {oid}: sha256 {got} != pinned {o['sha256']}")
        dest.write_bytes(data)
    return dest


class Ran:
    """A finished (or timed-out) subprocess, never an exception: a broken build shows up in the
    case's own assertion, not as a harness error."""

    def __init__(self, returncode, stdout, stderr, timed_out=False):
        self.returncode, self.stdout, self.stderr, self.timed_out = returncode, stdout, stderr, timed_out

    def __repr__(self):
        tail = (self.stderr or "")[-1500:]
        return f"<exit {self.returncode}{' TIMEOUT' if self.timed_out else ''}; stderr tail: {tail!r}>"


def run(args, timeout=120, cwd=ROOT) -> Ran:
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return Ran(p.returncode, p.stdout, p.stderr)
    except subprocess.TimeoutExpired as e:
        return Ran(None, (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""),
                   (e.stderr or b"").decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or ""),
                   timed_out=True)


def cli(*args, timeout=120) -> Ran:
    return run([NODE, str(ROOT / "src" / "cli" / "nes.ts"), *map(str, args)], timeout=timeout)


def drive(request: dict, timeout=120) -> dict:
    """Run the core API through cases/drive.ts. Always returns a dict; a failed run has ok False and
    says why (its stage, error, or the process's exit)."""
    r = run([NODE, str(CASES / "drive.ts"), json.dumps(request)], timeout=timeout)
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.startswith("{")]
    if r.timed_out:
        return {"ok": False, "stage": "timeout", "error": f"no result within {timeout}s", "ran": repr(r)}
    if not lines:
        return {"ok": False, "stage": "process", "error": repr(r)}
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return {"ok": False, "stage": "output", "error": lines[-1][:500]}
