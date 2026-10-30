"""I10's shared helpers: the smoke run (J5), run the way its consumer runs it: in a checkout, with the one
npm command, then the report and the exit code read. Nothing here grades the build or the smoke run; each
case asserts its own promise (see CONTRACT-I10.md).

- `Checkout`: a temporary `git worktree` of HEAD (clean), or of HEAD with the working tree laid over it (a
  copy of the checkout as it stands, so a smoke run not yet committed is the one run), with `.venv` and
  `node_modules` as directories of links to this repository's (directories, so git still ignores them).
  Removed after, worktree registration and all.
- The broken builds (CONTRACT-I10, "How the cases break a build on purpose"): each a patch to a copy.
- `smoke()`: `npm run smoke` in a checkout, in its own session, bounded in time; never raises. Chromium
  under it can be killed mid-run.
- `Report`: what a run left in `out/smoke/`, and the predicates every report must meet, stated in words.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import yaml

from conftest import ROOT

CASES = ROOT / "cases"
GAMES = ROOT / "roms" / "games"
NODE = shutil.which("node") or "node"
NPM = shutil.which("npm") or "npm"

FULL_RUN_S = 300            # J5: the whole run within 5 minutes (Jason, at approval)
REGISTRY_TODAY = [0, 1, 2, 3, 4, 7]   # the board: mappers 0, 1, 2, 3, 4 and 7; I9 added none

CHECKS = ["file-input", "game-menu", "title", "key", "sound", "reset", "power", "loaded-once"]
NEEDS = {"file-input": None, "game-menu": "file-input", "title": "file-input", "key": "title",
         "sound": "file-input", "reset": "title", "power": "title", "loaded-once": "file-input"}
RESULTS = {"PASSED", "FAILED", "NOT RUN", "N/A"}
PICTURE_CHECKS = {"game-menu", "title", "key", "reset", "power"}


def mapper_of(rom: bytes) -> int:
    """The iNES mapper number, as the core reads it (CONTRACT.md): an archaic header (junk in bytes 12-15
    of a non-NES 2.0 file) has its upper nibble ignored."""
    nes2 = (rom[7] & 0x0C) == 0x08
    junk = not nes2 and any(rom[12:16])
    return (rom[6] >> 4) | (0 if junk else rom[7] & 0xF0)


def vendored(root: Path = ROOT) -> dict:
    """The vendored games of a checkout: {"roms/games/<file>": mapper}."""
    return {f"roms/games/{p.name}": mapper_of(p.read_bytes()) for p in sorted((root / "roms" / "games").glob("*.nes"))}


# ---------------------------------------------------------------- a checkout

def git(*args, cwd=ROOT, timeout=60) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)


def head_sha() -> str:
    return git("rev-parse", "HEAD").stdout.strip()


def _mirror(src: Path, dest: Path):
    """dest as a real directory holding a link to each entry of src (so a `.venv/` or `node_modules/`
    pattern in .gitignore still matches it)."""
    if not src.is_dir():
        return
    dest.mkdir(exist_ok=True)
    for child in src.iterdir():
        (dest / child.name).symlink_to(child)


class Checkout:
    """A temporary checkout of this repository: `git worktree add --detach` of HEAD, and unless clean,
    the working tree laid over it (tracked files as they stand, untracked files that aren't ignored, and
    deleted ones deleted). Use as a context manager; removed on exit."""

    def __init__(self, clean=False):
        self.base = Path(tempfile.mkdtemp(prefix="nes-i10-"))
        self.path = self.base / "checkout"
        r = git("worktree", "add", "--detach", "--quiet", str(self.path), "HEAD", timeout=120)
        assert r.returncode == 0, f"git worktree add failed: {r.stderr}"
        if not clean:
            self._overlay()
        _mirror(ROOT / ".venv", self.path / ".venv")
        _mirror(ROOT / "node_modules", self.path / "node_modules")

    def _overlay(self):
        listed = git("ls-files", "-z", "-c", "-o", "--exclude-standard").stdout.split("\0")
        for rel in filter(None, listed):
            src, dest = ROOT / rel, self.path / rel
            if src.is_symlink() or src.is_file():
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.is_symlink() or dest.exists():
                    dest.unlink()
                if src.is_symlink():
                    dest.symlink_to(os.readlink(src))
                else:
                    shutil.copy2(src, dest)
        for rel in filter(None, git("ls-files", "-z", "-d").stdout.split("\0")):
            (self.path / rel).unlink(missing_ok=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.remove()

    def remove(self):
        git("worktree", "remove", "--force", str(self.path), timeout=120)
        shutil.rmtree(self.base, ignore_errors=True)
        git("worktree", "prune")

    def status(self) -> str:
        return git("status", "--porcelain", cwd=self.path).stdout

    def registry(self):
        """MAPPERS, as the checkout's own core exports it (CONTRACT-I10), or the reason it can't be read."""
        code = ("const m = await import(process.argv[1]); "
                "console.log(JSON.stringify(Array.isArray(m.MAPPERS) ? m.MAPPERS : {missing: typeof m.MAPPERS}));")
        r = subprocess.run([NODE, "--input-type=module", "-e", code, str(self.path / "src" / "core" / "index.ts")],
                           capture_output=True, text=True, timeout=60)
        try:
            return json.loads(r.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            return {"error": r.stderr[-800:]}

    # -- the patches (CONTRACT-I10, "How the cases break a build on purpose")
    def file(self, rel: str) -> Path:
        return self.path / rel

    def wrap_core(self, body: str):
        """Move src/core/index.ts aside to index.built.ts and put in its place a module that re-exports
        it, with `body` after."""
        core = self.file("src/core")
        assert (core / "index.ts").is_file(), "the copy has no src/core/index.ts"
        (core / "index.ts").rename(core / "index.built.ts")
        (core / "index.ts").write_text("// The case's broken build (cases/i10_support.py): the built core, re-exported "
                                       "with one change.\nexport * from './index.built.ts';\n" + body)

    def refuse_mapper(self, m: int):
        """`NES` refuses mapper m, as an unsupported number is refused; MAPPERS still lists it."""
        self.wrap_core(f"""import {{ NES as Built, RomError }} from './index.built.ts';
