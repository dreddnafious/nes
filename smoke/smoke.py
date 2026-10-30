"""The smoke run (J5; cases/CONTRACT-I10.md): `npm run smoke [-- --mapper <n> ...]`.

It serves this checkout's page on a free local port (`node src/web/serve.ts`), drives it in headless
Chromium through its real entry points (the file input and the game menu, the keyboard, the canvas, Reset
and Power, and the sound at the AudioContext's destination) for a game or a probe on every mapper the
core's registry (`MAPPERS`) lists, and writes a report a person reads:
out/smoke/<run>/report.md, its JSON twin report.json, and the frames it judged as PNGs. out/smoke/latest
leads to the last run. It exits 0 when every check passed, 1 otherwise.

Its code lives here, outside src/ (I1: nothing under src/ starts a process). It writes only under
out/smoke/; what it compares against (smoke/references/, smoke/subjects.json) is committed and only read.
"""
import argparse
import base64
import json
import math
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import zlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from probes import BUTTON_KEYS, PROBES

ROOT = Path(__file__).resolve().parent.parent
SMOKE = ROOT / "smoke"
GAMES = ROOT / "roms" / "games"
NODE = shutil.which("node") or "node"

CHECKS = ["file-input", "game-menu", "title", "key", "sound", "reset", "power", "loaded-once"]
NEEDS = {"game-menu": "file-input", "title": "file-input", "key": "title", "sound": "file-input",
         "reset": "title", "power": "title", "loaded-once": "file-input"}
BUDGET_S = 270              # J5: the whole run within 5 minutes; past this, what's left isn't run
FLOOR_DBFS = -60.0          # sound is "not silent" above this RMS (DC removed), as I4's floor
SOUND_S = 1.5               # how long the sound check plays, stepping one frame each 1/60 s
SETTLE_S = 0.25             # the sound's window starts this long after the gesture
DEFAULTS = {"frame": 120, "key_frames": 30, "play": 60, "sound": "start", "sound_starts": [0]}


# ---------------------------------------------------------------- PNGs (stdlib only)

def write_png(path: Path, rgb: bytes, w=256, h=240):
    rows = b"".join(b"\0" + rgb[y * w * 3:(y + 1) * w * 3] for y in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))


