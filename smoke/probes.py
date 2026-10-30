"""The smoke run's own cartridges (cases/CONTRACT-I10.md, "The subjects"): one probe for each mapper with no
vendored game, built here from the spec (NESdev) and written under the run's directory, never committed.

Every probe draws the same screen, from banks only its mapper reaches when it works:
- row 4: as many green cells as its mapper number;
- row 8: controller 1's eight buttons (A, B, Select, Start, Up, Down, Left, Right), each a red outline,
  white while the button is held (it shows two frames after the press);
- row 12: sixteen striped cells, whose tile numbers come from a PRG bank the probe switches in and whose
  pattern comes from a CHR bank it switches in (any other bank holds a checker instead);
and plays a steady pulse tone (about 440 Hz) from power-on, so its sound is there on its title.

Spec: https://www.nesdev.org/wiki/MMC1, https://www.nesdev.org/wiki/INES_Mapper_003,
https://www.nesdev.org/wiki/AxROM, https://www.nesdev.org/wiki/APU_Pulse.
"""
import struct

# ---------------------------------------------------------------- a small 6502 assembler

IMPLIED = {"sei": 0x78, "cld": 0xD8, "txs": 0x9A, "inx": 0xE8, "dex": 0xCA, "iny": 0xC8, "dey": 0x88,
           "tax": 0xAA, "txa": 0x8A, "tay": 0xA8, "tya": 0x98, "rti": 0x40, "rts": 0x60, "pha": 0x48,
           "pla": 0x68, "nop": 0xEA, "clc": 0x18, "sec": 0x38, "lsr": 0x4A, "asl": 0x0A, "rol": 0x2A,
           "ror": 0x6A}
IMMEDIATE = {"lda#": 0xA9, "ldx#": 0xA2, "ldy#": 0xA0, "cmp#": 0xC9, "cpx#": 0xE0, "cpy#": 0xC0,
             "and#": 0x29, "ora#": 0x09, "eor#": 0x49}
ABSOLUTE = {"lda": 0xAD, "ldx": 0xAE, "ldy": 0xAC, "sta": 0x8D, "stx": 0x8E, "sty": 0x8C, "bit": 0x2C,
            "jmp": 0x4C, "jsr": 0x20, "inc": 0xEE, "dec": 0xCE,
            "lda,x": 0xBD, "sta,x": 0x9D, "lda,y": 0xB9, "sta,y": 0x99}
BRANCH = {"bpl": 0x10, "bmi": 0x30, "bne": 0xD0, "beq": 0xF0, "bcc": 0x90, "bcs": 0xB0}


def _size(op: str) -> int:
    if op in IMPLIED:
        return 1
    if op in IMMEDIATE or op in BRANCH:
        return 2
    if op in ABSOLUTE:
        return 3
    raise ValueError(f"unknown instruction {op}")


def assemble(prog: list, org: int) -> tuple:
    """Labels are str ending in ':'; instructions are (op,) or (op, operand), the operand an int or a
    label; ('db', bytes) is data. Returns (code, {label: address})."""
    labels, pc = {}, org
    for item in prog:
        if isinstance(item, str):
            labels[item.rstrip(":")] = pc
        else:
            pc += len(item[1]) if item[0] == "db" else _size(item[0])
    out = bytearray()
    for item in prog:
        if isinstance(item, str):
            continue
        op = item[0]
        if op == "db":
            out += item[1]
            continue
        v = item[1] if len(item) > 1 else None
        v = labels[v] if isinstance(v, str) else v
        if op in IMPLIED:
            out.append(IMPLIED[op])
        elif op in IMMEDIATE:
            out += bytes([IMMEDIATE[op], v & 0xFF])
        elif op in BRANCH:
            d = v - (org + len(out) + 2)
            assert -128 <= d <= 127, f"branch out of range: {item}"
            out += bytes([BRANCH[op], d & 0xFF])
        else:
            out.append(ABSOLUTE[op])
            out += struct.pack("<H", v)
    return bytes(out), labels


# ---------------------------------------------------------------- what every probe draws