export class NES extends Built {{
  constructor(rom: Uint8Array) {{
    const nes2 = (rom[7] & 0x0c) === 0x08;
    const junk = !nes2 && (rom[12] | rom[13] | rom[14] | rom[15]) !== 0;
    const m = (rom[6] >> 4) | (junk ? 0 : rom[7] & 0xf0);
    if (rom.length >= 16 && m === {m}) throw new RomError('mapper {m} is not supported');
    super(rom);
  }}
}}
""")

    def add_to_registry(self, m: int):
        """MAPPERS lists one number more, which no game or probe covers."""
        self.wrap_core(f"""import {{ MAPPERS as BUILT }} from './index.built.ts';
export const MAPPERS: readonly number[] = [...BUILT, {m}];
""")

    def unmap_key(self, code: str):
        """Take the key's line out of keyboard.ts's KEY_MAP."""
        kb = self.file("src/web/keyboard.ts")
        text = kb.read_text()
        new, n = re.subn(rf"^[ \t]*{re.escape(code)}[ \t]*:[ \t]*BUTTON_\w+,?[^\n]*\n", "", text, flags=re.M)
        assert n == 1, f"keyboard.ts has {n} KEY_MAP lines for {code}, not 1: the patch can't be made"
        kb.write_text(new)

    def swap_red_blue(self):
        pal = self.file("src/core/palette.ts")
        text = pal.read_text()
        assert re.search(r"export const RGB\b", text), "palette.ts exports no RGB table: the patch can't be made"
        pal.write_text(text + "\n// The case's broken build: red and blue swapped.\nfor (let i = 0; i < RGB.length; i++) "
                       "{ const c = RGB[i]; RGB[i] = ((c & 0xff) << 16) | (c & 0xff00) | ((c >>> 16) & 0xff); }\n")

    def head_script(self, js: str):
        """A script first in index.html's <head>: it runs after the run's init scripts, before the page's."""
        page = self.file("src/web/index.html")
        text = page.read_text()
        new, n = re.subn(r"(<head[^>]*>)", lambda m: m.group(1) + "\n<script>/* the case's broken build */\n"
                         + js + "\n</script>", text, count=1, flags=re.I)
        assert n == 1, "index.html has no <head>: the patch can't be made"
        page.write_text(new)

    def empty_menu(self):
        page = self.file("src/web/index.html")
        text = page.read_text()
        assert "<!-- game menu entries -->" in text, "index.html has no game-menu slot: the patch can't be made"
        page.write_text(text.replace("<!-- game menu entries -->", ""))

    def touch_tracked(self):
        """An uncommitted change to a tracked file the build doesn't read (J5l: a dirty checkout)."""
        readme = self.file("README.md")
        readme.write_text(readme.read_text() + "\n<!-- an uncommitted line (the I10 case's dirty checkout) -->\n")


