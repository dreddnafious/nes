"""I5's shared helpers: the page over time (pacing against the display, a hidden tab, the sound buffer and
the heap over a long sitting) and the core over 30 minutes. Nothing here grades the build; each case
asserts its own promise (see CONTRACT-I5.md).

- `tone_probe`: an NROM game, built from the spec, that plays an unbroken 440 Hz tone and adds a louder
  880 Hz one while A is held, so an underrun (exact silence) and a key's latency both show at the output.
- `SIM_JS`: requestAnimationFrame driven at a stated rate of real time (as I3's RAF_STUB), recording each
  refresh's time, `nesTest.frames` and the callbacks it ran into a preallocated buffer, plus a hidden tab simulated as Chrome
  documents it: `visibilityState`/`hidden`, `visibilitychange`, rAF stopped, timers throttled to >= 1 s,
  and a clock that can be moved forward while hidden.
- `TAP5_JS`: I4's tap on the AudioContext's destination, reduced in the page to each render quantum's
  audio-clock time, RMS and longest run of exact zeros, in preallocated buffers (so the tap doesn't grow
  the heap it measures).
- `I5Tab`: I3's Tab with both installed, and the page's heap through CDP after a forced GC.
- `drive_i5`: the core through `node --expose-gc cases/drive_i5.ts`.
"""
import json
import statistics
from pathlib import Path

from conftest import ROOT, run
from i3_support import Tab, ines, nes15
from i6_support import i6_oracle
from i7_support import i7_oracle
from i8_support import assemble, i8_oracle

CASES = ROOT / "cases"
NODE = "node"
NTSC_FPS = 1789772.7272 / 29780.5        # 60.0988 frames a second
X = "KeyX"                                 # controller 1's A (CONTRACT-I3)


# ---------------------------------------------------------------- the tone probe

def tone_probe() -> bytes:
    """NROM-128. Pulse 1: 440 Hz (period $FD), duty 50 %, constant volume 4, length counter halted, from
    power-on. Each NMI strobes controller 1 and reads A: pulse 2 plays 880 Hz (period $7E) at volume 15
    while A is held, volume 0 otherwise. The APU frame IRQ is off ($4017 = $40); rendering stays off."""
    prog = [
        "reset:", ("sei", None), ("cld", None), ("ldx#", 0xFF), ("txs", None),
        ("lda#", 0), ("sta", 0x2000), ("sta", 0x2001),
        ("bit", 0x2002), "w1:", ("bit", 0x2002), ("bpl", "w1"), "w2:", ("bit", 0x2002), ("bpl", "w2"),
        ("lda#", 0x40), ("sta", 0x4017), ("lda#", 0x03), ("sta", 0x4015),
        ("lda#", 0xB4), ("sta", 0x4000), ("lda#", 0x08), ("sta", 0x4001), ("lda#", 0xFD), ("sta", 0x4002),
        ("lda#", 0x00), ("sta", 0x4003),
        ("lda#", 0xB0), ("sta", 0x4004), ("lda#", 0x08), ("sta", 0x4005), ("lda#", 0x7E), ("sta", 0x4006),
        ("lda#", 0x00), ("sta", 0x4007),
        ("lda#", 0x80), ("sta", 0x2000),
        "main:", ("jmp", "main"),
        "nmi:", ("pha", None), ("lda#", 1), ("sta", 0x4016), ("lda#", 0), ("sta", 0x4016),
        ("lda", 0x4016), ("and#", 1), ("beq", "off"),
        ("lda#", 0xBF), ("sta", 0x4004), ("pla", None), ("rti", None),
        "off:", ("lda#", 0xB0), ("sta", 0x4004), ("pla", None), ("rti", None),
        "irq:", ("rti", None),
    ]
    code, labels = assemble(prog, 0xC000)
    prg = bytearray(0x4000)
    prg[:len(code)] = code
    prg[0x3FFA:0x4000] = bytes([labels["nmi"] & 0xFF, labels["nmi"] >> 8, labels["reset"] & 0xFF,
                                labels["reset"] >> 8, labels["irq"] & 0xFF, labels["irq"] >> 8])
    return ines(bytes(prg), bytes(0x2000), 0x01)


