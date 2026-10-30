"""I9's shared helpers: the collection session (J2), measured the way a person at a desktop browser meets
it. Nothing here grades the build; each case asserts its own promise (see CONTRACT-I9.md).

- The vendored games: every `.nes` file under `roms/games/` (committed, each with a row in
  `roms/LICENSES.md`), which the page's game menu must offer; and `i9_oracle`, I9's files fetched by hash
  from `cases/i9_oracles.json` into `roms/cache/`, under the same rules as conftest.oracle.
- `CollectionTab`: I3's Tab, with what a case needs to stand in for the network and to count what the
  page builds: the ROM answers installed before the page loads (Playwright routing: a status, other
  bytes, a dropped connection, or an answer held back until the case releases it), the game menu's
  entries, `nesTest.loads`, file picks through the browser's own file chooser, and optionally I4's tap on
  the destination (full samples), I5's pacing stub and hidden-tab simulation, and a count of the
  AudioWorkletNodes and window/document listeners the page makes.
- Probe cartridges built in the case from the spec (NESdev "CPU memory map", "PPU registers", "APU",
  "MMC3", "AxROM", "NROM", "Standard controller"):
    - `dump_probe`: at power-on, before it changes anything, it reads what a console it has just been
      plugged into holds (CPU RAM, the APU status, the controller's shift register, the palette, the
      nametables and their arrangement, CHR, OAM, PRG-RAM, and whether an IRQ arrives with its own
      sources off) and shows every byte as a tile, so the canvas is the dump;
    - `mess_probe`: an MMC3 game that leaves everything it can dirty: its IRQ line asserted and never
      acknowledged, PRG-RAM, CPU RAM, CHR-RAM, nametables, OAM and the palette written, horizontal
      arrangement, every APU channel sounding with its length counter halted, the controller strobe high;
    - `note_probe` / `script_probe`: I4's apu_probe with a held note, or a sound script from frame 0.
- Sound measures written from their definitions: the level at one frequency (Goertzel), and the RMS
  envelope of a window and its best correlation with another's.
"""
import base64
import hashlib
import json
import math
import random
import struct
import time
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pytest
import yaml

from conftest import ROOT
from i3_support import READ_CANVAS, Tab, ines   # noqa: F401
from i4_support import (READ_TAP, Capture, apu_probe, enable, pulse_tone, tap_script, triangle_tone)
from i5_support import READ_TICKS, SIM_JS

CASES = ROOT / "cases"
CACHE = ROOT / "roms" / "cache"
GAMES = ROOT / "roms" / "games"
LICENSES = ROOT / "roms" / "LICENSES.md"
GAMES_URL = "/roms/games/"
W, H = 256, 240

_MANIFEST_PATH = CASES / "i9_oracles.json"
MANIFEST = json.loads(_MANIFEST_PATH.read_text()) if _MANIFEST_PATH.exists() else {"oracles": [], "games": []}
ORACLES = {o["id"]: o for o in MANIFEST["oracles"]}


# ---------------------------------------------------------------- ROMs

def vendored() -> list:
    """The vendored games: every .nes file under roms/games/, sorted by name."""
    return sorted(GAMES.glob("*.nes")) if GAMES.is_dir() else []


def i9_oracle(oid: str) -> Path:
    """The path of an I9 file, fetched from its pinned URL on first use and checked by SHA-256. A mismatch
    is an error, never a silent swap."""
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


def mapper_of(rom: bytes) -> int:
    """The iNES / NES 2.0 mapper number (NESdev "INES", "NES 2.0")."""
    m = (rom[6] >> 4) | (rom[7] & 0xF0)
    if rom[7] & 0x0C == 0x08:
        m |= (rom[8] & 0x0F) << 8
    return m


def write(tmp_path, name: str, data: bytes) -> str:
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


# ---------------------------------------------------------------- a 6502 assembler