HEAD_SCRIPTS = {
    "file-input-dead": """window.addEventListener('change', e => {
  if (e.target && e.target.id === 'rom-file') e.stopImmediatePropagation();
}, true);""",
    "canvas-blank": "CanvasRenderingContext2D.prototype.putImageData = function () {};",
    "reset-dead": """window.addEventListener('click', e => {
  if (e.target && e.target.closest && e.target.closest('#reset')) { e.stopImmediatePropagation(); e.preventDefault(); }
}, true);""",
    "power-resets": """window.addEventListener('click', e => {
  if (e.target && e.target.closest && e.target.closest('#power')) {
    e.stopImmediatePropagation(); e.preventDefault();
    document.getElementById('reset').click();
  }
}, true);""",
    "audio-silenced": """(() => {
  const connect = AudioNode.prototype.connect;
  AudioNode.prototype.connect = function (dest, output, input) {
    if (dest instanceof AudioDestinationNode) {
      const mute = new GainNode(dest.context, { gain: 0 });
      connect.call(this, mute, output || 0);
      connect.call(mute, dest);
      return dest;
    }
    return connect.apply(this, arguments);
  };
})();""",
    "never-resumed": """(() => {
  const Ctx = window.AudioContext;
  if (!Ctx) return;
  const early = new Ctx({ latencyHint: 'interactive' });
  Ctx.prototype.resume = function () { return Promise.resolve(); };
  const Early = function () { return early; };
  Early.prototype = Ctx.prototype;
  window.AudioContext = Early;
  window.webkitAudioContext = Early;
})();""",
}


def break_build(co: Checkout, patch: str, arg=None):
    """Apply one row's patch (i10_cases.yaml, `patch` and `arg`) to the copy."""
    if patch == "refuse-mapper":
        co.refuse_mapper(int(arg))
    elif patch == "add-to-registry":
        co.add_to_registry(int(arg))
    elif patch == "unmap-key":
        co.unmap_key(str(arg))
    elif patch == "swap-red-blue":
        co.swap_red_blue()
    elif patch == "empty-menu":
        co.empty_menu()
    elif patch in HEAD_SCRIPTS:
        co.head_script(HEAD_SCRIPTS[patch])
    else:
        raise ValueError(f"unknown patch {patch}")


# ---------------------------------------------------------------- the run

@dataclass
class Run:
    returncode: object
    stdout: str
    stderr: str
    seconds: float
    timed_out: bool = False
    killed: list = field(default_factory=list)

    def __repr__(self):
        return (f"<npm run smoke: exit {self.returncode}{' TIMEOUT' if self.timed_out else ''} after "
                f"{self.seconds:.0f} s; stdout tail {self.stdout[-600:]!r}; stderr tail {self.stderr[-900:]!r}>")


def _procs() -> dict:
    """{pid: (ppid, cmdline)} for every process we can see."""
    out = {}
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            stat = (d / "stat").read_text()
            ppid = int(stat[stat.rindex(")") + 2:].split()[1])
            cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except (OSError, ValueError, IndexError):
            continue
        out[int(d.name)] = (ppid, cmd)
    return out


def descendants(pid: int) -> dict:
    procs = _procs()
    kids, found, todo = {}, {}, [pid]
    for p, (pp, _) in procs.items():
        kids.setdefault(pp, []).append(p)
    while todo:
        for c in kids.get(todo.pop(), []):
            if c not in found:
                found[c] = procs[c][1]
                todo.append(c)
    return found


def chromium_under(pid: int) -> list:
    return [p for p, cmd in descendants(pid).items() if re.search(r"chrom|headless_shell", cmd, re.I)]