def write(tmp_path, name: str, data: bytes) -> str:
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


# ---------------------------------------------------------------- what the cases install in the page

SIM_JS = """(() => {
  // I5's pacing stub and hidden-tab simulation (cases/i5_support.py, CONTRACT-I5.md).
  const HZ = %(hz)s, CAP = 40000;
  const realNow = performance.now.bind(performance);
  const realSetTimeout = window.setTimeout.bind(window), realSetInterval = window.setInterval.bind(window);
  let offset = 0, hidden = false;
  performance.now = () => realNow() + offset;
  Object.defineProperty(document, 'visibilityState', { get: () => hidden ? 'hidden' : 'visible', configurable: true });
  Object.defineProperty(document, 'hidden', { get: () => hidden, configurable: true });
  window.setTimeout = (fn, ms, ...a) => realSetTimeout(fn, hidden ? Math.max(ms || 0, 1000) : ms, ...a);
  window.setInterval = (fn, ms, ...a) => realSetInterval(fn, hidden ? Math.max(ms || 0, 1000) : ms, ...a);
  const sim = window.__i5 = { n: 0, ticks: new Float64Array(3 * CAP), hidden: () => hidden,
    hide() { hidden = true; document.dispatchEvent(new Event('visibilitychange')); },
    show(jumpMs) { offset += jumpMs || 0; hidden = false; document.dispatchEvent(new Event('visibilitychange')); } };
  const period = 1000 / HZ;
  let queue = new Map(), nextId = 1;
  window.requestAnimationFrame = cb => { const id = nextId++; queue.set(id, cb); return id; };
  window.cancelAnimationFrame = id => { queue.delete(id); };
  let due = realNow() + period;
  function tick() {
    if (!hidden) {
      const now = performance.now();
      const run = queue; queue = new Map();
      for (const cb of run.values()) { try { cb(now); } catch (e) { realSetTimeout(() => { throw e; }); } }
      const t = window.nesTest;
      if (sim.n < CAP) {
        sim.ticks[3 * sim.n] = performance.now();
        sim.ticks[3 * sim.n + 1] = t && typeof t.frames === 'number' ? t.frames : -1;
        sim.ticks[3 * sim.n + 2] = run.size;
        sim.n++;
      }
    }
    due += period;
    if (due < realNow() - 50) due = realNow() + period;
    realSetTimeout(tick, Math.max(0, due - realNow()));
  }
  realSetTimeout(tick, period);
})();"""

