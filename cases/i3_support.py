"""I3's shared helpers: the page, served and driven the way a person meets it. Nothing here grades the
build; each case asserts its own promise (see CONTRACT-I3.md).

- `site`, `browser`: module fixtures. `site` starts `node src/web/serve.ts --port N` on a free port, bounded
  in time, and never raises: a missing or broken server shows up as the case's own assertion.
- `Tab`: one fresh browser context on the page, with its page errors, requests and navigations recorded,
  and helpers for the file input, the keyboard, the buttons, the test hooks and the canvas.
- The canvas is read as RGB and compared with an expected frame by palette index: each index one colour,
  distinct indices distinct colours (the palette's RGB is the build's choice, Decided Q5).
- nes15's expected title, built from the author's published name table, palettes and CHR through I2's
  spec renderer.
- A small 6502 assembler and two probe ROMs built from the spec (NESdev "Standard controller", "CPU memory
  map", "iNES"): `pad_probe` shows what the controller ports read; `boot_probe` shows what survives a
  Reset and a power cycle.
"""
import base64
import hashlib
import json
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from conftest import ROOT
from i2_support import Scene, reference_frame

CASES = ROOT / "cases"
CACHE = ROOT / "roms" / "cache"
NODE = shutil.which("node") or "node"
W, H = 256, 240
NTSC_FPS = 1789772.7272 / 29780.5          # 60.0988 frames a second (NESdev "Cycle reference chart")

MANIFEST = json.loads((CASES / "i3_oracles.json").read_text())
ORACLES = {o["id"]: o for o in MANIFEST["oracles"]}


# The keyboard map Decided at approval, in the controller's read order (NESdev "Standard controller").
BUTTONS = ["A", "B", "Select", "Start", "Up", "Down", "Left", "Right"]
KEY = {"A": "KeyX", "B": "KeyZ", "Select": "ShiftRight", "Start": "Enter",
       "Up": "ArrowUp", "Down": "ArrowDown", "Left": "ArrowLeft", "Right": "ArrowRight"}


# ---------------------------------------------------------------- ROMs