OPS = {
    "lda#": (0xA9, 1), "lda.z": (0xA5, 1), "lda.zx": (0xB5, 1), "lda": (0xAD, 2), "lda,x": (0xBD, 2),
    "lda,y": (0xB9, 2), "lda.iy": (0xB1, 1),
    "ldx#": (0xA2, 1), "ldx.z": (0xA6, 1), "ldx": (0xAE, 2), "ldy#": (0xA0, 1), "ldy.z": (0xA4, 1), "ldy": (0xAC, 2),
    "sta.z": (0x85, 1), "sta.zx": (0x95, 1), "sta": (0x8D, 2), "sta,x": (0x9D, 2), "sta,y": (0x99, 2),
    "sta.iy": (0x91, 1), "stx.z": (0x86, 1), "stx": (0x8E, 2), "sty.z": (0x84, 1), "sty": (0x8C, 2),
    "adc#": (0x69, 1), "sbc#": (0xE9, 1), "and#": (0x29, 1), "ora#": (0x09, 1), "eor#": (0x49, 1),
    "cmp#": (0xC9, 1), "cpx#": (0xE0, 1), "cpy#": (0xC0, 1),
    "ora.z": (0x05, 1), "ora,x": (0x1D, 2), "ora.iy": (0x11, 1), "eor.z": (0x45, 1), "eor.iy": (0x51, 1),
    "inc.z": (0xE6, 1), "inc": (0xEE, 2), "dec.z": (0xC6, 1), "bit": (0x2C, 2),
    "jmp": (0x4C, 2), "jsr": (0x20, 2), "rts": (0x60, 0), "rti": (0x40, 0),
    "tax": (0xAA, 0), "txa": (0x8A, 0), "tay": (0xA8, 0), "tya": (0x98, 0), "tsx": (0xBA, 0), "txs": (0x9A, 0),
    "inx": (0xE8, 0), "iny": (0xC8, 0), "dex": (0xCA, 0), "dey": (0x88, 0),
    "pha": (0x48, 0), "pla": (0x68, 0), "sei": (0x78, 0), "cli": (0x58, 0), "cld": (0xD8, 0),
    "clc": (0x18, 0), "sec": (0x38, 0), "nop": (0xEA, 0), "lsr": (0x4A, 0), "asl": (0x0A, 0),
    "bpl": (0x10, "rel"), "bmi": (0x30, "rel"), "bvc": (0x50, "rel"), "bvs": (0x70, "rel"),
    "bcc": (0x90, "rel"), "bcs": (0xB0, "rel"), "bne": (0xD0, "rel"), "beq": (0xF0, "rel"),
}


def assemble(prog: list, org: int) -> tuple:
    """Labels are str ending in ':'; instructions (mnemonic, operand) with an int, a label, or
    ('<', label) / ('>', label); ('db', bytes) is data. Returns (code, {label: address})."""
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
        return labels[v] if isinstance(v, str) else (v or 0)

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


def _vblanks(n: int, tag: str) -> list:
    out = [("bit", 0x2002)]
    for k in range(n):
        out += [f"{tag}{k}:", ("bit", 0x2002), ("bpl", f"{tag}{k}")]
    return out


def _set_ppu(addr: int) -> list:
    return [("bit", 0x2002), ("lda#", addr >> 8), ("sta", 0x2006), ("lda#", addr & 0xFF), ("sta", 0x2006)]


# ---------------------------------------------------------------- the dump probe

BUF = 0x0400              # the dump, 960 bytes: one tile per byte on nametable 0
ZP_OR, ZP_XOR = 0xF0, 0xF8
IRQS = 0xE0               # zero page: IRQs taken while the probe's own sources are off
PTR = 0xE2

# Where each part of the dump lands in BUF (and so on the screen: byte k is cell k % 32, row k // 32).
DUMP = {
    "ram_or": (0, 8), "ram_xor": (8, 8), "apu_status": (16, 1), "pad": (17, 8), "irqs": (25, 1),
    "palette": (32, 32), "nametables": (64, 64), "arrangement": (128, 4), "chr": (160, 64), "oam": (224, 64),
    "prg_ram": (288, 64),
}


def byte_tiles() -> bytes:
    """8 KiB of CHR in which tile n shows the byte n: its top four rows are n's bits (plane 0), the rest
    blank. Both pattern tables hold the same."""
    t = bytearray()
    for n in range(256):
        t += bytes([n] * 4 + [0] * 4) + bytes(8)
    return bytes(t) * 2