TAP5_JS = r"""(() => {
  // I5's tap: I4's (cases/i4_support.py TAP_JS), each render quantum reduced to its audio-clock time,
  // its RMS and its longest run of exact zeros, stored in preallocated buffers.
  const CAP = 120000;
  const tap = window.__i5tap = { n: 0, time: new Float64Array(CAP), rms: new Float32Array(CAP),
    zrun: new Uint16Array(CAP), ctx: new Uint8Array(CAP), marks: [], ctxs: [], errors: [] };
  const Real = window.AudioContext;
  if (!Real) { tap.errors.push('no AudioContext in this browser'); return; }
  class TappedContext extends Real {
    constructor(options) { super(options); tap.ctxs.push(this); }
  }
  window.AudioContext = TappedContext;
  window.webkitAudioContext = TappedContext;
  const mark = label => {
    if (tap.marks.length < 2000) tap.marks.push([label, performance.now(), tap.ctxs.map(c => c.currentTime)]);
  };
  document.addEventListener('change', e => { const id = e.target && e.target.id; if (id) mark('change:' + id); }, true);
  document.addEventListener('click', e => { const id = e.target && e.target.id; if (id) mark('click:' + id); }, true);
  window.addEventListener('keydown', e => { if (e.isTrusted) mark('keydown:' + e.code); }, true);
  window.addEventListener('keyup', e => { if (e.isTrusted) mark('keyup:' + e.code); }, true);
  document.addEventListener('visibilitychange', () => mark('visibility:' + document.visibilityState), true);
  const SRC = `registerProcessor('i5-tap', class extends AudioWorkletProcessor {
    process(inputs) {
      const ch = inputs[0] && inputs[0].length ? inputs[0][0] : null;
      let sum = 0, run = 0, best = 0;
      if (ch) {
        for (let i = 0; i < ch.length; i++) {
          const v = ch[i];
          sum += v * v;
          if (v === 0) { run++; if (run > best) best = run; } else run = 0;
        }
        this.port.postMessage([currentTime, Math.sqrt(sum / ch.length), best]);
      } else {
        this.port.postMessage([currentTime, -1, 128]);
      }
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
        const node = new AudioWorkletNode(ctx, 'i5-tap', { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1] });
        node.port.onmessage = e => {
          if (tap.n >= CAP) return;
          const k = tap.n++;
          tap.time[k] = e.data[0]; tap.rms[k] = e.data[1]; tap.zrun[k] = e.data[2]; tap.ctx[k] = index;
        };
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

READ_TAP5 = """() => {
  const t = window.__i5tap;
  if (!t) return null;
  const n = t.n;
  return { time: Array.from(t.time.subarray(0, n)), rms: Array.from(t.rms.subarray(0, n)),
           zrun: Array.from(t.zrun.subarray(0, n)), ctx: Array.from(t.ctx.subarray(0, n)),
           marks: t.marks, errors: t.errors, rates: t.ctxs.map(c => c.sampleRate) };
}"""

READ_TICKS = """() => { const s = window.__i5; return s ? Array.from(s.ticks.subarray(0, 3 * s.n)) : null; }"""


class Quanta:
    """What reached the destination, a render quantum at a time: (context, audio-clock time, RMS, longest
    run of exact zeros); RMS -1 for a quantum with nothing connected. Plus the marks (loads, clicks, keys,
    visibility changes) with every context's audio-clock time at that moment."""

    def __init__(self, raw: dict):
        raw = raw or {"time": [], "rms": [], "zrun": [], "ctx": [], "marks": [], "errors": ["no tap"], "rates": []}
        self.rows = list(zip(raw["ctx"], raw["time"], raw["rms"], raw["zrun"]))
        self.marks, self.errors, self.rates = raw["marks"], raw["errors"], raw["rates"]

    def ctx(self) -> int:
        """The context the page plays through (the last one heard)."""
        assert self.rows, f"nothing reached the destination ({self.errors})"
        return self.rows[-1][0]

    def marks_of(self, prefix: str) -> list:
        """(label, performance.now(), audio-clock time in the played context) of each mark. A mark made
        before that context existed (a load that creates it) takes the context's first rendered quantum."""
        c = self.ctx()
        first = min((r[1] for r in self.rows if r[0] == c), default=None)
        return [(m[0], m[1], m[2][c] if c < len(m[2]) else first) for m in self.marks if m[0].startswith(prefix)]

    def window(self, t0: float, t1: float) -> list:
        c = self.ctx()
        return [r for r in self.rows if r[0] == c and t0 <= r[1] < t1]

    def underruns(self, t0: float, t1: float, minrun=8) -> list:
        """The audio-clock times in [t0, t1) of quanta holding a run of at least minrun exact zeros."""
        return [r[1] for r in self.window(t0, t1) if r[3] >= minrun]

    def median_rms(self, t0: float, t1: float) -> float:
        v = [r[2] for r in self.window(t0, t1) if r[2] >= 0]
        return statistics.median(v) if v else 0.0

    def onset(self, t0: float, level: float, within=1.0):
        """The audio-clock time of the first quantum from t0 on (within `within` s) whose RMS reaches
        level, or None."""
        for r in self.window(t0, t0 + within):
            if r[2] >= level:
                return r[1]
        return None


