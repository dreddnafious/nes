"""I6's shared helpers: the discrete mappers (UxROM 2, CNROM 3, AxROM 7), measured the way their consumers
meet them. Nothing here grades the build; each case asserts its own promise (see CONTRACT-I6.md).

- `i6_oracle`: I6's oracle files, fetched from their pinned URLs (cases/i6_oracles.json) and checked by
  SHA-256, under the same rules as conftest.oracle. Holy Mapperel's ROMs come inside one 7z release
  archive: the archive is pinned by its hash, and each ROM is the pinned slice of one of its LZMA2 blocks
  (decoded with Python's own lzma module), checked by its own hash too.
- `drive_i6`: the core API through the frozen driver cases/drive_i6.ts.
- `Board`: the three boards in miniature, written from the NESdev wiki pages "UxROM", "CNROM", "AxROM",
  "Mirroring" and "NES 2.0 submappers": which bank each CPU and PPU address sees, what a write to
  $8000-$FFFF latches (with the submapper's bus-conflict rule), and the nametable mapping. Where the wiki
  says a state is unknown (every bank register at power-on, CHR-RAM and nametable RAM contents at
  power-on, the bus-conflict rule of submapper 0), the model refuses to answer, so no case can pin it.
- `Probe`: a probe cartridge built in the case. Its program is a tiny interpreter that runs a script of
  CPU writes and reads and PPU writes, reads and copies, logs every read to RAM from $0300 on, sets $0200
  to $AA when it is done, then turns rendering on with the script's $2000/$2001 (optionally running a
  sprite-0 split each frame: one bank write in the NMI, another once sprite 0 has hit). The script is built
  against `Board`, so the expected log and the expected frame come from the spec, never from the build.
  Every PRG bank is filled with seeded random bytes, so each bank (and each offset in it) reads apart;
  the program, an identity table (the byte at $F300 + n is n) and the script sit in the top 4 KiB,
  which is the fixed bank on UxROM and CNROM and is repeated in every 32 KiB bank on AxROM (as AxROM
  games do, since the bank seen at power-on is unknown).
- Readers for Holy Mapperel's result screen (its own font, from its CHR-ROM build) and Cheril the
  Goddess's text (its own font, from the CHR bank its source selects).
"""
import base64
import hashlib
import json
import lzma
import random
import struct
import urllib.request
from pathlib import Path

from conftest import NODE, ROOT, run
from i2_support import Scene, decode_frame, glyph_mask, read_screen, reference_frame

CASES = ROOT / "cases"
CACHE = ROOT / "roms" / "cache"
MANIFEST = json.loads((CASES / "i6_oracles.json").read_text())
ORACLES = {o["id"]: o for o in MANIFEST["oracles"]}
ARCHIVES = {a["id"]: a for a in MANIFEST.get("archives", [])}
W, H = 256, 240
DONE, LOG = 0x0200, 0x0300
OAM_PAGE = 0x0600         # the probe's OAM buffer, sent by $4014 DMA
LOG_MAX = OAM_PAGE - LOG
BLOCK = 0xF000            # the program's 4 KiB: fixed (UxROM, CNROM) or in every bank (AxROM)
TABLE = 0xF300            # the identity table: the byte at TABLE + n is n
SCRIPT = 0xF400           # the script, up to the vectors


# ---------------------------------------------------------------- oracles and the driver

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