def _dump_program(mapper: int) -> list:
    """The probe's program (see dump_probe)."""
    p = ["reset:", ("sei", None), ("cld", None), ("ldx#", 0xFF), ("txs", None)]
    # 1. CPU RAM, before anything is written: the OR and the XOR of each 256-byte page, page 0 first,
    #    kept in page 0 once page 0 has been read (nothing touches the stack before this is done).
    for page in range(8):
        p += [("lda#", 0), ("ldx#", 0), f"ro{page}:", ("ora,x", page * 0x100), ("inx", None), ("bne", f"ro{page}"),
              ("tay", None),
              ("lda#", 0), ("ldx#", 0), f"rx{page}:", ("eor_abs_x", page * 0x100), ("inx", None), ("bne", f"rx{page}"),
              ("sta.z", ZP_XOR + page), ("sty.z", ZP_OR + page)]
    # 2. The APU's status, and eight reads of the controller port with no strobe written.
    p += [("lda", 0x4015), ("sta", BUF + DUMP["apu_status"][0])]
    p += [("ldx#", 0), "pd:", ("lda", 0x4016), ("sta,x", BUF + DUMP["pad"][0]), ("inx", None), ("cpx#", 8),
          ("bne", "pd")]
    p += [("ldx#", 0), "cpr:", ("lda.zx", ZP_OR), ("sta,x", BUF + DUMP["ram_or"][0]), ("inx", None),
          ("cpx#", 16), ("bne", "cpr")]
    # 3. Is an IRQ delivered with the probe's own sources off? Frame IRQ inhibited and acknowledged,
    #    DMC IRQ off, then interrupts allowed for two frames.
    p += [("lda#", 0x40), ("sta", 0x4017), ("lda#", 0), ("sta", 0x4010), ("sta.z", IRQS), ("nop", None),
          ("nop", None), ("lda", 0x4015)]
    p += [("cli", None)] + _vblanks(3, "iw") + [("sei", None), ("lda.z", IRQS), ("sta", BUF + DUMP["irqs"][0])]
    # 4. The PPU, warmed up: palette, the start of each nametable, CHR, OAM.
    p += _set_ppu(0x3F00) + [("ldx#", 0), "pal:", ("lda", 0x2007), ("sta,x", BUF + DUMP["palette"][0]),
                             ("inx", None), ("cpx#", 32), ("bne", "pal")]
    for t in range(4):
        p += _set_ppu(0x2000 + t * 0x400) + [("lda", 0x2007), ("ldx#", 0), f"nt{t}:", ("lda", 0x2007),
                                             ("sta,x", BUF + DUMP["nametables"][0] + t * 16), ("inx", None),
                                             ("cpx#", 16), ("bne", f"nt{t}")]
    for k, a in enumerate((0x0000, 0x0FF0, 0x1000, 0x1FF0)):
        p += _set_ppu(a) + [("lda", 0x2007), ("ldx#", 0), f"ch{k}:", ("lda", 0x2007),
                            ("sta,x", BUF + DUMP["chr"][0] + k * 16), ("inx", None), ("cpx#", 16), ("bne", f"ch{k}")]
    p += [("ldx#", 0), "om:", ("stx", 0x2003), ("lda", 0x2004), ("sta,x", BUF + DUMP["oam"][0]), ("inx", None),
          ("cpx#", 64), ("bne", "om")]
    # 5. PRG-RAM, where the cartridge has it ($6000-$601F and $7FE0-$7FFF).
    p += [("ldx#", 0), "pr:", ("lda,x", 0x6000), ("sta,x", BUF + DUMP["prg_ram"][0]), ("lda,x", 0x7FE0),
          ("sta,x", BUF + DUMP["prg_ram"][0] + 32), ("inx", None), ("cpx#", 32), ("bne", "pr")]
    # 6. The nametable arrangement: a mark written to each nametable in turn, then the four read back.
    if mapper == 7:
        p += [("lda#", 0), ("sta", "zero")]        # AxROM: bank 0, page 0 (a write of 0 over a 0 byte)
    for t in range(4):
        p += _set_ppu(0x2000 + t * 0x400 + 0x3BF) + [("lda#", 0xC1 + t), ("sta", 0x2007)]
    for t in range(4):
        p += _set_ppu(0x2000 + t * 0x400 + 0x3BF) + [("lda", 0x2007), ("lda", 0x2007),
                                                     ("sta", BUF + DUMP["arrangement"][0] + t)]
    # 7. The picture: the byte tiles (CHR-RAM boards), a white-on-black palette, the dump on nametable 0.
    if mapper == 7:
        p += _set_ppu(0x0000) + [("ldx#", 0), "bt:", ("stx", 0x2007), ("stx", 0x2007), ("stx", 0x2007),
                                 ("stx", 0x2007), ("lda#", 0), ("ldy#", 12), "bz:", ("sta", 0x2007), ("dey", None),
                                 ("bne", "bz"), ("inx", None), ("bne", "bt")]
    p += [("lda#", 0), ("ldx#", 63), "ca:", ("sta,x", BUF + 960), ("dex", None), ("bpl", "ca")]
    p += _set_ppu(0x3F00) + [("ldx#", 0), "wp:", ("lda,x", "palette"), ("sta", 0x2007), ("inx", None),
                             ("cpx#", 32), ("bne", "wp")]
    p += _set_ppu(0x2000) + [("lda#", BUF & 0xFF), ("sta.z", PTR), ("lda#", BUF >> 8), ("sta.z", PTR + 1),
                             ("ldx#", 4), ("ldy#", 0),
                             "wn:", ("lda.iy", PTR), ("sta", 0x2007), ("iny", None), ("bne", "wn"),
                             ("inc.z", PTR + 1), ("dex", None), ("bne", "wn")]
    # (BUF + 960 .. BUF + 1023 lands on the attribute table: those bytes are kept at 0 below.)
    p += [("lda#", 0), ("sta", 0x2005), ("sta", 0x2005), ("sta", 0x2000)] + _vblanks(1, "on") + [
        ("lda#", 0x0A), ("sta", 0x2001),
        "spin:", ("jmp", "spin"),
        "nmi:", ("rti", None),
        # An IRQ is counted, and the I flag set in the status it returns to, so a line that stays
        # asserted is counted once a CLI rather than forever.
        "irq:", ("pha", None), ("txa", None), ("pha", None), ("inc.z", IRQS), ("tsx", None),
        ("lda_abs_x", 0x0103), ("ora#", 0x04), ("sta,x", 0x0103), ("pla", None), ("tax", None), ("pla", None),
        ("rti", None),
        "palette:", ("db", bytes([0x0F, 0x30, 0x0F, 0x0F] * 8)),
        "zero:", ("db", b"\x00")]
    return p


OPS["eor_abs_x"] = (0x5D, 2)
OPS["lda_abs_x"] = (0xBD, 2)