def i3_oracle(oid: str) -> Path:
    """The path of an I3 oracle file, fetched from its pinned URL on first use and checked by SHA-256.
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


def nes15() -> Path:
    """The nes15 ROM, fetched by hash (i3_oracles.json; roms/LICENSES.md says why it isn't committed)."""
    return i3_oracle("nes15")


def nes15_title() -> list:
    """nes15's title as the 2C02 draws it, by palette index: the author's title.nam (name table and
    attributes, at $2000), bgd.pal + spr.pal at $3F00, the ROM's 8 KiB CHR, $2000 = $80 (both tables at
    $0000), $2001 = $1E, and every sprite parked at Y = 248 by its oam_clear (nes-lib/oam_clear.s, which
    leaves the other OAM bytes as RAM's power-on $00). Vertical mirroring, per its header."""
    rom = nes15().read_bytes()
    chr8 = rom[16 + 0x4000:16 + 0x4000 + 0x2000]
    pal = i3_oracle("nes15/bgd.pal").read_bytes() + i3_oracle("nes15/spr.pal").read_bytes()
    scene = Scene(chr=chr8, uploads=[(0x2000, i3_oracle("nes15/title.nam").read_bytes()), (0x3F00, pal)],
                  oam=bytes([248, 0, 0, 0]) * 64, ctrl=0x80, mask=0x1E, mirroring="vertical")
    return reference_frame(scene)


NES15_TITLE_FRAME = 60   # the stated frame by which nes15 shows its title (J1d); 5 s is about 300 frames


def ines(prg16: bytes, chr8: bytes, flags6: int) -> bytes:
    """An iNES 1.0 NROM-128 file (mapper 0): 16 KiB PRG, 8 KiB CHR-ROM, header byte 6 as given
    (bit 0 vertical mirroring, bit 1 battery-backed PRG-RAM at $6000)."""
    assert len(prg16) == 0x4000 and len(chr8) == 0x2000
    return b"NES\x1a" + bytes([1, 1, flags6, 0]) + bytes(8) + prg16 + chr8


# ---------------------------------------------------------------- a small 6502 assembler

OPS = {
    "sei": (0x78, 0), "cld": (0xD8, 0), "clc": (0x18, 0), "txs": (0x9A, 0), "inx": (0xE8, 0),
    "dex": (0xCA, 0), "iny": (0xC8, 0), "dey": (0x88, 0), "tax": (0xAA, 0), "txa": (0x8A, 0),
    "tay": (0xA8, 0), "tya": (0x98, 0), "rti": (0x40, 0), "pha": (0x48, 0), "pla": (0x68, 0),
    "nop": (0xEA, 0),
    "lda#": (0xA9, 1), "ldx#": (0xA2, 1), "ldy#": (0xA0, 1), "cpx#": (0xE0, 1), "cpy#": (0xC0, 1),
    "cmp#": (0xC9, 1), "and#": (0x29, 1), "ora#": (0x09, 1),
    "lda": (0xAD, 2), "ldx": (0xAE, 2), "ldy": (0xAC, 2), "sta": (0x8D, 2), "stx": (0x8E, 2),
    "sty": (0x8C, 2), "bit": (0x2C, 2), "jmp": (0x4C, 2), "inc": (0xEE, 2), "cmp": (0xCD, 2),
    "lda,x": (0xBD, 2), "sta,x": (0x9D, 2), "ora,x": (0x1D, 2),
    "bpl": (0x10, "rel"), "bmi": (0x30, "rel"), "bne": (0xD0, "rel"), "beq": (0xF0, "rel"),
    "bcc": (0x90, "rel"), "bcs": (0xB0, "rel"),
}


def assemble(prog: list, org: int) -> tuple:
    """Labels are str ending in ':'; instructions are (mnemonic, operand) with an int or a label;
    ('db', bytes) is data. Returns (code, {label: address})."""
    labels, pc = {}, org
    for item in prog:
        if isinstance(item, str):
            labels[item.rstrip(":")] = pc
        elif item[0] == "db":
            pc += len(item[1])
        else:
            size = OPS[item[0]][1]
            pc += 1 + (1 if size == "rel" else size)
    out = bytearray()
    for item in prog:
        if isinstance(item, str):
            continue
        if item[0] == "db":
            out += item[1]
            continue
        op, size = OPS[item[0]]
        v = labels[item[1]] if isinstance(item[1], str) else item[1]
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


def build(prog: list, flags6=0x01) -> bytes:
    """An NROM-128 file for a program at $C000 whose vectors point at nmi, reset and irq. The CHR has
    tile 0 empty and tile 1 solid (colour 3); the program's palette makes colour 3 white on black."""
    code, labels = assemble(prog, 0xC000)
    assert len(code) <= 0x3FFA, f"program too big: {len(code)} bytes"
    prg = bytearray(code + bytes(0x4000 - len(code)))
    prg[0x3FFA:0x4000] = struct.pack("<HHH", labels["nmi"], labels["reset"], labels["irq"])
    chr8 = bytearray(0x2000)
    chr8[0x10:0x20] = b"\xff" * 16
    return ines(bytes(prg), bytes(chr8), flags6)


PALETTE = bytes([0x0F, 0x0F, 0x0F, 0x30] * 8)

# Rows of 8x8 cells the probes draw into (name table $2000 + row * 32).
ROW_PORT1, ROW_PORT2, ROW_STROBE = 10, 11, 12
ROW_RAM, ROW_BATTERY, ROW_CLEAN = 10, 12, 14


def _init(prog_after_vblank: list) -> list:
    """Reset: interrupts off, rendering off, two vblanks for the PPU to warm up, the palette, and a
    cleared name table 0."""
    return (["reset:", ("sei", None), ("cld", None), ("ldx#", 0xFF), ("txs", None),
             ("lda#", 0), ("sta", 0x2000), ("sta", 0x2001)]
            + prog_after_vblank
            + [("bit", 0x2002), "w1:", ("bit", 0x2002), ("bpl", "w1"), "w2:", ("bit", 0x2002), ("bpl", "w2"),
               ("lda#", 0x3F), ("sta", 0x2006), ("lda#", 0x00), ("sta", 0x2006),
               ("ldx#", 0), "pal:", ("lda,x", "palette"), ("sta", 0x2007), ("inx", None), ("cpx#", 32),
               ("bne", "pal"),
               ("lda#", 0x20), ("sta", 0x2006), ("lda#", 0x00), ("sta", 0x2006),
               ("lda#", 0), ("ldx#", 0), ("ldy#", 4), "clr:", ("sta", 0x2007), ("inx", None), ("bne", "clr"),
               ("dey", None), ("bne", "clr")])


def pad_probe() -> bytes:
    """Each frame, after the NMI: strobe $4016 (1 then 0), read $4016 24 times and $4017 8 times, then
    hold the strobe at 1 and read $4016 3 times. Each read's bit 0 goes to a buffer ($0400-$045F) that
    the next NMI copies to rows 10-12 of the name table: row 10 cells 0-23 are $4016's reads, row 11
    cells 0-7 are $4017's, row 12 cells 0-2 the strobe-high reads. A 1 is a lit cell. So a button held
    now shows on the canvas two frames later."""
    prog = _init([]) + [
        ("lda#", 0), ("ldx#", 0), "cb:", ("sta,x", 0x0400), ("inx", None), ("cpx#", 96), ("bne", "cb"),
        ("sta", 0x0010), ("sta", 0x2005), ("sta", 0x2005),
        ("lda#", 0x80), ("sta", 0x2000),
        "main:", ("lda", 0x0010), ("beq", "main"), ("lda#", 0), ("sta", 0x0010),
        ("lda#", 1), ("sta", 0x4016), ("lda#", 0), ("sta", 0x4016),
        ("ldx#", 0), "r1:", ("lda", 0x4016), ("and#", 1), ("sta,x", 0x0400), ("inx", None), ("cpx#", 24),
        ("bne", "r1"),
        ("ldx#", 0), "r2:", ("lda", 0x4017), ("and#", 1), ("sta,x", 0x0420), ("inx", None), ("cpx#", 8),
        ("bne", "r2"),
        ("lda#", 1), ("sta", 0x4016),
        ("ldx#", 0), "r3:", ("lda", 0x4016), ("and#", 1), ("sta,x", 0x0440), ("inx", None), ("cpx#", 3),
        ("bne", "r3"),
        ("lda#", 0), ("sta", 0x4016),
        ("jmp", "main"),
        "nmi:", ("pha", None), ("txa", None), ("pha", None),
        ("bit", 0x2002), ("lda#", 0x21), ("sta", 0x2006), ("lda#", 0x40), ("sta", 0x2006),
        ("ldx#", 0), "up:", ("lda,x", 0x0400), ("sta", 0x2007), ("inx", None), ("cpx#", 96), ("bne", "up"),
        ("lda#", 0), ("sta", 0x2005), ("sta", 0x2005), ("lda#", 0x80), ("sta", 0x2000),
        ("lda#", 0x0A), ("sta", 0x2001),
        ("lda#", 1), ("sta", 0x0010),
        ("pla", None), ("tax", None), ("pla", None), ("rti", None),
        "irq:", ("rti", None),
        "palette:", ("db", PALETTE)]
    return build(prog, flags6=0x01)


def boot_probe() -> bytes:
    """Battery-backed NROM. On every reset, before writing anything: A = the OR of all 2 KiB of RAM
    (0 means RAM was clean). Then, behind the signature "SAV" at $6001-$6003 (set up on first use), add
    one to the battery counter at $6000; add one to the RAM counter at $0300. Draw: row 10, as many lit
    cells as the RAM counter; row 12, as many as the battery counter; row 14 cell 0 lit if RAM was clean.
    Then rendering on, and it sits still."""
    scan = []
    for page in range(8):
        scan += [("ldx#", 0), f"s{page}:", ("ora,x", page * 0x100), ("inx", None), ("bne", f"s{page}")]
    pre = [("lda#", 0)] + scan + [
        ("tay", None),
        ("lda", 0x6001), ("cmp#", 0x53), ("bne", "fresh"),
        ("lda", 0x6002), ("cmp#", 0x41), ("bne", "fresh"),
        ("lda", 0x6003), ("cmp#", 0x56), ("beq", "signed"),
        "fresh:", ("lda#", 0), ("sta", 0x6000), ("lda#", 0x53), ("sta", 0x6001), ("lda#", 0x41),
        ("sta", 0x6002), ("lda#", 0x56), ("sta", 0x6003),
        "signed:", ("inc", 0x6000), ("inc", 0x0300), ("sty", 0x0301)]
    post = [
        ("lda#", 0x21), ("sta", 0x2006), ("lda#", 0x40), ("sta", 0x2006),
        ("ldx", 0x0300), ("beq", "k1"), "d1:", ("lda#", 1), ("sta", 0x2007), ("dex", None), ("bne", "d1"),
        "k1:", ("lda#", 0x21), ("sta", 0x2006), ("lda#", 0x80), ("sta", 0x2006),
        ("ldx", 0x6000), ("beq", "k2"), "d2:", ("lda#", 1), ("sta", 0x2007), ("dex", None), ("bne", "d2"),
        "k2:", ("lda", 0x0301), ("bne", "k3"),
        ("lda#", 0x21), ("sta", 0x2006), ("lda#", 0xC0), ("sta", 0x2006), ("lda#", 1), ("sta", 0x2007),
        "k3:", ("lda#", 0), ("sta", 0x2005), ("sta", 0x2005), ("sta", 0x2000),
        ("bit", 0x2002), "w3:", ("bit", 0x2002), ("bpl", "w3"),
        ("lda#", 0x0A), ("sta", 0x2001),
        "forever:", ("jmp", "forever"),
        "nmi:", ("rti", None), "irq:", ("rti", None),
        "palette:", ("db", PALETTE)]
    return build(_init(pre) + post, flags6=0x03)


def write(tmp_path, name: str, data: bytes) -> str:
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


# ---------------------------------------------------------------- the canvas

def brightness(p: int) -> int:
    return ((p >> 16) & 0xFF) + ((p >> 8) & 0xFF) + (p & 0xFF)


def cell_lit(rgb: list, cx: int, cy: int) -> bool:
    """An 8x8 cell is lit when its centre 4x4 pixels are bright on average (white tile on black)."""
    total = sum(brightness(rgb[(cy * 8 + y) * W + cx * 8 + x]) for y in range(2, 6) for x in range(2, 6))
    return total / 16 > 384


def row_bits(rgb: list, row: int, n: int) -> list:
    return [int(cell_lit(rgb, cx, row)) for cx in range(n)]


def run_length(rgb: list, row: int) -> int:
    """How many lit cells from cell 0 on, before the first dark one."""
    n = 0
    while n < 32 and cell_lit(rgb, n, row):
        n += 1
    return n


def pad_read(rgb: list) -> dict:
    """What pad_probe drew: the 24 reads of $4016, 8 of $4017, and 3 strobe-high reads."""
    return {"port1": row_bits(rgb, ROW_PORT1, 24), "port2": row_bits(rgb, ROW_PORT2, 8),
            "strobe": row_bits(rgb, ROW_STROBE, 3)}


def held(names) -> list:
    """The first 8 bits a controller with these buttons held gives, in read order."""
    return [int(b in names) for b in BUTTONS]


def boot_read(rgb: list) -> dict:
    """What boot_probe drew: the RAM counter, the battery counter, and whether RAM was clean at boot."""
    return {"ram": run_length(rgb, ROW_RAM), "battery": run_length(rgb, ROW_BATTERY),
            "clean": cell_lit(rgb, 0, ROW_CLEAN)}


def compare_by_index(rgb: list, want: list) -> dict:
    """Is the canvas the expected frame, by palette index? Each index must show one colour everywhere,
    and distinct indices distinct colours. Returns the mismatched pixel count (0 = the same picture)
    and the first few mismatches."""
    colour, bad = {}, []
    for i, (p, v) in enumerate(zip(rgb, want)):
        c = colour.setdefault(v, p)
        if c != p:
            bad.append(i)
    merged = len(set(colour.values())) != len(colour)
    return {"mismatched": len(bad), "merged_indices": merged,
            "first": [(i % W, i // W, f"{rgb[i]:06X}", f"{want[i]:03X}") for i in bad[:6]],
            "colours": len(set(rgb)), "indices": len(colour)}


def same_picture(rgb, want) -> bool:
    if rgb is None or len(rgb) != len(want):
        return False
    c = compare_by_index(rgb, want)
    return c["mismatched"] == 0 and not c["merged_indices"]


READ_CANVAS = """() => {
  const c = document.getElementById('screen');
  if (!c || c.tagName !== 'CANVAS') return {error: 'no <canvas id="screen"> on the page'};
  if (c.width !== 256 || c.height !== 240) return {error: `the canvas is ${c.width}x${c.height}, not 256x240`};
  const o = document.createElement('canvas'); o.width = 256; o.height = 240;
  const g = o.getContext('2d'); g.drawImage(c, 0, 0);
  const d = g.getImageData(0, 0, 256, 240).data;
  const rgb = new Uint8Array(256 * 240 * 3);
  for (let i = 0, j = 0; i < d.length; i += 4) { rgb[j++] = d[i]; rgb[j++] = d[i + 1]; rgb[j++] = d[i + 2]; }
  let bin = '';
  for (let i = 0; i < rgb.length; i += 0x8000) bin += String.fromCharCode.apply(null, rgb.subarray(i, i + 0x8000));
  return {b64: btoa(bin)};
}"""


# ---------------------------------------------------------------- the server

@dataclass
class Site:
    url: str
    why: str
    proc: object = None

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(port: int, log, seconds=1200) -> subprocess.Popen:
    """`node src/web/serve.ts --port N`, under coreutils `timeout` so it can't outlive the run."""
    return subprocess.Popen(["timeout", "-k", "5", str(seconds), NODE, str(ROOT / "src" / "web" / "serve.ts"),
                             "--port", str(port)], cwd=ROOT, stdout=log, stderr=log)


def start_site() -> Site:
    """Start the page's server on a free port and wait until GET / answers 200. Never raises: a server
    that's missing, crashes or doesn't answer gives a Site with no url and the reason."""
    if not (ROOT / "src" / "web" / "serve.ts").is_file():
        return Site(None, "src/web/serve.ts doesn't exist (CONTRACT-I3: `node src/web/serve.ts --port N`)")
    port = free_port()
    log = tempfile.TemporaryFile()
    proc = serve(port, log)
    url = f"http://127.0.0.1:{port}/"
    deadline = time.monotonic() + 15
    last = ""
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            log.seek(0)
            return Site(None, f"the server exited ({proc.returncode}): {log.read()[-1500:]!r}")
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return Site(url, "", proc)
                last = f"GET / answered {r.status}"
        except Exception as e:      # not up yet
            last = repr(e)
        time.sleep(0.2)
    site = Site(None, f"GET {url} didn't answer 200 within 15 s ({last})", proc)
    site.stop()
    return site


@pytest.fixture(scope="module")
def site():
    s = start_site()
    yield s
    s.stop()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(timeout=60000)
        yield b
        b.close()


# ---------------------------------------------------------------- a tab on the page

class Tab:
    """One fresh browser context with the page open. Records page errors, requests and main-frame
    navigations, and sets a marker on window after the load (a reload would lose it)."""

    def __init__(self, browser, site, init_script: str = None):
        assert site.url, f"the page isn't served: {site.why}"
        self.site = site
        self.context = browser.new_context(viewport={"width": 1000, "height": 760})
        self.context.set_default_timeout(15000)
        if init_script:
            self.context.add_init_script(init_script)
        self.page = page = self.context.new_page()
        self.errors, self.requests, self.navigations = [], [], 0
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.on("request", lambda r: self.requests.append(r.url))

        def nav(frame):
            if frame == page.main_frame:
                self.navigations += 1
        page.on("framenavigated", nav)
        page.goto(site.url, wait_until="load", timeout=20000)
        page.evaluate("window.__i3_marker = 'loaded once'")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.context.close()

    # -- the page's own surface
    def has(self, selector: str) -> bool:
        return self.page.locator(selector).count() == 1

    def load(self, path):
        assert self.has("#rom-file"), "no <input id=\"rom-file\"> on the page"
        self.page.set_input_files("#rom-file", path)

    def click(self, button: str):
        assert self.has(f"#{button}"), f"no <button id=\"{button}\"> on the page"
        self.page.click(f"#{button}")

    def message(self) -> str:
        """The #message element's text if it's visible, else ''."""
        if not self.has("#message") or not self.page.locator("#message").is_visible():
            return ""
        return self.page.locator("#message").inner_text().strip()

    def loaded_once(self) -> bool:
        return self.navigations == 1 and self.page.evaluate("window.__i3_marker") == "loaded once"

    # -- the test hooks
    def hooks(self):
        ok = self.page.evaluate("""() => typeof window.nesTest === 'object' && window.nesTest !== null &&
            typeof window.nesTest.pause === 'function' && typeof window.nesTest.step === 'function' &&
            typeof window.nesTest.frames === 'number'""")
        assert ok, "window.nesTest (CONTRACT-I3: frames, pause(), step(n)) isn't on the page"

    def frames(self) -> int:
        self.hooks()
        return self.page.evaluate("window.nesTest.frames")

    def pause(self):
        self.hooks()
        self.page.evaluate("window.nesTest.pause()")

    def step(self, n: int) -> int:
        self.hooks()
        return self.page.evaluate("async n => await window.nesTest.step(n)", n)

    # -- the canvas
    def canvas(self) -> list:
        """The canvas as a row-major list of 0xRRGGBB, or an AssertionError saying why it can't be read."""
        res = self.page.evaluate(READ_CANVAS)
        assert "b64" in res, res.get("error")
        raw = base64.b64decode(res["b64"])
        return [int.from_bytes(raw[i:i + 3], "big") for i in range(0, len(raw), 3)]

    def try_canvas(self):
        res = self.page.evaluate(READ_CANVAS)
        if "b64" not in res:
            return None
        raw = base64.b64decode(res["b64"])
        return [int.from_bytes(raw[i:i + 3], "big") for i in range(0, len(raw), 3)]

    def wait_for(self, pred, seconds: float, every=0.1):
        """Poll the canvas until pred(rgb) holds; returns (seconds taken or None, the last canvas)."""
        t0 = time.monotonic()
        rgb = None
        while True:
            rgb = self.try_canvas()
            if rgb is not None and pred(rgb):
                return time.monotonic() - t0, rgb
            if time.monotonic() - t0 > seconds:
                return None, rgb
            time.sleep(every)

    # -- the keyboard
    def down(self, code: str):
        self.page.keyboard.down(code)

    def up(self, code: str):
        self.page.keyboard.up(code)

    def press(self, code: str, hold_ms=120):
        self.page.keyboard.down(code)
        self.page.wait_for_timeout(hold_ms)
        self.page.keyboard.up(code)

    def blur(self):
        self.page.evaluate("window.dispatchEvent(new FocusEvent('blur'))")

    def focus(self):
        self.page.evaluate("window.dispatchEvent(new FocusEvent('focus'))")


def play_script(tab: Tab, script: list, end: int) -> list:
    """Paused, with a game just loaded at frame 0: step to each (frame, 'down'|'up', key) event in
    order, apply it through the keyboard, and step on to frame `end`. Returns the canvas at `end`."""
    at = 0
    for frame, kind, code in script:
        if frame > at:
            at = tab.step(frame - at)
            assert at == frame, f"step landed on frame {at}, not {frame}"
        (tab.down if kind == "down" else tab.up)(code)
    if end > at:
        at = tab.step(end - at)
    assert at == end, f"step landed on frame {at}, not {end}"
    return tab.canvas()


def fresh_run(browser, site, rom, script: list, end: int) -> list:
    """A fresh page, paused before the load, the script played, and the canvas at frame `end`."""
    with Tab(browser, site) as tab:
        tab.pause()
        tab.load(rom)
        assert tab.frames() == 0, f"a paused load left frames at {tab.frames()}, not 0"
        rgb = play_script(tab, script, end)
        assert not tab.errors, tab.errors
        return rgb


def digest(rgb: list) -> str:
    return hashlib.sha256(b"".join(p.to_bytes(3, "big") for p in rgb)).hexdigest()[:16]