def i6_oracle(oid: str) -> Path:
    """The path of an I6 oracle file, fetched on first use and checked by SHA-256. A mismatch is an
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


def drive_i6(rom, steps: list, timeout=180) -> dict:
    """Run the core API through cases/drive_i6.ts. Always returns a dict; a failed run has ok False and
    says why (its stage, error, or the process's exit)."""
    r = run([NODE, str(CASES / "drive_i6.ts"), json.dumps({"rom": str(rom), "steps": steps})], timeout=timeout)
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
    if not res.get("ok"):
        return res
    return {"ok": True, "out": [f"<frame>" if isinstance(o, str) else (o if not isinstance(o, list) or len(o) < 24
                                                                        else f"<{len(o)} bytes>") for o in res["out"]]}


def loads(res: dict, what: str) -> str:
    """The message for a run that didn't get past loading (or crashed): the case's own assertion
    reports it, so "mapper 2 is not supported" fails at the promise, not in the harness."""
    return f"{what}: the core didn't run it: {res}"


# ---------------------------------------------------------------- the iNES / NES 2.0 header

def header(mapper: int, prg: int, chr_rom: int, mirroring="v", nes2=False, submapper=0, chr_ram=0) -> bytes:
    """A 16-byte header (NESdev "INES", "NES 2.0"). prg and chr_rom are sizes in bytes; chr_rom 0 means
    CHR-RAM, whose size NES 2.0 states in byte 11 (chr_ram bytes, 64 << n). mirroring "v" (vertical,
    byte 6 bit 0 set) or "h". No PRG-RAM is declared (NES 2.0 byte 10 = 0; iNES 1.0 can't say)."""
    pu, cu = prg // 0x4000, chr_rom // 0x2000
    f6 = ((mapper & 0x0F) << 4) | (1 if mirroring == "v" else 0)
    if not nes2:
        assert pu <= 255 and cu <= 255 and mapper <= 255
        return b"NES\x1a" + bytes([pu, cu, f6, mapper & 0xF0]) + bytes(8)
    ram = 0
    if chr_ram:
        ram = chr_ram.bit_length() - 7
        assert 64 << ram == chr_ram
    return b"NES\x1a" + bytes([pu & 0xFF, cu & 0xFF, f6, (mapper & 0xF0) | 0x08,
                               (submapper << 4) | (mapper >> 8), ((cu >> 8) << 4) | (pu >> 8),
                               0, ram, 0, 0, 0, 0])


# ---------------------------------------------------------------- the boards, from the wiki

class Unknown(AssertionError):
    """The model was asked for something the spec leaves unknown: a case-construction error."""


class Board:
    """UxROM (2), CNROM (3) or AxROM (7) as the NESdev wiki describes them.
    - UxROM: CPU $8000-$BFFF is the 16 KiB bank the register selects; $C000-$FFFF is fixed to the last
      bank. CHR is 8 KiB, unbanked. Mirroring is fixed by the header.
    - CNROM: CPU $8000-$FFFF is the fixed PRG (16 KiB mirrored, or 32 KiB); PPU $0000-$1FFF is the 8 KiB
      CHR bank the register selects. Mirroring is fixed by the header.
    - AxROM: CPU $8000-$FFFF is the 32 KiB bank in register bits 0-2; bit 4 selects the 1 KiB nametable
      page that all four nametables show (single-screen). CHR is 8 KiB, unbanked.
    - The register is written by any CPU write to $8000-$FFFF. The bank number wraps at the board's bank
      count (the unused high bits drive no ROM address line); counts are powers of two here.
    - Bus conflicts (NES 2.0 submappers): 1 = none (the written value is latched), 2 = AND (the value
      ANDed with the ROM byte at that address). "match" = unknown (submapper 0, iNES 1.0 for UxROM and
      CNROM): only writes whose value equals the ROM byte may be made, since they latch the same either way.
      AxROM submapper 0: "emulators shouldn't emulate bus conflicts" (NESdev "AxROM").
    - Unknown: every register at power-on, CHR-RAM and nametable RAM contents at power-on."""

    def __init__(self, mapper: int, prg: bytes, chr_bytes: bytes, chr_ram: bool, mirroring="v",
                 conflicts="match"):
        self.mapper, self.prg, self.chr_ram, self.mirroring, self.conflicts = mapper, prg, chr_ram, mirroring, conflicts
        self.chr = bytearray(chr_bytes)
        self.chr_known = bytearray([0 if chr_ram else 1]) * len(self.chr)
        self.ciram = bytearray(0x800)
        self.ciram_known = bytearray(0x800)
        self.pal = bytearray(32)
        self.bank = None        # UxROM 16 KiB bank / AxROM 32 KiB bank / CNROM CHR bank
        self.page = None        # AxROM nametable page
        size = {2: 0x4000, 3: 0x2000, 7: 0x8000}[mapper]
        self.count = (len(self.chr) if mapper == 3 else len(prg)) // size
        assert self.count & (self.count - 1) == 0, "bank counts here are powers of two"

    # -- the CPU side
    def prg_offset(self, a: int) -> int:
        """Where in the PRG a CPU read of a ($8000-$FFFF) lands."""
        assert 0x8000 <= a <= 0xFFFF
        if self.mapper == 3:
            return (a - 0x8000) % len(self.prg)
        if self.mapper == 2:
            if a >= 0xC000:
                return len(self.prg) - 0x4000 + a - 0xC000
            if self.bank is None:
                raise Unknown(f"the UxROM bank at ${a:04X} is unknown (power-on)")
            return self.bank * 0x4000 + a - 0x8000
        if self.bank is None:
            if a >= BLOCK:
                return a - 0x8000                    # the program's 4 KiB: the same in every bank
            raise Unknown(f"the AxROM bank at ${a:04X} is unknown (power-on)")
        return self.bank * 0x8000 + a - 0x8000

    def cpu_read(self, a: int) -> int:
        return self.prg[self.prg_offset(a)]

    def latched(self, a: int, v: int) -> int:
        """What a write of v to a ($8000-$FFFF) latches."""
        rom = self.cpu_read(a)
        if v == rom:
            return v
        rule = self.conflicts
        if rule == "match":
            raise Unknown(f"a write of ${v:02X} over ROM byte ${rom:02X} at ${a:04X}: the conflict rule is unknown")
        return v & rom if rule == "and" else v

    def cpu_write(self, a: int, v: int):
        if a < 0x8000:
            return
        v = self.latched(a, v)
        if self.mapper == 7:
            self.bank, self.page = v % self.count, (v >> 4) & 1
        else:
            self.bank = v % self.count

    # -- the PPU side
    def nt_index(self, a: int) -> int:
        t = (a >> 10) & 3
        if self.mapper == 7:
            if self.page is None:
                raise Unknown("the AxROM nametable page is unknown (power-on)")
            p = self.page
        else:
            p = t & 1 if self.mirroring == "v" else t >> 1
        return p * 0x400 + (a & 0x3FF)

    def chr_index(self, a: int) -> int:
        if self.mapper == 3:
            if self.bank is None:
                raise Unknown("the CNROM CHR bank is unknown (power-on)")
            return self.bank * 0x2000 + (a & 0x1FFF)
        return a & 0x1FFF

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
        base = self.chr_index(0)
        return bytes(self.chr[base:base + 0x2000])


# ---------------------------------------------------------------- the probe program

OPS = {
    "sei": (0x78, 0), "cld": (0xD8, 0), "txs": (0x9A, 0), "tax": (0xAA, 0), "pha": (0x48, 0),
    "pla": (0x68, 0), "iny": (0xC8, 0), "rts": (0x60, 0), "rti": (0x40, 0),
    "lda#": (0xA9, 1), "ldx#": (0xA2, 1), "ldy#": (0xA0, 1), "cmp#": (0xC9, 1),
    "sta.z": (0x85, 1), "lda.z": (0xA5, 1), "inc.z": (0xE6, 1), "cpy.z": (0xC4, 1),
    "lda.iy": (0xB1, 1), "sta.iy": (0x91, 1),
    "lda": (0xAD, 2), "sta": (0x8D, 2), "stx": (0x8E, 2), "bit": (0x2C, 2), "jmp": (0x4C, 2), "jsr": (0x20, 2),
    "tay": (0xA8, 0), "tya": (0x98, 0), "sta,x": (0x9D, 2), "inx": (0xE8, 0),
    "bpl": (0x10, "rel"), "bne": (0xD0, "rel"), "beq": (0xF0, "rel"), "bvs": (0x70, "rel"), "bvc": (0x50, "rel"),
}
PTR, ADDR, VAL, LOGP, N, SRC, CTRL, MASK = 0x00, 0x02, 0x04, 0x05, 0x07, 0x08, 0x10, 0x11
SPLIT, WA, VA, WB, VB, FLAG = 0x12, 0x13, 0x15, 0x16, 0x18, 0x19


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
    """The probe's program. The script is a list of ops, each an op byte and its operands:
      0 END ctrl mask mode [alo ahi va blo bhi vb]
                                set $0200 = $AA, then in the next vblank $2005 = 0, 0, $2000 = ctrl,
                                $2001 = mask. Mode 0: spin. Mode 1 (a split, as a status bar is made):
                                every NMI writes va at A (for the top of the next frame), and each frame,
                                once sprite 0 has hit, the main loop writes vb at B. The main loop reads
                                $2002 only between the NMI and the hit, so it can't race the vblank flag.
      1 W   lo hi v             CPU write v to hi:lo (STA (zp),Y with Y = 0)
      2 R   lo hi               CPU read hi:lo, logged
      3 PR  hi lo               PPU read: $2006 = hi, lo; one $2007 read to fill the buffer, the next logged
      4 PW  hi lo v             PPU write v at hi:lo
      5 PC  hi lo slo shi n     PPU copy: n bytes (0 = 256) from CPU shi:slo on, written at PPU hi:lo
      6 PF  hi lo v n           PPU fill: n bytes (0 = 256) of v at PPU hi:lo
      7 OAM y tile attr x       sprite 0 as given and the other 63 at Y = $FF (off screen), sent by
                                $4014 DMA from $0600
    The log starts at $0300; every reset starts it again from the top, with $0200 cleared first."""
    def dispatch(n, label):
        return [("cmp#", n), ("bne", f"not{n}"), ("jmp", label), f"not{n}:"]
    return [
        "reset:", ("sei", None), ("cld", None), ("ldx#", 0xFF), ("txs", None),
        ("lda#", 0), ("sta", 0x2000), ("sta", 0x2001), ("sta", DONE), ("sta.z", SPLIT), ("sta.z", FLAG),
        ("bit", 0x2002), "w1:", ("bit", 0x2002), ("bpl", "w1"), "w2:", ("bit", 0x2002), ("bpl", "w2"),
        ("lda#", SCRIPT & 0xFF), ("sta.z", PTR), ("lda#", SCRIPT >> 8), ("sta.z", PTR + 1),
        ("lda#", LOG & 0xFF), ("sta.z", LOGP), ("lda#", LOG >> 8), ("sta.z", LOGP + 1),
        "next:", ("ldy#", 0), ("lda.iy", PTR),
        ("bne", "op1"), ("jmp", "end"), "op1:",
        *dispatch(1, "op_w"), *dispatch(2, "op_r"), *dispatch(3, "op_pr"), *dispatch(4, "op_pw"),
        *dispatch(5, "op_pc"), *dispatch(6, "op_pf"), *dispatch(7, "op_oam"),
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
        "end:", ("jsr", "getb"), ("sta.z", CTRL), ("jsr", "getb"), ("sta.z", MASK),
        ("jsr", "getb"), ("beq", "e1"),
        ("jsr", "getb"), ("sta.z", WA), ("jsr", "getb"), ("sta.z", WA + 1), ("jsr", "getb"), ("sta.z", VA),
        ("jsr", "getb"), ("sta.z", WB), ("jsr", "getb"), ("sta.z", WB + 1), ("jsr", "getb"), ("sta.z", VB),
        ("lda#", 1), ("sta.z", SPLIT), "e1:",
        ("lda.z", LOGP), ("sta", DONE + 1), ("lda.z", LOGP + 1), ("sta", DONE + 2),
        ("lda#", 0xAA), ("sta", DONE),
        ("bit", 0x2002), "v3:", ("bit", 0x2002), ("bpl", "v3"),
        ("lda#", 0), ("sta", 0x2005), ("sta", 0x2005), ("lda.z", CTRL), ("sta", 0x2000),
        ("lda.z", MASK), ("sta", 0x2001),
        ("lda.z", SPLIT), ("bne", "sw"),
        "spin:", ("jmp", "spin"),
        "sw:", ("lda.z", FLAG), ("beq", "sw"), ("lda#", 0), ("sta.z", FLAG),
        "h0:", ("bit", 0x2002), ("bvs", "h0"),
        "h1:", ("bit", 0x2002), ("bvc", "h1"),
        ("ldy#", 0), ("lda.z", VB), ("sta.iy", WB), ("jmp", "sw"),
        "nmi:", ("pha", None), ("lda.z", SPLIT), ("beq", "n1"), ("tya", None), ("pha", None),
        ("ldy#", 0), ("lda.z", VA), ("sta.iy", WA), ("lda#", 1), ("sta.z", FLAG),
        ("pla", None), ("tay", None), "n1:", ("pla", None), ("rti", None),
        "irq:", ("rti", None),
    ]


CODE, LABELS = assemble(_interpreter(), BLOCK)
assert BLOCK + len(CODE) <= TABLE, f"the probe's program is {len(CODE)} bytes"


class Probe:
    """A probe cartridge for mapper 2, 3 or 7, and the script its program runs, built against the spec
    model. Ops are added in order; each CPU write is checked against the model as it's added (so a case
    can't write over a byte whose conflict rule is unknown), and `expected_log` replays the whole script
    on a fresh model once the image is final."""

    def __init__(self, mapper: int, prg_kib: int, chr_kib=0, mirroring="v", nes2=False, submapper=0,
                 seed=1, chr_seed=None):
        self.mapper, self.mirroring, self.nes2, self.submapper = mapper, mirroring, nes2, submapper
        rng = random.Random(f"i6-prg-{seed}")
        prg = bytearray(rng.randbytes(prg_kib * 1024))
        self.prg = prg
        self.chr_rom = b""
        if chr_kib:
            crng = random.Random(f"i6-chr-{chr_seed if chr_seed is not None else seed}")
            self.chr_rom = crng.randbytes(chr_kib * 1024)
        if mapper in (2, 3):
            blocks = [len(prg) - 0x1000]                     # CNROM-16 KiB: seen at $B000 and $F000
        else:
            blocks = [b * 0x8000 + 0x7000 for b in range(len(prg) // 0x8000)]
        self.blocks = blocks
        for b in blocks:                                     # the table is known from the start
            prg[b + (TABLE - BLOCK):b + (TABLE - BLOCK) + 256] = bytes(range(256))
            prg[b:b + (TABLE - BLOCK)] = bytes(TABLE - BLOCK)
            prg[b + (SCRIPT - BLOCK):b + 0x1000] = bytes(0x1000 - (SCRIPT - BLOCK))
        self.ops = []                                        # (kind, args)
        self.labels = []                                     # what each logged read is, for messages
        self.plan = self.model()
        self.ctrl = self.mask = None
        self.split = None
        self.oam = None

    # -- the cartridge
    def conflicts(self) -> str:
        if self.nes2 and self.submapper == 1:
            return "none"
        if self.nes2 and self.submapper == 2:
            return "and"
        return "none" if self.mapper == 7 else "match"

    def model(self) -> Board:
        return Board(self.mapper, bytes(self.prg), self.chr_rom or bytes(0x2000), not self.chr_rom,
                     self.mirroring, self.conflicts())

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
        hdr = header(self.mapper, len(prg), len(self.chr_rom), self.mirroring, self.nes2, self.submapper,
                     chr_ram=0x2000 if (self.nes2 and not self.chr_rom) else 0)
        return hdr + bytes(prg) + self.chr_rom

    def write_to(self, tmp_path, name: str) -> str:
        p = tmp_path / name
        p.write_bytes(self.image())
        return str(p)

    # -- the script, checked against the model as it is built
    def in_program(self, a: int) -> bool:
        """Is a (as mapped now) one of the program's bytes (code or script), whose values aren't final
        while the script is being built? The identity table is known from the start."""
        off = self.plan.prg_offset(a)
        return any(b <= off < b + 0x1000 and not (TABLE - BLOCK <= off - b < TABLE - BLOCK + 256)
                   for b in self.blocks)

    def write(self, a: int, v: int):
        """A CPU write of v at a, exactly. Above $8000 its effect must be known: v equal to the ROM byte
        there, or a submapper that states the conflict rule."""
        if a >= 0x8000:
            assert not self.in_program(a), f"${a:04X} is in the program's code or script"
        self.plan.cpu_write(a, v)
        self.ops.append(("W", (a, v)))

    def addr_with(self, v: int, lo: int, hi: int, salt=0) -> int:
        """An address in [lo, hi) outside the program's 4 KiB (or in its identity table) whose byte, as
        mapped now, is v: a write of v there has no bus conflict, whatever the board does about them."""
        if lo <= TABLE + v < hi:
            cands = [TABLE + v]
        else:
            cands = []
        span = hi - lo
        start = random.Random(f"{lo}-{v}-{salt}").randrange(span)
        for k in range(span):
            a = lo + (start + k) % span
            try:
                if not self.in_program(a) and self.plan.cpu_read(a) == v:
                    return a
            except Unknown:
                break
        if cands:
            return cands[0]
        raise AssertionError(f"no byte ${v:02X} in ${lo:04X}-${hi - 1:04X}")

    def select(self, v: int, lo=0x8000, hi=0x10000, salt=0) -> int:
        """Latch v with a conflict-free write somewhere in [lo, hi). Returns the address used."""
        a = self.addr_with(v, lo, hi, salt)
        self.write(a, v)
        return a

    def read(self, a: int, why=""):
        self.ops.append(("R", (a,)))
        self.labels.append(f"CPU ${a:04X} {why}".strip())

    def ppu_read(self, a: int, why=""):
        self.ops.append(("PR", (a,)))
        self.labels.append(f"PPU ${a:04X} {why}".strip())

    def addr_where(self, pred, lo: int, hi: int, salt=0) -> int:
        """An address in [lo, hi), outside the program's bytes, whose ROM byte as mapped now satisfies
        pred(byte)."""
        span = hi - lo
        start = random.Random(f"w{lo}-{salt}").randrange(span)
        for k in range(span):
            a = lo + (start + k) % span
            if not self.in_program(a) and pred(self.plan.cpu_read(a)):
                return a
        raise AssertionError(f"no byte for the predicate in ${lo:04X}-${hi - 1:04X}")

    def ppu_write(self, a: int, v: int):
        self.plan.ppu_write(a, v)
        self.ops.append(("PW", (a, v)))

    def ppu_copy(self, a: int, src: int, n: int):
        """n (1-256) bytes from CPU src on to PPU a on."""
        assert src >= 0x8000 and not any(self.in_program(src + i) for i in range(n)), "copy from ROM data only"
        for i in range(n):
            self.plan.ppu_write(a + i, self.plan.cpu_read(src + i))
        self.ops.append(("PC", (a, src, n)))

    def ppu_fill(self, a: int, v: int, n: int):
        for i in range(n):
            self.plan.ppu_write(a + i, v)
        self.ops.append(("PF", (a, v, n)))

    def sprite0(self, y: int, tile: int, attr: int, x: int):
        """Sprite 0 at (x, OAM y), the other 63 sprites off screen (Y = $FF)."""
        self.oam = bytes([y, tile, attr, x]) + b"\xff" * 252
        self.ops.append(("OAM", (y, tile, attr, x)))

    def end(self, ctrl=0x00, mask=0x00, split=None):
        """The script's end: rendering on with $2000 = ctrl and $2001 = mask. With split = ((A, va),
        (B, vb)), every frame starts with va written at A (in the NMI) and gets vb written at B once
        sprite 0 has hit (NMI on: ctrl bit 7 is set). Both writes must be conflict-free."""
        self.ctrl, self.mask = ctrl | (0x80 if split else 0), mask
        if split:
            m = self.model()
            for a, v in split:
                assert m.cpu_read(a) == v if a >= BLOCK else True, "split writes go in the identity table"
            self.split = split

    # -- what the spec says the run gives
    def replay(self) -> tuple:
        """(the log, the final model) from running the finished script on a fresh model."""
        self.image()
        m = self.model()
        log = []
        for kind, args in self.ops:
            if kind == "W":
                m.cpu_write(*args)
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
        assert len(log) <= LOG_MAX, f"the log would be {len(log)} bytes"
        return log, m

    def expected_log(self) -> list:
        return self.replay()[0]

    def expected_frame(self) -> list:
        """The frame the 2C02 draws once the script has turned rendering on (background only, scroll 0),
        through I2's spec renderer: the four nametables as this board maps them, the palette, and the
        8 KiB of CHR the board shows."""
        _, m = self.replay()
        tables = bytearray()
        for t in range(4):
            for off in range(0x400):
                i = m.nt_index(0x2000 + t * 0x400 + off)
                if not m.ciram_known[i]:
                    raise Unknown(f"nametable ${0x2000 + t * 0x400 + off:04X} was never written")
                tables.append(m.ciram[i])
        chr8 = m.chr_view()
        pal = bytearray(m.pal)
        for i in (0x10, 0x14, 0x18, 0x1C):             # the mirrors, so writing all 32 changes nothing
            pal[i] = pal[i - 0x10]
        scene = Scene(chr=chr8, uploads=[(0x2000, bytes(tables)), (0x3F00, bytes(pal))],
                      oam=self.oam, ctrl=self.ctrl & 0x7F, mask=self.mask, mirroring="four")
        return reference_frame(scene)

    def split_frames(self) -> tuple:
        """The whole frame as the spec draws it with the top write (va at A) in effect, and with the
        bottom write (vb at B) in effect: the split frame is the first's rows above the split and the
        second's below."""
        (wa, va), (wb, vb) = self.split
        frames = []
        for a, v in ((wa, va), (wb, vb)):
            self.ops.append(("W", (a, v)))
            try:
                frames.append(self.expected_frame())
            finally:
                self.ops.pop()
        return tuple(frames)


def run_probe(path, frames=30, extra=None, timeout=180) -> dict:
    """Run a probe until it reports done ($0200 = $AA, within `frames`), then read the log; `extra`
    steps follow. Returns the driver result."""
    steps = [["until", DONE, 0xAA, frames], ["peek", DONE, 3], ["peek", LOG, LOG_MAX]] + (extra or [])
    return drive_i6(path, steps, timeout=timeout)


def probe_log(res: dict, n: int) -> list:
    """The first n log bytes from a run_probe result, after checking the probe finished."""
    assert res.get("ok"), loads(res, "the probe")
    took, done = res["out"][0], res["out"][1]
    assert took > 0 and done[0] == 0xAA, f"the probe never finished its script (it got stuck): {done}"
    got_n = done[1] | (done[2] << 8)
    assert got_n - LOG == n, f"the probe logged {got_n - LOG} reads, not {n}"
    return res["out"][2][:n]


def first_mismatch(got: list, want: list, labels=None) -> str:
    """The first few logged reads that differ from the spec's, with what each read was."""
    bad = []
    for i, (g, w) in enumerate(zip(got, want)):
        if g != w:
            what = labels[i] if labels else f"read {i}"
            bad.append(f"read {i} ({what}): got ${g:02X}, the spec says ${w:02X}")
            if len(bad) == 4:
                break
    n = sum(1 for g, w in zip(got, want) if g != w)
    return f"{n} of {len(want)} reads differ; " + "; ".join(bad) if bad else "none"


def check_probe(p: "Probe", res: dict, what: str):
    """Assert a finished probe's log is the spec's, read for read."""
    want = p.expected_log()
    got = probe_log(res, len(want)) if res.get("ok") else None
    assert res.get("ok"), loads(res, what)
    assert got == want, f"{what}: {first_mismatch(got, want, p.labels)}"


# ---------------------------------------------------------------- reading a frame

def frame_of(res: dict, k=-1) -> list:
    frames = [o for o in res["out"] if isinstance(o, str)]
    return decode_frame(frames[k])


def font_tiles(chr_bytes: bytes, base: int, chars) -> dict:
    """mask -> character, for tiles at base + 16 * i for the i-th character of chars (a pixel is lit
    when either plane is set). Earlier characters win when two glyphs share a mask."""
    font = {}
    for i, c in enumerate(chars):
        t = chr_bytes[base + i * 16:base + i * 16 + 16]
        font.setdefault(glyph_mask(t[r] | t[r + 8] for r in range(8)), c)
    return font


def screen_text(pixels: list, font: dict) -> list:
    """The frame's text rows read with a font; the backdrop (the most common value) is unlit."""
    from collections import Counter
    bg = Counter(pixels).most_common(1)[0][0]
    return read_screen(pixels, font, lambda v: v != bg)


def mapperel_font() -> dict:
    """Holy Mapperel's font, from its own CHR-ROM build (M3_P32K_C32K_H, CHR $0000-$03FF): tile n is
    character $40 + n for n < 32 (@, A-Z, ...) and character n for n = 32-63 (space, digits, punctuation),
    as its strings are stored (ASCII & $3F). Digits are listed first, so '0' wins over the identical 'O'."""
    rom = i6_oracle("holy-mapperel/M3_P32K_C32K_H").read_bytes()
    chr_rom = rom[16 + rom[4] * 0x4000:]
    order = list(range(32, 64)) + list(range(0, 32))
    font = {}
    for n in order:
        t = chr_rom[n * 16:n * 16 + 16]
        font.setdefault(glyph_mask(t[r] | t[r + 8] for r in range(8)), chr(0x40 + n) if n < 32 else chr(n))
    return font


def goddess_font(bank: int) -> dict:
    """Cheril the Goddess's font in one CHR bank: its pr_str writes character c as tile c - 32
    (neslib's text), from pattern table 0 (bank_bg(0) in game.c)."""
    rom = i6_oracle("cheril-the-goddess").read_bytes()
    chr_rom = rom[16 + rom[4] * 0x4000:]
    return font_tiles(chr_rom, bank * 0x2000, [chr(32 + i) for i in range(64)])


# ---------------------------------------------------------------- scripts the cases share

def look(p: Probe, why: str, offsets=(0x0000, 0x1234, 0x2BCD, 0x3FFF)):
    """Reads that show which bank each window holds: PRG offsets through the CPU (UxROM's $8000-$BFFF
    and its fixed $C000-$EFFF; AxROM's $8000-$EFFF), or CHR through the PPU (CNROM)."""
    if p.mapper == 2:
        for o in offsets:
            p.read(0x8000 + o, why)
        for o in (0x0000, 0x2ABC):
            p.read(0xC000 + o, why + " (fixed)")
    elif p.mapper == 7:
        for o in (0x0000, 0x1234, 0x3FFF, 0x5A5A, 0x6FFF):
            p.read(0x8000 + o, why)
    else:
        for o in (0x0000, 0x0FFF, 0x1000, 0x1ABC, 0x1FFF):
            p.ppu_read(o, why)


def picture_probe(mapper: int, choice: int) -> Probe:
    """A probe that puts a picture up the way a game on that board does, then renders it (background
    only, all 240 lines, scroll 0):
    - UxROM (CHR-RAM, vertical): tiles copied into CHR-RAM from PRG bank `choice`, the nametables from
      other banks, with $2000 showing nametable 1 ($2400).
    - CNROM (32 KiB CHR-ROM, horizontal): CHR bank `choice`; nametables from the PRG; $2000 shows
      nametable 2 ($2800).
    - AxROM (CHR-RAM): both VRAM pages filled with different data, then page `choice` selected; $2000
      shows nametable 2 ($2800), which is that page."""
    if mapper == 3:
        p = Probe(3, 32, chr_kib=32, mirroring="h")
        p.select(choice, TABLE, TABLE + 256)
    else:
        p = Probe(mapper, 128, mirroring="v")
        p.select(choice, TABLE, TABLE + 256)
        for k in range(16):
            p.ppu_copy(k * 0x100, 0x8000 + k * 0x100, 256)
    pal = [0x0F, 0x16, 0x2A, 0x30, 0x0F, 0x01, 0x11, 0x21, 0x0F, 0x06, 0x17, 0x28, 0x0F, 0x09, 0x1A, 0x3C]
    for i, c in enumerate(pal):
        p.ppu_write(0x3F00 + i, c)
    if mapper == 7:
        for page, b in ((0, 1), (1, 2)):
            p.select(0x10 * page | b, TABLE, TABLE + 256)
            for k in range(4):
                p.ppu_copy(0x2000 + k * 0x100, 0x9000 + k * 0x100 + page * 0x400, 256)
        p.select(0x10 * choice | 3, TABLE, TABLE + 256)
        p.end(ctrl=0x02, mask=0x0A)
    elif mapper == 2:
        for t, b in ((0, 1), (1, 3)):
            p.select(b, TABLE, TABLE + 256)
            for k in range(4):
                p.ppu_copy(0x2000 + t * 0x400 + k * 0x100, 0xA000 + k * 0x100, 256)
        p.end(ctrl=0x01, mask=0x0A)
    else:
        for t in range(2):
            for k in range(4):
                p.ppu_copy(0x2000 + t * 0x800 + k * 0x100, 0x8800 + t * 0x1000 + k * 0x100, 256)
        p.end(ctrl=0x02, mask=0x0A)
    return p