def read_png(path: Path) -> bytes:
    """An 8-bit RGB or RGBA PNG (not interlaced) as RGB bytes, row-major."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path} isn't a PNG"
    pos, idat, w = 8, b"", 0
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            w, h, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", body)
            assert depth == 8 and colour in (2, 6) and not interlace, f"{path}: unsupported PNG"
            bpp = 3 if colour == 2 else 4
        elif kind == b"IDAT":
            idat += body
        pos += 12 + n
    raw, stride, out, prev = zlib.decompress(idat), w * bpp, bytearray(), bytearray(w * bpp)
    for y in range(h):
        f, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b, c = prev[i], (prev[i - bpp] if i >= bpp else 0)
            if f == 1:
                line[i] = (line[i] + a) & 0xFF
            elif f == 2:
                line[i] = (line[i] + b) & 0xFF
            elif f == 3:
                line[i] = (line[i] + (a + b) // 2) & 0xFF
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
        prev = line
        for x in range(w):
            out += line[x * bpp:x * bpp + 3]
    return bytes(out)


def differing(a: bytes, b: bytes) -> int:
    """How many pixels of two RGB frames differ."""
    return sum(1 for i in range(0, min(len(a), len(b)), 3) if a[i:i + 3] != b[i:i + 3]) + abs(len(a) - len(b)) // 3


# ---------------------------------------------------------------- the checkout

def git(*args) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=60)
    return r.stdout.strip() if r.returncode == 0 else ""


def mapper_of(rom: bytes) -> int:
    """The iNES mapper number as the core reads it (CONTRACT.md): an archaic header (junk in bytes 12-15
    of a file that isn't NES 2.0) has its upper nibble ignored."""
    nes2 = (rom[7] & 0x0C) == 0x08
    junk = not nes2 and any(rom[12:16])
    return (rom[6] >> 4) | (0 if junk else rom[7] & 0xF0)


def registry():
    """MAPPERS, as this checkout's core exports it, or (None, why)."""
    code = ("const m = await import(process.argv[1]); "
            "console.log(JSON.stringify(Array.isArray(m.MAPPERS) ? m.MAPPERS : null));")
    r = subprocess.run([NODE, "--input-type=module", "-e", code, str(ROOT / "src" / "core" / "index.ts")],
                       capture_output=True, text=True, timeout=60, cwd=ROOT)
    try:
        got = json.loads(r.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return None, f"the core couldn't be read: {r.stderr.strip()[-300:]}"
    if not isinstance(got, list):
        return None, "src/core/index.ts exports no MAPPERS"
    return got, ""


def subjects_for(mappers: list, run_dir: Path) -> list:
    """Every vendored game on these mappers (through the file input and its menu entry), and a probe on
    each of them with no vendored game (written into the run's directory)."""
    params = json.loads((SMOKE / "subjects.json").read_text())
    subjects = []
    for p in sorted(GAMES.glob("*.nes")):
        m = mapper_of(p.read_bytes())
        if m in mappers:
            name = p.stem
            subjects.append({"name": name, "mapper": m, "menu": True, "rom": f"roms/games/{p.name}",
                             "path": p, "file": p.name, **DEFAULTS, **params.get(name, {})})
    for m in mappers:
        if m in PROBES and not any(s["mapper"] == m for s in subjects):
            name, build = PROBES[m]
            path = run_dir / f"{name}.nes"
            path.write_bytes(build())
            subjects.append({"name": name, "mapper": m, "menu": False, "rom": None, "path": path,
                             **DEFAULTS, **params.get(name, {})})
    return subjects


# ---------------------------------------------------------------- the server

class Server:
    """`node src/web/serve.ts --port P` on a free port, chosen again if it's taken before the bind."""

    def __init__(self):
        self.proc, self.port, self.why = None, None, ""
        for _ in range(10):
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
            log = tempfile.TemporaryFile("w+")
            proc = subprocess.Popen([NODE, str(ROOT / "src" / "web" / "serve.ts"), "--port", str(port)], cwd=ROOT,
                                    stdout=subprocess.DEVNULL, stderr=log, text=True)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and proc.poll() is None:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as r:
                        if r.status == 200:
                            self.proc, self.port = proc, port
                            return
                except OSError:
                    time.sleep(0.1)
            self._kill(proc)
            log.seek(0)
            err = log.read()
            self.why = f"the server didn't answer on port {port}: {err.strip()[-300:] or 'no answer in 15 s'}"
            if "already in use" not in err:
                return

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/"

    @staticmethod
    def _kill(proc):
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    def stop(self):
        if self.proc is not None:
            self._kill(self.proc)


# ---------------------------------------------------------------- a tab on the page

# Installed before the page's scripts: the autoplay policy as Chrome documents it (headless Chromium
# doesn't enforce it), and a tap that hears whatever reaches each AudioContext's destination.
INIT_JS = r"""(() => {
  const tap = window.__smoke = { quanta: [], marks: [], ctxs: [], activated: false, errors: [] };
  const Real = window.AudioContext;
  if (!Real) { tap.errors.push('no AudioContext in this browser'); return; }
  const activate = e => { if (e.isTrusted && !(e.type === 'keydown' && e.key === 'Escape')) tap.activated = true; };
  for (const t of ['keydown', 'mousedown', 'pointerdown', 'pointerup', 'touchend']) window.addEventListener(t, activate, true);
  const blocked = new WeakSet();
  class PolicedContext extends Real {
    constructor(options) {
      super(options);
      tap.ctxs.push(this);
      if (!tap.activated) { blocked.add(this); Real.prototype.suspend.call(this); }
    }
    get state() {
      const s = Object.getOwnPropertyDescriptor(BaseAudioContext.prototype, 'state').get.call(this);
      return blocked.has(this) && !tap.activated ? 'suspended' : s;
    }
    resume() {
      if (!tap.activated) return new Promise(() => {});
      return Real.prototype.resume.call(this);
    }
  }
  window.AudioContext = PolicedContext;
  window.webkitAudioContext = PolicedContext;
  const mark = label => tap.marks.push([label, tap.ctxs.map(c => c.currentTime)]);
  document.addEventListener('click', e => { const id = e.target && e.target.id; mark('click:' + (id || '?')); }, true);
  window.addEventListener('keydown', e => { if (e.isTrusted) mark('keydown:' + e.code); }, true);
  const SRC = `registerProcessor('smoke-tap', class extends AudioWorkletProcessor {
    process(inputs) {
      const ch = inputs[0] && inputs[0].length ? inputs[0][0] : null;
      this.port.postMessage([currentTime, ch ? ch.slice() : null]);
      return true;
    } });`;
  const connect = AudioNode.prototype.connect;
  const recorders = new WeakMap();
  function recorder(ctx) {
    let p = recorders.get(ctx);
    if (!p) {
      const url = URL.createObjectURL(new Blob([SRC], { type: 'text/javascript' }));
      const index = tap.ctxs.indexOf(ctx);
      p = ctx.audioWorklet.addModule(url).then(() => {
        const node = new AudioWorkletNode(ctx, 'smoke-tap', { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1] });
        node.port.onmessage = e => tap.quanta.push([index, e.data[0], e.data[1]]);
        const mute = new GainNode(ctx, { gain: 0 });
        connect.call(node, mute);
        connect.call(mute, ctx.destination);
        return node;
      }).catch(e => { tap.errors.push(String(e)); return null; });
      recorders.set(ctx, p);
    }
    return p;
  }
  AudioNode.prototype.connect = function (dest, output, input) {
    const r = connect.apply(this, arguments);
    if (dest instanceof AudioDestinationNode) {
      const src = this;
      recorder(dest.context).then(node => { if (node) connect.call(src, node, output || 0); });
    }
    return r;
  };
})();"""

READ_CANVAS = """() => {
  const c = document.getElementById('screen');
  const o = document.createElement('canvas'); o.width = 256; o.height = 240;
  const g = o.getContext('2d'); g.drawImage(c, 0, 0);
  const d = g.getImageData(0, 0, 256, 240).data;
  const rgb = new Uint8Array(256 * 240 * 3);
  for (let i = 0, j = 0; i < d.length; i += 4) { rgb[j++] = d[i]; rgb[j++] = d[i + 1]; rgb[j++] = d[i + 2]; }
  let bin = '';
  for (let i = 0; i < rgb.length; i += 0x8000) bin += String.fromCharCode.apply(null, rgb.subarray(i, i + 0x8000));
  return btoa(bin);
}"""

# The sound heard at the destination since a mark: the last context's samples rendered at or after
# the mark's context time plus `settle`, as RMS with DC removed, and how many there were.
READ_SOUND = """([label, settle]) => {
  const t = window.__smoke;
  if (!t) return {error: 'no tap on the page'};
  const ctx = t.ctxs.length - 1;
  const m = t.marks.filter(m => m[0] === label).pop();
  if (ctx < 0) return {error: 'the page made no AudioContext'};
  if (!m) return {error: 'the gesture was not seen'};
  const from = (m[1][ctx] ?? 0) + settle;
  let n = 0, sum = 0, sq = 0;
  for (const [c, time, s] of t.quanta) {
    if (c !== ctx || time < from || !s) continue;
    for (const v of s) { n++; sum += v; sq += v * v; }
  }
  const mean = n ? sum / n : 0;
  return {n, rms: n ? Math.sqrt(Math.max(0, sq / n - mean * mean)) : 0, rate: t.ctxs[ctx].sampleRate,
          state: t.ctxs[ctx].state, errors: t.errors};
}"""

# Plays the paused page as it would run: one frame each 1/60 s for `ms`, through nesTest.step.
PLAY_JS = """ms => new Promise(done => {
  const t0 = performance.now();
  let ran = 0;
  const id = setInterval(() => {
    const due = Math.floor((performance.now() - t0) / (1000 / 60.0988));
    if (due > ran) { window.nesTest.step(due - ran); ran = due; }
    if (performance.now() - t0 >= ms) { clearInterval(id); done(ran); }
  }, 4);
})"""


class Tab:
    """A fresh browser context with the page open and paused (frame-exact: frames move only by
    nesTest.step). Counts main-frame navigations, and marks window once loaded (a reload loses it)."""

    def __init__(self, browser, url):
        self.context = browser.new_context(viewport={"width": 1000, "height": 800})
        self.context.set_default_timeout(15000)
        self.context.add_init_script(INIT_JS)
        self.page = page = self.context.new_page()
        self.navigations = 0

        def nav(frame):
            if frame == page.main_frame:
                self.navigations += 1
        page.on("framenavigated", nav)
        page.goto(url, wait_until="load", timeout=20000)
        page.evaluate("window.__smoke_loaded = 'once'")
        page.evaluate("window.nesTest.pause()")

    def close(self):
        try:
            self.context.close()
        except Exception:
            pass

    def loaded_once(self) -> bool:
        return self.navigations == 1 and self.page.evaluate("window.__smoke_loaded") == "once"

    def loads(self) -> int:
        return self.page.evaluate("window.nesTest.loads")

    def frames(self) -> int:
        return self.page.evaluate("window.nesTest.frames")

    def step(self, n: int) -> int:
        return self.page.evaluate("n => window.nesTest.step(n)", n) if n > 0 else self.frames()

    def canvas(self) -> bytes:
        return base64.b64decode(self.page.evaluate(READ_CANVAS))

    def message(self) -> str:
        loc = self.page.locator("#message")
        return loc.inner_text().strip() if loc.count() and loc.is_visible() else ""

    def wait_loads(self, n: int, seconds=5.0) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.loads() >= n:
                return True
            time.sleep(0.05)
        return self.loads() >= n

    def pick(self, path: Path):
        """A load through the file input; returns (loaded, why not)."""
        before = self.loads()
        self.page.set_input_files("#rom-file", str(path))
        if not self.wait_loads(before + 1):
            msg = self.message()
            return False, f"nesTest.loads stayed at {self.loads()} after the pick" + (f"; #message: {msg}" if msg else "")
        return True, ""

    def fresh(self, path: Path, frame: int):
        ok, why = self.pick(path)
        if not ok:
            raise RuntimeError(f"a reload through the file input failed: {why}")
        self.step(frame)

    def hold(self, key: str, frames: int):
        """The key held down for this many frames, then let go."""
        self.page.keyboard.down(key)
        try:
            self.step(frames)
        finally:
            self.page.keyboard.up(key)


# ---------------------------------------------------------------- the checks of one subject

class Halt(Exception):
    """The browser is gone: nothing more can run."""


class Subject:
    def __init__(self, s: dict, run_dir: Path, url: str, browser, record):
        self.s, self.dir, self.url, self.browser, self.record = s, run_dir, url, browser, record
        self.results, self.tabs, self.title_canvas = {}, [], None
        ref = SMOKE / "references" / f"{s['name']}.png"
        self.ref = read_png(ref) if ref.is_file() else None

    def png(self, check: str, rgb: bytes) -> str:
        name = f"{self.s['name']}-{check}.png"
        write_png(self.dir / name, rgb)
        return name

    def tab(self) -> Tab:
        t = Tab(self.browser, self.url)
        self.tabs.append(t)
        return t

    def put(self, check, result, detail, png=None):
        self.results[check] = (result, detail, png)
        self.record(self.s, check, result, detail, png)

    def passed(self, check) -> bool:
        return self.results.get(check, ("",))[0] == "PASSED"

    def run(self, deadline: float):
        """Every check, in order; a check whose prerequisite didn't pass is NOT RUN."""
        s, n = self.s, self.s["frame"]
        tab = None
        order = ["file-input", "title", "game-menu", "key", "reset", "power", "sound", "loaded-once"]
        broken = None
        for check in order:
            need = NEEDS.get(check)
            if check == "game-menu" and not s["menu"]:
                self.put(check, "N/A", "a probe has no game-menu entry")
                continue
            if broken:
                self.put(check, "NOT RUN", broken)
                continue
            if need and not self.passed(need):
                self.put(check, "NOT RUN", f"needs {need}, which didn't pass")
                continue
            if check == "sound" and s["sound"] == "start" and not self.passed("key"):
                self.put(check, "NOT RUN", "its window comes after Start (a silent title), and key didn't pass")
                continue
            if time.monotonic() > deadline:
                broken = "the run's time ran out (J5: 5 minutes) before this check"
                self.put(check, "NOT RUN", broken)
                continue
            try:
                if check == "file-input":
                    tab = self.tab()
                result, detail, png = getattr(self, "check_" + check.replace("-", "_"))(tab, n)
                self.put(check, result, detail, png)
            except Exception as e:      # the check couldn't finish
                if not self.browser_alive():
                    self.put(check, "FAILED", f"Chromium went away: {str(e).splitlines()[0][:200]}")
                    self.close()
                    raise Halt()
                self.put(check, "FAILED", f"couldn't finish: {str(e).splitlines()[0][:300]}")
                broken = f"{check} couldn't finish, so the page's state is unknown"
        self.close()

    def close(self):
        for t in self.tabs:
            t.close()

    def browser_alive(self) -> bool:
        if not self.browser.is_connected():
            return False
        try:
            self.browser.new_context().close()
            return True
        except Exception:
            return False

    # -- each check: returns (result, detail, png)
    def check_file_input(self, tab, n):
        before = tab.loads()
        ok, why = tab.pick(self.s["path"])
        if not ok:
            return "FAILED", why, None
        frames, msg = tab.frames(), tab.message()
        if frames != 0:
            return "FAILED", f"the console was at frame {frames} after the load, not 0", None
        if msg:
            return "FAILED", f"#message says: {msg}", None
        return "PASSED", f"loaded {self.s['path'].name} at frame 0 (nesTest.loads {before} -> {before + 1})", None

    def check_title(self, tab, n):
        tab.step(n)
        rgb = self.title_canvas = tab.canvas()
        png = self.png("title", rgb)
        if self.ref is None:
            return "FAILED", f"no committed reference smoke/references/{self.s['name']}.png", png
        d = differing(rgb, self.ref)
        if d:
            return "FAILED", f"the canvas at frame {n} differs from the reference in {d} pixels", png
        return "PASSED", f"the canvas at frame {n} is the reference, colour for colour", png

    def check_game_menu(self, tab, n):
        entry = self.page_entry(tab)
        if entry is None:
            return "FAILED", f"#game-menu has no entry for {self.s['file']}", None
        before = tab.loads()
        entry.click()
        if not tab.wait_loads(before + 1):
            msg = tab.message()
            return "FAILED", f"nesTest.loads stayed at {tab.loads()} after the click" + (f"; #message: {msg}" if msg else ""), None
        if tab.frames() != 0:
            return "FAILED", f"the console was at frame {tab.frames()} after the menu's load, not 0", None
        tab.step(n)
        rgb = tab.canvas()
        png = self.png("game-menu", rgb)
        d = differing(rgb, self.title_canvas)
        if d:
            return "FAILED", f"at frame {n} the menu's load differs from the file input's in {d} pixels", png
        return "PASSED", f"the menu entry loaded it: frame {n} is the file input's, pixel for pixel", png

    def page_entry(self, tab):
        loc = tab.page.locator(f'#game-menu [data-rom$="/{quote(self.s["file"])}"]')
        return loc.first if loc.count() else None

    def check_key(self, tab, n):
        """Each key held from frame N for K frames, against the same K frames with no key, frame by frame
        (a title that blinks can show the same picture at any one frame either way)."""
        k, path = self.s["key_frames"], self.s["path"]

        def frames_after(key=None) -> list:
            tab.fresh(path, n)
            if key:
                tab.page.keyboard.down(key)
            try:
                return [(tab.step(1), tab.canvas())[1] for _ in range(k)]
            finally:
                if key:
                    tab.page.keyboard.up(key)
        idle = frames_after()
        keys = BUTTON_KEYS if not self.s["menu"] else [("Start", "Enter")]
        dead, shown = [], None
        for button, key in keys:
            pressed = frames_after(key)
            moved = [p for p, q in zip(pressed, idle) if differing(p, q)]
            if moved:
                shown = shown or moved[0]
            else:
                dead.append(f"{button} ({key})")
                shown = shown or pressed[-1]
        png = self.png("key", shown)
        if dead:
            return "FAILED", f"held from frame {n} for {k} frames, {', '.join(dead)} left the screen as with no key", png
        return "PASSED", (f"each of the {len(keys)} keys changed the screen within {k} frames" if len(keys) > 1
                          else f"Start held from frame {n} changed the screen within {k} frames"), png

    def to_play(self, tab, n):
        """From a fresh load: the title, then Start pressed for 5 frames and `play` frames of play."""
        tab.fresh(self.s["path"], n)
        tab.hold("Enter", 5)
        tab.step(self.s["play"])

    def check_reset(self, tab, n):
        self.to_play(tab, n)
        played = tab.canvas()
        tab.page.click("#reset")
        tab.step(n)
        rgb = tab.canvas()
        png = self.png("reset", rgb)
        d = differing(rgb, self.ref)
        if d:
            same = " (the screen of play was the title too)" if differing(played, self.ref) == 0 else ""
            return "FAILED", f"{n} frames after Reset the canvas differs from the title in {d} pixels{same}", png
        return "PASSED", f"Reset from play brought back the title by frame {n} after it", png

    def check_power(self, tab, n):
        self.to_play(tab, n)
        tab.page.click("#power")
        frames = tab.frames()
        if frames != 0:
            rgb = tab.canvas()
            return "FAILED", f"after Power the console was at frame {frames}, not 0 (not a fresh load)", self.png("power", rgb)
        tab.step(n)
        rgb = tab.canvas()
        png = self.png("power", rgb)
        d = differing(rgb, self.ref)
        if d:
            return "FAILED", f"at frame {n} after Power the canvas differs from the title in {d} pixels", png
        return "PASSED", f"Power from play was a fresh load: frame 0, and the title at frame {n}", png

    def check_sound(self, tab, n):
        t = self.tab()
        ok, why = t.pick(self.s["path"])
        if not ok:
            return "FAILED", f"the sound's tab couldn't load it: {why}", None
        t.step(n)
        if self.s["sound"] == "start":
            label = "keydown:Enter"
            at = 0
            for offset in self.s["sound_starts"]:
                t.step(offset - at)
                t.hold("Enter", 5)
                at = offset + 5
            where = "after Start" if len(self.s["sound_starts"]) == 1 else f"after Start at +{self.s['sound_starts']} frames"
        else:
            label = "click:screen"
            t.page.click("#screen")
            where = "on the title, after a click"
        t.page.evaluate(PLAY_JS, int(SOUND_S * 1000))
        got = t.page.evaluate(READ_SOUND, [label, SETTLE_S])
        if "error" in got:
            return "FAILED", got["error"], None
        if not got["n"]:
            return "FAILED", f"nothing reached the destination {where} (the context is {got['state']})", None
        db = 20 * math.log10(got["rms"]) if got["rms"] > 0 else -math.inf
        heard = f"{got['n'] / got['rate']:.2f} s at the destination {where}: RMS {db:.1f} dBFS"
        if db <= FLOOR_DBFS:
            return "FAILED", f"silent: {heard} (floor {FLOOR_DBFS:.0f} dBFS)", None
        return "PASSED", heard, None

    def check_loaded_once(self, tab, n):
        counts = [t.navigations for t in self.tabs]
        if all(t.loaded_once() for t in self.tabs):
            return "PASSED", f"each of its {len(self.tabs)} tabs loaded the page once", None
        return "FAILED", f"the page loaded more than once in a tab (navigations per tab: {counts})", None


# ---------------------------------------------------------------- the report

def tally(rows: list) -> dict:
    return {k: sum(1 for r in rows if r["result"] == k) for k in ("PASSED", "FAILED", "NOT RUN", "N/A")}


def write_report(run_dir: Path, report: dict):
    (run_dir / "report.json").write_text(json.dumps(report, indent=1) + "\n")
    md = [f"# Smoke run {run_dir.name}", "",
          f"- **result: {report['result']}** ({report['counts']['PASSED']} passed, {report['counts']['FAILED']} failed, "
          f"{report['counts']['NOT RUN']} not run, {report['counts']['N/A']} n/a)",
          f"- commit: {report['commit'] or '(unknown)'}",
          f"- tree: {'dirty' if report['dirty'] else 'clean'}",
          f"- started: {report['started']}, took {report['seconds']:.0f} s, served on port {report['port']}",
          f"- mappers (the core's registry): {', '.join(map(str, report['mappers'])) or 'none'}"
          + (f"; asked for: {', '.join(map(str, report['only']))}" if report["only"] is not None else ""),
          "- subjects: " + (", ".join(f"{s['name']} (mapper {s['mapper']}, {'menu and file input' if s['menu'] else 'probe'})"
                                      for s in report["subjects"]) or "none")]
    if report.get("error"):
        md.append(f"- error: {report['error']}")
    md += ["", "| mapper | subject | check | result | detail | frame |", "|---|---|---|---|---|---|"]
    for r in report["checks"]:
        detail = r["detail"].replace("|", "/")
        frame = f"[png]({r['png']})" if r["png"] else ""
        md.append(f"| {r['mapper']} | {r['subject'] or '-'} | {r['check']} | {r['result']} | {detail} | {frame} |")
    (run_dir / "report.md").write_text("\n".join(md) + "\n")


def link_latest(run_dir: Path):
    tmp = run_dir.parent / f".latest-{os.getpid()}"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(run_dir.name)
    os.replace(tmp, run_dir.parent / "latest")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="npm run smoke --", description="The smoke run (cases/CONTRACT-I10.md).")
    ap.add_argument("--mapper", type=int, action="append", help="only this mapper's checks (any number of times)")
    args = ap.parse_args(argv)
    t0, started = time.monotonic(), datetime.now(timezone.utc)
    commit = git("rev-parse", "HEAD")
    dirty = bool(git("status", "--porcelain"))
    out = ROOT / "out" / "smoke"
    out.mkdir(parents=True, exist_ok=True)
    run_dir = out / f"{started.strftime('%Y%m%dT%H%M%S.%fZ')}-{commit[:7] or 'nocommit'}-{os.getpid()}"
    run_dir.mkdir()

    mappers, why = registry()
    only = sorted(set(args.mapper)) if args.mapper else None
    asked = [m for m in (mappers or []) if only is None or m in only]
    subjects = subjects_for(asked, run_dir)
    rows, results = [], {}

    def record(subject, check, result, detail, png=None):
        m = subject["mapper"] if isinstance(subject, dict) else subject
        name = subject["name"] if isinstance(subject, dict) else None
        results[(m, name, check)] = {"mapper": m, "subject": name, "check": check, "result": result,
                                     "detail": detail, "png": png}
        print(f"mapper {m} · {name or '-'} · {check}: {result} ({detail})", flush=True)

    for m in asked:
        names = [s["name"] for s in subjects if s["mapper"] == m]
        if names:
            record(m, "covered", "PASSED", f"covered by {', '.join(names)}")
        else:
            record(m, "covered", "FAILED", f"no vendored game or probe covers mapper {m}")
            for c in CHECKS:
                record(m, c, "NOT RUN", "the mapper isn't covered")

    error, port = why or None, None
    pending = [s for s in subjects]
    if pending:
        server = Server()
        port = server.port
        try:
            if server.proc is None:
                error = server.why
            else:
                error = drive(pending, run_dir, server.url, record, t0 + BUDGET_S) or error
        finally:
            server.stop()
    for s in subjects:              # whatever couldn't run is listed, never left out
        for c in CHECKS:
            if (s["mapper"], s["name"], c) not in results:
                if c == "game-menu" and not s["menu"]:
                    record(s, c, "N/A", "a probe has no game-menu entry")
                else:
                    record(s, c, "NOT RUN", f"not run: {error or 'the run stopped before it'}")

    for m in asked:
        rows.append(results[(m, None, "covered")])
        keys = [s["name"] for s in subjects if s["mapper"] == m] or [None]
        for name in keys:
            rows += [results[(m, name, c)] for c in CHECKS]
    counts = tally(rows)
    failures = counts["FAILED"] + counts["NOT RUN"]
    result = "PASSED" if counts["PASSED"] > 0 and failures == 0 else "FAILED"
    report = {"commit": commit, "dirty": dirty, "started": started.isoformat(timespec="seconds").replace("+00:00", "Z"),
              "seconds": round(time.monotonic() - t0, 1), "port": port, "mappers": mappers or [], "only": only,
              "subjects": [{"name": s["name"], "mapper": s["mapper"], "menu": s["menu"], "rom": s["rom"]} for s in subjects],
              "checks": rows, "counts": counts, "failures": failures, "result": result}
    if error:
        report["error"] = error
    if not rows:
        report["error"] = (error + "; " if error else "") + "no check ran"
    write_report(run_dir, report)
    link_latest(run_dir)
    print(f"smoke run {result}: {counts['PASSED']} passed, {failures} failed or not run; "
          f"report: {run_dir.relative_to(ROOT)}/report.md", flush=True)
    return 0 if result == "PASSED" else 1


def drive(subjects: list, run_dir: Path, url: str, record, deadline: float):
    """Every subject's checks in headless Chromium; returns why the run stopped early, or None."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True, timeout=60000)
        except Exception as e:
            return f"Chromium wouldn't start: {str(e).splitlines()[0][:300]}"
        try:
            for s in subjects:
                Subject(s, run_dir, url, browser, record).run(deadline)
        except Halt:
            return "Chromium went away mid-run"
        finally:
            try:
                browser.close()
            except Exception:
                pass
    return None


if __name__ == "__main__":
    sys.exit(main())