PALETTE = bytes([0x0F, 0x16, 0x2A, 0x30] * 8)       # black, red, green, white
ROW_MAPPER, ROW_BUTTONS, ROW_SIGNATURE, ROW_MARKER = 4, 8, 12, 16
BUTTON_COLUMN = 4                                   # buttons at columns 4, 6, ..., 18
T_EMPTY, T_HELD, T_RELEASED, T_GREEN, T_STRIPES, T_CHECKER = 0, 1, 2, 3, 4, 5
BUF = 0x0400                                        # the button row: 16 tiles, a button then a gap
FLAG = 0x0010                                       # set by the NMI: a frame has been shown


def _tile(rows_lo: list, rows_hi: list) -> bytes:
    return bytes(rows_lo) + bytes(rows_hi)


def tiles(good: bool) -> bytes:
    """The probe's six tiles (96 bytes). A good bank has them; any other bank has a checker in every
    tile but the empty one, so a picture from the wrong bank can't match the reference."""
    checker = _tile([0xAA, 0x55] * 4, [0x55, 0xAA] * 4)
    if not good:
        return bytes(16) + checker * 5
    solid3 = _tile([0xFF] * 8, [0xFF] * 8)
    outline1 = _tile([0xFF, 0x81, 0x81, 0x81, 0x81, 0x81, 0x81, 0xFF], [0] * 8)
    solid2 = _tile([0] * 8, [0xFF] * 8)
    stripes = _tile([0xFF, 0, 0xFF, 0, 0xFF, 0, 0xFF, 0], [0xFF, 0xFF, 0, 0, 0xFF, 0xFF, 0, 0])
    return bytes(16) + solid3 + outline1 + solid2 + stripes + checker


def signature(good: bool) -> bytes:
    """The tile numbers of row 12, as a PRG bank holds them: the stripes in the bank the probe chooses,
    the checker in every other bank."""
    return bytes([T_STRIPES if good else T_CHECKER] * 16)


def _set_vram(addr: int) -> list:
    return [("lda#", addr >> 8), ("sta", 0x2006), ("lda#", addr & 0xFF), ("sta", 0x2006)]


def _row(row: int, col: int = 0) -> int:
    return 0x2000 + row * 32 + col


def _boot(mapper_reset: list) -> list:
    """Power-on and Reset: interrupts and rendering off, the APU frame IRQ off, the mapper's own reset,
    and two vblanks for the PPU to warm up."""
    return (["reset:", ("sei",), ("cld",), ("ldx#", 0xFF), ("txs",)] + mapper_reset
            + [("lda#", 0), ("sta", 0x2000), ("sta", 0x2001), ("lda#", 0x40), ("sta", 0x4017),
               ("bit", 0x2002), "vb1:", ("bit", 0x2002), ("bpl", "vb1"), "vb2:", ("bit", 0x2002), ("bpl", "vb2")])