class I5Tab(Tab):
    """I3's Tab with I5's tap and its pacing stub (at `hz` refreshes a second of real time) installed
    before the page's scripts."""

    def __init__(self, browser, site, hz=60):
        super().__init__(browser, site, init_script=TAP5_JS + "\n" + SIM_JS % {"hz": hz})
        self._cdp = None

    def hide(self) -> int:
        """Hide the tab, and return nesTest.frames at that moment: read in the same task, so no refresh
        can land between the reading and the hiding."""
        return self.page.evaluate("() => { const f = window.nesTest ? window.nesTest.frames : -1; "
                                  "window.__i5.hide(); return f; }")

    def show(self, jump_ms=0):
        self.page.evaluate("ms => window.__i5.show(ms)", jump_ms)

    def ticks(self) -> list:
        """[(performance.now(), nesTest.frames)] after each refresh the stub ran (frames -1: no hooks)."""
        raw = self.page.evaluate(READ_TICKS) or []
        return [(raw[i], int(raw[i + 1])) for i in range(0, len(raw), 3)]

    def callbacks(self) -> list:
        """[(performance.now(), how many requestAnimationFrame callbacks that refresh ran)]."""
        raw = self.page.evaluate(READ_TICKS) or []
        return [(raw[i], int(raw[i + 2])) for i in range(0, len(raw), 3)]

    def now(self) -> float:
        return self.page.evaluate("performance.now()")

    def quanta(self) -> Quanta:
        return Quanta(self.page.evaluate(READ_TAP5))

    def heap(self) -> int:
        """The page's JS heap in use after a forced garbage collection (CDP)."""
        if self._cdp is None:
            self._cdp = self.context.new_cdp_session(self.page)
        self._cdp.send("HeapProfiler.enable")
        self._cdp.send("HeapProfiler.collectGarbage")
        self._cdp.send("HeapProfiler.collectGarbage")
        return int(self._cdp.send("Runtime.getHeapUsage")["usedSize"])

    def wait(self, seconds: float):
        self.page.wait_for_timeout(int(seconds * 1000))


def increments(ticks: list, t0: float, t1: float) -> list:
    """The frames each refresh in [t0, t1) emulated (the change in nesTest.frames from the refresh before)."""
    out = []
    for (ta, fa), (tb, fb) in zip(ticks, ticks[1:]):
        if t0 <= tb < t1 and fa >= 0 and fb >= 0:
            out.append(fb - fa)
    return out


def fit(points: list):
    """Least-squares slope and intercept of [(x, y)]."""
    n = len(points)
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    slope = sum((x - mx) * (y - my) for x, y in points) / sum((x - mx) ** 2 for x, _ in points)
    return slope, my - slope * mx


# ---------------------------------------------------------------- the core over time

def drive_i5(request: dict, timeout=900) -> dict:
    """Run the core through `node --expose-gc cases/drive_i5.ts`. Always returns a dict; a failed run has
    ok False and says why."""
    r = run([NODE, "--expose-gc", str(CASES / "drive_i5.ts"), json.dumps(request)], timeout=timeout)
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.startswith("{")]
    if r.timed_out:
        return {"ok": False, "stage": "timeout", "error": f"no result within {timeout}s"}
    if not lines:
        return {"ok": False, "stage": "process", "error": repr(r)[:2000]}
    return json.loads(lines[-1])


def corpus_games() -> list:
    """(name, path) of every homebrew game and demo the corpus fetches by hash (I3, I6, I7, I8)."""
    return [("nes15 (NROM)", nes15()),
            ("240p Test Suite mini (mapper 2)", i6_oracle("240pee")),
            ("Cheril the Goddess (mapper 3)", i6_oracle("cheril-the-goddess")),
            ("Nova the Squirrel (MMC1)", i7_oracle("nova-the-squirrel")),
            ("Minekart Madness (MMC1)", i7_oracle("minekart-madness")),
            ("nesdoug's 33_MMC3 (MMC3)", i8_oracle("nesdoug-33_MMC3"))]


GAME_IDS = ["nes15", "240pee", "cheril", "nova", "minekart", "nesdoug"]


def game_path(i: int) -> Path:
    return Path(corpus_games()[i][1])
