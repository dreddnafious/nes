"""I4's shared helpers: the APU and the sound, measured the way their consumers meet them. Nothing here
grades the build; each case asserts its own promise (see CONTRACT-I4.md).

- `i4_oracle`: I4's test ROMs, fetched from their pinned URLs (cases/i4_oracles.json) and checked by
  SHA-256, under the same rules as conftest.oracle.
- `drive_apu` / `audio`: the core's sample stream through the frozen driver cases/drive_apu.ts.
- Measurements on a sample stream, written from their definitions: the level in dBFS with DC removed
  (J1h), the pitch (rising crossings of the mid level, with hysteresis), the duty, the most common level.
- The spec's numbers (NESdev "APU", "APU Pulse", "APU Triangle", "APU Noise", "APU DMC", "APU Mixer",
  "APU Envelope", "APU Sweep", "APU Length Counter"): the pitch formulas, the noise and DMC period tables,
  the noise shift register, and the mixer formula.
- `apu_probe`: a small NROM program, built in the case, that writes a script of APU register writes, each
  at a stated frame (counted in NMIs), and counts its own idle-loop iterations per frame, so DMC DMA's
  stolen cycles show up in the count (J4c).
- The screen readers for the two kinds of APU ROM that report only on screen (blargg's 2005 APU tests,
  and the older shell of dmc_dma_during_read4), each with the font the ROM itself loads.
- nes15's message line ("SOLVED!" when the puzzle is done), read off a frame with the ROM's own CHR.
- `AudioTab`: I3's Tab with a tap on the page's sound: an init script records every render quantum that
  reaches the AudioContext's destination (wherever the page's nodes connect to it), can force the
  context's sample rate, and can simulate the browser's autoplay policy, which headless Chromium doesn't
  enforce.
"""
import array
import base64
import hashlib
import json
import math
import re
import struct
import urllib.request
from pathlib import Path

from conftest import NODE, ROOT, run
from i2_support import font_from_chr, glyph_mask
from i3_support import Tab, ines, nes15

CASES = ROOT / "cases"
CACHE = ROOT / "roms" / "cache"
MANIFEST = json.loads((CASES / "i4_oracles.json").read_text())
ORACLES = {o["id"]: o for o in MANIFEST["oracles"]}

CPU_HZ = 1789772.7272                # NTSC CPU clock (NESdev "Cycle reference chart")
FRAME_CYCLES = 29780.5               # CPU cycles in an NTSC frame, averaged over odd and even frames
FLOOR_DBFS = -60.0                   # J1h: the noise floor, after DC is removed
A440_PERIOD = 253                    # pulse period register for 440.4 Hz: CPU / (16 * 254)


# ---------------------------------------------------------------- oracles and the driver

def i4_oracle(oid: str) -> Path:
    """The path of an I4 oracle file, fetched from its pinned URL on first use and checked by SHA-256.
    A mismatch is an error, never a silent swap."""
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


def drive_apu(request: dict, timeout=180) -> dict:
    """Run the core API through cases/drive_apu.ts. Always returns a dict; a failed run has ok False
    and says why (its stage, error, or the process's exit)."""
    r = run([NODE, str(CASES / "drive_apu.ts"), json.dumps(request)], timeout=timeout)
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.startswith("{")]
    if r.timed_out:
        return {"ok": False, "stage": "timeout", "error": f"no result within {timeout}s", "ran": repr(r)}
    if not lines:
        return {"ok": False, "stage": "process", "error": repr(r)}
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return {"ok": False, "stage": "output", "error": lines[-1][:500]}


def short(res: dict) -> dict:
    return {k: (v if k not in ("samples", "peeks", "counts") else f"<{len(v) if v else 0}>")
            for k, v in res.items()}


def decode_samples(b64: str) -> array.array:
    a = array.array("f")
    a.frombytes(base64.b64decode(b64))
    return a


def audio(rom, frames: int, rate=48000, **kw) -> dict:
    """The core's samples over `frames` frames: the driver's result with "x" (the samples, an array of
    floats; empty when the run failed) added. It never raises: the case asserts res["ok"] itself."""
    req = {"mode": "audio", "rom": str(rom), "frames": frames, "rate": rate, **kw}
    res = drive_apu(req, timeout=240)
    res["x"] = decode_samples(res["samples"]) if res.get("ok") else array.array("f")
    return res


def why(res: dict) -> str:
    return f"the core gave no sound (CONTRACT-I4: setSampleRate, audioSamples): {short(res)}"