def _program(mapper: int, mapper_reset: list, setup: list, sig_addr: int, after_screen: list = ()) -> list:
    """The probe's whole program: boot, the mapper's setup, the screen, the tone, then a loop reading
    controller 1 into the button row, which the NMI copies to the nametable each frame."""
    prog = _boot(mapper_reset) + setup
    prog += _set_vram(0x3F00) + [("ldx#", 0), "pal:", ("lda,x", "palette"), ("sta", 0x2007), ("inx",),
                                 ("cpx#", 32), ("bne", "pal")]
    prog += _set_vram(0x2000) + [("lda#", 0), ("ldx#", 0), ("ldy#", 4), "clr:", ("sta", 0x2007), ("inx",),
                                 ("bne", "clr"), ("dey",), ("bne", "clr")]
    # row 4: the mapper number, in green cells
    prog += _set_vram(_row(ROW_MAPPER, BUTTON_COLUMN)) + [("lda#", T_GREEN)] + [("sta", 0x2007)] * mapper
    # row 12: the signature, from the switched PRG bank
    prog += _set_vram(_row(ROW_SIGNATURE, BUTTON_COLUMN)) + [
        ("ldx#", 0), "sig:", ("lda,x", sig_addr), ("sta", 0x2007), ("inx",), ("cpx#", 16), ("bne", "sig")]
    # the button row's buffer: every button released
    prog += [("ldx#", 0), "buf:", ("lda#", T_RELEASED), ("sta,x", BUF), ("inx",), ("lda#", T_EMPTY),
             ("sta,x", BUF), ("inx",), ("cpx#", 16), ("bne", "buf")]
    prog += list(after_screen)
    # the tone: pulse 1, duty 50 %, constant volume 12, no sweep, period 253 (1789773 / 16 / 254 = 440 Hz)
    prog += [("lda#", 0x01), ("sta", 0x4015), ("lda#", 0xBC), ("sta", 0x4000), ("lda#", 0x08), ("sta", 0x4001),
             ("lda#", 0xFD), ("sta", 0x4002), ("lda#", 0x00), ("sta", 0x4003)]
    prog += [("lda#", 0), ("sta", FLAG), ("sta", 0x2005), ("sta", 0x2005), ("lda#", 0x80), ("sta", 0x2000)]
    # the loop: once a frame, controller 1's eight buttons into the buffer
    prog += ["main:", ("lda", FLAG), ("beq", "main"), ("lda#", 0), ("sta", FLAG),
             ("lda#", 1), ("sta", 0x4016), ("lda#", 0), ("sta", 0x4016),
             ("ldx#", 0), "rd:", ("lda", 0x4016), ("and#", 1), ("beq", "up"),
             ("lda#", T_HELD), ("bne", "put"), "up:", ("lda#", T_RELEASED), "put:", ("sta,x", BUF),
             ("inx",), ("inx",), ("cpx#", 16), ("bne", "rd"), ("jmp", "main")]
    prog += ["nmi:", ("pha",), ("txa",), ("pha",), ("bit", 0x2002)] + _set_vram(_row(ROW_BUTTONS, BUTTON_COLUMN)) + [
        ("ldx#", 0), "cp:", ("lda,x", BUF), ("sta", 0x2007), ("inx",), ("cpx#", 16), ("bne", "cp"),
        ("lda#", 0), ("sta", 0x2005), ("sta", 0x2005), ("lda#", 0x80), ("sta", 0x2000),
        ("lda#", 0x0A), ("sta", 0x2001), ("lda#", 1), ("sta", FLAG),
        ("pla",), ("tax",), ("pla",), ("rti",),
        "irq:", ("rti",),
        "palette:", ("db", PALETTE)]
    return prog


def ines(prg: bytes, chr_: bytes, mapper: int, flags6_low: int = 0) -> bytes:
    """An iNES 1.0 file: PRG in 16 KiB units, CHR in 8 KiB units (none: CHR-RAM)."""
    assert len(prg) % 0x4000 == 0 and len(chr_) % 0x2000 == 0
    return (b"NES\x1a" + bytes([len(prg) // 0x4000, len(chr_) // 0x2000, ((mapper & 0x0F) << 4) | flags6_low,
                                mapper & 0xF0]) + bytes(8) + prg + chr_)


def _vectors(bank: bytearray, labels: dict):
    bank[-6:] = struct.pack("<HHH", labels["nmi"], labels["reset"], labels["irq"])


# ---------------------------------------------------------------- mapper 1: MMC1

def _mmc1(addr: int, value: int) -> list:
    """One MMC1 register written through its serial port: five writes, bit 0 first."""
    out = [("lda#", value)]
    for i in range(5):
        out.append(("sta", addr))
        if i < 4:
            out.append(("lsr",))
    return out


MMC1_PRG_BANKS, MMC1_PRG_GOOD = 8, 5        # 128 KiB of PRG: bank 5 holds the signature
MMC1_CHR_BANKS, MMC1_CHR_GOOD = 8, 6        # 32 KiB of CHR-ROM in 4 KiB banks: bank 6 holds the tiles


def mmc1_probe() -> bytes:
    """Every 16 KiB bank holds the same program (assembled for $C000) and its own signature at offset
    $3000. A write of $80 resets the MMC1 to PRG mode 3 (the last bank fixed at $C000), so the program
    runs whatever bank power-on shows there. It sets control $1E (vertical mirroring, PRG mode 3, 4 KiB
    CHR banks), CHR bank 0 = 6, CHR bank 1 = 3 and PRG bank 5, then reads the signature at $B000."""
    setup = (_mmc1(0x8000, 0x1E) + _mmc1(0xA000, MMC1_CHR_GOOD) + _mmc1(0xC000, 3)
             + _mmc1(0xE000, MMC1_PRG_GOOD))
    code, labels = assemble(_program(1, [("lda#", 0x80), ("sta", 0x8000)], setup, 0xB000), 0xC000)
    assert len(code) <= 0x3000
    prg = bytearray()
    for b in range(MMC1_PRG_BANKS):
        bank = bytearray(0x4000)
        bank[:len(code)] = code
        bank[0x3000:0x3010] = signature(b == MMC1_PRG_GOOD)
        _vectors(bank, labels)
        prg += bank
    chr_ = b"".join(tiles(b == MMC1_CHR_GOOD).ljust(0x1000, b"\0") for b in range(MMC1_CHR_BANKS))
    return ines(bytes(prg), chr_, 1)