def dump_probe(mapper=0) -> bytes:
    """At power-on, before writing anything, it reads and keeps (as DUMP lays out):
      - CPU RAM: the OR and the XOR of each of its eight 256-byte pages;
      - $4015 (the APU's length counters and IRQ flags) and eight reads of $4016 with no strobe written
        (the controller's shift register as found);
      - whether an IRQ is taken over three frames with the frame IRQ inhibited and acknowledged and the
        DMC's IRQ off: its own sources off, so any IRQ is someone else's;
      - after the PPU has warmed up: the 32 palette bytes; 16 bytes from the start of each nametable; 16
        CHR bytes at $0000, $0FF0, $1000, $1FF0; the 64 first OAM bytes ($2003/$2004);
      - PRG-RAM: 32 bytes at $6000 and 32 at $7FE0;
      - the nametable arrangement: a mark written into each of the four nametables in turn, then the four
        read back.
    Then it shows the 960 bytes as tiles on nametable 0 (tile n draws n's bits: `byte_tiles`), white on
    black, rendering the background only. mapper 0: NROM-128 with byte_tiles as CHR-ROM and the battery
    bit (8 KiB of PRG-RAM), vertical. mapper 7: AxROM, 64 KiB (the same 32 KiB twice, since the bank at
    power-on is unknown), 8 KiB CHR-RAM, into which it writes byte_tiles after the dump."""
    code, labels = assemble(_dump_program(mapper), 0x8000 if mapper == 7 else 0xC000)
    vectors = struct.pack("<HHH", labels["nmi"], labels["reset"], labels["irq"])
    if mapper == 0:
        prg = bytearray(0x4000)
        prg[:len(code)] = code
        prg[0x3FFA:] = vectors
        return ines(bytes(prg), byte_tiles()[:0x2000], 0x03)
    assert mapper == 7
    bank = bytearray(0x8000)
    bank[:len(code)] = code
    bank[0x7FFA:] = vectors
    return b"NES\x1a" + bytes([4, 0, 0x70, 0x00]) + bytes(8) + bytes(bank) * 2


def dump_read(rgb: list) -> list:
    """The 960 bytes a dump_probe shows: cell k's top row of 8 pixels is byte k's bits, MSB on the left,
    a lit (bright) pixel a 1."""
    out = []
    for k in range(960):
        cx, cy = k % 32, k // 32
        v = 0
        for x in range(8):
            p = rgb[(cy * 8 + 1) * W + cx * 8 + x]
            lit = ((p >> 16) & 0xFF) + ((p >> 8) & 0xFF) + (p & 0xFF) > 384
            v = (v << 1) | int(lit)
        out.append(v)
    return out


def dump_part(dump: list, name: str) -> list:
    a, n = DUMP[name]
    return dump[a:a + n]


# ---------------------------------------------------------------- the mess probe (game A)

