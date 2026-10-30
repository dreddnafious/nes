"""I7's shared helpers: MMC1 (iNES mapper 1), measured the way its consumers meet it. Nothing here grades
the build; each case asserts its own promise (see CONTRACT-I7.md).

- `i7_oracle`: I7's oracle files, fetched from their pinned URLs (cases/i7_oracles.json) and checked by
  SHA-256, under the same rules as conftest.oracle. Holy Mapperel's MMC1 builds are cut from the same
  pinned release archive I6 uses (a raw LZMA2 block, then a pinned slice, each checked by its hash).
- `Mmc1`: the MMC1 in miniature, written from the NESdev wiki page "MMC1" (the serial port, the four
  registers, the consecutive-cycle rule, the SxROM connection variants) and "NES 2.0" (the sizes). Where
  the wiki says a fact is unknown or varies by ASIC revision or board, the model refuses to answer
  (`Unknown`), so no case can pin it:
    - every register and the shift register at power-on ("some tests have found ... power on in the last
      bank ...; other tests have found that this is fragile");
    - PRG-RAM while $E000 bit 4 is set (MMC1A: always enabled; MMC1B: disabled), and on SNROM while the
      CHR bank's bit 4 is set ("not strictly required for compatible emulation");
    - PRG-RAM and CHR-RAM contents never written; PRG-RAM on an iNES 1.0 file past its first 8 KiB (the
      size "has to be assumed");
    - SOROM/SUROM/SXROM's PRG-RAM bank and outer PRG bank in 4 KiB CHR mode when the two CHR registers
      disagree ("must be set to the same values, or ... bankswitched/enabled as the PPU renders");
    - 32 KiB PRG files (SEROM/SHROM, submapper 5, "register $E000 is completely without function").
- `Mmc1Probe`: a probe cartridge built in the case, on the same plan as I6's (`i6_support.Probe`): every
  PRG bank filled with seeded random bytes, CHR-ROM too; a small interpreter in the top 4 KiB of *every*
  16 KiB bank (so whatever the MMC1 maps at $C000-$FFFF, at power-on or after any switch, the program is
  there, as Holy Mapperel and real MMC1 games keep a reset stub in every bank). Its reset code writes $80
  to $8000 (the wiki's "resetMapper"), then runs a script of CPU writes (one write each, so a register load
  is five of them) and reads, read-modify-write instructions on a ROM address, and PPU reads, writes and
  copies; it logs every read at $0300 on and sets $0200 to $AA when done (the same RAM layout as I6's, so
  `i6_support.run_probe` and `check_probe` read it). Optionally it then runs a status-bar split every
  frame: one register loaded in the NMI, and another loaded after sprite 0 hits (its first four writes
  before the hit, the fifth, which commits, right after).
"""
import hashlib
import json
import lzma
import random
import struct
import urllib.request
from pathlib import Path

from conftest import ROOT
from i2_support import Scene, glyph_mask, reference_frame
from i6_support import DONE, LOG, LOG_MAX, OAM_PAGE, Unknown, font_tiles, screen_text  # noqa: F401

CASES = ROOT / "cases"
CACHE = ROOT / "roms" / "cache"
MANIFEST = json.loads((CASES / "i7_oracles.json").read_text())
ORACLES = {o["id"]: o for o in MANIFEST["oracles"]}
ARCHIVES = {a["id"]: a for a in MANIFEST.get("archives", [])}
W, H = 256, 240
BLOCK = 0xF000            # the program's 4 KiB, at the top of every 16 KiB bank
SCRIPT = 0xF400           # the script, up to the vectors
EXEC = 0x0700             # where op 8 builds its one instruction (and an RTS) in RAM
CONTROL, CHR0, CHR1, PRG = 0x8000, 0xA000, 0xC000, 0xE000     # the registers' first addresses
REG_NAMES = {0: "control", 1: "CHR bank 0", 2: "CHR bank 1", 3: "PRG bank"}

# Controller bits (CONTRACT-I4's setButtons).
A, B, SELECT, START, UP, DOWN, LEFT, RIGHT = 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80


# ---------------------------------------------------------------- oracles

def _fetch(url: str, sha256: str, dest: Path, what: str) -> bytes:
    if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() == sha256:
        return dest.read_bytes()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=60) as r:
        data = r.read()
    got = hashlib.sha256(data).hexdigest()
    if got != sha256:
        raise RuntimeError(f"{what}: sha256 {got} != pinned {sha256}")
    dest.write_bytes(data)
    return data


