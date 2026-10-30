"""I8's shared helpers: MMC3 (iNES mapper 4), measured the way its consumers meet it. Nothing here grades
the build; each case asserts its own promise (see CONTRACT-I8.md).

- `i8_oracle`: I8's oracle files, fetched from their pinned URLs (cases/i8_oracles.json) and checked by
  SHA-256, under the same rules as conftest.oracle. Holy Mapperel's MMC3 builds are cut from the same pinned
  release archive I6 and I7 use (a raw LZMA2 block, then a pinned slice, each checked by its hash).
- `Mmc3`: the MMC3 in miniature, written from the NESdev wiki pages "MMC3" (banks, registers, IRQ
  specifics, the iNES mapper 004 and MMC6 note) and "NES 2.0 submappers" (004: 0 is the Sharp MMC3, the
  MMC3B/C "new" IRQ behaviour the board targets, Decided Q2). Where the wiki says a fact is unknown,
  revision-dependent or conflated with the MMC6, the model refuses to answer (`Unknown`), so no case can pin
  it:
    - every register, the IRQ latch, counter, reload flag and enable at power-on ("the values in R6, R7,
      and $8000 are unspecified at power on"; only $E000-$FFFF, fixed to the last bank, is known);
    - the "$C001 reload glitch": $C001 written when the counter's last clock was a reload by the flag
      (the counter then becomes a revision-dependent value above 127: NEC MMC3A OR $F0, NEC MMC3B-C OR
      $FF, Sharp OR $80);
    - an A12 rise that follows a low time too short to call against the filter ("a rising edge after the
      line has remained low for three falling edges of M2"), and any rise caused by a PPU address in
      $3000-$3FFF (the wiki only speaks of $1000-$1FFF accesses through $2006/$2007);
    - PRG-RAM before $A001 is written, and on an iNES 1.0 file anything but $A001 = $80 (enabled,
      writable): "Many emulators choose not to implement them as part of iNES Mapper 004 to avoid an
      incompatibility with the MMC6"; bank select bit 5 on an iNES 1.0 file (MMC6's PRG-RAM enable);
    - what a read returns when there's no PRG-RAM, or it's disabled (open bus): the cases only ask that
      it isn't the byte stored there (`NotStored`);
    - PRG-RAM and CHR-RAM contents never written; more than 8 KiB of PRG-RAM ("wrammax=8K").
- `Mmc3Probe`: a probe cartridge built in the case, on the plan of I6's and I7's: every PRG bank (8 KiB) and
  the CHR-ROM filled with seeded random bytes; a small interpreter in the last 8 KiB bank (fixed at
  $E000-$FFFF, the only window known at power-on). It runs a script of CPU writes and reads, PPU reads,
  writes, copies and fills, $2006 writes that move PPU A12 by hand, IRQ queries (CLI, two NOPs, SEI: was an
  IRQ taken?) and waits for vblank; it logs reads at $0300, sets $0200 = $AA when done (I6's layout, so
  `i6_support.run_probe` reads it), then turns rendering on in one of three modes: still; a sprite-0 split
  (one bank-data write in the NMI, another after the hit); or an MMC3 IRQ split, where every NMI sets the
  top scroll, writes $C000 = N, $C001 and $E001, and the IRQ handler sets the next band's scroll (and
  optionally a CHR bank, a second latch, re-enabling for every IRQ), counting IRQs per frame in RAM.
- The spec's frame for each of those, through I2's spec renderer, and the IRQ schedule a frame gives
  (241 counter clocks a frame with the background at $0000 and sprites at $1000, NESdev "MMC3", IRQ
  Specifics), from `Mmc3` itself.
- Readers for Holy Mapperel's result screen (I6's font) and nesdoug's 33_MMC3 demo (its ASCII font).
"""
import hashlib
import json
import lzma
import random
import struct
from collections import Counter
from pathlib import Path

from conftest import ROOT
from i2_support import Scene, reference_frame
from i6_support import DONE, LOG, LOG_MAX, OAM_PAGE, Unknown, font_tiles, glyph_mask  # noqa: F401
from i7_support import _fetch

CASES = ROOT / "cases"
CACHE = ROOT / "roms" / "cache"
MANIFEST = json.loads((CASES / "i8_oracles.json").read_text())
ORACLES = {o["id"]: o for o in MANIFEST["oracles"]}
ARCHIVES = {a["id"]: a for a in MANIFEST.get("archives", [])}
W, H = 256, 240
BLOCK = 0xE000            # the program, in the last 8 KiB bank (always at $E000-$FFFF)
SCRIPT = 0xE900           # the script, up to the vectors
TOTALS = 0x0700           # 16-bit counts: MMC3 IRQs ($0700), APU frame IRQs ($0702), NMIs ($0704)
RING = 0x0740             # the MMC3 IRQ count of each of the last 64 frames, indexed by frame number & 63
NMIV, IRQV = 0x07F0, 0x07F2

# Controller bits (CONTRACT-I4's setButtons).
A, B, SELECT, START, UP, DOWN, LEFT, RIGHT = 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80


# ---------------------------------------------------------------- oracles