def mess_probe() -> bytes:
    """An MMC3 game (iNES 1.0, mapper 4: 32 KiB PRG, 8 KiB CHR-RAM, PRG-RAM at $6000) that leaves the
    console as dirty as it can, and keeps it so (NESdev "MMC3", "APU", "PPU registers"):
      - PRG-RAM enabled ($A001 = $80) and all 8 KiB written $A5; CPU RAM $0200-$07FF written $5A;
      - CHR-RAM, both nametables, the palette (all 32 entries $16) and OAM (by $4014 DMA of $5A) written;
      - horizontal arrangement ($A000 = 1);
      - every APU channel sounding with its length counter halted ($4015 = $0F), the frame counter in
        4-step mode with its IRQ allowed ($4017 = 0);
      - the controller strobe left at 1 ($4016 = 1);
      - the MMC3 IRQ with latch 0 and enabled ($C000 = 0, $C001, $E001), rendering on with the background
        at $0000 and sprites at $1000, so the counter is clocked every line and its IRQ line is asserted
        from the first rendered line on and never acknowledged (interrupts stay masked: SEI).
    Its NMI (on) does nothing. It sits in a loop at $E000 (the last bank, fixed)."""
    p = ["reset:", ("sei", None), ("cld", None), ("ldx#", 0xFF), ("txs", None),
         ("lda#", 0), ("sta", 0x2000), ("sta", 0x2001)] + _vblanks(2, "w")
    p += [("lda#", 6), ("sta", 0x8000), ("lda#", 0), ("sta", 0x8001), ("lda#", 7), ("sta", 0x8000),
          ("lda#", 1), ("sta", 0x8001)]
    for r, v in enumerate((0, 2, 4, 5, 6, 7)):
        p += [("lda#", r), ("sta", 0x8000), ("lda#", v), ("sta", 0x8001)]
    p += [("lda#", 1), ("sta", 0xA000), ("lda#", 0x80), ("sta", 0xA001)]
    # PRG-RAM $6000-$7FFF = $A5.
    p += [("lda#", 0x00), ("sta.z", 0x00), ("lda#", 0x60), ("sta.z", 0x01), ("ldx#", 32), ("ldy#", 0),
          ("lda#", 0xA5), "fr:", ("sta.iy", 0x00), ("iny", None), ("bne", "fr"), ("inc.z", 0x01), ("dex", None),
          ("bne", "fr")]
    # CPU RAM $0200-$07FF = $5A.
    p += [("lda#", 0x00), ("sta.z", 0x00), ("lda#", 0x02), ("sta.z", 0x01), ("ldx#", 6), ("ldy#", 0),
          ("lda#", 0x5A), "fm:", ("sta.iy", 0x00), ("iny", None), ("bne", "fm"), ("inc.z", 0x01), ("dex", None),
          ("bne", "fm")]
    # CHR-RAM ($0000-$1FFF) = $C3, nametables ($2000-$2FFF) = $33.
    p += _set_ppu(0x0000) + [("lda#", 0xC3), ("ldx#", 32), ("ldy#", 0), "fc:", ("sta", 0x2007), ("iny", None),
                             ("bne", "fc"), ("dex", None), ("bne", "fc")]
    p += _set_ppu(0x2000) + [("lda#", 0x33), ("ldx#", 16), ("ldy#", 0), "fn:", ("sta", 0x2007), ("iny", None),
                             ("bne", "fn"), ("dex", None), ("bne", "fn")]
    p += _set_ppu(0x3F00) + [("lda#", 0x16), ("ldx#", 32), "fp:", ("sta", 0x2007), ("dex", None), ("bne", "fp")]
    p += [("lda#", 0), ("sta", 0x2003), ("lda#", 0x02), ("sta", 0x4014)]
    # The APU: pulse 1, pulse 2, the triangle and the noise, all held.
    p += [("lda#", 0x0F), ("sta", 0x4015),
          ("lda#", 0xBF), ("sta", 0x4000), ("lda#", 0x08), ("sta", 0x4001), ("lda#", 0xFD), ("sta", 0x4002),
          ("lda#", 0x00), ("sta", 0x4003),
          ("lda#", 0x7F), ("sta", 0x4004), ("lda#", 0x08), ("sta", 0x4005), ("lda#", 0x7E), ("sta", 0x4006),
          ("lda#", 0x00), ("sta", 0x4007),
          ("lda#", 0xFF), ("sta", 0x4008), ("lda#", 0x7E), ("sta", 0x400A), ("lda#", 0x00), ("sta", 0x400B),
          ("lda#", 0x3F), ("sta", 0x400C), ("lda#", 0x04), ("sta", 0x400E), ("lda#", 0x00), ("sta", 0x400F),
          ("lda#", 0x00), ("sta", 0x4017),
          ("lda#", 1), ("sta", 0x4016)]
    p += [("lda#", 0), ("sta", 0xC000), ("sta", 0xC001), ("sta", 0xE001),
          ("lda#", 0), ("sta", 0x2005), ("sta", 0x2005)] + _vblanks(1, "on") + [
        ("lda#", 0x88), ("sta", 0x2000), ("lda#", 0x1E), ("sta", 0x2001),
        "spin:", ("jmp", "spin"),
        "nmi:", ("rti", None), "irq:", ("rti", None)]
    code, labels = assemble(p, 0xE000)
    assert len(code) <= 0x1FFA
    prg = bytearray(random.Random("i9-mess").randbytes(0x8000))
    prg[0x6000:0x6000 + len(code)] = code
    prg[0x7FFA:] = struct.pack("<HHH", labels["nmi"], labels["reset"], labels["irq"])
    return b"NES\x1a" + bytes([2, 0, 0x40, 0x00]) + bytes(8) + bytes(prg)


# ---------------------------------------------------------------- sound probes (I4's apu_probe)

def note_probe(period=401) -> bytes:
    """Game A for the sound cases: the triangle holding one note from its first frame on (linear counter
    control set), period 401: CPU / (32 x 402) = 139.1 Hz (NESdev "APU Triangle"), a pure low tone whose
    odd harmonics (417, 696 Hz ...) fall 19 dB and more below it."""
    return apu_probe(enable(0, 0x04) + triangle_tone(0, period))


NOTE_HZ = 1789772.7272 / (32 * 402)


def script_probe() -> bytes:
    """Game B for the sound cases: a sound from its first frame and a different one each third of a
    second for 2 s, every pitch 500 Hz or more above game A's note (NESdev "APU Pulse", f = CPU / 16(t+1),
    50 % duty, so odd harmonics only): pulse 1 at 661.9 Hz (period 168), volume 15; at frame 20, 880.8 Hz
    (126) at volume 8; at frame 40, 1054.6 Hz (106) at volume 15 with pulse 2 added at 1331.7 Hz (83),
    volume 6; at frame 60 both stop; at frame 80 pulse 1 comes back at 661.9 Hz, volume 4; at frame 100,
    volume 15 again, and it holds."""
    s = (enable(0, 0x03) + pulse_tone(0, 168, vol=15)
         + pulse_tone(20, 126, vol=8)
         + pulse_tone(40, 106, vol=15) + pulse_tone(40, 83, vol=6, ch=2)
         + enable(60, 0x00)
         + enable(80, 0x01) + pulse_tone(80, 168, vol=4)
         + pulse_tone(100, 168, vol=15))
    return apu_probe(s)