def _kill_tree(pid: int):
    for p in list(descendants(pid)):
        try:
            os.kill(p, signal.SIGKILL)
        except OSError:
            pass
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


def smoke(checkout: Path, *args, timeout=FULL_RUN_S + 60, kill_chromium_after=None) -> Run:
    """`npm run smoke -- <args>` in the checkout, in its own session, bounded by `timeout` (then it and
    everything under it is killed). With kill_chromium_after=<text>, once that text is on its stdout and
    Chromium has run under it for 3 s, every Chromium process under it is killed (SIGKILL), once. Never
    raises."""
    out, err = tempfile.TemporaryFile("w+"), tempfile.TemporaryFile("w+")
    cmd = [NPM, "run", "smoke"] + (["--", *map(str, args)] if args else [])
    t0 = time.monotonic()
    p = subprocess.Popen(cmd, cwd=checkout, stdout=out, stderr=err, text=True, start_new_session=True,
                         env={**os.environ, "CI": "1", "NO_COLOR": "1"})
    killed, timed_out, seen = [], False, None
    try:
        while p.poll() is None:
            if time.monotonic() - t0 > timeout:
                timed_out = True
                break
            if kill_chromium_after is not None and not killed:
                out.seek(0)
                said = kill_chromium_after in out.read()
                live = chromium_under(p.pid)
                if live and seen is None:
                    seen = time.monotonic()
                # mid-run: a check has passed, and Chromium has been up for 3 s (it is driving the page)
                if said and live and time.monotonic() - seen >= 3:
                    for c in live:
                        try:
                            os.kill(c, signal.SIGKILL)
                            killed.append(c)
                        except OSError:
                            pass
            time.sleep(0.2)
    finally:
        if p.poll() is None:
            _kill_tree(p.pid)
            p.wait(timeout=30)
        else:
            try:                     # nothing left in its process group may outlive it
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
    seconds = time.monotonic() - t0
    out.seek(0)
    err.seek(0)
    return Run(None if timed_out else p.returncode, out.read(), err.read(), seconds, timed_out, killed)


# ---------------------------------------------------------------- the report

class Report:
    """A run's directory under out/smoke/: report.json, report.md and the PNGs."""

    def __init__(self, path: Path):
        self.dir = path
        self.json = json.loads((path / "report.json").read_text())
        self.md = (path / "report.md").read_text()

    @property
    def checks(self) -> list:
        return self.json.get("checks") or []

    def rows(self, **where) -> list:
        return [r for r in self.checks if all(r.get(k) == v for k, v in where.items())]

    def failed(self) -> list:
        return [r for r in self.checks if r.get("result") == "FAILED"]

    def subjects(self) -> list:
        return self.json.get("subjects") or []


def runs_in(checkout: Path) -> list:
    """The run directories under out/smoke/ (the `latest` link left out), oldest name first."""
    d = checkout / "out" / "smoke"
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_dir() and not p.is_symlink())


def latest(checkout: Path):
    """The directory out/smoke/latest links to, or None."""
    link = checkout / "out" / "smoke" / "latest"
    if not link.is_symlink():
        return None
    return link.resolve()


def report_of(checkout: Path, run: Run) -> Report:
    """The report of the run that just finished (out/smoke/latest), or an AssertionError saying why there's
    none."""
    d = latest(checkout)
    assert d is not None and (d / "report.json").is_file() and (d / "report.md").is_file(), \
        f"no report: out/smoke/latest doesn't lead to a report.json and report.md ({run!r})"
    return Report(d)