def frame_samples(rate: float) -> float:
    return rate * FRAME_CYCLES / CPU_HZ


# ---------------------------------------------------------------- measurements

def dbfs(x) -> float:
    """The level of a window in dBFS with its DC removed (J1h): 20 log10 of the RMS of x minus its mean,
    where a full-scale (+-1.0) square wave is 0 dBFS. Silence (or a constant) is -inf, reported -999."""
    n = len(x)
    if n == 0:
        return -999.0
    m = sum(x) / n
    v = sum((s - m) ** 2 for s in x) / n
    return 20 * math.log10(math.sqrt(v)) if v > 1e-30 else -999.0


def raw_dbfs(x) -> float:
    """The level with the DC left in (what J1h warns about)."""
    n = len(x)
    v = sum(s * s for s in x) / n if n else 0
    return 20 * math.log10(math.sqrt(v)) if v > 1e-30 else -999.0


def span(x, rate, t0: float, t1: float):
    """Samples from t0 to t1 seconds."""
    return x[int(t0 * rate):int(t1 * rate)]


def percentile(x, p: float) -> float:
    s = sorted(x)
    return s[min(len(s) - 1, max(0, int(p * (len(s) - 1))))]


def crossings(x):
    """Rising crossings of the mid level, with hysteresis at a quarter of the peak-to-peak (so ringing
    and droop near an edge can't make extra ones), each placed between samples by linear
    interpolation. The mid level and peak-to-peak come from the 1st and 99th percentiles."""
    if len(x) < 8:
        return [], 0.0
    lo, hi = percentile(x, 0.01), percentile(x, 0.99)
    mid, h = (lo + hi) / 2, (hi - lo) / 4
    if hi - lo <= 1e-6:
        return [], hi - lo
    out, up = [], x[0] > mid
    for i in range(1, len(x)):
        v = x[i]
        if not up and v > mid + h:
            up = True
            k = i
            while k > 0 and x[k - 1] > mid:
                k -= 1
            if k > 0:
                a, b = x[k - 1], x[k]
                out.append(k - 1 + (mid - a) / (b - a) if b != a else k)
        elif up and v < mid - h:
            up = False
    return out, hi - lo


def pitch(x, rate) -> float:
    """The frequency of a periodic signal in Hz, from its rising crossings (0.0 if fewer than 3)."""
    c, _ = crossings(x)
    if len(c) < 3:
        return 0.0
    return (len(c) - 1) * rate / (c[-1] - c[0])


def duty(x) -> float:
    """The fraction of each period spent above the mid level, over whole periods."""
    c, _ = crossings(x)
    if len(c) < 3:
        return 0.0
    lo, hi = percentile(x, 0.01), percentile(x, 0.99)
    mid = (lo + hi) / 2
    a, b = int(math.ceil(c[0])), int(c[-1])
    seg = x[a:b]
    return sum(1 for v in seg if v > mid) / len(seg) if seg else 0.0


def level_mode(x, step=0.002) -> float:
    """The most common level: the mean of the samples in the most populated bin of width `step`."""
    bins = {}
    for v in x:
        bins.setdefault(round(v / step), []).append(v)
    best = max(bins.values(), key=len)
    return sum(best) / len(best)


def peak_to_peak(x) -> float:
    return percentile(x, 0.995) - percentile(x, 0.005)


def swing(x) -> float:
    """A two-level wave's step: the most common level above the mid level minus the most common below."""
    lo, hi = min(x), max(x)
    mid = (lo + hi) / 2
    above = [v for v in x if v > mid]
    below = [v for v in x if v <= mid]
    return level_mode(above) - level_mode(below) if above and below else 0.0


# ---------------------------------------------------------------- the spec's numbers

def pulse_hz(t: int) -> float:
    """NESdev "APU Pulse": f = CPU / (16 (t + 1))."""
    return CPU_HZ / (16 * (t + 1))


def triangle_hz(t: int) -> float:
    """NESdev "APU Triangle": f = CPU / (32 (t + 1)) (a 32-step sequence)."""
    return CPU_HZ / (32 * (t + 1))


NOISE_PERIODS = [4, 8, 16, 32, 64, 96, 128, 160, 202, 254, 380, 508, 762, 1016, 2034, 4068]   # NTSC, CPU cycles
DMC_RATES = [428, 380, 340, 320, 286, 254, 226, 214, 190, 160, 142, 128, 106, 84, 72, 54]       # NTSC, CPU cycles a bit