def late_probe() -> bytes:
    """Game B for the leak case: silent for its first 18 frames (0.3 s), then pulse 1 holding 880.8 Hz
    (period 126, 50 %, volume 15), 740 Hz above game A's note, so nothing B does lands at A's pitch."""
    return apu_probe(enable(18, 0x01) + pulse_tone(18, 126, vol=15))


# ---------------------------------------------------------------- sound measures

def goertzel(x, rate: float, hz: float, hann=True) -> float:
    """The amplitude of the hz component of x (a sine of amplitude a reads a), by the Goertzel recurrence,
    over a Hann-windowed block, or a plain (rectangular) one, which weighs the block's edges fully."""
    n = len(x)
    if n < 16:
        return 0.0
    w = 2 * math.pi * hz / rate
    c = 2 * math.cos(w)
    s1 = s2 = 0.0
    for i, v in enumerate(x):
        h = 0.5 - 0.5 * math.cos(2 * math.pi * i / (n - 1)) if hann else 1.0
        s0 = v * h + c * s1 - s2
        s2, s1 = s1, s0
    power = s1 * s1 + s2 * s2 - c * s1 * s2
    return 2 * math.sqrt(max(power, 0.0)) / (n * (0.5 if hann else 1.0))


def rms(x) -> float:
    n = len(x)
    if not n:
        return 0.0
    m = sum(x) / n
    return math.sqrt(sum((v - m) ** 2 for v in x) / n)


def envelope(x, rate: float, step=0.02) -> list:
    """The RMS (DC removed) of each `step`-second bin of x."""
    k = max(1, int(rate * step))
    return [rms(x[i:i + k]) for i in range(0, len(x) - k + 1, k)]


def correlation(a: list, b: list) -> float:
    n = min(len(a), len(b))
    if n < 4:
        return 0.0
    a, b = a[:n], b[:n]
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((v - ma) ** 2 for v in a)
    vb = sum((v - mb) ** 2 for v in b)
    if va <= 1e-18 or vb <= 1e-18:
        return 0.0
    return sum((p - ma) * (q - mb) for p, q in zip(a, b)) / math.sqrt(va * vb)


def best_match(a: list, b: list, max_lag: int) -> tuple:
    """(the best correlation of envelope a against b shifted by -max_lag..max_lag bins, that lag)."""
    best = (-2.0, 0)
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            c = correlation(a[lag:], b)
        else:
            c = correlation(a, b[-lag:])
        if c > best[0]:
            best = (c, lag)
    return best


# ---------------------------------------------------------------- what the cases install in the page

COUNT_JS = r"""(() => {
  // I9's counters (cases/i9_support.py): the AudioWorkletNodes made, by processor name, and the
  // listeners added to window and document, less those removed.
  const c = window.__i9 = { worklets: {}, listeners: 0, marks: [] };
  // Each click on a menu entry and each file change, with every tapped context's audio-clock time then.
  const mark = label => c.marks.push([label, performance.now(),
    window.__i4 ? window.__i4.ctxs.map(x => x.currentTime) : []]);
  document.addEventListener('click', e => {
    const t = e.target && e.target.closest ? e.target.closest('#game-menu [data-rom]') : null;
    if (t) mark('menu');
  }, true);
  document.addEventListener('change', e => { if (e.target && e.target.id === 'rom-file') mark('file'); }, true);
  const Real = window.AudioWorkletNode;
  if (Real) {
    window.AudioWorkletNode = class extends Real {
      constructor(ctx, name, options) {
        super(ctx, name, options);
        c.worklets[name] = (c.worklets[name] || 0) + 1;
      }
    };
  }
  const add = EventTarget.prototype.addEventListener, remove = EventTarget.prototype.removeEventListener;
  const counted = t => t === window || t === document;
  const seen = new WeakMap();
  const key = (type, fn, opts) => type + '|' + (typeof opts === 'boolean' ? opts : !!(opts && opts.capture));
  EventTarget.prototype.addEventListener = function (type, fn, opts) {
    if (counted(this) && fn) {
      let m = seen.get(fn); if (!m) { m = new Set(); seen.set(fn, m); }
      const k = key(type, fn, opts) + (this === window ? 'w' : 'd');
      if (!m.has(k)) { m.add(k); c.listeners++; }
    }
    return add.apply(this, arguments);
  };
  EventTarget.prototype.removeEventListener = function (type, fn, opts) {
    if (counted(this) && fn) {
      const m = seen.get(fn), k = key(type, fn, opts) + (this === window ? 'w' : 'd');
      if (m && m.has(k)) { m.delete(k); c.listeners--; }
    }
    return remove.apply(this, arguments);
  };
})();"""

TAP_PROCESSORS = ("i4-tap", "i5-tap")

ENTRIES_JS = """() => {
  const m = document.getElementById('game-menu');
  if (!m) return null;
  return Array.from(m.querySelectorAll('[data-rom]')).map(e => ({
    rom: new URL(e.getAttribute('data-rom'), location.href).href,
    text: (e.innerText || e.textContent || '').trim(),
    visible: !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length),
  }));
}"""