def whole(rep: Report, registry, only=None) -> list:
    """The report is whole (J5b, J5c), stated in words and checked; returns what's wrong (empty: whole).
      - its mappers are the registry, all of it, and `only` is what was asked for;
      - each mapper asked for has exactly one `covered` row (subject null), and each subject on it one row of
        each subject check; a mapper not covered has one row of each subject check with subject null;
      - every vendored game on a mapper asked for is a subject, a menu one, with its file and its mapper;
      - every result is one of the four; N/A only on a probe's game-menu, and a probe's game-menu always N/A;
      - only the root cause fails: a row whose "runs only if" check didn't pass is NOT RUN (or N/A);
      - the counts tally the rows, failures = FAILED + NOT RUN, and the result is PASSED exactly when at least
        one row passed and none failed or didn't run."""
    j, bad = rep.json, []
    if sorted(j.get("mappers") or []) != sorted(registry) or (j.get("mappers") or []) != sorted(j.get("mappers") or []):
        bad.append(f"mappers {j.get('mappers')} aren't the registry {registry}, ascending")
    got_only = sorted(j["only"]) if isinstance(j.get("only"), list) else j.get("only")
    if got_only != (sorted(only) if only else None):
        bad.append(f"only is {j.get('only')}, not {sorted(only) if only else None}")
    asked = sorted(only) if only else list(registry)
    subjects = {s.get("name"): s for s in rep.subjects()}
    for m in asked:
        cov = rep.rows(mapper=m, check="covered")
        if len(cov) != 1 or cov[0].get("subject") is not None:
            bad.append(f"mapper {m}: {len(cov)} covered rows (want one, subject null)")
            continue
        names = [s for s, v in subjects.items() if v.get("mapper") == m]
        if cov[0]["result"] == "PASSED" and not names:
            bad.append(f"mapper {m}: covered PASSED with no subject")
        if cov[0]["result"] != "PASSED" and names:
            bad.append(f"mapper {m}: covered {cov[0]['result']} with subjects {names}")
        for s in (names or [None]):
            for c in CHECKS:
                n = len(rep.rows(mapper=m, subject=s, check=c))
                if n != 1:
                    bad.append(f"mapper {m}, subject {s}: {n} rows of {c} (want 1)")
    for rom, m in vendored(rep.dir.parents[2]).items():
        if m in asked and not any(s.get("rom") == rom and s.get("menu") is True and s.get("mapper") == m
                                  for s in subjects.values()):
            bad.append(f"the vendored game {rom} (mapper {m}) isn't a menu subject on mapper {m}")
    for r in rep.checks:
        res, c, s = r.get("result"), r.get("check"), r.get("subject")
        if res not in RESULTS:
            bad.append(f"{r}: result {res!r}")
            continue
        menu = subjects.get(s, {}).get("menu") if s is not None else None
        if res == "N/A" and not (c == "game-menu" and menu is False):
            bad.append(f"{c} on {s} (mapper {r.get('mapper')}) is N/A")
        if c == "game-menu" and menu is False and res != "N/A":
            bad.append(f"the probe {s}'s game-menu is {res}, not N/A")
        need = "covered" if c == "file-input" else NEEDS.get(c)
        if need:
            pre = rep.rows(mapper=r.get("mapper"), subject=None if need == "covered" else s, check=need)
            if pre and pre[0].get("result") != "PASSED" and res not in ("NOT RUN", "N/A"):
                bad.append(f"{c} on {s} (mapper {r.get('mapper')}) is {res} though {need} is {pre[0].get('result')}")
    tally = {k: sum(1 for r in rep.checks if r.get("result") == k) for k in RESULTS}
    if j.get("counts") != tally:
        bad.append(f"counts {j.get('counts')} don't tally the rows {tally}")
    if j.get("failures") != tally["FAILED"] + tally["NOT RUN"]:
        bad.append(f"failures {j.get('failures')} != FAILED + NOT RUN ({tally['FAILED'] + tally['NOT RUN']})")
    want = "PASSED" if tally["PASSED"] > 0 and tally["FAILED"] + tally["NOT RUN"] == 0 else "FAILED"
    if j.get("result") != want:
        bad.append(f"result {j.get('result')} (the rows say {want})")
    return bad


def png_size(path: Path):
    """(width, height) from a PNG's IHDR, or None if it isn't a PNG."""
    try:
        head = path.read_bytes()[:24]
    except OSError:
        return None
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


# ---------------------------------------------------------------- the broken builds' rows

CASE_ROWS = yaml.safe_load((CASES / "i10_cases.yaml").read_text())


def params(section: str, delta_mark) -> list:
    """The rows of one section of i10_cases.yaml as pytest params, each marked delta_mark when its data
    says `delta: true`."""
    return [pytest.param(r, id=str(r["id"]), marks=[delta_mark] if r.get("delta") is True else [])
            for r in CASE_ROWS[section]]
