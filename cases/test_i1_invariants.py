"""I1: invariants derived from the Intent and Decided, each stated in words, then its predicate."""
import json
import re
from pathlib import Path

import pytest

from conftest import ROOT, drive, oracle, run

DELTA = pytest.mark.xfail(strict=True, reason="delta")


def test_the_core_is_pure_and_has_no_browser_code():
    """purpose: "The core in src/core is pure TypeScript with no DOM": it exists (src/core/index.ts),
    and no file under src/core names a browser global (window, document, navigator,
    requestAnimationFrame, AudioContext, HTMLCanvasElement, localStorage).
    origin: design"""
    core = ROOT / "src" / "core"
    assert (core / "index.ts").is_file(), "src/core/index.ts doesn't exist"
    banned = re.compile(r"\b(window|document|navigator|requestAnimationFrame|AudioContext|"
                        r"HTMLCanvasElement|localStorage)\b")
    hits = [f"{p.relative_to(ROOT)}:{i}" for p in core.rglob("*.ts")
            for i, line in enumerate(p.read_text().splitlines(), 1) if banned.search(line)]
    assert not hits, hits


def test_no_emulator_is_vendored_or_depended_on():
    """purpose: "don't vendor one in": package.json exists, has no runtime dependencies at all, and no
    package anywhere in it or its lockfile is an emulator or NES/6502 package by name.
    origin: design"""
    pkg_path = ROOT / "package.json"
    assert pkg_path.is_file(), "no package.json"
    pkg = json.loads(pkg_path.read_text())
    assert not pkg.get("dependencies"), f"runtime dependencies: {pkg.get('dependencies')}"
    names = set()
    for k in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        names |= set(pkg.get(k) or {})
    lock = ROOT / "package-lock.json"
    if lock.is_file():
        names |= {p.split("node_modules/")[-1] for p in json.loads(lock.read_text()).get("packages", {}) if p}
    bad = re.compile(r"(^|[-_@/.])(nes|6502|famicom|nintendo|2a03|2c02)([-_./]|$)|emulat", re.I)
    assert not [n for n in names if bad.search(n)], sorted(n for n in names if bad.search(n))


def test_the_build_never_calls_out_to_another_program():
    """purpose: "don't call another emulator": nothing under src/ starts another process or loads
    native code: no child_process, exec/spawn, process.dlopen, native .node addon, or
    WebAssembly instantiation in any .ts/.js/.mjs file under src/.
    origin: design"""
    src = ROOT / "src"
    banned = re.compile(r"child_process|process\.dlopen|\bexecFile\b|\bspawnSync?\b|\.node['\"]|"
                        r"WebAssembly\.(instantiate|compile)")
    hits = [f"{p.relative_to(ROOT)}:{i}" for p in src.rglob("*") if p.suffix in (".ts", ".js", ".mjs")
            for i, line in enumerate(p.read_text().splitlines(), 1) if banned.search(line)]
    assert not hits, hits


def test_no_rom_is_committed_without_a_licence_saying_so():
    """purpose: "never commercial ROMs; vendored only after its licence is checked to allow
    redistribution": every .nes file tracked by git is named in roms/LICENSES.md on a line that
    marks it committed; everything else is fetched by hash into the gitignored roms/cache/.
    origin: design"""
    r = run(["git", "ls-files", "*.nes", "*.NES", "*.fds", "*.unf", "*.unif"])
    assert r.returncode == 0, repr(r)
    tracked = [p for p in r.stdout.splitlines() if p.strip()]
    lic = (ROOT / "roms" / "LICENSES.md").read_text() if (ROOT / "roms" / "LICENSES.md").is_file() else ""
    unlisted = [p for p in tracked
                if not any(Path(p).name in line and "committed" in line.lower() for line in lic.splitlines())]
    assert not unlisted, f"ROMs in git with no licence line marking them committed: {unlisted}"


def test_the_typescript_type_checks_under_strict():
    """purpose: the TypeScript source type-checks with the project's own compiler under strict mode
    (tsconfig.json compilerOptions.strict true; `npx --no-install tsc --noEmit -p .` exits 0).
    origin: design"""
    cfg = ROOT / "tsconfig.json"
    assert cfg.is_file(), "no tsconfig.json"
    text = re.sub(r"//.*", "", cfg.read_text())
    assert re.search(r'"strict"\s*:\s*true', text), "strict is not on"
    r = run(["npx", "--no-install", "tsc", "--noEmit", "-p", "."], timeout=300)
    assert r.returncode == 0, (r.stdout or "")[-3000:] + repr(r)


def test_runs_are_deterministic():
    """purpose: the same ROM run twice from power-on gives the same result, frame count, RAM and
    PRG-RAM contents (power-on state is fixed; nothing reads the wall clock or a random source).
    origin: design"""
    req = {"mode": "blargg", "rom": str(oracle("instr_test-v5/01-basics")), "maxFrames": 3600}
    a, b = drive(req, timeout=180), drive(req, timeout=180)
    assert a.get("ok") and a.get("done"), a
    keys = ("status", "text", "frames", "resets", "ram", "prgRam")
    assert {k: a.get(k) for k in keys} == {k: b.get(k) for k in keys}


def test_headless_core_runs_at_least_five_times_real_time():
    """purpose: headless, the core emulates 1,800 frames (30 s of NTSC time) of a real test ROM in at
    most 6.0 s of wall time on this machine: at least 5x real time, so the corpus runs in minutes
    (J3j; a bound every later increment must keep).
    origin: design"""
    res = drive({"mode": "bench", "rom": str(oracle("instr_test-v5/01-basics")), "frames": 1800},
                timeout=120)
    assert res.get("ok"), res
    assert res["frames"] == 1800
    assert res["ms"] <= 6000, f"{res['ms']:.0f} ms for 1800 frames"