def pulse_level(n: float) -> float:
    """NESdev "APU Mixer": pulse_out = 95.88 / (8128 / (pulse1 + pulse2) + 100)."""
    return 0.0 if n == 0 else 95.88 / (8128 / n + 100)


def tnd_level(t=0, n=0, d=0) -> float:
    """NESdev "APU Mixer": tnd_out = 159.79 / (1 / (t / 8227 + n / 12241 + d / 22638) + 100)."""
    s = t / 8227 + n / 12241 + d / 22638
    return 0.0 if s == 0 else 159.79 / (1 / s + 100)


def lfsr_bits(short_mode: bool, start=1, n=32767) -> str:
    """NESdev "APU Noise": the 15-bit shift register; each clock, feedback = bit 0 XOR bit 1 (bit 6 in
    short mode), shift right, feedback into bit 14. The channel outputs its volume while bit 0 is 0.
    Returns the output after each clock as '1' (sounding) or '0'."""
    r, out = start, []
    tap = 6 if short_mode else 1
    for _ in range(n):
        fb = (r ^ (r >> tap)) & 1
        r = (r >> 1) | (fb << 14)
        out.append("0" if r & 1 else "1")
    return "".join(out)


# ---------------------------------------------------------------- a small 6502 assembler

OPS = {
    "sei": (0x78, 0), "cld": (0xD8, 0), "clc": (0x18, 0), "txs": (0x9A, 0), "inx": (0xE8, 0),
    "dex": (0xCA, 0), "iny": (0xC8, 0), "tax": (0xAA, 0), "txa": (0x8A, 0), "tay": (0xA8, 0),
    "tya": (0x98, 0), "rti": (0x40, 0), "pha": (0x48, 0), "pla": (0x68, 0), "nop": (0xEA, 0),
    "lda#": (0xA9, 1), "ldx#": (0xA2, 1), "ldy#": (0xA0, 1), "cmp#": (0xC9, 1), "cpx#": (0xE0, 1),
    "adc#": (0x69, 1),
    "lda.z": (0xA5, 1), "sta.z": (0x85, 1), "inc.z": (0xE6, 1), "cmp.z": (0xC5, 1), "sta.zx": (0x95, 1),
    "lda.iy": (0xB1, 1),
    "lda": (0xAD, 2), "sta": (0x8D, 2), "bit": (0x2C, 2), "jmp": (0x4C, 2), "sta,x": (0x9D, 2),
    "bpl": (0x10, "rel"), "bne": (0xD0, "rel"), "beq": (0xF0, "rel"), "bcc": (0x90, "rel"),
}


def assemble(prog: list, org: int) -> tuple:
    """Labels are str ending in ':'; instructions are (mnemonic, operand), the operand an int, a label,
    or ('<', label) / ('>', label) for a label's low or high byte; ('db', bytes) is data.
    Returns (code, {label: address})."""
    labels, pc = {}, org
    for item in prog:
        if isinstance(item, str):
            labels[item.rstrip(":")] = pc
        elif item[0] == "db":
            pc += len(item[1])
        else:
            size = OPS[item[0]][1]
            pc += 1 + (1 if size == "rel" else size)

    def value(v):
        if isinstance(v, tuple):
            a = labels[v[1]]
            return a & 0xFF if v[0] == "<" else a >> 8
        return labels[v] if isinstance(v, str) else v

    out = bytearray()
    for item in prog:
        if isinstance(item, str):
            continue
        if item[0] == "db":
            out += item[1]
            continue
        op, size = OPS[item[0]]
        v = value(item[1]) if item[1] is not None else 0
        out.append(op)
        if size == "rel":
            d = v - (org + len(out) + 1)
            assert -128 <= d <= 127, f"branch out of range: {item}"
            out.append(d & 0xFF)
        elif size == 1:
            out.append(v & 0xFF)
        elif size == 2:
            out += struct.pack("<H", v)
    return bytes(out), labels


# ---------------------------------------------------------------- the APU probe ROM

FRAME_LO, FRAME_HI, PTR, LOOP_LO, LOOP_HI = 0x10, 0x11, 0x12, 0x20, 0x21
LOOP_COUNT = 0x0300        # the idle-loop count of the frame before the last NMI (lo, hi)
CODE_END = 0xE000          # the probe's code and script live below this; DMC samples go at $E000 and up