# ---------------------------------------------------------------- mapper 3: CNROM

CNROM_CHR_BANKS, CNROM_CHR_GOOD = 4, 2      # 32 KiB of CHR-ROM in 8 KiB banks: bank 2 holds the tiles


def cnrom_probe() -> bytes:
    """32 KiB of PRG (the program at $8000) and four 8 KiB CHR banks. It chooses CHR bank 2 by writing 2
    to a ROM byte that holds 2 (the board's bus conflict ANDs the two)."""
    setup = [("lda#", CNROM_CHR_GOOD), ("sta", "good_bank")]
    prog = _program(3, [], setup, "sigdata") + ["good_bank:", ("db", bytes([CNROM_CHR_GOOD])),
                                                "sigdata:", ("db", signature(True))]
    code, labels = assemble(prog, 0x8000)
    prg = bytearray(0x8000)
    prg[:len(code)] = code
    _vectors(prg, labels)
    chr_ = b"".join(tiles(b == CNROM_CHR_GOOD).ljust(0x2000, b"\0") for b in range(CNROM_CHR_BANKS))
    return ines(bytes(prg), chr_, 3, flags6_low=0x01)


# ---------------------------------------------------------------- mapper 7: AxROM

AXROM_PRG_BANKS, AXROM_PRG_GOOD = 4, 2      # 128 KiB of PRG in 32 KiB banks: bank 2 holds the signature


def axrom_probe() -> bytes:
    """Every 32 KiB bank holds the same program (assembled for $8000) and its own signature at offset
    $7000, so it runs whatever bank power-on shows. CHR is RAM: it copies its tiles in. It chooses PRG
    bank 2 with the upper one-screen nametable (writes of $12 to a byte that holds $12), draws there,
    then switches to the lower nametable to put a checker row (row 16) there, and switches back: with
    the nametable bit working, that row is never seen."""
    to_b = [("lda#", 0x10 | AXROM_PRG_GOOD), ("sta", "sel_b")]
    to_a = [("lda#", AXROM_PRG_GOOD), ("sta", "sel_a")]
    chr_copy = _set_vram(0x0000) + [("ldx#", 0), "tl:", ("lda,x", "tiles"), ("sta", 0x2007), ("inx",),
                                    ("cpx#", 96), ("bne", "tl")]
    marker = to_a + _set_vram(_row(ROW_MARKER, BUTTON_COLUMN)) + [("lda#", T_CHECKER)] + [("sta", 0x2007)] * 16 + to_b
    prog = _program(7, [], to_b + chr_copy, 0xF000, after_screen=marker) + [
        "sel_a:", ("db", bytes([AXROM_PRG_GOOD])), "sel_b:", ("db", bytes([0x10 | AXROM_PRG_GOOD])),
        "tiles:", ("db", tiles(True))]
    code, labels = assemble(prog, 0x8000)
    assert len(code) <= 0x7000
    prg = bytearray()
    for b in range(AXROM_PRG_BANKS):
        bank = bytearray(0x8000)
        bank[:len(code)] = code
        bank[0x7000:0x7010] = signature(b == AXROM_PRG_GOOD)
        _vectors(bank, labels)
        prg += bank
    return ines(bytes(prg), b"", 7)


# ---------------------------------------------------------------- the probes, by mapper

PROBES = {1: ("probe-mmc1", mmc1_probe), 3: ("probe-cnrom", cnrom_probe), 7: ("probe-axrom", axrom_probe)}

# Controller 1's buttons in the order the probe reads and draws them, with the key that presses each
# (CONTRACT-I3's keyboard map).
BUTTON_KEYS = [("A", "KeyX"), ("B", "KeyZ"), ("Select", "ShiftRight"), ("Start", "Enter"),
               ("Up", "ArrowUp"), ("Down", "ArrowDown"), ("Left", "ArrowLeft"), ("Right", "ArrowRight")]
