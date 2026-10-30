"""I2's shared helpers. Nothing here grades the build; each case asserts its own promise.

- `i2_oracle`: the I2 oracle ROMs, fetched from their pinned URLs (cases/i2_oracles.json) and checked by
  SHA-256, under the same rules as conftest.oracle.
- `drive_ppu`: runs the core API through the frozen driver cases/drive_ppu.ts (see CONTRACT-I2.md).
- Frame and PNG decoding, and a screen reader that reads text off a frame using the ROM's own font.
- A tiny 6502 assembler and an NROM "scene" ROM builder, so a case can put a known picture on the screen
  through the registers, as a game does.
- `reference_frame`: what the 2C02 draws for a static scene, written from the NESdev wiki's PPU pages
  (PPU_rendering, PPU_scrolling, PPU_attribute_tables, PPU_palettes, PPU_OAM, PPU_sprite_priority, Mirroring).
  It is the spec in miniature for the scenes the cases build; it is not taken from any emulator.
"""
import base64
import hashlib
import json
import random
import re
import shutil
import struct
import urllib.request
import zlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from conftest import ROOT, run

CASES = ROOT / "cases"
CACHE = ROOT / "roms" / "cache"
MANIFEST = json.loads((CASES / "i2_oracles.json").read_text())
ORACLES = {o["id"]: o for o in MANIFEST["oracles"]}
NODE = shutil.which("node") or "node"
W, H = 256, 240


# ---------------------------------------------------------------- oracles and the driver