def apu_probe(script: list, samples: dict = None) -> bytes:
    """An NROM-128 program that plays a script of APU writes. script is [(frame, register, value), ...],
    register an address $4000-$4017, frame counted in NMIs from 0 (the first NMI after the program has
    waited two vblanks and turned NMI on; NMIs come once a frame, 60.1 a second). The writes for a frame
    are made in that NMI, in the order given. On every reset (power-on or the Reset button) it starts
    again from frame 0 with its zero page cleared (it doesn't count on RAM surviving).

    Between NMIs it runs an idle loop (INC zp / BNE: 8 cycles an iteration) and each NMI stores the
    previous frame's iteration count at $0300 (lo) / $0301 (hi): the cycles DMC DMA steals show as a
    smaller count. Its frame counter (NMIs so far) is at $10 / $11. Rendering stays off.

    samples is {address: bytes} for DMC sample data at $E000-$FFF9 (64-byte aligned addresses, NESdev
    "APU DMC": $C000 + A * 64)."""
    rows = sorted(script, key=lambda e: e[0])
    table = b"".join(struct.pack("<HBB", f, reg - 0x4000, v) for f, reg, v in rows) + struct.pack("<HBB", 0xFFFF, 0, 0)
    prog = [
        "reset:", ("sei", None), ("cld", None), ("ldx#", 0xFF), ("txs", None),
        ("lda#", 0), ("sta", 0x2000), ("sta", 0x2001),
        ("ldx#", 0), "clr:", ("sta.zx", 0), ("inx", None), ("bne", "clr"),
        ("sta", LOOP_COUNT), ("sta", LOOP_COUNT + 1),
        ("bit", 0x2002), "w1:", ("bit", 0x2002), ("bpl", "w1"), "w2:", ("bit", 0x2002), ("bpl", "w2"),
        ("lda#", ("<", "script")), ("sta.z", PTR), ("lda#", (">", "script")), ("sta.z", PTR + 1),
        ("lda#", 0x80), ("sta", 0x2000),
        "main:", ("inc.z", LOOP_LO), ("bne", "main"), ("inc.z", LOOP_HI), ("jmp", "main"),
        "nmi:", ("pha", None), ("txa", None), ("pha", None), ("tya", None), ("pha", None),
        ("lda.z", LOOP_LO), ("sta", LOOP_COUNT), ("lda.z", LOOP_HI), ("sta", LOOP_COUNT + 1),
        ("lda#", 0), ("sta.z", LOOP_LO), ("sta.z", LOOP_HI),
        "sc:", ("ldy#", 0), ("lda.iy", PTR), ("cmp.z", FRAME_LO), ("bne", "sdone"),
        ("ldy#", 1), ("lda.iy", PTR), ("cmp.z", FRAME_HI), ("bne", "sdone"),
        ("ldy#", 2), ("lda.iy", PTR), ("tax", None), ("ldy#", 3), ("lda.iy", PTR), ("sta,x", 0x4000),
        ("lda.z", PTR), ("clc", None), ("adc#", 4), ("sta.z", PTR), ("bcc", "sc"), ("inc.z", PTR + 1),
        ("jmp", "sc"),
        "sdone:", ("inc.z", FRAME_LO), ("bne", "s2"), ("inc.z", FRAME_HI),
        "s2:", ("pla", None), ("tay", None), ("pla", None), ("tax", None), ("pla", None), ("rti", None),
        "irq:", ("rti", None),
        "script:", ("db", table)]
    code, labels = assemble(prog, 0xC000)
    assert 0xC000 + len(code) <= CODE_END, f"probe too big: {len(code)} bytes"
    prg = bytearray(0x4000)
    prg[:len(code)] = code
    for addr, data in (samples or {}).items():
        assert addr >= CODE_END and addr % 64 == 0 and addr + len(data) <= 0xFFFA, hex(addr)
        prg[addr - 0xC000:addr - 0xC000 + len(data)] = data
    prg[0x3FFA:0x4000] = struct.pack("<HHH", labels["nmi"], labels["reset"], labels["irq"])
    return ines(bytes(prg), bytes(0x2000), 0x01)