def i7_oracle(oid: str) -> Path:
    """The path of an I7 oracle file, fetched on first use and checked by SHA-256. A mismatch is an
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
    """A NES 2.0 RAM-size nibble: 0 for none, else n with 64 << n == size."""
    if not size:
        return 0
    n = size.bit_length() - 7
    assert 64 << n == size, f"{size} isn't a NES 2.0 RAM size"
    return n


def header(prg: int, chr_rom: int, mapper=1, mirroring="v", nes2=True, submapper=0, prg_ram=0, prg_nvram=0,
           chr_ram=0, battery=False) -> bytes:
    """A 16-byte header (NESdev "INES", "NES 2.0"). Sizes in bytes. chr_rom 0 means CHR-RAM, whose size
    NES 2.0 gives in byte 11 (chr_ram); NES 2.0 byte 10 gives the PRG-RAM (volatile, low nibble) and the
    battery-backed PRG-NVRAM (high nibble). Byte 6: bit 0 vertical mirroring, bit 1 battery."""
    pu, cu = prg // 0x4000, chr_rom // 0x2000
    f6 = ((mapper & 0x0F) << 4) | (1 if mirroring == "v" else 0) | (2 if battery else 0)
    if not nes2:
        assert pu <= 255 and cu <= 255 and mapper <= 255
        return b"NES\x1a" + bytes([pu, cu, f6, mapper & 0xF0]) + bytes(8)
    return b"NES\x1a" + bytes([pu & 0xFF, cu & 0xFF, f6, (mapper & 0xF0) | 0x08,
                               (submapper << 4) | (mapper >> 8), ((cu >> 8) << 4) | (pu >> 8),
                               (_ram_shift(prg_nvram) << 4) | _ram_shift(prg_ram), _ram_shift(chr_ram), 0, 0, 0, 0])


# ---------------------------------------------------------------- the MMC1, from the wiki

class Mmc1:
    """The MMC1 on an SxROM board, as the NESdev wiki's "MMC1" page describes it.
    - The load register ($8000-$FFFF): a write with bit 7 set clears the shift register and ORs the
      control register with $0C (PRG mode 3); CHR mode and mirroring are left alone. Otherwise bit 0 is
      shifted in, low bit first; the fifth write copies the five bits into the register its address's
      bits 14-13 select (control, CHR bank 0, CHR bank 1, PRG bank) and clears the shift register. On a
      write in the cycle right after another write (a read-modify-write's second write) bit 0 is ignored,
      but the bit-7 reset never is.
    - Control: bits 0-1 mirroring (0 one-screen A, 1 one-screen B, 2 vertical, 3 horizontal); bits 2-3
      PRG mode (0/1: 32 KiB at $8000, the bank's low bit ignored; 2: first bank fixed at $8000, $C000
      switched; 3: last bank fixed at $C000, $8000 switched); bit 4 CHR mode (0: 8 KiB, CHR bank 0's
      low bit ignored and CHR bank 1 unused; 1: two 4 KiB banks).
    - PRG bank bits 0-3 select a 16 KiB bank (wrapping at the ROM's bank count); bit 4 is PRG-RAM
      enable on MMC1B and something else on MMC1A (not pinned).
    - SUROM/SXROM (512 KiB PRG): CHR bank bit 4 selects the 256 KiB half, the "fixed" bank included.
      SXROM (32 KiB PRG-RAM): CHR bank bits 3-2 select the 8 KiB PRG-RAM bank (SOROM, 16 KiB: bit 3).
      SNROM (8 KiB CHR-RAM, <= 256 KiB PRG, 8 KiB PRG-RAM): CHR bank bit 4 is a PRG-RAM disable (not
      pinned). In 4 KiB CHR mode both CHR registers must agree on these bits, or the answer is unknown.
    - No bus conflicts ("busconflicts=No")."""

    def __init__(self, prg: bytes, chr_bytes: bytes, chr_ram: bool, prg_ram: int, nes2=True):
        assert len(prg) >= 0x10000 and len(prg) & (len(prg) - 1) == 0, "the probes use 64-512 KiB of PRG"
        self.prg, self.chr_ram, self.nes2 = prg, chr_ram, nes2
        self.chr = bytearray(chr_bytes)
        self.chr_known = bytearray([0 if chr_ram else 1]) * len(self.chr)
        self.ram_size = prg_ram                  # bytes; for iNES 1.0 the pinned part (8 KiB)
        self.ram = bytearray(max(prg_ram, 0x2000))
        self.ram_known = bytearray(len(self.ram))
        self.ciram = bytearray(0x800)
        self.ciram_known = bytearray(0x800)
        self.pal = bytearray(32)
        self.sr = None                           # bits shifted in so far (a list), None = unknown
        self.reg = [None, None, None, None]      # control, CHR bank 0, CHR bank 1, PRG bank
        self.control_known = 0                   # which control bits are known (after a reset write)

    # -- the serial port
    def write(self, a: int, v: int, consecutive=False):
        if a < 0x6000:
            return
        if a < 0x8000:
            if self.ram_size == 0:
                return
            self._ram_check()
            i = self._ram_index(a)
            self.ram[i], self.ram_known[i] = v, 1
            return
        if v & 0x80:
            self.sr = []
            c = self.reg[0] or 0
            self.reg[0] = c | 0x0C
            self.control_known |= 0x0C
            return
        if consecutive:
            return
        if self.sr is None:
            raise Unknown("the shift register's contents at power-on are unknown")
        self.sr.append(v & 1)
        if len(self.sr) == 5:
            value = sum(b << i for i, b in enumerate(self.sr))
            r = (a >> 13) & 3
            self.reg[r] = value
            if r == 0:
                self.control_known = 0x1F
            self.sr = []

    def shift_count(self) -> int:
        if self.sr is None:
            raise Unknown("the shift register's contents at power-on are unknown")
        return len(self.sr)

    def _control(self, bits: int, what: str) -> int:
        if self.control_known & bits != bits:
            raise Unknown(f"the control register's {what} is unknown (power-on)")
        return self.reg[0] & bits

    def _register(self, r: int) -> int:
        if self.reg[r] is None:
            raise Unknown(f"the {REG_NAMES[r]} register is unknown (power-on)")
        return self.reg[r]

    def _upper_bits(self, mask: int, what: str) -> int:
        """SxROM's repurposed CHR-register bits: CHR bank 0's in 8 KiB mode; in 4 KiB mode both
        registers must agree."""
        b0 = self._register(1) & mask
        if self._control(0x10, "CHR mode"):
            if self._register(2) & mask != b0:
                raise Unknown(f"{what}: the two CHR registers disagree in 4 KiB mode (bankswitched as the PPU renders)")
        return b0

    # -- the CPU side
    def prg_offset(self, a: int) -> int:
        assert 0x8000 <= a <= 0xFFFF
        outer = 0
        if len(self.prg) > 0x40000:
            outer = 0x40000 if self._upper_bits(0x10, "the outer PRG bank") else 0
        count = min(16, len(self.prg) // 0x4000)
        mode = self._control(0x0C, "PRG mode") >> 2
        if mode == 3 and a >= 0xC000:
            bank = 15
        elif mode == 2 and a < 0xC000:
            bank = 0
        else:
            reg = self._register(3) & 0x0F
            bank = ((reg & 0x0E) | ((a >> 14) & 1)) if mode < 2 else reg
        return outer + (bank % count) * 0x4000 + (a & 0x3FFF)

    def cpu_read(self, a: int) -> int:
        if a >= 0x8000:
            return self.prg[self.prg_offset(a)]
        assert 0x6000 <= a < 0x8000, "the probes read the cartridge only at $6000-$FFFF"
        if self.ram_size == 0:
            raise Unknown("no PRG-RAM: open bus")
        self._ram_check()
        i = self._ram_index(a)
        if not self.ram_known[i]:
            raise Unknown(f"PRG-RAM ${a:04X} was never written")
        return self.ram[i]

    def _ram_check(self):
        if self._register(3) & 0x10:
            raise Unknown("PRG-RAM with $E000 bit 4 set differs by MMC1 revision")
        snrom = self.chr_ram and len(self.chr) == 0x2000 and len(self.prg) <= 0x40000 and self.ram_size == 0x2000
        if snrom and self._upper_bits(0x10, "SNROM's PRG-RAM disable"):
            raise Unknown("SNROM's CHR bit 4 PRG-RAM disable isn't pinned")

    def _ram_index(self, a: int) -> int:
        bank = 0
        if self.ram_size == 0x8000:
            bank = self._upper_bits(0x0C, "the PRG-RAM bank") >> 2
        elif self.ram_size == 0x4000:
            bank = self._upper_bits(0x08, "the PRG-RAM bank") >> 3
        elif not self.nes2 and self._upper_bits(0x0C, "the PRG-RAM bank"):
            raise Unknown("an iNES 1.0 file's PRG-RAM past its first 8 KiB (its size has to be assumed)")
        return bank * 0x2000 + (a & 0x1FFF)

    # -- the PPU side
    def nt_index(self, a: int) -> int:
        m = self._control(0x03, "mirroring")
        t = (a >> 10) & 3
        page = {0: 0, 1: 1, 2: t & 1, 3: t >> 1}[m]
        return page * 0x400 + (a & 0x3FF)

    def chr_index(self, a: int) -> int:
        a &= 0x1FFF
        if self._control(0x10, "CHR mode"):
            bank = self._register(1 if a < 0x1000 else 2)
            off = bank * 0x1000 + (a & 0x0FFF)
        else:
            off = (self._register(1) & 0x1E) * 0x1000 + a
        return off % len(self.chr)

    def ppu_read(self, a: int) -> int:
        a &= 0x3FFF
        if a < 0x2000:
            i = self.chr_index(a)
            if not self.chr_known[i]:
                raise Unknown(f"CHR-RAM ${a:04X} was never written")
            return self.chr[i]
        assert a < 0x3F00, "palette reads aren't buffered; the probe doesn't make them"
        i = self.nt_index(a)
        if not self.ciram_known[i]:
            raise Unknown(f"nametable RAM ${a:04X} was never written")
        return self.ciram[i]

    def ppu_write(self, a: int, v: int):
        a &= 0x3FFF
        if a < 0x2000:
            if self.chr_ram:
                i = self.chr_index(a)
                self.chr[i], self.chr_known[i] = v, 1
        elif a < 0x3F00:
            i = self.nt_index(a)
            self.ciram[i], self.ciram_known[i] = v, 1
        else:
            i = a & 0x1F
            if i & 0x13 == 0x10:
                i &= 0x0F
            self.pal[i] = v & 0x3F

    def chr_view(self) -> bytes:
        """The 8 KiB the PPU sees at $0000-$1FFF now."""
        out = bytearray()
        for a in range(0x2000):
            i = self.chr_index(a)
            if not self.chr_known[i]:
                raise Unknown(f"CHR ${a:04X} was never written")
            out.append(self.chr[i])
        return bytes(out)


# ---------------------------------------------------------------- the probe program

OPS = {
    "sei": (0x78, 0), "cld": (0xD8, 0), "txs": (0x9A, 0), "tax": (0xAA, 0), "pha": (0x48, 0),
    "pla": (0x68, 0), "iny": (0xC8, 0), "rts": (0x60, 0), "rti": (0x40, 0), "tay": (0xA8, 0),
    "tya": (0x98, 0), "inx": (0xE8, 0), "lsr": (0x4A, 0), "sec": (0x38, 0), "clc": (0x18, 0),
    "lda#": (0xA9, 1), "ldx#": (0xA2, 1), "ldy#": (0xA0, 1), "cmp#": (0xC9, 1),
    "sta.z": (0x85, 1), "lda.z": (0xA5, 1), "inc.z": (0xE6, 1), "cpy.z": (0xC4, 1),
    "lda.iy": (0xB1, 1), "sta.iy": (0x91, 1),
    "lda": (0xAD, 2), "sta": (0x8D, 2), "stx": (0x8E, 2), "bit": (0x2C, 2), "jmp": (0x4C, 2), "jsr": (0x20, 2),
    "sta,x": (0x9D, 2),
    "bpl": (0x10, "rel"), "bne": (0xD0, "rel"), "beq": (0xF0, "rel"), "bvs": (0x70, "rel"), "bvc": (0x50, "rel"),
}
PTR, ADDR, VAL, LOGP, N, SRC, CTRL, MASK = 0x00, 0x02, 0x04, 0x05, 0x07, 0x08, 0x10, 0x11
SPLIT, WA, VA, WB, VB, FLAG, VB4 = 0x12, 0x13, 0x15, 0x16, 0x18, 0x19, 0x1A


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


def _interpreter() -> list:
    """The probe's program, I6's interpreter with an MMC1 reset write first and one more op. The script
    is a list of ops, each an op byte and its operands:
      0 END ctrl mask mode [alo ahi va blo bhi vb]
                                set $0200 = $AA, then in the next vblank $2005 = 0, 0, $2000 = ctrl,
                                $2001 = mask. Mode 0: spin. Mode 1 (a status-bar split): every NMI loads
                                va into the register at A (five writes, low bit first); each frame the
                                main loop makes vb's first four writes at B, waits for sprite 0 to hit,
                                then makes the fifth.
      1 W   lo hi v             CPU write v to hi:lo (one STA (zp),Y)
      2 R   lo hi               CPU read hi:lo, logged
      3 PR  hi lo               PPU read: $2006 = hi, lo; one $2007 read to fill the buffer, the next logged
      4 PW  hi lo v             PPU write v at hi:lo
      5 PC  hi lo slo shi n     PPU copy: n bytes (0 = 256) from CPU shi:slo on, written at PPU hi:lo
      6 PF  hi lo v n           PPU fill: n bytes (0 = 256) of v at PPU hi:lo
      7 OAM y tile attr x       sprite 0 as given, the other 63 at Y = $FF, sent by $4014 DMA from $0600
      8 X   opcode lo hi c      run one instruction `opcode lo hi` (absolute, or absolute,X with X = 0)
                                with the carry set if c is 1, from RAM at $0700 (it ends in an RTS)
    The log starts at $0300; every reset starts it again from the top, with $0200 cleared first."""
    def dispatch(n, label):
        return [("cmp#", n), ("bne", f"not{n}"), ("jmp", label), f"not{n}:"]
    five = [("sta.iy", WA), ("lsr", None)] * 4 + [("sta.iy", WA)]
    four = [("sta.iy", WB), ("lsr", None)] * 3 + [("sta.iy", WB)]
    return [
        "reset:", ("sei", None), ("cld", None), ("lda#", 0x80), ("sta", 0x8000),
        ("ldx#", 0xFF), ("txs", None),
        ("lda#", 0), ("sta", 0x2000), ("sta", 0x2001), ("sta", DONE), ("sta.z", SPLIT), ("sta.z", FLAG),
        ("bit", 0x2002), "w1:", ("bit", 0x2002), ("bpl", "w1"), "w2:", ("bit", 0x2002), ("bpl", "w2"),
        ("lda#", SCRIPT & 0xFF), ("sta.z", PTR), ("lda#", SCRIPT >> 8), ("sta.z", PTR + 1),
        ("lda#", LOG & 0xFF), ("sta.z", LOGP), ("lda#", LOG >> 8), ("sta.z", LOGP + 1),
        "next:", ("ldy#", 0), ("lda.iy", PTR),
        ("bne", "op1"), ("jmp", "end"), "op1:",
        *dispatch(1, "op_w"), *dispatch(2, "op_r"), *dispatch(3, "op_pr"), *dispatch(4, "op_pw"),
        *dispatch(5, "op_pc"), *dispatch(6, "op_pf"), *dispatch(7, "op_oam"), *dispatch(8, "op_x"),
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
        "op_x:", ("jsr", "getb"), ("sta", EXEC), ("jsr", "getb"), ("sta", EXEC + 1),
        ("jsr", "getb"), ("sta", EXEC + 2), ("lda#", 0x60), ("sta", EXEC + 3),
        ("jsr", "getb"), ("ldx#", 0), ("clc", None), ("cmp#", 0), ("beq", "x1"), ("sec", None), "x1:",
        ("jsr", EXEC), ("jmp", "adv"),
        "end:", ("jsr", "getb"), ("sta.z", CTRL), ("jsr", "getb"), ("sta.z", MASK),
        ("jsr", "getb"), ("beq", "e1"),
        ("jsr", "getb"), ("sta.z", WA), ("jsr", "getb"), ("sta.z", WA + 1), ("jsr", "getb"), ("sta.z", VA),
        ("jsr", "getb"), ("sta.z", WB), ("jsr", "getb"), ("sta.z", WB + 1), ("jsr", "getb"), ("sta.z", VB),
        ("lsr", None), ("lsr", None), ("lsr", None), ("lsr", None), ("sta.z", VB4),
        ("lda#", 1), ("sta.z", SPLIT), "e1:",
        ("lda.z", LOGP), ("sta", DONE + 1), ("lda.z", LOGP + 1), ("sta", DONE + 2),
        ("lda#", 0xAA), ("sta", DONE),
        ("bit", 0x2002), "v3:", ("bit", 0x2002), ("bpl", "v3"),
        ("lda#", 0), ("sta", 0x2005), ("sta", 0x2005), ("lda.z", CTRL), ("sta", 0x2000),
        ("lda.z", MASK), ("sta", 0x2001),
        ("lda.z", SPLIT), ("bne", "sw"),
        "spin:", ("jmp", "spin"),
        "sw:", ("lda.z", FLAG), ("beq", "sw"), ("lda#", 0), ("sta.z", FLAG),
        ("ldy#", 0), ("lda.z", VB), *four,
        "h0:", ("bit", 0x2002), ("bvs", "h0"),
        "h1:", ("bit", 0x2002), ("bvc", "h1"),
        ("ldy#", 0), ("lda.z", VB4), ("sta.iy", WB), ("jmp", "sw"),
        "nmi:", ("pha", None), ("lda.z", SPLIT), ("beq", "n1"), ("tya", None), ("pha", None),
        ("ldy#", 0), ("lda.z", VA), *five, ("lda#", 1), ("sta.z", FLAG),
        ("pla", None), ("tay", None), "n1:", ("pla", None), ("rti", None),
        "irq:", ("rti", None),
    ]


CODE, LABELS = assemble(_interpreter(), BLOCK)
assert BLOCK + len(CODE) <= SCRIPT, f"the probe's program is {len(CODE)} bytes"

# Read-modify-write instructions op 8 runs: (opcode, what its second write is, from the ROM byte and carry).
RMW = {
    "inc": (0xEE, lambda b, c: (b + 1) & 0xFF),
    "dec": (0xCE, lambda b, c: (b - 1) & 0xFF),
    "ror": (0x6E, lambda b, c: (c << 7) | (b >> 1)),
    "rra,x": (0x7F, lambda b, c: (c << 7) | (b >> 1)),        # unofficial RRA abs,X (X = 0): ROR, then ADC
}


class Mmc1Probe:
    """A probe cartridge for mapper 1, and the script its program runs, built against `Mmc1`. Each CPU
    write is played on the model as it's added (so a case can't depend on what the wiki leaves unknown);
    `expected_log` replays the whole script on a fresh model once the image is final."""

    def __init__(self, prg_kib=128, chr_kib=0, prg_ram_kib=8, prg_nvram_kib=0, battery=False, nes2=True,
                 mirroring="v", seed=1, chr_seed=None):
        self.nes2, self.mirroring, self.battery = nes2, mirroring, battery
        rng = random.Random(f"i7-prg-{seed}")
        self.prg = bytearray(rng.randbytes(prg_kib * 1024))
        self.chr_rom = b""
        if chr_kib:
            crng = random.Random(f"i7-chr-{chr_seed if chr_seed is not None else seed}")
            self.chr_rom = crng.randbytes(chr_kib * 1024)
        self.prg_ram = prg_ram_kib * 1024
        self.prg_nvram = prg_nvram_kib * 1024
        self.blocks = [b * 0x4000 + 0x3000 for b in range(len(self.prg) // 0x4000)]
        for b in self.blocks:
            self.prg[b:b + 0x1000] = bytes(0x1000)
        self.ops, self.labels = [], []
        self.ctrl = self.mask = None
        self.split = None
        self.oam = None
        self.plan = self.model()

    # -- the cartridge
    def ram_total(self) -> int:
        if not self.nes2:
            return 0x2000                      # the part an iNES 1.0 file can count on
        return self.prg_ram + self.prg_nvram

    def model(self) -> Mmc1:
        m = Mmc1(bytes(self.prg), self.chr_rom or bytes(0x2000), not self.chr_rom, self.ram_total(), self.nes2)
        m.write(0x8000, 0x80)                  # the program's first write
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
            elif kind == "X":
                name, a, c = args
                out += bytes([8, RMW[name][0], a & 0xFF, a >> 8, c])
        out += bytes([0, self.ctrl or 0, self.mask or 0])
        if self.split:
            (wa, va), (wb, vb) = self.split
            out += bytes([1, wa & 0xFF, wa >> 8, va, wb & 0xFF, wb >> 8, vb])
        else:
            out += bytes([0])
        return bytes(out)

    def image(self) -> bytes:
        """The finished .nes file."""
        script = self.script_bytes()
        assert SCRIPT + len(script) <= 0xFFFA, f"the script is {len(script)} bytes"
        prg = self.prg
        for b in self.blocks:
            prg[b:b + len(CODE)] = CODE
            prg[b + (SCRIPT - BLOCK):b + (SCRIPT - BLOCK) + len(script)] = script
            prg[b + 0xFFA:b + 0x1000] = struct.pack("<HHH", LABELS["nmi"], LABELS["reset"], LABELS["irq"])
        hdr = header(len(prg), len(self.chr_rom), 1, self.mirroring, self.nes2, prg_ram=self.prg_ram,
                     prg_nvram=self.prg_nvram, chr_ram=0x2000 if (self.nes2 and not self.chr_rom) else 0,
                     battery=self.battery)
        return hdr + bytes(prg) + self.chr_rom

    def write_to(self, tmp_path, name: str) -> str:
        p = tmp_path / name
        p.write_bytes(self.image())
        return str(p)

    # -- the script, played on the model as it is built
    def in_program(self, a: int) -> bool:
        """Is CPU address a (as mapped now) inside a bank's program block (whose bytes aren't final
        while the script is being built)?"""
        off = self.plan.prg_offset(a)
        return any(b <= off < b + 0x1000 for b in self.blocks)

    def write(self, a: int, v: int):
        """One CPU write of v at a."""
        self.plan.write(a, v)
        self.ops.append(("W", (a, v)))

    def load(self, reg: int, value: int, addrs=None):
        """Load a register the way the wiki's examples do: five writes, value's bit 0 first, the fifth at
        an address in the register's range (reg is $8000, $A000, $C000 or $E000). addrs, if given, are
        the five addresses (only the fifth's bits 14-13 should matter)."""
        addrs = addrs or [reg] * 5
        assert len(addrs) == 5 and (addrs[4] >> 13) & 3 == (reg >> 13) & 3
        for i, a in enumerate(addrs):
            self.write(a, (value >> i) & 1)

    def reset_write(self, a=0x8000, v=0x80):
        self.write(a, v)

    def rmw(self, name: str, a: int, carry=0, why=""):
        """Run one read-modify-write instruction on ROM address a: it reads the byte there, writes it
        back unchanged, then writes the modified value on the very next cycle."""
        assert a >= 0x8000 and not self.in_program(a), "RMW on the probe's random ROM bytes only"
        b = self.plan.cpu_read(a)
        self.plan.write(a, b)
        self.plan.write(a, RMW[name][1](b, carry), consecutive=True)
        self.ops.append(("X", (name, a, carry)))

    def addr_where(self, pred, lo: int, hi: int, salt=0) -> int:
        """An address in [lo, hi), outside the program's blocks, whose ROM byte as mapped now satisfies
        pred(byte)."""
        span = hi - lo
        start = random.Random(f"i7w{lo}-{salt}").randrange(span)
        for k in range(span):
            a = lo + (start + k) % span
            if not self.in_program(a) and pred(self.plan.cpu_read(a)):
                return a
        raise AssertionError(f"no byte for the predicate in ${lo:04X}-${hi - 1:04X}")

    def read(self, a: int, why=""):
        self.ops.append(("R", (a,)))
        self.labels.append(f"CPU ${a:04X} {why}".strip())

    def ppu_read(self, a: int, why=""):
        self.ops.append(("PR", (a,)))
        self.labels.append(f"PPU ${a:04X} {why}".strip())

    def ppu_write(self, a: int, v: int):
        self.plan.ppu_write(a, v)
        self.ops.append(("PW", (a, v)))

    def ppu_copy(self, a: int, src: int, n: int):
        """n (1-256) bytes from CPU src on (ROM, outside the program's blocks) to PPU a on."""
        assert src >= 0x8000 and not any(self.in_program(src + i) for i in range(n)), "copy from ROM data only"
        for i in range(n):
            self.plan.ppu_write(a + i, self.plan.cpu_read(src + i))
        self.ops.append(("PC", (a, src, n)))

    def ppu_fill(self, a: int, v: int, n: int):
        for i in range(n):
            self.plan.ppu_write(a + i, v)
        self.ops.append(("PF", (a, v, n)))

    def sprite0(self, y: int, tile: int, attr: int, x: int):
        self.oam = bytes([y, tile, attr, x]) + b"\xff" * 252
        self.ops.append(("OAM", (y, tile, attr, x)))

    def end(self, ctrl=0x00, mask=0x00, split=None):
        """The script's end: rendering on with $2000 = ctrl and $2001 = mask. With split = ((A, va),
        (B, vb)), every NMI loads va into the register at A, and every frame vb is loaded into the
        register at B, its fifth write right after sprite 0 hits."""
        self.ctrl, self.mask = ctrl | (0x80 if split else 0), mask
        self.split = split

    # -- what the spec says the run gives
    def replay(self) -> tuple:
        """(the log, the final model) from running the finished script on a fresh model."""
        self.image()
        m = self.model()
        log = []
        for kind, args in self.ops:
            if kind == "W":
                m.write(*args)
            elif kind == "R":
                log.append(m.cpu_read(args[0]))
            elif kind == "PR":
                log.append(m.ppu_read(args[0]))
            elif kind == "PW":
                m.ppu_write(*args)
            elif kind == "PC":
                a, src, n = args
                for i in range(n or 256):
                    m.ppu_write(a + i, m.cpu_read(src + i))
            elif kind == "PF":
                a, v, n = args
                for i in range(n or 256):
                    m.ppu_write(a + i, v)
            elif kind == "X":
                name, a, c = args
                b = m.cpu_read(a)
                m.write(a, b)
                m.write(a, RMW[name][1](b, c), consecutive=True)
        assert len(log) <= LOG_MAX, f"the log would be {len(log)} bytes"
        return log, m

    def expected_log(self) -> list:
        return self.replay()[0]

    def expected_frame(self, extra=()) -> list:
        """The frame the 2C02 draws once the script has turned rendering on (scroll 0), through I2's spec
        renderer: the four nametables as the MMC1 maps them, the palette, and the 8 KiB of CHR it shows.
        extra: more CPU writes played on the model first (a split's register loads)."""
        _, m = self.replay()
        for a, v in extra:
            m.write(a, v)
        tables = bytearray()
        for t in range(4):
            for off in range(0x400):
                i = m.nt_index(0x2000 + t * 0x400 + off)
                if not m.ciram_known[i]:
                    raise Unknown(f"nametable ${0x2000 + t * 0x400 + off:04X} was never written")
                tables.append(m.ciram[i])
        pal = bytearray(m.pal)
        for i in (0x10, 0x14, 0x18, 0x1C):
            pal[i] = pal[i - 0x10]
        scene = Scene(chr=m.chr_view(), uploads=[(0x2000, bytes(tables)), (0x3F00, bytes(pal))],
                      oam=self.oam, ctrl=self.ctrl & 0x7F, mask=self.mask, mirroring="four")
        return reference_frame(scene)

    def split_frames(self) -> tuple:
        """The whole frame as the spec draws it with the NMI's register load in effect, and with the
        main loop's: the split frame is the first's rows above the split and the second's below."""
        (wa, va), (wb, vb) = self.split
        return tuple(self.expected_frame([(a, (v >> i) & 1) for i in range(5)]) for a, v in ((wa, va), (wb, vb)))


# ---------------------------------------------------------------- scripts the cases share

def look(p: Mmc1Probe, why: str, windows=(0x8000, 0xC000), offsets=(0x0000, 0x1234, 0x2FFF)):
    """Reads that show which 16 KiB bank each CPU window holds (each bank's first 12 KiB are random)."""
    for w in windows:
        for o in offsets:
            p.read(w + o, why)


def look_chr(p: Mmc1Probe, why: str, offsets=(0x0000, 0x0ABC, 0x0FFF, 0x1000, 0x1ABC, 0x1FFF)):
    for o in offsets:
        p.ppu_read(o, why)


def setup(p: Mmc1Probe, control: int, chr0=0, chr1=0, prg=0):
    """Load all four registers (control first), as games do in their reset code."""
    p.load(CONTROL, control)
    p.load(CHR0, chr0)
    p.load(CHR1, chr1)
    p.load(PRG, prg)


PICTURE_PAL = [0x0F, 0x16, 0x2A, 0x30, 0x0F, 0x01, 0x11, 0x21, 0x0F, 0x06, 0x17, 0x28, 0x0F, 0x09, 0x1A, 0x3C]


def picture_probe(chr_bank=5, screen=1) -> Mmc1Probe:
    """An MMC1 game's picture, put up the way an SKROM/SLROM game does: 128 KiB of PRG, 128 KiB of CHR-ROM
    in 4 KiB mode with the background from CHR bank `chr_bank` (at PPU $0000) and sprites' table from
    another; the two VRAM pages filled with different nametables (from PRG bank 3) under vertical
    mirroring, then one-screen mirroring on page `screen` (control bits 0-1), so all four nametables show
    that page. Renders background only, $2000 showing nametable 2 ($2800)."""
    p = Mmc1Probe(128, chr_kib=128, mirroring="h")
    setup(p, 0x1E, chr0=chr_bank, chr1=chr_bank ^ 0x11, prg=3)
    for i, c in enumerate(PICTURE_PAL):
        p.ppu_write(0x3F00 + i, c)
    for page in (0, 1):
        for k in range(4):
            p.ppu_copy(0x2000 + page * 0x400 + k * 0x100, 0x8000 + page * 0x1000 + k * 0x100, 256)
    p.load(CONTROL, 0x1C | screen)
    p.end(ctrl=0x02, mask=0x0A)
    return p


# ---------------------------------------------------------------- reading the homebrew screens

def ascii_font(chr_bytes: bytes, base: int) -> dict:
    """mask -> character for a font laid out as tile = ASCII code (neslib's vram_write text), chars
    32-95, in the 4 KiB pattern table at base."""
    return font_tiles(chr_bytes, base + 0x200, [chr(32 + i) for i in range(64)])


def tile_reader(chr_bytes: bytes, base: int) -> dict:
    """mask -> tile number (as a 1-char string, chr(n)) for all 256 tiles of the pattern table at base;
    the lowest number wins when two tiles look alike."""
    return font_tiles(chr_bytes, base, [chr(n) for n in range(256)])


def chr_of(rom: bytes) -> bytes:
    """The CHR-ROM of an iNES/NES 2.0 file (no trainer)."""
    prg = (rom[4] | ((rom[9] & 0x0F) << 8 if rom[7] & 0x0C == 0x08 else 0)) * 0x4000
    return rom[16 + prg:]


def cell_mismatches(pixels: list, chr_bytes: bytes, base: int, rows: dict) -> list:
    """Which 8x8 cells don't show the tile the game's nametable puts there. rows: {row: (first column,
    [tile numbers])}; the tiles are read from the 4 KiB pattern table at chr_bytes[base:]. A cell shows a
    tile when its pixels follow the tile's 2-bit pattern: every pixel of one pattern value has one frame
    value, and pattern value 0 is the backdrop (the frame's most common value). Whatever the palette,
    a cell drawn from other tiles (another bank) breaks that."""
    from collections import Counter
    backdrop = Counter(pixels).most_common(1)[0][0]
    bad = []
    for r, (c0, tiles) in rows.items():
        for k, t in enumerate(tiles):
            cx = c0 + k
            tb = chr_bytes[base + t * 16:base + t * 16 + 16]
            seen = {}
            ok = True
            for y in range(8):
                for x in range(8):
                    p = ((tb[y] >> (7 - x)) & 1) | (((tb[y + 8] >> (7 - x)) & 1) << 1)
                    v = pixels[(r * 8 + y) * W + cx * 8 + x]
                    if seen.setdefault(p, v) != v or (p == 0 and v != backdrop):
                        ok = False
            if not ok:
                bad.append((r, cx, f"${t:02X}"))
    return bad