class Rule:
    """How the case answers a request for one ROM path: `status` (an HTTP status with an empty body),
    `body` (these bytes, 200), `abort` (the connection fails), and `hold` (the answer, whichever it is,
    waits until the case releases it)."""

    def __init__(self, status=None, body=None, abort=False, hold=False):
        self.status, self.body, self.abort, self.hold = status, body, abort, hold
        self.held = []
        self.requests = 0


class CollectionTab(Tab):
    """I3's Tab on the page, with ROM answers installed before the page loads (`rules`: {URL path: Rule}),
    I9's counters, and optionally I4's tap (`tap`, at a forced `rate`) and I5's pacing stub and hidden-tab
    simulation (`hz`)."""

    def __init__(self, browser, site, rules=None, tap=False, rate=None, hz=None):
        assert site.url, f"the page isn't served: {site.why}"
        self.site = site
        self.rules = dict(rules or {})
        self.context = browser.new_context(viewport={"width": 1000, "height": 760})
        self.context.set_default_timeout(15000)
        scripts = [COUNT_JS]
        if tap:
            scripts.append(tap_script(rate))
        if hz:
            scripts.append(SIM_JS % {"hz": hz})
        self.context.add_init_script("\n".join(scripts))
        self.context.route("**/*", self._answer)
        self.page = page = self.context.new_page()
        self.errors, self.requests, self.navigations = [], [], 0
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.on("request", lambda r: self.requests.append(r.url))
        self.choosers = 0
        page.on("filechooser", self._chooser)

        def nav(frame):
            if frame == page.main_frame:
                self.navigations += 1
        page.on("framenavigated", nav)
        page.goto(site.url, wait_until="load", timeout=20000)
        page.evaluate("window.__i3_marker = 'loaded once'")

    # -- the network, as the case answers it
    def _answer(self, route):
        path = urlparse(route.request.url).path
        rule = self.rules.get(path)
        if rule is None:
            route.continue_()
            return
        rule.requests += 1
        if rule.hold:
            rule.held.append(route)
            return
        self._settle(rule, route)

    @staticmethod
    def _settle(rule, route):
        if rule.abort:
            route.abort("connectionfailed")
        elif rule.body is not None:
            route.fulfill(status=200, body=rule.body, headers={"content-type": "application/octet-stream"})
        elif rule.status is not None:
            route.fulfill(status=rule.status, body=b"")
        else:
            route.continue_()

    def release(self, path: str):
        """Let the held answers for path go (as the rule says), and stop holding."""
        rule = self.rules[path]
        rule.hold = False
        held, rule.held = rule.held, []
        for route in held:
            self._settle(rule, route)

    def _chooser(self, fc):
        self.choosers += 1
        self._last_chooser = fc

    # -- the page's surface
    def pick(self, path):
        """Pick a file the way a person does: a click on #rom-file opens the browser's file chooser, and
        the file is chosen in it."""
        assert self.has("#rom-file"), "no <input id=\"rom-file\"> on the page"
        with self.page.expect_file_chooser() as fc:
            self.page.click("#rom-file")
        fc.value.set_files(str(path))

    def entries(self) -> list:
        """The menu's entries: [{rom: absolute URL, text, visible}], or None when there's no #game-menu."""
        return self.page.evaluate(ENTRIES_JS)

    def entry_for(self, name: str):
        """The selector of the menu entry whose ROM is /roms/games/<name>."""
        ents = self.entries()
        assert ents is not None, "no #game-menu on the page (CONTRACT-I9)"
        want = urljoin(self.site.url, GAMES_URL + name)
        idx = [i for i, e in enumerate(ents) if e["rom"] == want]
        assert len(idx) == 1, f"the menu has {len(idx)} entries for {GAMES_URL}{name}: {[e['rom'] for e in ents]}"
        return f"#game-menu [data-rom] >> nth={idx[0]}"

    def choose(self, name: str):
        """Click the menu entry for /roms/games/<name>."""
        self.page.click(self.entry_for(name))

    def loads(self) -> int:
        n = self.page.evaluate("window.nesTest ? window.nesTest.loads : undefined")
        assert isinstance(n, (int, float)), "window.nesTest.loads (CONTRACT-I9) isn't on the page"
        return int(n)

    def wait_loads(self, n: int, seconds=5.0) -> bool:
        """Wait until nesTest.loads reaches n; False if it doesn't within `seconds`."""
        t0 = time.monotonic()
        while time.monotonic() - t0 < seconds:
            if self.page.evaluate("window.nesTest ? window.nesTest.loads : -1") >= n:
                return True
            self.page.wait_for_timeout(20)
        return False

    def wait(self, seconds: float):
        self.page.wait_for_timeout(int(seconds * 1000))

    # -- I4's tap
    def capture(self) -> Capture:
        return Capture(self.page.evaluate(READ_TAP))

    def clear_tap(self):
        """Forget what the tap recorded so far (it keeps recording)."""
        self.page.evaluate("() => { if (window.__i4) { window.__i4.quanta = []; window.__i4.marks = []; } }")

    # -- I5's stub and simulation
    def ticks(self) -> list:
        raw = self.page.evaluate(READ_TICKS) or []
        return [(raw[i], int(raw[i + 1]), int(raw[i + 2])) for i in range(0, len(raw), 3)]

    def now(self) -> float:
        return self.page.evaluate("performance.now()")

    def hide(self) -> int:
        return self.page.evaluate("() => { const f = window.nesTest ? window.nesTest.frames : -1; "
                                  "window.__i5.hide(); return f; }")

    def show(self, jump_ms=0):
        self.page.evaluate("ms => window.__i5.show(ms)", jump_ms)

    # -- I9's counters
    def worklets(self) -> dict:
        """The AudioWorkletNodes the page made, by processor name (the tap's own left out)."""
        w = self.page.evaluate("window.__i9 ? window.__i9.worklets : {}")
        return {k: v for k, v in w.items() if k not in TAP_PROCESSORS}

    def listeners(self) -> int:
        return self.page.evaluate("window.__i9 ? window.__i9.listeners : -1")

    def marks(self) -> list:
        """[(label 'menu' | 'file', performance.now(), [each tapped context's time])] of each load asked for."""
        return self.page.evaluate("window.__i9 ? window.__i9.marks : []")

    # -- a load, either way in
    def load_by(self, way: str, slot: int, path=None):
        """Ask for a load: way "file" picks path through the file chooser; way "menu" clicks the entry of
        the slot-th vendored game (whose answer the case's rules give)."""
        if way == "file":
            self.pick(path)
        else:
            self.choose(slot_name(slot))

    def frame_and_canvas(self):
        """(nesTest.frames, the canvas) read in one task, so no refresh lands between them."""
        res = self.page.evaluate("() => { const f = window.nesTest ? window.nesTest.frames : -1; "
                                 "return [f, (" + READ_CANVAS + ")()]; }")
        f, c = res
        assert "b64" in c, c.get("error")
        raw = base64.b64decode(c["b64"])
        return int(f), [int.from_bytes(raw[i:i + 3], "big") for i in range(0, len(raw), 3)]