def i8_oracle(oid: str) -> Path:
    """The path of an I8 oracle file, fetched on first use and checked by SHA-256. A mismatch is an
    error, never a silent swap. A file with "archive" is cut from that pinned archive: its LZMA2 block
    (raw, at the pinned offset and size) is decoded and the file is the pinned slice of the output."""
    o = ORACLES[oid]
    dest = CACHE / o["path"]
    if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() == o["sha256"]:
        return dest
    if "archive" not in o:
        _fetch(o["url"], o["sha256"], dest, f"oracle {oid}")
        return dest
    a = ARCHIVES[o["archive"]]
    blob = _fetch(a["url"], a["sha256"], CACHE / a["path"], f"archive {a['id']}")
    block = a["blocks"][o["block"]]
    dec = lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA2, "dict_size": 8 << 20}])
    out = dec.decompress(blob[block["offset"]:block["offset"] + block["size"]])
    data = out[o["offset"]:o["offset"] + o["size"]]
    got = hashlib.sha256(data).hexdigest()
    if got != o["sha256"]:
        raise RuntimeError(f"oracle {oid}: sha256 {got} != pinned {o['sha256']}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


# ---------------------------------------------------------------- the iNES / NES 2.0 header

def _ram_shift(size: int) -> int:
    if not size:
        return 0
    n = size.bit_length() - 7
    assert 64 << n == size, f"{size} isn't a NES 2.0 RAM size"
    return n


def header(prg: int, chr_rom: int, mapper=4, nes2=True, submapper=0, prg_ram=0, prg_nvram=0, chr_ram=0,
           battery=False, four_screen=False, mirroring="h") -> bytes:
    """A 16-byte header (NESdev "INES", "NES 2.0"). Sizes in bytes; chr_rom 0 means CHR-RAM (NES 2.0
    byte 11 gives its size). Byte 6: bit 0 vertical mirroring (the MMC3 ignores it), bit 1 battery, bit 3
    four-screen VRAM. NES 2.0 byte 10: PRG-RAM (low nibble) and battery-backed PRG-NVRAM (high)."""
    pu, cu = prg // 0x4000, chr_rom // 0x2000
    f6 = ((mapper & 0x0F) << 4) | (1 if mirroring == "v" else 0) | (2 if battery else 0) | (8 if four_screen else 0)
    if not nes2:
        assert pu <= 255 and cu <= 255 and mapper <= 255
        return b"NES\x1a" + bytes([pu, cu, f6, mapper & 0xF0]) + bytes(8)
    return b"NES\x1a" + bytes([pu & 0xFF, cu & 0xFF, f6, (mapper & 0xF0) | 0x08,
                               (submapper << 4) | (mapper >> 8), ((cu >> 8) << 4) | (pu >> 8),
                               (_ram_shift(prg_nvram) << 4) | _ram_shift(prg_ram), _ram_shift(chr_ram), 0, 0, 0, 0])


# ---------------------------------------------------------------- the MMC3, from the wiki

class NotStored:
    """An expected read that must not be the byte stored at its address: what a read of disabled or
    missing PRG-RAM gives (open bus, whose value isn't pinned) as long as the stored byte isn't $60-$7F."""

    def __init__(self, stored: int):
        assert not 0x60 <= stored <= 0x7F, "pick a stored byte open bus at $6000-$7FFF can't give"
        self.stored = stored

    def __repr__(self):
        return f"not ${self.stored:02X}"


class Mmc3:
    """The MMC3 on a TxROM board (iNES mapper 4; NES 2.0 submapper 0, the Sharp MMC3B/C), as the NESdev
    wiki's "MMC3" page describes it.
    - $8000-$9FFE even, bank select: bits 2-0 pick R0-R7 for the next bank data write; bit 6 the PRG mode
      (0: $8000 = R6, $C000 = the second-last bank; 1: the two swapped); bit 7 CHR A12 inversion (0: two
      2 KiB banks R0, R1 at $0000-$0FFF and four 1 KiB banks R2-R5 at $1000-$1FFF; 1: the halves swapped);
      bit 5 "Nothing on the MMC3" (MMC6's PRG-RAM enable), bits 4-3 unused.
    - $8001-$9FFF odd, bank data: R6/R7 "ignore the top two bits"; R0/R1 "ignore the bottom bit". Bank
      numbers past the ROM's (or CHR-RAM's) last bank wrap (sizes are powers of two here). $A000-$BFFF is
      always R7; $E000-$FFFF is always the last bank.
    - $A000 even, nametable arrangement: 0 = A10 (vertical mirroring), 1 = A11 (horizontal); no effect with
      four-screen VRAM (header byte 6 bit 3).
    - $A001 odd, PRG-RAM protect: bit 7 chip enable (0: reads are open bus), bit 6 write protection.
    - $C000 even latch; $C001 odd: the counter cleared at once and reloaded at the next clock; $E000 even:
      IRQs disabled and any pending one acknowledged; $E001 odd: enabled. Each register answers its whole
      range ($8000-$9FFF, $A000-$BFFF, $C000-$DFFF, $E000-$FFFF), even or odd address. No bus conflicts.
    - The counter is clocked by a filtered rise of PPU A12: if it's zero or the reload flag is set it's
      reloaded from the latch, else decremented; then if it's zero and IRQs are enabled, the IRQ line is
      asserted (the Sharp MMC3B/C, submapper 0: a latch of 0 gives an IRQ at every clock)."""

    def __init__(self, prg: bytes, chr_bytes: bytes, chr_ram: bool, prg_ram: int, nes2=True, four_screen=False):
        assert len(prg) >= 0x4000 and len(prg) & (len(prg) - 1) == 0, "the probes use power-of-two PRG sizes"
        assert len(chr_bytes) & (len(chr_bytes) - 1) == 0
        assert prg_ram in (0, 0x2000), "the MMC3 maps 8 KiB of PRG-RAM; other sizes aren't pinned"
        self.prg, self.chr_ram, self.nes2, self.four_screen = prg, chr_ram, nes2, four_screen
        self.chr = bytearray(chr_bytes)
        self.chr_known = bytearray([0 if chr_ram else 1]) * len(self.chr)
        self.ram_size = prg_ram
        self.ram = bytearray(0x2000)
        self.ram_known = bytearray(0x2000)
        self.vram = bytearray(0x1000)
        self.vram_known = bytearray(0x1000)
        self.pal = bytearray(32)
        self.select = None                 # the last bank select value
        self.r = [None] * 8                # R0-R7
        self.mirror = None                 # $A000 bit 0
        self.ram_ctl = None                # $A001 bits 7-6
        # The IRQ counter.
        self.latch = None
        self.counter = None
        self.flag = None                   # the reload flag
        self.last_flag_reload = None       # was the counter's last clock a reload by the flag?
        self.enabled = None
        self.pending = None                # the IRQ line asserted
        # PPU A12 as the cartridge sees it (rendering off: v's bit 12), and since when it has been low.
        self.bus = None
        self.low_since = None
        self.now = 0

    # -- the CPU side
    def _bank(self, reg: int) -> int:
        if self.r[reg] is None:
            raise Unknown(f"R{reg} is unknown (power-on)")
        return self.r[reg]

    def prg_offset(self, a: int) -> int:
        assert 0x8000 <= a <= 0xFFFF
        count = len(self.prg) // 0x2000
        window = (a >> 13) & 3
        if window == 3:
            bank = count - 1
        elif window == 1:
            bank = self._bank(7) & 0x3F
        else:
            if self.select is None:
                raise Unknown("the PRG mode (bank select bit 6) is unknown (power-on)")
            swapped = (self.select >> 6) & 1
            bank = (self._bank(6) & 0x3F) if (window == 0) != bool(swapped) else count - 2
        return (bank % count) * 0x2000 + (a & 0x1FFF)

    def _ram_state(self) -> str:
        """'rw', 'ro' or 'off' for the PRG-RAM now, or raise Unknown."""
        if self.ram_ctl is None:
            raise Unknown("PRG-RAM before $A001 is written (power-on)")
        if not self.nes2 and self.ram_ctl != 0x80:
            raise Unknown("$A001 other than $80 on an iNES 1.0 file (MMC3/MMC6 conflation)")
        if not self.ram_ctl & 0x80:
            return "off"
        return "ro" if self.ram_ctl & 0x40 else "rw"

    def cpu_read(self, a: int):
        if a >= 0x8000:
            return self.prg[self.prg_offset(a)]
        assert 0x6000 <= a < 0x8000, "the probes read the cartridge only at $6000-$FFFF"
        i = a & 0x1FFF
        if self.ram_size == 0 or self._ram_state() == "off":
            if not self.ram_known[i]:
                raise Unknown(f"${a:04X}: open bus (and nothing stored to tell it from)")
            return NotStored(self.ram[i])
        if not self.ram_known[i]:
            raise Unknown(f"PRG-RAM ${a:04X} was never written")
        return self.ram[i]

    def write(self, a: int, v: int):
        if a < 0x6000:
            return
        if a < 0x8000:
            if self.ram_size == 0:
                if self.nes2:
                    # Nothing there: the byte is remembered only so a later read can be told apart from it.
                    self.ram[a & 0x1FFF], self.ram_known[a & 0x1FFF] = v, 1
                return
            if self._ram_state() == "rw":
                self.ram[a & 0x1FFF], self.ram_known[a & 0x1FFF] = v, 1
            return
        reg = a & 0xE001
        if reg == 0x8000:
            if not self.nes2 and v & 0x20:
                raise Unknown("bank select bit 5 on an iNES 1.0 file (MMC6's PRG-RAM enable)")
            self.select = v
        elif reg == 0x8001:
            if self.select is None:
                raise Unknown("bank data before any bank select (power-on)")
            n = self.select & 7
            self.r[n] = v & (0x3F if n >= 6 else 0xFF)
        elif reg == 0xA000:
            self.mirror = v & 1
        elif reg == 0xA001:
            self.ram_ctl = v & 0xC0
        elif reg == 0xC000:
            self.latch = v
        elif reg == 0xC001:
            if self.last_flag_reload is not False:
                # The reload glitch (or not knowing whether it applies): the counter's next clocks aren't pinned.
                self.counter, self.flag = None, None
                self.last_flag_reload = None
            else:
                self.counter, self.flag = None, True    # "a value above 127", reloaded at the next clock
        elif reg == 0xE000:
            self.enabled, self.pending = False, False
        else:
            self.enabled = True

    # -- the IRQ counter
    def clock(self, maybe=False):
        """One filtered rise of A12 (maybe: one that may or may not have counted)."""
        if maybe:
            # If it counted, the flag is now clear; if the flag was clear already, it stays so, and no
            # flag reload happened either way.
            known_clear = self.flag is False and self.last_flag_reload is False
            self.counter = None
            self.flag = False if self.flag is False else None
            self.last_flag_reload = False if known_clear else None
            if self.enabled is not False and self.pending is not True:
                self.pending = None
            return
        if self.flag is True:
            self.counter, self.flag, self.last_flag_reload = self.latch, False, True
        elif self.flag is False and self.counter is not None:
            self.counter = self.latch if self.counter == 0 else self.counter - 1
            self.last_flag_reload = False
        else:
            # The counter's value isn't known; whatever the flag was, this clock leaves it clear.
            self.counter, self.last_flag_reload = None, None if self.flag is None else False
            self.flag = False
        if self.counter == 0:
            if self.enabled is True:
                self.pending = True
            elif self.enabled is None:
                self.pending = None
        elif self.counter is None and self.enabled is not False and self.pending is not True:
            self.pending = None

    def irq_taken(self) -> bool:
        if self.pending is None:
            raise Unknown("whether the IRQ line is asserted isn't known here")
        return self.pending

    def ppu_bus(self, addr: int, dt: int):
        """PPU address `addr` put on the bus `dt` CPU cycles after the last change (rendering off)."""
        self.now += dt
        new = (addr >> 12) & 1
        old = self.bus
        if new and not old:
            if old is None or (addr & 0x3FFF) >= 0x3000:
                self.clock(maybe=True)
            elif self.now - self.low_since >= 8:
                self.clock()
            else:
                self.clock(maybe=True)
        if not new and old != 0:
            self.low_since = self.now
        self.bus = new

    # -- the PPU side
    def nt_index(self, a: int) -> int:
        t = (a >> 10) & 3
        if self.four_screen:
            return t * 0x400 + (a & 0x3FF)
        if self.mirror is None:
            raise Unknown("the nametable arrangement ($A000) is unknown (power-on)")
        page = (t & 1) if self.mirror == 0 else (t >> 1)
        return page * 0x400 + (a & 0x3FF)

    def chr_index(self, a: int) -> int:
        a &= 0x1FFF
        if self.select is None:
            raise Unknown("the CHR A12 inversion (bank select bit 7) is unknown (power-on)")
        slot = (a >> 10) & 7
        if self.select & 0x80:
            slot ^= 4
        if slot < 4:
            bank = (self._bank(slot >> 1) & 0xFE) | (slot & 1)
        else:
            bank = self._bank(slot - 2)
        return (bank * 0x400 + (a & 0x3FF)) % len(self.chr)

    def ppu_read(self, a: int) -> int:
        a &= 0x3FFF
        if a < 0x2000:
            i = self.chr_index(a)
            if not self.chr_known[i]:
                raise Unknown(f"CHR-RAM ${a:04X} was never written")
            return self.chr[i]
        assert a < 0x3F00, "palette reads aren't buffered; the probe doesn't make them"
        i = self.nt_index(a)
        if not self.vram_known[i]:
            raise Unknown(f"nametable RAM ${a:04X} was never written")
        return self.vram[i]

    def ppu_write(self, a: int, v: int):
        a &= 0x3FFF
        if a < 0x2000:
            if self.chr_ram:
                i = self.chr_index(a)
                self.chr[i], self.chr_known[i] = v, 1
        elif a < 0x3F00:
            i = self.nt_index(a)
            self.vram[i], self.vram_known[i] = v, 1
        else:
            i = a & 0x1F
            if i & 0x13 == 0x10:
                i &= 0x0F
            self.pal[i] = v & 0x3F

    def chr_view(self) -> bytes:
        out = bytearray()
        for a in range(0x2000):
            i = self.chr_index(a)
            if not self.chr_known[i]:
                raise Unknown(f"CHR ${a:04X} was never written")
            out.append(self.chr[i])
        return bytes(out)


def irq_schedule(latch: int, second=None, every=False, renders=True) -> list:
    """The IRQs of one frame of the probe's IRQ split, from the wiki's counter (`Mmc3.clock`): the NMI has
    written $C000 = latch, $C001 and $E001 in vblank; with the background at $0000 and sprites at $1000
    the counter is clocked once a line, at the sprite fetches (dot 260), on the pre-render line and lines
    0-239: 241 clocks ("oscillate exactly one time per scanline and 241 times per frame"). The handler
    writes $E000 (acknowledge and disable); on its first IRQ, with `second`, $C000 = second and $E001;
    with `every`, $E001 each time. With rendering off there are no clocks. Returns [(line, phase)], the
    pre-render line as -1."""
    m = Mmc3(bytes(0x4000), bytes(0x2000), False, 0)
    m.latch, m.flag, m.last_flag_reload, m.enabled, m.pending = latch, True, False, True, False
    out = []
    if not renders:
        return out
    for c in range(241):
        m.clock()
        if m.irq_taken():
            phase = len(out)
            m.write(0xE000, 0)
            if phase == 0 and second is not None:
                m.write(0xC000, second)
                m.write(0xE001, 0)
            if every:
                m.write(0xE001, 0)
            out.append((c - 1, phase))
    return out


# ---------------------------------------------------------------- the probe program

OPS = {
    "sei": (0x78, 0), "cli": (0x58, 0), "cld": (0xD8, 0), "txs": (0x9A, 0), "tax": (0xAA, 0), "txa": (0x8A, 0),
    "tay": (0xA8, 0), "tya": (0x98, 0), "pha": (0x48, 0), "pla": (0x68, 0), "iny": (0xC8, 0), "inx": (0xE8, 0),
    "rts": (0x60, 0), "rti": (0x40, 0), "nop": (0xEA, 0), "lsr": (0x4A, 0), "clc": (0x18, 0),
    "lda#": (0xA9, 1), "ldx#": (0xA2, 1), "ldy#": (0xA0, 1), "cmp#": (0xC9, 1), "and#": (0x29, 1),
    "sta.z": (0x85, 1), "lda.z": (0xA5, 1), "inc.z": (0xE6, 1), "dec.z": (0xC6, 1), "cpy.z": (0xC4, 1),
    "ldx.z": (0xA6, 1), "lda.iy": (0xB1, 1), "sta.iy": (0x91, 1),
    "lda": (0xAD, 2), "sta": (0x8D, 2), "stx": (0x8E, 2), "bit": (0x2C, 2), "jmp": (0x4C, 2), "jsr": (0x20, 2),
    "inc": (0xEE, 2), "sta,x": (0x9D, 2), "jmp.i": (0x6C, 2),
    "bpl": (0x10, "rel"), "bmi": (0x30, "rel"), "bne": (0xD0, "rel"), "beq": (0xF0, "rel"),
    "bvs": (0x70, "rel"), "bvc": (0x50, "rel"),
}
# Zero page.
PTR, ADDR, VAL, LOGP, N, SRC, QFLAG = 0x00, 0x02, 0x04, 0x05, 0x07, 0x08, 0x0A
CTRL, MASK, FLAG, WA, VA, WB, VB = 0x10, 0x11, 0x13, 0x14, 0x16, 0x17, 0x19
XA, XB, XC, N1, N2, SEL, BKA, BKB, OPT, PHASE, COUNT, FIDX = range(0x20, 0x2C)
# The IRQ split's options (OPT bits).
O_CHR, O_SECOND, O_EVERY, O_APU, O_DMA, O_BLANK = 0x01, 0x02, 0x04, 0x08, 0x10, 0x20


def assemble(prog: list, org: int) -> tuple:
    """Labels are str ending in ':'; instructions (mnemonic, operand) with an int, a label, or
    ('<', label) / ('>', label). Returns (code, {label: address})."""
    labels, pc = {}, org
    for item in prog:
        if isinstance(item, str):
            labels[item.rstrip(":")] = pc
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


def _vector(at: int, label: str) -> list:
    return [("lda#", ("<", label)), ("sta", at), ("lda#", (">", label)), ("sta", at + 1)]


def _inc16(a: int, tag: str) -> list:
    return [("inc", a), ("bne", tag), ("inc", a + 1), f"{tag}:"]


def _interpreter() -> list:
    """The probe's program. The script is a list of ops, each an op byte and its operands:
      0 END ctrl mask mode ...   set $0200 = $AA, then in the next vblank $2005 = scroll, $2000 = ctrl,
                                 $2001 = mask. Mode 0: spin (I set). Mode 1 (alo ahi va blo bhi vb): a
                                 sprite-0 split, as I6's: each NMI writes va at A; each frame the main loop
                                 waits for the hit and writes vb at B. Mode 2 (xa xb xc n1 n2 sel bka bkb
                                 opt): an MMC3 IRQ split (see `IrqSplit`).
      1 W   lo hi v              CPU write v to hi:lo (one STA (zp),Y)
      2 R   lo hi                CPU read hi:lo, logged
      3 PR  hi lo                PPU read: $2006 = hi, lo; one $2007 read to fill the buffer, the next logged
      4 PW  hi lo v              PPU write v at hi:lo
      5 PC  hi lo slo shi n      PPU copy: n bytes (0 = 256) from CPU shi:slo on, written at PPU hi:lo
      6 PF  hi lo v n            PPU fill: n bytes (0 = 256) of v at PPU hi:lo
      7 OAM y tile attr x        sprite 0 as given, the other 63 at Y = $FF, sent by $4014 DMA from $0600
      8 A6  hi lo                $2006 = hi, lo, nothing more (PPU A12 moved by hand, as blargg's tests do)
      9 Q                        IRQ query: CLI, NOP, NOP, SEI; logs 1 if an IRQ was taken (its handler
                                 writes $E000, acknowledging and disabling), else 0
     10 V   n                    wait for n vblank flags ($2002 bit 7)
    Reset: SEI, $4017 = $40 (no APU frame IRQ), $4010 = 0, $2000 = $2001 = 0, $E000 (no MMC3 IRQ), two
    vblanks; the log starts at $0300 again, with $0200 cleared first."""
    def dispatch(n, label):
        return [("cmp#", n), ("bne", f"not{n}"), ("jmp", label), f"not{n}:"]
    return [
        "reset:", ("sei", None), ("cld", None), ("ldx#", 0xFF), ("txs", None),
        ("lda#", 0x40), ("sta", 0x4017), ("lda#", 0), ("sta", 0x4010), ("sta", 0x2000), ("sta", 0x2001),
        ("sta", DONE), ("sta", 0xE000), ("bit", 0x4015),
        *_vector(NMIV, "rti_only"), *_vector(IRQV, "irq_q"),
        ("bit", 0x2002), "w1:", ("bit", 0x2002), ("bpl", "w1"), "w2:", ("bit", 0x2002), ("bpl", "w2"),
        ("lda#", SCRIPT & 0xFF), ("sta.z", PTR), ("lda#", SCRIPT >> 8), ("sta.z", PTR + 1),
        ("lda#", LOG & 0xFF), ("sta.z", LOGP), ("lda#", LOG >> 8), ("sta.z", LOGP + 1),
        "next:", ("ldy#", 0), ("lda.iy", PTR),
        ("bne", "op1"), ("jmp", "end"), "op1:",
        *dispatch(1, "op_w"), *dispatch(2, "op_r"), *dispatch(3, "op_pr"), *dispatch(4, "op_pw"),
        *dispatch(5, "op_pc"), *dispatch(6, "op_pf"), *dispatch(7, "op_oam"), *dispatch(8, "op_a6"),
        *dispatch(9, "op_q"), *dispatch(10, "op_v"),
        "halt:", ("jmp", "halt"),
        "getb:", ("inc.z", PTR), ("bne", "g1"), ("inc.z", PTR + 1), "g1:", ("ldy#", 0), ("lda.iy", PTR), ("rts", None),
        "log:", ("ldy#", 0), ("sta.iy", LOGP), ("inc.z", LOGP), ("bne", "l1"), ("inc.z", LOGP + 1), "l1:", ("rts", None),
        "adv:", ("inc.z", PTR), ("bne", "a1"), ("inc.z", PTR + 1), "a1:", ("jmp", "next"),
        "op_w:", ("jsr", "getb"), ("sta.z", ADDR), ("jsr", "getb"), ("sta.z", ADDR + 1), ("jsr", "getb"),
        ("ldy#", 0), ("sta.iy", ADDR), ("jmp", "adv"),
        "op_r:", ("jsr", "getb"), ("sta.z", ADDR), ("jsr", "getb"), ("sta.z", ADDR + 1),
        ("ldy#", 0), ("lda.iy", ADDR), ("jsr", "log"), ("jmp", "adv"),
        "op_pr:", ("jsr", "getb"), ("tax", None), ("jsr", "getb"), ("pha", None),
        ("bit", 0x2002), ("stx", 0x2006), ("pla", None), ("sta", 0x2006),
        ("lda", 0x2007), ("lda", 0x2007), ("jsr", "log"), ("jmp", "adv"),
        "op_pw:", ("jsr", "getb"), ("tax", None), ("jsr", "getb"), ("pha", None), ("jsr", "getb"), ("sta.z", VAL),
        ("bit", 0x2002), ("stx", 0x2006), ("pla", None), ("sta", 0x2006), ("lda.z", VAL), ("sta", 0x2007),
        ("jmp", "adv"),
        "op_pc:", ("jsr", "getb"), ("tax", None), ("jsr", "getb"), ("pha", None),
        ("jsr", "getb"), ("sta.z", SRC), ("jsr", "getb"), ("sta.z", SRC + 1), ("jsr", "getb"), ("sta.z", N),
        ("bit", 0x2002), ("stx", 0x2006), ("pla", None), ("sta", 0x2006),
        ("ldy#", 0), "pc1:", ("lda.iy", SRC), ("sta", 0x2007), ("iny", None), ("cpy.z", N), ("bne", "pc1"),
        ("jmp", "adv"),
        "op_pf:", ("jsr", "getb"), ("tax", None), ("jsr", "getb"), ("pha", None),
        ("jsr", "getb"), ("sta.z", VAL), ("jsr", "getb"), ("sta.z", N),
        ("bit", 0x2002), ("stx", 0x2006), ("pla", None), ("sta", 0x2006),
        ("ldy#", 0), ("lda.z", VAL), "pf1:", ("sta", 0x2007), ("iny", None), ("cpy.z", N), ("bne", "pf1"),
        ("jmp", "adv"),
        "op_oam:", ("ldx#", 0), ("lda#", 0xFF), "o1:", ("sta,x", OAM_PAGE), ("inx", None), ("bne", "o1"),
        ("jsr", "getb"), ("sta", OAM_PAGE), ("jsr", "getb"), ("sta", OAM_PAGE + 1),
        ("jsr", "getb"), ("sta", OAM_PAGE + 2), ("jsr", "getb"), ("sta", OAM_PAGE + 3),
        ("lda#", 0), ("sta", 0x2003), ("lda#", OAM_PAGE >> 8), ("sta", 0x4014), ("jmp", "adv"),
        "op_a6:", ("jsr", "getb"), ("tax", None), ("jsr", "getb"), ("pha", None),
        ("bit", 0x2002), ("stx", 0x2006), ("pla", None), ("sta", 0x2006), ("jmp", "adv"),
        "op_q:", ("lda#", 0), ("sta.z", QFLAG), ("cli", None), ("nop", None), ("nop", None), ("sei", None),
        ("lda.z", QFLAG), ("jsr", "log"), ("jmp", "adv"),
        "op_v:", ("jsr", "getb"), ("sta.z", N), "vw:", ("bit", 0x2002), ("bpl", "vw"), ("dec.z", N), ("bne", "vw"),
        ("jmp", "adv"),
        # -- END
        "end:", ("jsr", "getb"), ("sta.z", CTRL), ("jsr", "getb"), ("sta.z", MASK), ("jsr", "getb"),
        ("cmp#", 1), ("bne", "e2"),
        ("jsr", "getb"), ("sta.z", WA), ("jsr", "getb"), ("sta.z", WA + 1), ("jsr", "getb"), ("sta.z", VA),
        ("jsr", "getb"), ("sta.z", WB), ("jsr", "getb"), ("sta.z", WB + 1), ("jsr", "getb"), ("sta.z", VB),
        ("lda#", 0), ("sta.z", FLAG), *_vector(NMIV, "nmi1"),
        ("jsr", "done"), ("lda#", 0), ("jsr", "show"), "sw:", ("lda.z", FLAG), ("beq", "sw"), ("lda#", 0), ("sta.z", FLAG),
        "h0:", ("bit", 0x2002), ("bvs", "h0"), "h1:", ("bit", 0x2002), ("bvc", "h1"),
        ("ldy#", 0), ("lda.z", VB), ("sta.iy", WB), ("jmp", "sw"),
        "e2:", ("cmp#", 2), ("beq", "e3"),
        ("jsr", "done"), ("lda#", 0), ("jsr", "show"), "spin:", ("jmp", "spin"),
        "e3:", ("ldx#", 0), "e4:", ("jsr", "getb"), ("sta,x", XA), ("inx", None),
        ("txa", None), ("cmp#", 9), ("bne", "e4"),
        ("lda#", 0), ("ldx#", 0), "e5:", ("sta,x", TOTALS), ("inx", None), ("bne", "e5"),
        ("sta.z", PHASE), ("sta.z", COUNT), ("sta.z", FIDX),
        *_vector(NMIV, "nmi2"), *_vector(IRQV, "split"),
        ("lda.z", OPT), ("and#", O_APU), ("beq", "e6"), *_vector(IRQV, "split_apu"), ("lda#", 0), ("sta", 0x4017),
        "e6:", ("jsr", "done"), ("lda.z", XA), ("jsr", "show"), ("cli", None),
        ("lda.z", OPT), ("and#", O_DMA), ("bne", "dma"),
        "spin2:", ("jmp", "spin2"),
        "dma:", ("lda#", OAM_PAGE >> 8), ("sta", 0x4014), ("jmp", "dma"),
        # done: the log's end and $AA at $0200. show (A = X scroll): in the next vblank, scroll, ctrl, mask.
        "done:", ("lda.z", LOGP), ("sta", DONE + 1), ("lda.z", LOGP + 1), ("sta", DONE + 2),
        ("lda#", 0xAA), ("sta", DONE), ("rts", None),
        "show:", ("pha", None), ("bit", 0x2002), "v3:", ("bit", 0x2002), ("bpl", "v3"),
        ("pla", None), ("sta", 0x2005), ("lda#", 0), ("sta", 0x2005), ("lda.z", CTRL), ("sta", 0x2000),
        ("lda.z", MASK), ("sta", 0x2001), ("rts", None),
        # -- interrupts
        "nmi:", ("jmp.i", NMIV), "irq:", ("jmp.i", IRQV),
        "rti_only:", ("rti", None),
        "irq_q:", ("pha", None), ("lda#", 1), ("sta.z", QFLAG), ("sta", 0xE000), ("pla", None), ("rti", None),
        "nmi1:", ("pha", None), ("tya", None), ("pha", None),
        ("ldy#", 0), ("lda.z", VA), ("sta.iy", WA), ("lda#", 1), ("sta.z", FLAG),
        ("pla", None), ("tay", None), ("pla", None), ("rti", None),
        "nmi2:", ("pha", None), ("txa", None), ("pha", None), ("bit", 0x2002),
        ("lda.z", XA), ("sta", 0x2005), ("lda#", 0), ("sta", 0x2005), ("lda.z", CTRL), ("sta", 0x2000),
        ("ldx.z", FIDX), ("lda.z", COUNT), ("sta,x", RING), ("lda#", 0), ("sta.z", COUNT), ("sta.z", PHASE),
        ("inx", None), ("txa", None), ("and#", 0x3F), ("sta.z", FIDX),
        ("lda.z", OPT), ("and#", O_BLANK), ("beq", "n2"), ("lda.z", FIDX), ("and#", 1), ("beq", "n2"),
        ("lda#", 0), ("sta", 0x2001), ("jmp", "n3"),
        "n2:", ("lda.z", MASK), ("sta", 0x2001),
        "n3:", ("lda.z", OPT), ("and#", O_CHR), ("beq", "n4"),
        ("lda.z", SEL), ("sta", 0x8000), ("lda.z", BKA), ("sta", 0x8001),
        "n4:", ("lda.z", N1), ("sta", 0xC000), ("sta", 0xC001), ("sta", 0xE001),
        *_inc16(TOTALS + 4, "n5"),
        ("pla", None), ("tax", None), ("pla", None), ("rti", None),
        "split_apu:", ("pha", None), ("lda", 0x4015), ("and#", 0x40), ("beq", "split_m"),
        *_inc16(TOTALS + 2, "sa1"), ("pla", None), ("rti", None),
        "split:", ("pha", None),
        "split_m:", ("lda.z", PHASE), ("bne", "p1"),
        ("lda.z", XB), ("sta", 0x2005), ("lda#", 0), ("sta", 0x2005), ("sta", 0xE000), ("inc.z", PHASE),
        ("lda.z", OPT), ("and#", O_CHR), ("beq", "s1"), ("lda.z", SEL), ("sta", 0x8000), ("lda.z", BKB), ("sta", 0x8001),
        "s1:", ("lda.z", OPT), ("and#", O_SECOND), ("beq", "count"), ("lda.z", N2), ("sta", 0xC000), ("sta", 0xE001),
        ("jmp", "count"),
        "p1:", ("cmp#", 1), ("bne", "pe"),
        ("lda.z", XC), ("sta", 0x2005), ("lda#", 0), ("sta", 0x2005), ("sta", 0xE000), ("inc.z", PHASE),
        ("jmp", "count"),
        "pe:", ("sta", 0xE000),
        "count:", ("lda.z", OPT), ("and#", O_EVERY), ("beq", "c3"), ("sta", 0xE001),
        "c3:", ("inc.z", COUNT), *_inc16(TOTALS, "c4"), ("pla", None), ("rti", None),
    ]


CODE, LABELS = assemble(_interpreter(), BLOCK)
assert BLOCK + len(CODE) <= SCRIPT, f"the probe's program is {len(CODE)} bytes"


class IrqSplit:
    """The probe's MMC3 IRQ split (END mode 2), as an MMC3 game makes a status bar. Every NMI: $2005 =
    xa, 0 and $2000 = ctrl for the top of the frame; with chr, $8000 = sel and $8001 = bka; $C000 = n1,
    $C001 and $E001; the last frame's IRQ count to RAM. The main loop: CLI, then spin (or, with dma, OAM
    DMA back to back). The IRQ handler, on its first IRQ of a frame: $2005 = xb, 0, then $E000; with chr,
    $8000 = sel and $8001 = bkb; with second, $C000 = second and $E001. On its second: $2005 = xc, 0 and
    $E000. With every, it writes $E001 after each, and counts. With apu, the APU's frame IRQ is on too
    ($4017 = $00), and the handler takes it first when $4015 bit 6 says so. With blank, every odd
    frame (by the NMI's count) is drawn with rendering off. Scroll values are multiples of 8, so a fine-X
    write in the middle of a line changes nothing."""

    def __init__(self, n1, xa=0, xb=0, xc=0, second=None, chr=None, every=False, apu=False, dma=False, blank=False):
        assert xa % 8 == xb % 8 == xc % 8 == 0
        self.n1, self.xa, self.xb, self.xc, self.second, self.chr = n1, xa, xb, xc, second, chr
        self.every, self.apu, self.dma, self.blank = every, apu, dma, blank

    def opt(self) -> int:
        return ((O_CHR if self.chr else 0) | (O_SECOND if self.second is not None else 0) | (O_EVERY if self.every else 0)
                | (O_APU if self.apu else 0) | (O_DMA if self.dma else 0) | (O_BLANK if self.blank else 0))

    def params(self) -> bytes:
        sel, bka, bkb = self.chr or (0, 0, 0)
        return bytes([self.xa, self.xb, self.xc, self.n1, self.second or 0, sel, bka, bkb, self.opt()])

    def schedule(self) -> list:
        return irq_schedule(self.n1, self.second, self.every)


class Mmc3Probe:
    """A probe cartridge for mapper 4, and the script its program runs, built against `Mmc3`. Each CPU
    write is played on the model as it's added (so a case can't depend on what the wiki leaves unknown);
    `expected_log` replays the whole script on a fresh model once the image is final."""

    GAP = 20          # CPU cycles from one op's last PPU bus change to the next op's first, at least

    def __init__(self, prg_kib=128, chr_kib=128, chr_ram_kib=8, prg_ram_kib=8, prg_nvram_kib=0, battery=False,
                 nes2=True, submapper=0, four_screen=False, mirroring="h", seed=1):
        self.nes2, self.submapper, self.four_screen, self.mirroring = nes2, submapper, four_screen, mirroring
        self.battery = battery
        rng = random.Random(f"i8-prg-{seed}")
        self.prg = bytearray(rng.randbytes(prg_kib * 1024))
        self.prg[-0x2000:] = bytes(0x2000)
        self.chr_rom = random.Random(f"i8-chr-{seed}").randbytes(chr_kib * 1024) if chr_kib else b""
        self.chr_ram = 0 if chr_kib else chr_ram_kib * 1024
        self.prg_ram, self.prg_nvram = prg_ram_kib * 1024, prg_nvram_kib * 1024
        self.ops, self.labels = [], []
        self.ctrl = self.mask = None
        self.mode = (0, b"")
        self.oam = None
        self.bank_mode = 0x00
        self.plan = self.model()

    # -- the cartridge
    def ram_total(self) -> int:
        return (self.prg_ram + self.prg_nvram) if self.nes2 else 0x2000

    def model(self) -> Mmc3:
        m = Mmc3(bytes(self.prg), self.chr_rom or bytes(self.chr_ram), not self.chr_rom, self.ram_total(), self.nes2,
                 self.four_screen)
        m.write(0xE000, 0)                     # the program's reset code
        return m

    def script_bytes(self) -> bytes:
        out = bytearray()
        for kind, args in self.ops:
            if kind == "W":
                a, v = args
                out += bytes([1, a & 0xFF, a >> 8, v])
            elif kind == "R":
                out += bytes([2, args[0] & 0xFF, args[0] >> 8])
            elif kind == "PR":
                out += bytes([3, args[0] >> 8, args[0] & 0xFF])
            elif kind == "PW":
                a, v = args
                out += bytes([4, a >> 8, a & 0xFF, v])
            elif kind == "PC":
                a, src, n = args
                out += bytes([5, a >> 8, a & 0xFF, src & 0xFF, src >> 8, n & 0xFF])
            elif kind == "PF":
                a, v, n = args
                out += bytes([6, a >> 8, a & 0xFF, v, n & 0xFF])
            elif kind == "OAM":
                out += bytes([7, *args])
            elif kind == "A6":
                out += bytes([8, args[0] >> 8, args[0] & 0xFF])
            elif kind == "Q":
                out += bytes([9])
            elif kind == "V":
                out += bytes([10, args[0]])
        mode, params = self.mode
        out += bytes([0, self.ctrl or 0, self.mask or 0, mode]) + params
        return bytes(out)

    def image(self) -> bytes:
        script = self.script_bytes()
        assert SCRIPT + len(script) <= 0xFFFA, f"the script is {len(script)} bytes"
        prg = self.prg
        b = len(prg) - 0x2000
        prg[b:b + len(CODE)] = CODE
        prg[b + (SCRIPT - BLOCK):b + (SCRIPT - BLOCK) + len(script)] = script
        prg[b + 0x1FFA:b + 0x2000] = struct.pack("<HHH", LABELS["nmi"], LABELS["reset"], LABELS["irq"])
        hdr = header(len(prg), len(self.chr_rom), 4, self.nes2, self.submapper, prg_ram=self.prg_ram,
                     prg_nvram=self.prg_nvram, chr_ram=self.chr_ram if (self.nes2 and not self.chr_rom) else 0,
                     battery=self.battery, four_screen=self.four_screen, mirroring=self.mirroring)
        return hdr + bytes(prg) + self.chr_rom

    def write_to(self, tmp_path, name: str) -> str:
        p = tmp_path / name
        p.write_bytes(self.image())
        return str(p)

    # -- the script, played on the model as it is built
    def _play(self, m: Mmc3, kind: str, args, log: list):
        """One op on a model; reads go to log."""
        g = self.GAP
        if kind == "W":
            m.write(*args)
        elif kind == "R":
            log.append(m.cpu_read(args[0]))
        elif kind == "PR":
            a = args[0]
            m.ppu_bus(a, g)
            m.ppu_bus(a + 1, 4)
            log.append(m.ppu_read(a))
            m.ppu_bus(a + 2, 4)
        elif kind == "PW":
            a, v = args
            m.ppu_bus(a, g)
            m.ppu_write(a, v)
            m.ppu_bus(a + 1, 7)
        elif kind in ("PC", "PF"):
            a, x, n = args
            m.ppu_bus(a, g)
            for i in range(n or 256):
                m.ppu_write(a + i, m.cpu_read(x + i) if kind == "PC" else x)
                m.ppu_bus(a + i + 1, (11 if kind == "PC" else 9) if i == 0 else (17 if kind == "PC" else 12))
        elif kind == "A6":
            m.ppu_bus(args[0], g)
        elif kind == "Q":
            taken = m.irq_taken()
            log.append(1 if taken else 0)
            if taken:
                m.write(0xE000, 0)
        elif kind == "OAM" or kind == "V":
            pass

    def _add(self, kind, args, label=None):
        self._play(self.plan, kind, args, [])
        self.ops.append((kind, args))
        if label is not None:
            self.labels.append(label)

    def write(self, a: int, v: int):
        """One CPU write of v at a."""
        self._add("W", (a, v))

    def select(self, reg: int, value: int, at=0x8000, data_at=0x8001):
        """Bank select (with the probe's current PRG mode and CHR inversion), then bank data."""
        self.write(at, self.bank_mode | reg)
        self.write(data_at, value)

    def banks(self, prg_mode=0, chr_inv=0, r=(0, 2, 4, 5, 6, 7, 0, 1)):
        """Set the PRG mode and CHR inversion, and every bank register, as an MMC3 game's reset code does."""
        self.bank_mode = (prg_mode << 6) | (chr_inv << 7)
        for n, v in enumerate(r):
            self.select(n, v)

    def read(self, a: int, why=""):
        self._add("R", (a,), f"CPU ${a:04X} {why}".strip())

    def ppu_read(self, a: int, why=""):
        self._add("PR", (a,), f"PPU ${a:04X} {why}".strip())

    def ppu_write(self, a: int, v: int):
        self._add("PW", (a, v))

    def ppu_copy(self, a: int, src: int, n: int):
        """n (1-256) bytes from CPU src on (PRG-ROM outside the program's bank, or PRG-RAM) to PPU a on."""
        assert all(not self.in_program(src + i) for i in range(n)), "copy from data, not the program"
        self._add("PC", (a, src, n))

    def ppu_fill(self, a: int, v: int, n: int):
        self._add("PF", (a, v, n))

    def a6(self, addr: int):
        self._add("A6", (addr,))

    def clock(self):
        """One rise of PPU A12 by hand ($2006 to $1000, then back to $0000), as blargg's clock_counter."""
        self.a6(0x1000)
        self.a6(0x0000)

    def query(self, why=""):
        self._add("Q", (), f"IRQ? {why}".strip())

    def wait(self, frames: int):
        self._add("V", (frames,))

    def sprite0(self, y: int, tile: int, attr: int, x: int):
        self.oam = bytes([y, tile, attr, x]) + b"\xff" * 252
        self.ops.append(("OAM", (y, tile, attr, x)))

    def in_program(self, a: int) -> bool:
        if a < 0x8000:
            return False
        return self.plan.prg_offset(a) >= len(self.prg) - 0x2000

    def irq_setup(self):
        """The IRQ off and the counter clocked twice, so a later $C001 meets no reload glitch (as blargg's
        begin_counter_test does)."""
        self.write(0xE000, 0)
        self.a6(0x0000)
        self.clock()
        self.clock()

    def end(self, ctrl=0x00, mask=0x00, split=None, irq: IrqSplit = None):
        """The script's end: rendering on with $2000 = ctrl and $2001 = mask. split = ((A, va), (B, vb)):
        I6's sprite-0 split. irq: an MMC3 IRQ split."""
        if split:
            (wa, va), (wb, vb) = split
            self.mode = (1, bytes([wa & 0xFF, wa >> 8, va, wb & 0xFF, wb >> 8, vb]))
            ctrl |= 0x80
        elif irq:
            self.mode = (2, irq.params())
            ctrl |= 0x80
        else:
            self.mode = (0, b"")
        self.ctrl, self.mask = ctrl, mask
        self.split, self.irq = split, irq

    # -- what the spec says the run gives
    def replay(self, ram_preset=None) -> tuple:
        """(the log, the final model) from running the finished script on a fresh model; ram_preset: PRG-RAM
        bytes {address: value} the cartridge holds at the start (a battery save)."""
        self.image()
        m = self.model()
        for a, v in (ram_preset or {}).items():
            m.ram[a & 0x1FFF], m.ram_known[a & 0x1FFF] = v, 1
        log = []
        for kind, args in self.ops:
            self._play(m, kind, args, log)
        assert len(log) <= LOG_MAX, f"the log would be {len(log)} bytes"
        return log, m

    def expected_log(self, ram_preset=None) -> list:
        return self.replay(ram_preset)[0]

    def expected_frame(self, scroll_x=0, extra=(), ram_preset=None) -> list:
        """The frame the 2C02 draws once the script has turned rendering on, through I2's spec renderer:
        the four nametables as the MMC3 maps them, the palette, the 8 KiB of CHR it shows, scroll
        (scroll_x, 0). extra: more CPU writes played on the model first."""
        _, m = self.replay(ram_preset)
        for a, v in extra:
            m.write(a, v)
        tables = bytearray()
        for t in range(4):
            for off in range(0x400):
                i = m.nt_index(0x2000 + t * 0x400 + off)
                if not m.vram_known[i]:
                    raise Unknown(f"nametable ${0x2000 + t * 0x400 + off:04X} was never written")
                tables.append(m.vram[i])
        pal = bytearray(m.pal)
        for i in (0x10, 0x14, 0x18, 0x1C):
            pal[i] = pal[i - 0x10]
        scene = Scene(chr=m.chr_view(), uploads=[(0x2000, bytes(tables)), (0x3F00, bytes(pal))],
                      oam=self.oam, ctrl=self.ctrl & 0x7F, scroll=(scroll_x, 0), mask=self.mask, mirroring="four")
        return reference_frame(scene)

    def split_frames(self) -> tuple:
        """The sprite-0 split's two frames: with the NMI's write in effect, and with the main loop's."""
        (wa, va), (wb, vb) = self.split
        return self.expected_frame(extra=[(wa, va)]), self.expected_frame(extra=[(wb, vb)])

    def irq_frame(self) -> list:
        """The IRQ split's frame, by the spec: row by row from the top configuration (scroll xa, CHR bank
        bka) to the next (xb, bkb) and the third (xc), at the rows the IRQ schedule gives. An IRQ on line L
        (dot 260) lands its $2005 write in line L + 1, before its dot 257, so the new scroll shows from
        line L + 2 (NESdev "PPU scrolling": coarse X is copied from t at dot 257); a CHR bank write lands in
        line L + 1 too, during its fetches, so that line is None (not pinned) and the new bank shows from
        L + 2. Rows are lists of 256 values, or None."""
        q = self.irq
        sel, bka, bkb = q.chr or (0, 0, 0)
        extra_a = [(0x8000, sel), (0x8001, bka)] if q.chr else []
        extra_b = [(0x8000, sel), (0x8001, bkb)] if q.chr else []
        frames = {"a": self.expected_frame(q.xa, extra_a), "b": self.expected_frame(q.xb, extra_b)}
        if any(ph == 1 for _, ph in q.schedule()):
            frames["c"] = self.expected_frame(q.xc, extra_b)
        which = ["a"] * H
        unpinned = set()
        for line, phase in q.schedule():
            if phase > 1:
                continue
            for y in range(max(0, line + 2), H):
                which[y] = "b" if phase == 0 else "c"
            if phase == 0 and q.chr and 0 <= line + 1 < H:
                unpinned.add(line + 1)
        return [None if y in unpinned else frames[which[y]][y * W:(y + 1) * W] for y in range(H)]


# ---------------------------------------------------------------- comparing and reading frames

def rows_of(pixels: list) -> list:
    return [pixels[y * W:(y + 1) * W] for y in range(H)]


def row_mismatches(got: list, want_rows: list) -> list:
    """The lines where a frame differs from the spec's rows (None rows aren't pinned)."""
    return [y for y, row in enumerate(want_rows) if row is not None and got[y * W:(y + 1) * W] != row]


def frame_hash(pixels: list) -> str:
    """SHA-256 of a frame as cases/drive_ppu.ts hashes it (little-endian 16-bit values)."""
    return hashlib.sha256(struct.pack(f"<{W * H}H", *pixels)).hexdigest()


def check_log(p: Mmc3Probe, res: dict, what: str, ram_preset=None, k=0):
    """Assert a finished probe's log (the k-th peek of it in res, after the 'until' and done peeks) is the
    spec's, read for read; a NotStored entry must differ from its stored byte."""
    assert res.get("ok"), f"{what}: the core didn't run it: {res}"
    want = p.expected_log(ram_preset)
    took, done = res["out"][0], res["out"][1]
    assert took > 0 and done[0] == 0xAA, f"{what}: the probe never finished its script (it got stuck): {done}"
    n = (done[1] | (done[2] << 8)) - LOG
    assert n == len(want), f"{what}: the probe logged {n} reads, not {len(want)}"
    got = res["out"][2][:n]
    bad = []
    for i, (g, w) in enumerate(zip(got, want)):
        ok = g != w.stored if isinstance(w, NotStored) else g == w
        if not ok:
            bad.append(f"read {i} ({p.labels[i]}): got ${g:02X}, the spec says {w if isinstance(w, NotStored) else f'${w:02X}'}")
    assert not bad, f"{what}: {len(bad)} of {len(want)} reads differ; " + "; ".join(bad[:5])


def ascii_font(chr_bytes: bytes, base: int) -> dict:
    """mask -> character for a font laid out as tile = ASCII code (neslib's vram_write text), chars 32-95,
    in the 4 KiB pattern table at base."""
    return font_tiles(chr_bytes, base + 0x200, [chr(32 + i) for i in range(64)])


def chr_of(rom: bytes) -> bytes:
    prg = rom[4] * 0x4000
    return rom[16 + prg:]


def line_text_matches(pixels: list, y: int, col: int, text: str, chr_bytes: bytes, base=0, mask=0x3F, shift=0) -> bool:
    """Does line y show `text` from nametable cell `col` on, in the ASCII font (tile = code) at
    chr_bytes[base:], with the line scrolled by X = shift under horizontal mirroring (nametable x shows at
    screen x - shift, mod 256)? A pixel is lit when its value & mask (0x3F: the colour index, emphasis
    masked off; use 0xFFFFFF for a canvas's RGB) isn't the line's most common one."""
    line = [v & mask for v in pixels[y * W:(y + 1) * W]]
    bg = Counter(line).most_common(1)[0][0]
    j = y % 8
    for k, c in enumerate(text):
        t = chr_bytes[base + ord(c) * 16:base + ord(c) * 16 + 16]
        bits = t[j] | t[j + 8]
        for x in range(8):
            on = line[((col + k) * 8 + x - shift) % W] != bg
            if on != bool((bits >> (7 - x)) & 1):
                return False
    return True


def unrotate(pixels: list, s: int) -> list:
    """A frame whose every line is shifted back by an X scroll of s under horizontal mirroring (the two
    left/right nametables are one page, so a scrolled line is the unscrolled one rotated by s)."""
    out = []
    for y in range(H):
        row = pixels[y * W:(y + 1) * W]
        out += [row[(x - s) % W] for x in range(W)]
    return out


# ---------------------------------------------------------------- probes the cases share

PICTURE_PAL = [0x0F, 0x16, 0x2A, 0x30, 0x0F, 0x01, 0x11, 0x21, 0x0F, 0x06, 0x17, 0x28, 0x0F, 0x09, 0x1A, 0x3C,
               0x0F, 0x24, 0x24, 0x24] + [0x0F, 0x24, 0x24, 0x24] * 3


def setup_picture(p: Mmc3Probe, mirror=0):
    """The palette, and the two VRAM pages filled with different nametables copied from PRG bank 3
    (through $A000, R7), under arrangement `mirror` ($A000)."""
    for i, c in enumerate(PICTURE_PAL):
        p.ppu_write(0x3F00 + i, c)
    p.write(0xA000, 0)
    for page in (0, 1):
        for k in range(4):
            p.ppu_copy(0x2000 + page * 0x400 + k * 0x100, 0xA000 + page * 0x400 + k * 0x100, 256)
    p.write(0xA000, mirror)


def picture_probe(r0=8, r1=11, chr_inv=0, mirror=0, nt=1, seed=1) -> Mmc3Probe:
    """An MMC3 game's picture, put up the way a TxROM game does: 128 KiB PRG, 128 KiB CHR-ROM; the
    background's 4 KiB at $0000 from R0 and R1 (2 KiB banks; with chr_inv, from R2-R5's 1 KiB banks
    instead); the two VRAM pages with different nametables; $2000 showing nametable `nt`. Background only."""
    p = Mmc3Probe(128, chr_kib=128, seed=seed)
    if chr_inv:
        p.banks(prg_mode=0, chr_inv=1, r=(32, 34, r0, r0 + 1, r1, r1 + 1, 0, 3))
    else:
        p.banks(prg_mode=0, chr_inv=0, r=(r0, r1, 40, 41, 42, 43, 0, 3))
    setup_picture(p, mirror)
    p.sprite0(0xFF, 0, 0, 0)
    p.end(ctrl=nt, mask=0x0A)
    return p


def split_probe(irq: IrqSplit, prg_kib=128, chr_kib=128, ctrl=0x08, mask=0x0A, sprite=None, seed=2) -> Mmc3Probe:
    """An MMC3 status bar: the probe's IRQ split (`IrqSplit`) over a picture whose background is at $0000
    (R0 = 8, R1 = 11 in CHR mode 0) and sprites at $1000 (R2-R5), vertical arrangement, so an X scroll
    moves into the other nametable. Sprite 0 is parked off screen unless `sprite` (y, tile, attr, x) is
    given; the other 63 always are. The IRQ is set up as blargg's tests do (off, clocked twice) before
    rendering starts."""
    p = Mmc3Probe(prg_kib, chr_kib=chr_kib, seed=seed)
    p.banks(prg_mode=0, chr_inv=0, r=(8, 11, 4, 5, 6, 7, 0, 3))
    setup_picture(p, 0)
    p.sprite0(*(sprite or (0xFF, 0, 0, 0)))
    p.irq_setup()
    if irq.chr:
        p.write(0x8000, irq.chr[0])
    p.end(ctrl=ctrl, mask=mask, irq=irq)
    return p


SAVE = 0x6000
SAVE_BYTES = [0x91, 0xA2, 0xB3, 0xC4, 0xD5, 0xE6, 0xF7, 0x88] * 4      # none of them $60-$7F


def battery_probe(battery=True) -> Mmc3Probe:
    """A save on an MMC3 board (NES 2.0: 8 KiB of PRG-NVRAM with the battery bit, or 8 KiB of plain
    PRG-RAM without): with $A001 = $80, it logs the 32 bytes at $6000 as it finds them, copies them onto
    nametable row 12 (so the old save shows on screen), then writes SAVE_BYTES there, and puts its
    picture up."""
    p = Mmc3Probe(128, chr_kib=128, prg_ram_kib=0 if battery else 8, prg_nvram_kib=8 if battery else 0,
                  battery=battery, seed=5)
    p.banks(prg_mode=0, chr_inv=0, r=(8, 11, 4, 5, 6, 7, 0, 3))
    p.write(0xA001, 0x80)
    setup_picture(p, 0)
    p.ops += [("R", (SAVE + i,)) for i in range(32)]
    p.labels += [f"CPU ${SAVE + i:04X} (the save as found)" for i in range(32)]
    p.ops.append(("PC", (0x2000 + 12 * 32, SAVE, 32)))
    for i, v in enumerate(SAVE_BYTES):
        p.write(SAVE + i, v)
    p.sprite0(0xFF, 0, 0, 0)
    p.end(ctrl=0x00, mask=0x0A)
    return p


# ---------------------------------------------------------------- nesdoug's 33_MMC3 demo (hello_mmc3.c)

TEXT = {4: "BANK0", 6: "BANK1", 8: "BANK2  HI", 10: "BANK3  G4", 14: "BANK6  AC", 16: "BACK IN FIXED BANK"}
# The lines each band holds for certain: the IRQs come at the end of lines 46, 77, 108 and 139, and the
# handler's scroll write may show from the next line or the one after.
BANDS = {1: range(0, 47), 2: range(50, 78), 3: range(81, 109), 4: range(112, 140)}
EMPHASISED, PLAIN = range(81, H), range(0, 78)


def band_of(y: int) -> int:
    return next(b for b, r in BANDS.items() if y in r)