def repeat(pattern: bytes, n: int) -> bytes:
    return (pattern * (n // len(pattern) + 1))[:n]


def dmc_length(l_reg: int) -> int:
    """NESdev "APU DMC": a sample is L * 16 + 1 bytes."""
    return l_reg * 16 + 1


# Register writes for steady test tones (NESdev "APU" register layout).
def pulse_tone(frame, t, duty=2, vol=15, ch=1, sweep=0x08) -> list:
    """Pulse 1 or 2, constant volume, length counter halted (so it plays until stopped), sweep off
    with negate set ($08) unless given."""
    b = 0x4000 if ch == 1 else 0x4004
    return [(frame, b, (duty << 6) | 0x30 | vol), (frame, b + 1, sweep), (frame, b + 2, t & 0xFF),
            (frame, b + 3, (t >> 8) & 7)]


def triangle_tone(frame, t) -> list:
    """The triangle with its linear counter held (control set, reload $7F): it plays until stopped."""
    return [(frame, 0x4008, 0xFF), (frame, 0x400A, t & 0xFF), (frame, 0x400B, (t >> 8) & 7)]


def noise_tone(frame, period, short_mode=False, vol=15) -> list:
    return [(frame, 0x400C, 0x30 | vol), (frame, 0x400E, (0x80 if short_mode else 0) | period),
            (frame, 0x400F, 0)]


def enable(frame, bits) -> list:
    return [(frame, 0x4015, bits)]


def write(tmp_path, name: str, data: bytes) -> str:
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def rest_level(res: dict, rate: float):
    """The stream's level before the probe's first script write, and the triangle step t (0-15) it is.
    The triangle's sequencer holds its step when the channel is stopped (NESdev "APU Triangle"), and the
    triangle shares the nonlinear tnd mix with the noise and the DMC, so the levels those make depend
    on where it rests. Asserts the rest level is one of the triangle's 16 held levels (tnd(t, 0, 0),
    within 5 % or 0.002), which is all a silent APU can output."""
    x = res["x"][200:script_sample(res, 0, rate) - 50]
    assert len(x) > 200, "no samples before the probe's first write"
    level = level_mode(x)
    t = min(range(16), key=lambda s: abs(tnd_level(t=s) - level))
    assert abs(tnd_level(t=t) - level) <= max(0.002, tnd_level(t=t) * 0.05), \
        f"at rest the stream sits at {level:.4f}, which is none of the triangle's held levels tnd(0..15)"
    return level, t


def script_sample(res: dict, frame: int, rate: float) -> int:
    """Where in the sample stream the probe's script frame `frame` was played: the driver frame in which
    the probe's NMI count ($10, from peekEach [[0x10, 1], ...] first) first reads frame + 1, at the NMI's
    place in that frame (scanline 241 of 262)."""
    counts = res["counts"]
    for d, row in enumerate(res["peeks"]):
        if row[0] == (frame + 1) & 0xFF:
            before = sum(counts[:d])
            return int(before + counts[d] * 241 / 262)
    raise AssertionError(f"the probe never reached script frame {frame}")


# ---------------------------------------------------------------- screen-only ROMs

APU_2005_FONT = re.compile(rb"\xa9(.)\x85\x00\xa9(.)\x85\x01\xa0\x00\xa9\x3b\x85\x02", re.S)
CONSOLE_FONT = re.compile(rb"\xa0\x00\x8c\x06\x20\x8c\x06\x20\xa2(.)\x86\x0e\xa2(.)\x86\x0f", re.S)


def _prg(rom: bytes) -> bytes:
    return rom[16:16 + rom[4] * 0x4000]


def font_apu_2005(rom: bytes) -> dict:
    """mask -> character for blargg_apu_2005.07.30: its report routine loads 59 glyphs, ASCII 32-90, from
    a table into CHR-RAM tiles 32-90, 8 bytes each into the upper plane, with LDA #<table / STA $00 /
    LDA #>table / STA $01 / LDY #0 / LDA #59 / STA $02. This finds that code in the ROM and reads the
    glyphs it points at."""
    prg = _prg(rom)
    m = APU_2005_FONT.search(prg)
    assert m, "the 2005 APU tests' font loader isn't in this ROM"
    off = ((m.group(1)[0] | m.group(2)[0] << 8) - 0x8000) % len(prg)
    font = {}
    for i in range(59):
        font.setdefault(glyph_mask(prg[off + i * 8:off + i * 8 + 8]), chr(32 + i))
    return font


def font_console(rom: bytes) -> dict:
    """mask -> character for the older blargg shell (dmc_dma_during_read4, CHR_RAM builds): std_reset
    copies its ascii.chr (128 tiles, tile number = ASCII code) to CHR-RAM $0000 with LDY #0 / STY $2006
    / STY $2006 / LDX #<ascii / STX $0E / LDX #>ascii / STX $0F. This finds that code and reads the tiles."""
    prg = _prg(rom)
    m = CONSOLE_FONT.search(prg)
    assert m, "the console's CHR-RAM font loader isn't in this ROM"
    off = ((m.group(1)[0] | m.group(2)[0] << 8) - 0x8000) % len(prg)
    return font_from_chr(prg[off:off + 0x800])


# ---------------------------------------------------------------- nes15's message line

NES15_MESSAGES = {"blank": "          ", "solved": "SOLVED!   ", "solving": "SOLVING..."}
NES15_MSG_ROW, NES15_MSG_COL = 26, 6     # gfx.s: MSG_NT_ADDR = 6 + 26 * 32, MSG_LEN = 10


def nes15_message(pixels: list):
    """Which of nes15's three messages (gfx.s msg0-msg2, strings drawn as tile number = ASCII code) the
    message line shows, or None. pixels is a 256x240 frame by any value (palette index or RGB): a cell
    shows a tile when the tile's 2-bit pixels and the frame's values correspond one to one, consistently
    across all ten cells."""
    chr8 = nes15().read_bytes()[16 + 0x4000:16 + 0x6000]
    for name, text in NES15_MESSAGES.items():
        fwd, back, ok = {}, {}, True
        for i, ch in enumerate(text):
            t = ord(ch)
            for y in range(8):
                for x in range(8):
                    v = ((chr8[t * 16 + y] >> (7 - x)) & 1) | (((chr8[t * 16 + 8 + y] >> (7 - x)) & 1) << 1)
                    p = pixels[(NES15_MSG_ROW * 8 + y) * 256 + (NES15_MSG_COL + i) * 8 + x]
                    if fwd.setdefault(v, p) != p or back.setdefault(p, v) != v:
                        ok = False
                        break
                if not ok:
                    break
            if not ok:
                break
        if ok:
            return name
    return None


# ---------------------------------------------------------------- the page's sound, tapped

TAP_JS = r"""(() => {
  // I4's tap on the page's sound (cases/i4_support.py). Installed before the page's own scripts.
  const RATE = %(rate)s, POLICY = %(policy)s;
  const tap = window.__i4 = { quanta: [], marks: [], ctxs: [], activated: false, errors: [] };
  const Real = window.AudioContext;
  if (!Real) { tap.errors.push('no AudioContext in this browser'); return; }
  // User activation, as HTML defines its triggering events (headless Chromium reports it always on).
  const activate = e => {
    if (e.isTrusted && !(e.type === 'keydown' && e.key === 'Escape')) tap.activated = true;
  };
  for (const t of ['keydown', 'mousedown', 'pointerdown', 'pointerup', 'touchend']) window.addEventListener(t, activate, true);
  const blocked = new WeakSet();
  class TappedContext extends Real {
    constructor(options) {
      const o = Object.assign({}, options || {});
      if (RATE) o.sampleRate = RATE;
      super(o);
      tap.ctxs.push(this);
      // The autoplay policy (simulated): a context made before any user activation starts suspended.
      if (POLICY && !tap.activated) { blocked.add(this); Real.prototype.suspend.call(this); }
    }
    get state() {
      const s = Object.getOwnPropertyDescriptor(BaseAudioContext.prototype, 'state').get.call(this);
      return blocked.has(this) && !tap.activated ? 'suspended' : s;
    }
    resume() {
      // Before an activation, resume() does nothing and its promise never settles.
      if (POLICY && !tap.activated) return new Promise(() => {});
      return Real.prototype.resume.call(this);
    }
  }
  window.AudioContext = TappedContext;
  window.webkitAudioContext = TappedContext;
  const mark = label => {
    tap.marks.push([label, performance.now(), tap.ctxs.map(c => c.currentTime)]);
  };
  document.addEventListener('click', e => { const id = e.target && e.target.id; if (id) mark('click:' + id); }, true);
  document.addEventListener('change', e => { const id = e.target && e.target.id; if (id) mark('change:' + id); }, true);
  window.addEventListener('keydown', e => { if (e.isTrusted) mark('keydown:' + e.code); }, true);
  const SRC = `registerProcessor('i4-tap', class extends AudioWorkletProcessor {
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
        const node = new AudioWorkletNode(ctx, 'i4-tap', { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1] });
        node.port.onmessage = e => tap.quanta.push([index, e.data[0], performance.now(), e.data[1]]);
        const mute = new GainNode(ctx, { gain: 0 });
        connect.call(node, mute);
        connect.call(mute, ctx.destination);
        return node;
      }).catch(e => { tap.errors.push(String(e)); return null; });
      recorders.set(ctx, p);
    }
    return p;
  }
  // Whatever the page connects to its destination is also connected to the recorder: the recorder
  // hears exactly the sum the destination does.
  AudioNode.prototype.connect = function (dest, output, input) {
    const r = connect.apply(this, arguments);
    if (dest instanceof AudioDestinationNode) {
      const src = this;
      recorder(dest.context).then(node => { if (node) connect.call(src, node, output || 0); });
    }
    return r;
  };
})();"""

READ_TAP = """() => {
  const t = window.__i4;
  if (!t) return null;
  const q = t.quanta;
  let n = 0;
  for (const e of q) n += e[3] ? e[3].length : 128;
  const all = new Float32Array(n);
  let k = 0;
  const meta = [];
  for (const [c, time, arrival, s] of q) {
    const len = s ? s.length : 128;
    if (s) all.set(s, k);
    meta.push([c, time, arrival, len]);
    k += len;
  }
  const bytes = new Uint8Array(all.buffer);
  let bin = '';
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return { b64: btoa(bin), meta, marks: t.marks, errors: t.errors,
           rates: t.ctxs.map(c => c.sampleRate), states: t.ctxs.map(c => c.state) };
}"""


class Capture:
    """What reached the page's AudioContext destination: render quanta of (context index, the
    context's currentTime at the quantum, performance.now() when the tap's message arrived, samples),
    plus the marks (clicks, file changes, key presses) with each context's currentTime at that moment."""

    def __init__(self, raw: dict):
        raw = raw or {"b64": "", "meta": [], "marks": [], "errors": ["no tap on the page"], "rates": [], "states": []}
        x = decode_samples(raw["b64"])
        self.quanta, k = [], 0
        for c, time, arrival, n in raw["meta"]:
            self.quanta.append((c, time, arrival, x[k:k + n]))
            k += n
        self.marks, self.errors, self.rates, self.states = raw["marks"], raw["errors"], raw["rates"], raw["states"]

    def contexts(self) -> list:
        return sorted({q[0] for q in self.quanta})

    def samples(self, ctx=None, t0=-1e9, t1=1e9) -> array.array:
        """The samples of one context's quanta whose time is in [t0, t1) (the last context by default)."""
        if ctx is None:
            ctx = self.contexts()[-1] if self.quanta else 0
        out = array.array("f")
        for c, time, _, s in self.quanta:
            if c == ctx and t0 <= time < t1:
                out.extend(s)
        return out

    def rate(self, ctx=None) -> float:
        if not self.rates:
            return 0.0
        return self.rates[ctx if ctx is not None else (self.contexts()[-1] if self.quanta else -1)]

    def mark(self, prefix: str):
        """The first mark whose label starts with prefix: (label, performance.now(), [ctx times])."""
        for m in self.marks:
            if m[0].startswith(prefix):
                return m
        return None

    def heard_after(self, ctx: int, t: float) -> float:
        """The context time span of the quanta recorded after t (how much was rendered since)."""
        times = [q[1] for q in self.quanta if q[0] == ctx and q[1] >= t]
        return (max(times) - t) if times else 0.0


def tap_script(rate=None, policy=False) -> str:
    return TAP_JS % {"rate": json.dumps(rate), "policy": json.dumps(bool(policy))}


class AudioTab(Tab):
    """I3's Tab with the sound tapped (TAP_JS), optionally at a forced context sample rate, and
    optionally under the simulated autoplay policy."""

    def __init__(self, browser, site, rate=None, policy=False):
        super().__init__(browser, site, init_script=tap_script(rate, policy))

    def capture(self) -> Capture:
        return Capture(self.page.evaluate(READ_TAP))

    def quanta_count(self) -> int:
        return self.page.evaluate("window.__i4 ? window.__i4.quanta.length : 0")

    def sound_off_shown(self) -> bool:
        """Is the page's #sound-off indicator there and visible?"""
        loc = self.page.locator("#sound-off")
        return loc.count() == 1 and loc.is_visible()

    def wait(self, seconds: float):
        self.page.wait_for_timeout(int(seconds * 1000))