def slot_name(slot: int) -> str:
    """The file name of the slot-th vendored game (the entry a case routes its own bytes through)."""
    games = vendored()
    assert len(games) > slot, f"only {len(games)} games are vendored in roms/games/: a case needs {slot + 1}"
    return games[slot].name


def menu_rules(bodies: dict) -> dict:
    """Routing rules that answer the slot-th vendored entry with bodies[slot]."""
    return {GAMES_URL + slot_name(s): Rule(body=b) for s, b in bodies.items()}


def load_time(cap: Capture, mark, ctx: int) -> float:
    """The audio-clock time, in context ctx, of a load mark; a mark made before the context existed takes
    the context's first rendered quantum."""
    times = mark[2]
    if ctx < len(times):
        return times[ctx]
    return min(q[1] for q in cap.quanta if q[0] == ctx)


def fresh_frame(browser, site, rom, n: int, way="file", name=None, rules=None) -> list:
    """The canvas at frame n of a game on a fresh page, paused from before the load, no key pressed:
    through the file input ("file", rom a path) or the menu ("menu", the entry for name)."""
    with CollectionTab(browser, site, rules=rules) as tab:
        tab.pause()
        if way == "file":
            tab.load(rom)
        else:
            tab.choose(name)
            assert tab.wait_loads(1), "the menu entry didn't load on a fresh page"
        assert tab.frames() == 0
        got = tab.step(n)
        assert got == n, f"step landed on {got}"
        rgb = tab.canvas()
        assert not tab.errors, tab.errors
        return rgb


# ---------------------------------------------------------------- J1 per game: the collection's facts

START_KEY = "Enter"       # controller 1's Start (CONTRACT-I3)

CASE_ROWS = yaml.safe_load((CASES / "i9_cases.yaml").read_text())
J1_GAMES = CASE_ROWS["games"]       # each game's own facts (i9_cases.yaml says what each field means)


def params(section: str, delta_mark, *fields) -> list:
    """The rows of one section of i9_cases.yaml as pytest params (the row itself, or the named fields),
    each marked delta_mark when its data says `delta: true`."""
    out = []
    for r in CASE_ROWS[section]:
        values = [r.get(f) for f in fields] if fields else [r]
        out.append(pytest.param(*values, id=str(r["id"]), marks=[delta_mark] if r.get("delta") is True else []))
    return out


def game_path(g: dict) -> Path:
    return GAMES / g["file"] if "file" in g else i9_oracle(g["oracle"])


def digest(rgb: list) -> str:
    return hashlib.sha256(b"".join(p.to_bytes(3, "big") for p in rgb)).hexdigest()[:16]


def same_layout(rgb: list, ref: list) -> bool:
    """Is rgb the picture ref up to its palette: one colour of rgb for each colour of ref (a cycled
    palette, or a blinking colour gone to the backdrop), with no more than one of ref's colours lost?"""
    f = {}
    for a, b in zip(ref, rgb):
        if f.setdefault(a, b) != b:
            return False
    return len(set(f.values())) >= len(f) - 1 and len(f) >= 3