def i2_oracle(oid: str) -> Path:
    """The path of an I2 oracle file, fetched from its pinned URL on first use and checked by SHA-256.
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


def drive_ppu(request: dict, timeout=120) -> dict:
    """Run the core API through cases/drive_ppu.ts. Always returns a dict; a failed run has ok False
    and says why (its stage, error, or the process's exit)."""
    r = run([NODE, str(CASES / "drive_ppu.ts"), json.dumps(request)], timeout=timeout)
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
    """A driver result without its bulky fields, for assertion messages."""
    return {k: v for k, v in res.items() if k not in ("frame", "hashes")}


def decode_frame(b64: str) -> list:
    """The driver's frame: 256x240 little-endian 16-bit values, row-major from the top line.
    Bits 0-5 are the 2C02 colour index; bits 6-8 the emphasis bits (see CONTRACT-I2.md)."""
    raw = base64.b64decode(b64)
    assert len(raw) == W * H * 2, f"frame is {len(raw)} bytes, not {W * H * 2}"
    return list(struct.unpack(f"<{W * H}H", raw))


def first_diffs(got: list, want: list, n=8) -> list:
    """The first n pixels where two frames differ, as (x, y, got, want) in hex."""
    out = []
    for i, (g, w) in enumerate(zip(got, want)):
        if g != w:
            out.append((i % W, i // W, f"{g:03X}", f"{w:03X}"))
            if len(out) == n:
                break
    return out


def diff_count(got: list, want: list) -> int:
    return sum(1 for g, w in zip(got, want) if g != w)


# ---------------------------------------------------------------- PNG

def read_png(path: Path):
    """(width, height, pixels) for an 8-bit, non-interlaced RGB (colour type 2) or RGBA (6) PNG, with
    pixels a row-major list of (r, g, b). Raises ValueError for anything else."""
    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG: bad signature")
    pos, idat, ihdr = 8, b"", None
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if zlib.crc32(kind + body) & 0xFFFFFFFF != struct.unpack(">I", data[pos + 8 + n:pos + 12 + n])[0]:
            raise ValueError(f"bad CRC in chunk {kind!r}")
        if kind == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
        pos += 12 + n
    if ihdr is None:
        raise ValueError("no IHDR")
    w, h, depth, ctype, _, _, interlace = ihdr
    if depth != 8 or ctype not in (2, 6) or interlace != 0:
        raise ValueError(f"unsupported PNG: depth {depth}, colour type {ctype}, interlace {interlace}")
    bpp = 3 if ctype == 2 else 4
    raw = zlib.decompress(idat)
    stride = w * bpp
    if len(raw) != h * (stride + 1):
        raise ValueError(f"image data is {len(raw)} bytes, expected {h * (stride + 1)}")
    prev = bytearray(stride)
    pixels = []
    for y in range(h):
        f = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if f == 1:
                line[i] = (line[i] + a) & 0xFF
            elif f == 2:
                line[i] = (line[i] + b) & 0xFF
            elif f == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 0xFF
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
            elif f != 0:
                raise ValueError(f"bad filter type {f} on row {y}")
        pixels.extend(tuple(line[x * bpp:x * bpp + 3]) for x in range(w))
        prev = line
    return w, h, pixels


# ---------------------------------------------------------------- reading text off the screen

def glyph_mask(rows) -> tuple:
    """An 8x8 glyph as a tuple of 8 row bytes (bit 7 = the leftmost pixel)."""
    return tuple(rows)


def font_from_chr(chr_bytes: bytes, codes=range(32, 127)) -> dict:
    """mask -> character, for a font laid out as tile number = ASCII code in a CHR pattern table
    (blargg's newer shell, "Requires ASCII font in CHR"); a pixel is lit when either plane is set."""
    font = {}
    for c in codes:
        t = chr_bytes[c * 16:c * 16 + 16]
        font.setdefault(glyph_mask(t[i] | t[i + 8] for i in range(8)), chr(c))
    return font


FONT_2005_SIG = re.compile(rb"\xa9(.)\x85\xf0\xa9(.)\x85\xf1\xa0\x00\xa9\x3b\x85\xf2", re.S)


def font_2005(rom: bytes) -> dict:
    """mask -> character for blargg's 2005 runtime (sprite_hit_tests_2005, sprite_overflow_tests). Its
    console.a loads 59 glyphs, ASCII 32-90, from `chr_data` into CHR-RAM tiles 32-90, 8 bytes each into
    the upper plane. The code that does it is LDA #<chr_data / STA $F0 / LDA #>chr_data / STA $F1 /
    LDY #0 / LDA #59 / STA $F2; this finds that code in the ROM and reads the glyphs it points at."""
    prg = rom[16:16 + rom[4] * 0x4000]
    m = FONT_2005_SIG.search(prg)
    assert m, "the 2005 console's font loader isn't in this ROM"
    addr = m.group(1)[0] | m.group(2)[0] << 8
    off = (addr - 0x8000) % len(prg)
    font = {}
    for i in range(59):
        font.setdefault(glyph_mask(prg[off + i * 8:off + i * 8 + 8]), chr(32 + i))
    return font


def read_screen(pixels: list, font: dict, lit, cols=32, rows=30) -> list:
    """The screen's text, one string per 8-pixel character row (30 rows of 32 cells), read with the
    ROM's own font: each 8x8 cell's lit pixels (lit(pixel) is True) must match a glyph exactly. A cell
    that matches no glyph reads '?'. Trailing spaces are stripped."""
    out = []
    for cy in range(rows):
        s = ""
        for cx in range(cols):
            mask = []
            for r in range(8):
                b = 0
                base = (cy * 8 + r) * W + cx * 8
                for i in range(8):
                    if lit(pixels[base + i]):
                        b |= 0x80 >> i
                mask.append(b)
            s += font.get(tuple(mask), "?")
        out.append(s.rstrip())
    return out


def text_lines(screen: list) -> list:
    return [ln for ln in screen if ln.strip()]


def two_colours(pixels: list):
    """The background (most common) and foreground (second most common) values of a text screen."""
    common = Counter(pixels).most_common(2)
    return common[0][0], (common[1][0] if len(common) > 1 else None)


# ---------------------------------------------------------------- a tiny 6502 assembler

# mnemonic -> (opcode, operand size); 'rel' is a branch.
OPS = {
    "sei": (0x78, 0), "cld": (0xD8, 0), "txs": (0x9A, 0), "inx": (0xE8, 0), "dex": (0xCA, 0),
    "rti": (0x40, 0), "pha": (0x48, 0), "pla": (0x68, 0), "nop": (0xEA, 0),
    "lda#": (0xA9, 1), "ldx#": (0xA2, 1), "ldy#": (0xA0, 1), "cpx#": (0xE0, 1),
    "lda": (0xAD, 2), "lda,x": (0xBD, 2), "sta": (0x8D, 2), "sta,x": (0x9D, 2), "stx": (0x8E, 2),
    "bit": (0x2C, 2), "jmp": (0x4C, 2), "inc": (0xEE, 2),
    "bpl": (0x10, "rel"), "bmi": (0x30, "rel"), "bvc": (0x50, "rel"), "bvs": (0x70, "rel"),
    "bne": (0xD0, "rel"), "beq": (0xF0, "rel"),
}


def assemble(prog: list, org: int) -> tuple:
    """Assemble a list of str labels ending in ':' and (mnemonic, operand) tuples (operand an int, a
    label, or ('<', label) / ('>', label) for a label's low/high byte) and ('db', bytes) data.
    Returns (code bytes, {label: address})."""
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
        out.append(op)
        if size == "rel":
            d = value(item[1]) - (org + len(out) + 1)
            assert -128 <= d <= 127, f"branch out of range: {item}"
            out.append(d & 0xFF)
        elif size == 1:
            out.append(value(item[1]) & 0xFF)
        elif size == 2:
            out += struct.pack("<H", value(item[1]))
    return bytes(out), labels


# ---------------------------------------------------------------- scenes: a known picture, put up the way a game does

MIRRORING = {"horizontal": 0x00, "vertical": 0x01, "four": 0x08}


@dataclass
class Scene:
    """A static picture put up through the PPU's registers. `uploads` are (PPU address, bytes) written
    through $2006/$2007 with rendering off; `oam` is copied to $0200 and sent by $4014 DMA. Then $2000
    gets `ctrl`, $2005 gets `scroll`, and in the next vblank $2001 gets `mask`. With `split`, a
    (sprite-0 hit) status-bar loop runs: each frame, after sprite 0 hits, $2005 gets split[0] as the
    new X scroll; the NMI handler puts `scroll` back for the next frame."""
    chr: bytes
    uploads: list
    oam: bytes = None
    ctrl: int = 0x00
    scroll: tuple = (0, 0)
    mask: int = 0x1E
    mirroring: str = "vertical"
    split: tuple = None           # (new X scroll); the expected row is derived, not given
    readback: bool = False        # read palette and OAM back into RAM ($0300, $0400) before rendering
    meta: dict = field(default_factory=dict)


def scene_program(s: Scene) -> list:
    p = ["reset:", ("sei", None), ("cld", None), ("ldx#", 0xFF), ("txs", None),
         ("lda#", 0), ("sta", 0x2000), ("sta", 0x2001),
         ("bit", 0x2002), "v1:", ("bit", 0x2002), ("bpl", "v1"), "v2:", ("bit", 0x2002), ("bpl", "v2")]
    data = []
    for k, (addr, blob) in enumerate(s.uploads):
        p += [("bit", 0x2002), ("lda#", addr >> 8), ("sta", 0x2006), ("lda#", addr & 0xFF), ("sta", 0x2006)]
        for c in range(0, len(blob), 256):
            chunk = blob[c:c + 256]
            lab = f"d{k}_{c}"
            data += [f"{lab}:", ("db", bytes(chunk))]
            p += [("ldx#", 0), f"l{lab}:", ("lda,x", lab), ("sta", 0x2007), ("inx", None),
                  ("cpx#", len(chunk) & 0xFF), ("bne", f"l{lab}")]
    if s.oam is not None:
        data += ["oam:", ("db", bytes(s.oam))]
        p += [("ldx#", 0), "loam:", ("lda,x", "oam"), ("sta,x", 0x0200), ("inx", None), ("bne", "loam"),
              ("lda#", 0), ("sta", 0x2003), ("lda#", 0x02), ("sta", 0x4014)]
    if s.readback:
        p += [("bit", 0x2002), ("lda#", 0x3F), ("sta", 0x2006), ("lda#", 0x00), ("sta", 0x2006),
              ("ldx#", 0), "rpal:", ("lda", 0x2007), ("sta,x", 0x0300), ("inx", None), ("cpx#", 32), ("bne", "rpal"),
              ("ldx#", 0), "roam:", ("stx", 0x2003), ("lda", 0x2004), ("sta,x", 0x0400), ("inx", None), ("bne", "roam"),
              ("lda#", 0x5A), ("sta", 0x0500)]
    p += [("bit", 0x2002), ("lda#", s.ctrl), ("sta", 0x2000),
          ("lda#", s.scroll[0]), ("sta", 0x2005), ("lda#", s.scroll[1]), ("sta", 0x2005),
          "v3:", ("bit", 0x2002), ("bpl", "v3"), "enable:", ("lda#", s.mask), ("sta", 0x2001)]
    if s.split is None:
        p += ["main:", ("jmp", "main"), "nmi:", ("rti", None)]
    else:
        p += [("lda#", s.ctrl | 0x80), ("sta", 0x2000),
              "main:", ("lda", 0x0010), ("beq", "main"), ("lda#", 0), ("sta", 0x0010),
              "h0:", ("bit", 0x2002), ("bvs", "h0"),       # wait for the hit flag to clear (pre-render line)
              "h1:", ("bit", 0x2002), ("bvc", "h1"),       # wait for sprite 0 to hit
              ("lda#", s.split[0]), ("sta", 0x2005), ("lda#", 0), ("sta", 0x2005),
              ("jmp", "main"),
              "nmi:", ("pha", None), ("bit", 0x2002),
              ("lda#", s.scroll[0]), ("sta", 0x2005), ("lda#", s.scroll[1]), ("sta", 0x2005),
              ("lda#", 1), ("sta", 0x0010), ("pla", None), ("rti", None)]
    return p + ["irq:", ("rti", None)] + data


def nrom(prg16: bytes, chr8: bytes, mirroring="vertical") -> bytes:
    """An iNES NROM-128 file: 16 KiB PRG (seen at $8000 and $C000) and 8 KiB CHR-ROM."""
    assert len(prg16) == 0x4000 and len(chr8) == 0x2000
    return b"NES\x1a" + bytes([1, 1, MIRRORING[mirroring], 0]) + bytes(8) + prg16 + chr8


def build_rom(prog: list, chr8: bytes, mirroring="vertical") -> tuple:
    """(file bytes, labels) for a program at $C000 with its vectors pointing at nmi, reset, irq."""
    code, labels = assemble(prog, 0xC000)
    assert len(code) <= 0x3FFA, f"program too big: {len(code)} bytes"
    prg = bytearray(code + bytes(0x4000 - len(code)))
    prg[0x3FFA:0x4000] = struct.pack("<HHH", labels["nmi"], labels["reset"], labels["irq"])
    return nrom(bytes(prg), chr8, mirroring), labels


def scene_rom(s: Scene) -> bytes:
    return build_rom(scene_program(s), s.chr, s.mirroring)[0]


def write_rom(tmp_path, name: str, data: bytes) -> str:
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


# ---------------------------------------------------------------- the spec, in miniature

class PpuMemory:
    """The PPU's memory as $2006/$2007 writes see it: nametables mapped by the cartridge's mirroring
    (NESdev "Mirroring"), $3000-$3EFF mirroring $2000-$2EFF, and the 32-byte palette with $3F10/$14/$18/$1C
    mirroring $3F00/$04/$08/$0C (NESdev "PPU palettes")."""

    def __init__(self, mirroring: str):
        self.mirroring = mirroring
        self.ciram = bytearray(0x1000)
        self.pal = bytearray(32)

    def nt_index(self, addr: int) -> int:
        table, off = (addr >> 10) & 3, addr & 0x3FF
        phys = {"vertical": table & 1, "horizontal": table >> 1, "four": table}[self.mirroring]
        return phys * 0x400 + off

    def write(self, addr: int, value: int):
        addr &= 0x3FFF
        if 0x2000 <= addr < 0x3F00:
            self.ciram[self.nt_index(addr)] = value
        elif addr >= 0x3F00:
            i = addr & 0x1F
            if i & 0x13 == 0x10:
                i &= 0x0F
            self.pal[i] = value & 0x3F

    def nt(self, table: int, off: int) -> int:
        return self.ciram[self.nt_index(0x2000 + table * 0x400 + off)]


def reference_frame(s: Scene, split_row: int = None) -> list:
    """The frame the 2C02 draws for a static scene, from the NESdev wiki:
    - background: the tile and attribute at the scrolled position (scroll X/Y plus the $2000 nametable
      bits; 512x480 of four nametables through the mirroring); a pixel's 2 bits from the two planes;
    - sprites: OAM Y is the line above the sprite's top (a sprite shows on lines Y+1 .. Y+height); only
      the first 8 sprites in OAM order on a line are drawn; the lowest-indexed opaque sprite pixel wins
      among sprites, and only then is its priority bit weighed against the background ("PPU sprite
      priority"); 8x16 sprites take their table from the tile number's bit 0 and flip over all 16 rows;
    - $2001: bits 1 and 2 show the background and sprites in the leftmost 8 pixels; bit 0 (greyscale)
      ANDs every colour index with $30; bits 5-7 (emphasis) ride along in the frame value's bits 6-8;
    - a pixel that is transparent in both shows the backdrop, palette entry $3F00.
    With split_row, rows from split_row down use X scroll s.split[0] (the vertical position runs on)."""
    mem = PpuMemory(s.mirroring)
    for addr, blob in s.uploads:
        for i, b in enumerate(blob):
            mem.write(addr + i, b)
    chr8, oam, ctrl, mask = s.chr, s.oam or bytes(256), s.ctrl, s.mask
    pal = mem.pal
    h = 16 if ctrl & 0x20 else 8
    emph = (mask >> 5) << 6
    frame = []
    for y in range(H):
        line = [i for i in range(64) if 0 <= y - (oam[i * 4] + 1) < h][:8]
        sx = s.split[0] if split_row is not None and y >= split_row else s.scroll[0]
        for x in range(W):
            bg_p = bg_pal = 0
            if mask & 0x08 and (x >= 8 or mask & 0x02):
                X = (x + sx + 256 * (ctrl & 1)) % 512
                Y = (y + s.scroll[1] + 240 * ((ctrl >> 1) & 1)) % 480
                table = (X >= 256) + 2 * (Y >= 240)
                col, row = (X % 256) // 8, (Y % 240) // 8
                tile = mem.nt(table, row * 32 + col)
                attr = mem.nt(table, 0x3C0 + (row // 4) * 8 + col // 4)
                bg_pal = (attr >> (((row % 4) // 2) * 4 + ((col % 4) // 2) * 2)) & 3
                a = (0x1000 if ctrl & 0x10 else 0) + tile * 16 + Y % 8
                bit = 7 - X % 8
                bg_p = ((chr8[a] >> bit) & 1) | (((chr8[a + 8] >> bit) & 1) << 1)
            sp_p = sp_pal = sp_behind = 0
            if mask & 0x10 and (x >= 8 or mask & 0x04):
                for i in line:
                    c = x - oam[i * 4 + 3]
                    if not 0 <= c < 8:
                        continue
                    r = y - (oam[i * 4] + 1)
                    t, at = oam[i * 4 + 1], oam[i * 4 + 2]
                    if at & 0x40:
                        c = 7 - c
                    if at & 0x80:
                        r = h - 1 - r
                    if h == 8:
                        a = (0x1000 if ctrl & 0x08 else 0) + t * 16 + r
                    else:
                        a = (t & 1) * 0x1000 + ((t & 0xFE) + (r >= 8)) * 16 + (r & 7)
                    p = ((chr8[a] >> (7 - c)) & 1) | (((chr8[a + 8] >> (7 - c)) & 1) << 1)
                    if p:
                        sp_p, sp_pal, sp_behind = p, at & 3, at & 0x20
                        break
            if sp_p and (not bg_p or not sp_behind):
                idx = pal[0x10 + sp_pal * 4 + sp_p]
            elif bg_p:
                idx = pal[bg_pal * 4 + bg_p]
            else:
                idx = pal[0]
            idx &= 0x3F
            if mask & 0x01:
                idx &= 0x30
            frame.append(idx | emph)
    return frame


# ---------------------------------------------------------------- scene material

def make_chr(seed: int) -> bytes:
    """8 KiB of CHR for the scenes. Background table $0000: tile 0 transparent, tile 1 solid (colour 3),
    tiles 2-255 pseudo-random. Sprite table $1000: tile 0 transparent, tile 1 solid (colour 3), tile 2 a
    single pixel at its top-left corner (colour 1), tiles 3-255 pseudo-random. Random tiles mix all four
    pixel values, so a misplaced or misflipped tile shows."""
    rng = random.Random(seed)
    chr8 = bytearray(rng.randrange(256) for _ in range(0x2000))
    for base in (0x0000, 0x1000):
        chr8[base:base + 16] = bytes(16)
        chr8[base + 16:base + 32] = b"\xff" * 16
    chr8[0x1020:0x1030] = bytes([0x80] + [0] * 15)
    return bytes(chr8)


def random_nametable(rng: random.Random, tiles=range(2, 256)) -> bytes:
    """960 tile bytes and 64 attribute bytes."""
    return bytes(rng.choice(tiles) for _ in range(960)) + bytes(rng.randrange(256) for _ in range(64))


def random_palette(rng: random.Random) -> bytes:
    """32 palette bytes, distinct where they can be seen, with $3F10 = $3F00 so the mirror is moot."""
    vals = rng.sample(range(0x40), 32)
    vals[16] = vals[0]
    return bytes(vals)


def oam_of(sprites: list) -> bytes:
    """256 OAM bytes: the given (y, tile, attr, x) sprites first, the rest parked at Y = $FF (off screen)."""
    out = bytearray(b"\xff\x00\x00\x00" * 64)
    for i, (y, t, a, x) in enumerate(sprites):
        out[i * 4:i * 4 + 4] = bytes([y, t, a, x])
    return bytes(out)
