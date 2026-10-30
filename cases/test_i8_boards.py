"""I8: MMC3 (mapper 4) through the core API, on probe cartridges built in the case from the NESdev wiki
("MMC3", "NES 2.0 submappers", "Mirroring", "PPU scrolling").

Every PRG bank (8 KiB) of a probe is filled with seeded random bytes, CHR-ROM too, so each bank, and each
offset in it, reads apart. The probe's program sits in the last bank, the one window the MMC3 fixes at
$E000-$FFFF from power-on. It runs a script of CPU writes and reads, PPU reads and writes, $2006 writes
that move PPU A12 by hand, IRQ queries and waits; it logs every read in RAM, and the case compares the
log, read for read, with what `i8_support.Mmc3` (the wiki's MMC3 in miniature) says each read gives. The
IRQ split cases turn rendering on and compare the frames, row by row, with I2's spec renderer at the
rows the wiki's counter gives (241 clocks a frame with the background at $0000 and sprites at $1000).
Where the wiki leaves a fact unknown or revision-dependent (every register and the counter at power-on;
the $C001 reload glitch; PRG-RAM before $A001 is written, or $A001 on an iNES 1.0 file; open bus), the
model refuses to answer, so no probe depends on it. The target revision is the Sharp MMC3B/C (Decided Q2;
NES 2.0 submapper 0)."""
import re

import pytest

from i2_support import drive_ppu
from i6_support import drive_i6, frame_of, loads, run_probe, short
from i8_support import (H, RING, SAVE, SAVE_BYTES, TOTALS, IrqSplit, Mmc3Probe, battery_probe, check_log,
                        frame_hash, header, row_mismatches, rows_of, setup_picture, split_probe)

DELTA = pytest.mark.xfail(strict=True, reason="delta")


def run(p: Mmc3Probe, tmp_path, what: str, frames=60, timeout=180, extra=None):
    res = run_probe(p.write_to(tmp_path, "probe.nes"), frames=frames, extra=extra, timeout=timeout)
    check_log(p, res, what)
    return res


def look(p: Mmc3Probe, why: str, offsets=(0x0000, 0x1234, 0x1FFF)):
    """Reads that show which 8 KiB bank each switchable or fixed CPU window holds ($8000, $A000, $C000),
    and a byte of the last bank's program ($FFFC, the reset vector)."""
    for w in (0x8000, 0xA000, 0xC000):
        for o in offsets:
            p.read(w + o, why)
    p.read(0xFFFC, why + " (the last bank)")


def look_chr(p: Mmc3Probe, why: str):
    """Reads at both ends of each 1 KiB CHR slot."""
    for slot in range(8):
        for o in (0x000, 0x2A7):
            p.ppu_read(slot * 0x400 + o, why)


# ---------------------------------------------------------------- PRG banks

@pytest.mark.parametrize("kib", [32, 128, 512])
def test_r6_and_r7_select_8k_prg_banks_in_both_prg_modes_and_the_last_two_stay_fixed(tmp_path, kib):
    """purpose: bank select ($8000, even) and bank data ($8001, odd) set R6 and R7 as the wiki's PRG table
    says: in PRG mode 0 (bank select bit 6 clear) $8000-$9FFF is R6 and $C000-$DFFF the second-last bank;
    in mode 1, $C000-$DFFF is R6 and $8000-$9FFF the second-last; $A000-$BFFF is always R7 and
    $E000-$FFFF always the last bank. Mode 0, mode 1 and mode 0 again load 16, 24 and 16 values in a
    scrambled order, values with their top two bits set among them ("R6 and R7 will ignore the top two bits"), and numbers past the last bank
    wrap (32 KiB: 4 banks; 512 KiB, the MMC3's largest: all 64). The bank writes land on random ROM bytes
    ("busconflicts=No").
    origin: design"""
    p = Mmc3Probe(kib, chr_kib=8)
    p.banks()
    for mode in (0, 1, 0):
        p.bank_mode = mode << 6
        for k in range(16 if mode == 0 else 24):
            v = (k * 37 + 11 + mode * 5) % 256
            p.select(6, v)
            p.select(7, (v * 3 + 5) % 256)
            look(p, f"mode {mode}, R6 = ${v:02X}", offsets=(0x0000, 0x1A5B))
    p.end()
    path = p.write_to(tmp_path, "probe.nes")
    check_log(p, run_probe(path, frames=60, timeout=240), f"MMC3 {kib} KiB PRG")


# ---------------------------------------------------------------- CHR banks

@pytest.mark.parametrize("kib", [32, 128, 256])
def test_r0_to_r5_select_2k_and_1k_chr_banks_with_and_without_a12_inversion(tmp_path, kib):
    """purpose: CHR-ROM banking as the wiki's CHR table gives it: with bank select bit 7 clear, R0 and R1
    select 2 KiB at PPU $0000 and $0800 ("2 KiB banks can only select even numbered CHR banks. (The lowest
    bit is ignored.)") and R2-R5 1 KiB each at $1000, $1400, $1800, $1C00; with bit 7 set the two halves
    swap (R0/R1 at $1000/$1800, R2-R5 at $0000-$0C00). Ten settings of all six registers in each
    inversion, odd values in R0 and R1 among them, bank numbers past the ROM's last 1 KiB bank wrapping
    (32 KiB to 256 KiB, the MMC3's largest), each 1 KiB slot read at two offsets.
    origin: design"""
    p = Mmc3Probe(32, chr_kib=kib)
    p.banks()
    for inv in (0, 1):
        p.bank_mode = inv << 7
        for k in range(10):
            regs = [(k * 29 + 7 * n + 3 + inv * 50) % 256 for n in range(6)]
            for n, v in enumerate(regs):
                p.select(n, v)
            look_chr(p, f"inversion {inv}, R0-R5 = {regs}")
    p.end()
    run(p, tmp_path, f"MMC3 {kib} KiB CHR-ROM")


@pytest.mark.parametrize("ram_kib", [8, 32])
def test_chr_ram_is_banked_as_chr_rom_is(tmp_path, ram_kib):
    """purpose: on a board with CHR-RAM (no CHR-ROM in the header; 8 KiB, or 32 KiB by NES 2.0 byte 11)
    the MMC3's CHR banks select 1 KiB pieces of the RAM, wrapping past its size, as they do for ROM (TGROM;
    the wiki: "very few games have MMC3 and CHR RAM"): a byte written through each 1 KiB bank in turn
    reads back through R0-R5 in both inversions, and a write lands in the bank selected.
    origin: design"""
    p = Mmc3Probe(32, chr_kib=0, chr_ram_kib=ram_kib)
    p.banks(r=(0, 2, 4, 5, 6, 7, 0, 1))
    banks = ram_kib
    for b in range(banks):
        p.select(2, b)
        for o in (0x000, 0x155, 0x3FF):
            p.ppu_write(0x1000 + o, (b * 7 + o) & 0xFF)
    for inv in (0, 1, 0):
        p.bank_mode = inv << 7
        for k in range(6):
            regs = [(k * 11 + 5 * n + inv) % 64 for n in range(6)]
            for n, v in enumerate(regs):
                p.select(n, v)
            for slot in range(8):
                p.ppu_read(slot * 0x400 + 0x155, f"inversion {inv}, R0-R5 = {regs}")
        p.ppu_write(0x0155, 0xE1 + inv)
        p.ppu_read(0x0155, "a write in a switched bank")
    p.end()
    run(p, tmp_path, f"MMC3 {ram_kib} KiB CHR-RAM")


def test_chr_rom_is_read_and_never_written(tmp_path):
    """purpose: CHR-ROM in the file is ROM: writes through $2007 to every 1 KiB slot, in both inversions,
    leave the bytes the file has, while the nametables take their writes.
    origin: design"""
    p = Mmc3Probe(32, chr_kib=64)
    p.banks(r=(6, 9, 20, 33, 47, 63, 0, 1))
    p.write(0xA000, 0)
    for inv in (0, 1):
        p.bank_mode = inv << 7
        p.select(0, 6)
        for slot in range(8):
            p.ppu_write(slot * 0x400 + 0x123, 0xE7)
        look_chr(p, f"after writes, inversion {inv}")
    p.ppu_write(0x2000, 0x99)
    p.ppu_read(0x2000, "nametable")
    p.end()
    run(p, tmp_path, "MMC3 CHR-ROM")


# ---------------------------------------------------------------- the registers

def test_every_register_answers_its_whole_range_and_bank_select_bits_3_to_5_do_nothing(tmp_path):
    """purpose: "4 pairs of registers at $8000-$9FFF, $A000-$BFFF, $C000-$DFFF, and $E000-$FFFF - even
    addresses select the low register and odd addresses the high": bank select and data written at
    scattered even and odd addresses across $8000-$9FFF ($9FFE, $8A3C / $9FFF, $8001 + $1234...) act as at
    $8000/$8001, and $A000's arrangement at $BFFE and $A55A; and on a NES 2.0 MMC3 (submapper 0) bank
    select bits 3-5 ("Nothing on the MMC3") change nothing.
    origin: design"""
    p = Mmc3Probe(128, chr_kib=64)
    p.banks()
    pairs = [(0x9FFE, 0x9FFF), (0x8A3C, 0x9235), (0x8002, 0x8FFF), (0x9C00, 0x8C01)]
    for k, (sel, data) in enumerate(pairs):
        extra = [0x00, 0x08, 0x10, 0x38][k]
        p.write(sel, 0x06 | extra)
        p.write(data, (k * 5 + 3) % 16)
        p.write(sel ^ 0x0200, 0x07 | extra)
        p.write(data ^ 0x0200, (k * 7 + 9) % 16)
        p.write(sel, 0x02 | extra)
        p.write(data, (k * 13 + 1) % 64)
        look(p, f"registers written at ${sel:04X}/${data:04X}")
        p.ppu_read(0x1000, "R2")
    for k, a in enumerate((0xBFFE, 0xA55A, 0xA000, 0xB2B0)):
        p.write(a, k & 1)
        for t in range(4):
            p.ppu_write(0x2000 + t * 0x400 + k, 0x40 + 0x10 * t + k)
        for t in range(4):
            p.ppu_read(0x2000 + t * 0x400 + k, f"arrangement {k & 1} written at ${a:04X}")
    p.end()
    run(p, tmp_path, "MMC3 register ranges")


def test_writes_below_8000_never_reach_the_mmc3(tmp_path):
    """purpose: the MMC3's registers answer only $8000-$FFFF: writes of register-like values to $4020,
    $5000, $5FFF never select a bank or change it, and those to $6000-$7FFF go to the PRG-RAM (read back).
    origin: design"""
    p = Mmc3Probe(128, chr_kib=32)
    p.banks()
    p.write(0xA001, 0x80)
    for k, v in enumerate([3, 9, 14, 6]):
        p.write(0x8000, 0x06)
        for a in (0x4020, 0x5000, 0x5FFF, 0x6000, 0x7FFF):
            p.write(a, 0x07)
            p.write(a | 1, (v + 1) % 16)
            p.write(a, v)
        p.write(0x8001, v)
        look(p, f"after writes below $8000, R6 = {v}")
        p.read(0x6000, "PRG-RAM")
        p.read(0x7FFF, "PRG-RAM")
    p.end()
    run(p, tmp_path, "writes below $8000")


# ---------------------------------------------------------------- nametables

@pytest.mark.parametrize("hdr", ["v", "h"])
def test_a000_picks_the_arrangement_whatever_the_header_says(tmp_path, hdr):
    """purpose: $A000 bit 0 picks the nametable arrangement ("0: horizontal (A10); 1: vertical (A11)":
    0 maps $2000/$2800 and $2400/$2C00 together, vertical mirroring; 1 maps $2000/$2400 and $2800/$2C00,
    horizontal mirroring), switchable at any time, whatever header byte 6 bit 0 says: under each, a byte
    written through each nametable reads back through all four, and each VRAM page keeps its bytes while
    the other arrangement is in use.
    origin: design"""
    p = Mmc3Probe(64, chr_kib=8, mirroring=hdr)
    p.banks()
    p.write(0xA000, 0)
    p.ppu_fill(0x2000, 0x11, 0x80)
    p.ppu_fill(0x2400, 0x22, 0x80)
    for k, m in enumerate((0, 1, 1, 0, 1, 0)):
        p.write(0xA000, m | (0xFE if k % 2 else 0))
        for t in range(4):
            p.ppu_write(0x2000 + t * 0x400 + 0x40 + k * 4 + t, 0x80 | (k << 4) | t)
        for t in range(4):
            for o in range(4):
                p.ppu_read(0x2000 + t * 0x400 + 0x40 + k * 4 + o, f"arrangement {m}, nametable {t}")
    p.end()
    run(p, tmp_path, f"MMC3 arrangement, header {hdr}")


def test_a_four_screen_header_gives_four_nametables_and_a000_does_nothing(tmp_path):
    """purpose: "This bit has no effect on cartridges with hardwired 4-screen VRAM" (header byte 6 bit 3):
    a four-screen MMC3 file keeps four separate nametables ($2000, $2400, $2800, $2C00, each its own
    1 KiB), whichever arrangement $A000 is written with.
    origin: design"""
    p = Mmc3Probe(64, chr_kib=8, four_screen=True)
    p.banks()
    for t in range(4):
        p.ppu_fill(0x2000 + t * 0x400, 0x10 * (t + 1), 0x40)
    for k, m in enumerate((0, 1, 0)):
        p.write(0xA000, m)
        for t in range(4):
            p.ppu_write(0x2000 + t * 0x400 + 0x50 + k, 0xA0 | (k << 2) | t)
        for t in range(4):
            for o in (0x00, 0x50 + k):
                p.ppu_read(0x2000 + t * 0x400 + o, f"$A000 = {m}, nametable {t}")
    p.end()
    run(p, tmp_path, "MMC3 four-screen")


# ---------------------------------------------------------------- PRG-RAM

@pytest.mark.parametrize("kind", ["nes2-8k", "nes2-8k-battery", "ines"])
def test_prg_ram_at_6000_holds_what_is_written_once_a001_enables_it(tmp_path, kind):
    """purpose: "CPU $6000-$7FFF: 8 KiB PRG RAM bank": with $A001 = $80 (chip enabled, writes allowed), a
    TxROM board's PRG-RAM (NES 2.0 byte 10: 8 KiB, volatile or battery-backed; or an iNES 1.0 file, which
    has 8 KiB) holds bytes written across all of it, read back after PRG and CHR bank changes, mode
    changes and arrangement changes.
    origin: design"""
    nes2, battery = kind != "ines", kind.endswith("battery")
    p = Mmc3Probe(128, chr_kib=32, prg_ram_kib=0 if battery else 8, prg_nvram_kib=8 if battery else 0,
                  battery=battery, nes2=nes2)
    p.banks()
    p.write(0xA001, 0x80)
    spots = [0x6000, 0x6001, 0x63A5, 0x6FFF, 0x7000, 0x7ABC, 0x7FFE, 0x7FFF]
    for k, a in enumerate(spots):
        p.write(a, (0x5B * (k + 3)) & 0xFF)
    for k in range(4):
        p.bank_mode = ((k & 1) << 6) | ((k >> 1) << 7)
        p.select(6, k * 3 + 1)
        p.select(0, k * 4)
        p.write(0xA000, k & 1)
        for a in spots:
            p.read(a, f"after bank changes {k}")
        p.write(spots[k], 0xC0 | k)
        p.read(spots[k], "rewritten")
    p.end()
    run(p, tmp_path, f"MMC3 PRG-RAM ({kind})")


def test_a001_write_protects_and_disables_the_prg_ram(tmp_path):
    """purpose: $A001, "PRG RAM protect", functional on the MMC3 (NES 2.0, submapper 0): with bits 7-6 =
    11 (enabled, write-protected) reads give the stored bytes and writes are ignored; with bit 7 = 0
    ("PRG RAM chip enable ... 0: disable") reads aren't the stored bytes ("return open bus") and writes
    are ignored too; back at 10, the bytes are those written before the protection. Values stored are
    outside $60-$7F, so open bus can't pass for them.
    origin: design"""
    p = Mmc3Probe(64, chr_kib=8)
    p.banks()
    p.write(0xA001, 0x80)
    spots = [0x6000, 0x6789, 0x7FFF]
    for k, a in enumerate(spots):
        p.write(a, 0xA5 + k * 0x11)
    for ctl in (0xC0, 0x00, 0x40, 0x80, 0xC0, 0x80):
        p.write(0xB001 if ctl & 0x40 else 0xA001, ctl)
        for k, a in enumerate(spots):
            p.read(a, f"$A001 = ${ctl:02X}")
            p.write(a, 0x91 + k + ctl // 0x40)
            p.read(a, f"$A001 = ${ctl:02X}, after a write")
    p.end()
    run(p, tmp_path, "MMC3 $A001")


def test_a_nes2_header_with_no_prg_ram_has_none_at_6000(tmp_path):
    """purpose: the PRG-RAM is the NES 2.0 header's (byte 10 = 0: none; as I7 pins for MMC1, open item
    J1an): on such an MMC3 file, with $A001 = $80, a byte written at $6000, $6ABC or $7FFF doesn't read
    back (the read is open bus, not the byte).
    origin: design"""
    p = Mmc3Probe(64, chr_kib=8, prg_ram_kib=0)
    p.banks()
    p.write(0xA001, 0x80)
    for k, a in enumerate((0x6000, 0x6ABC, 0x7FFF)):
        p.write(a, 0xA5 ^ (k * 0x33))
        p.read(a, "no PRG-RAM")
    p.end()
    run(p, tmp_path, "MMC3 with no PRG-RAM")


def test_prg_ram_survives_reset_and_a_new_console_from_the_file_starts_as_a_fresh_load(tmp_path):
    """purpose: Decided Q3's core half on an MMC3 board: the battery probe logs the 32 bytes it finds at
    $6000, then writes its save. After Reset (the console's button, NES.reset()) it finds its save
    exactly, with the battery bit or without (RAM is kept); a new console made from the same file finds
    what the first one found at power-on, not the save (carrying a battery save across a power cycle is
    the page's Power, pinned in test_i8_consumers).
    origin: design"""
    want = list(SAVE_BYTES)
    for battery in (True, False):
        p = battery_probe(battery)
        path = p.write_to(tmp_path, f"save-{battery}.nes")
        steps = [["until", 0x200, 0xAA, 60], ["peek", 0x300, 32], ["peek", SAVE, 32],
                 ["reset"], ["until", 0x200, 0xAA, 60], ["peek", 0x300, 32],
                 ["power"], ["until", 0x200, 0xAA, 60], ["peek", 0x300, 32]]
        res = drive_i6(path, steps)
        assert res.get("ok"), loads(res, "the MMC3 save probe")
        o = res["out"]
        first, stored, after_reset, fresh = o[1], o[2], o[4], o[6]
        assert first != want, "a vacuous case: a fresh load already holds the save"
        assert stored == want, f"battery={battery}: the save wasn't written to PRG-RAM: {stored}"
        assert after_reset == want, f"battery={battery}: Reset lost the PRG-RAM: {after_reset}"
        assert fresh == first, f"battery={battery}: a new console found {fresh}, not a fresh load's {first}"


# ---------------------------------------------------------------- the IRQ counter, clocked by hand

def irq_probe() -> Mmc3Probe:
    """Rendering off, the IRQ off and the counter clocked twice (so $C001 meets no reload glitch)."""
    p = Mmc3Probe(32, chr_kib=8)
    p.banks()
    p.irq_setup()
    return p


def clocks(p: Mmc3Probe, n: int, why: str, query=True, reenable=True):
    for k in range(n):
        p.clock()
        if query:
            p.query(f"{why}, clock {k + 1}")
            if reenable:
                p.write(0xE001, 0)


@pytest.mark.parametrize("latch", [1, 2, 5, 16, 255])
def test_the_counter_reloads_from_the_latch_at_zero_and_counts_down_to_the_irq(tmp_path, latch):
    """purpose: "When the IRQ is clocked (filtered A12 0->1), the counter value is checked - if zero or the
    reload flag is true, it's reloaded with the IRQ latched value at $C000; otherwise, it decrements. If
    the IRQ counter is zero and IRQs are enabled ($E001), an IRQ is triggered": with rendering off and A12
    moved by $2006 ($1000, then $0000), after $C000 = latch, $C001 and $E001 the first clock reloads, the
    IRQ comes at the (latch + 1)th clock and every latch + 1 clocks after ("N+1, where N is the value
    written to $C000"), queried after every clock (CLI, two NOPs, SEI; the handler writes $E000, the case
    $E001 again). The latch's ends: 1 and 255.
    origin: design"""
    p = irq_probe()
    p.write(0xC000, latch)
    p.write(0xC001, 0)
    p.write(0xE001, 0)
    clocks(p, 2 * (latch + 1) + 2, f"latch {latch}")
    p.end()
    run(p, tmp_path, f"MMC3 counter, latch {latch}")


def test_a_latch_of_0_gives_an_irq_at_every_clock_on_the_mmc3b_and_c(tmp_path):
    """purpose: "Sharp MMC3B and MMC3C generate an IRQ on each scanline while $C000 is $00 ... represented
    by mapper 4 submapper 0" (the revision Decided Q2 targets): with $C000 = 0, every clock reloads 0 and
    asserts the IRQ, whether the counter was 0 before or came to 0 from 1 (latch 1 first, then 0 written
    while it counts), queried after each of 8 clocks; and a reload to 0 requested by $C001 fires too.
    origin: design"""
    p = irq_probe()
    p.write(0xC000, 0)
    p.write(0xC001, 0)
    p.write(0xE001, 0)
    clocks(p, 8, "latch 0")
    p.write(0xC000, 1)
    clocks(p, 3, "latch 1")
    p.write(0xC000, 0)
    clocks(p, 5, "latch 0 again")
    p.write(0xC000, 3)
    clocks(p, 2, "latch 3")
    p.write(0xC000, 0)
    p.write(0xC001, 0)
    clocks(p, 3, "$C001 with latch 0")
    p.end()
    run(p, tmp_path, "MMC3 latch 0")


def test_c000_only_sets_the_latch_and_c001_reloads_at_the_next_clock_without_an_irq(tmp_path):
    """purpose: "Writing to $C000 does not immediately affect the value within the counter - this value is
    only used when the counter is reloaded"; $C001 "clears the MMC3 IRQ counter immediately, and then
    reloads it at the NEXT rising edge" (no decrement on that clock), and "The IRQ flag is not set when
    the counter is cleared by writing to $C001" (blargg): a new latch written mid-count changes nothing
    until the counter reaches 0; $C001 mid-count makes the next clock a reload; $C001 when the counter
    is 1 or 0 asserts nothing by itself. $C000/$C001 are written at even/odd addresses across $C000-$DFFF.
    origin: design"""
    p = irq_probe()
    p.write(0xC000, 4)
    p.write(0xC001, 0)
    p.write(0xE001, 0)
    clocks(p, 2, "latch 4")
    p.write(0xD5AA, 1)
    clocks(p, 6, "latch 1 written at 2")
    p.write(0xC000, 6)
    clocks(p, 2, "latch 6")
    p.write(0xDFFF, 0)
    p.query("right after $C001")
    clocks(p, 8, "after $C001 mid-count")
    p.write(0xC001, 0)
    p.query("right after $C001 at 0")
    clocks(p, 3, "after $C001 at 0")
    p.end()
    run(p, tmp_path, "MMC3 $C000/$C001")


def test_e000_acknowledges_and_disables_and_the_counter_keeps_running(tmp_path):
    """purpose: "Writing to $E000 will only prevent the MMC3 from generating IRQs - the counter will
    continue to run"; it "will disable MMC3 interrupts AND acknowledge any pending interrupts"; "Writing to
    $E001 will simply allow the MMC3 to generate IRQs - the counter remains unaffected", so a zero passed
    while disabled is never delivered later, and one already asserted isn't dropped by $E001 (only $E000
    acknowledges). An asserted IRQ waits while the CPU masks it (SEI), through
    other writes and two frames, and is taken at the next CLI; then it's gone (acknowledged).
    origin: design"""
    p = irq_probe()
    p.write(0xC000, 3)
    p.write(0xC001, 0)
    p.write(0xE001, 0)
    clocks(p, 4, "latch 3", query=False)
    p.write(0x8000, 0x06)
    p.write(0x8001, 1)
    p.wait(2)
    p.query("the IRQ held while masked")
    p.query("after it was taken")
    p.write(0xE001, 0)
    clocks(p, 3, "enabled", query=False)
    p.write(0xFFFE, 0)
    p.query("after $E000 at $FFFE")
    clocks(p, 4, "disabled", query=False)
    p.write(0xE001, 0)
    p.query("$E001 after a zero passed while disabled")
    clocks(p, 4, "enabled again")
    p.write(0xE001, 0)
    clocks(p, 4, "asserted, then $E001 again", query=False)
    p.write(0xE001, 0)
    p.query("an asserted IRQ after $E001 is written again")
    p.write(0xF003, 0)
    clocks(p, 4, "enabled at $F003")
    p.end()
    run(p, tmp_path, "MMC3 $E000/$E001")


def test_only_a_rise_of_ppu_a12_clocks_the_counter(tmp_path):
    """purpose: "The counter is clocked on each rising edge of filtered A12, no matter what caused it, so it
    is possible to ... clock the counter by writing to $2006, regardless of whether PPU is/is not
    rendering" (blargg: "The counter can be clocked manually via bit 12 of the VRAM address even when $2000
    = $00"): with latch 2, an address change that keeps A12 low ($0000, $0FFF, $2FFF, $2000) or high
    ($1000, $1FFF) or takes it from 1 to 0 doesn't clock it; a rise does, whether by $2006, by a $2007 read
    that increments $0FFF to $1000, or by a $2007 write that does. Queried after each.
    origin: design"""
    p = irq_probe()
    p.write(0xC000, 2)
    p.write(0xC001, 0)
    p.write(0xE001, 0)
    seq = [("a6", 0x0FFF), ("a6", 0x2FFF), ("a6", 0x1000), ("a6", 0x1FFF), ("a6", 0x0000), ("a6", 0x2000),
           ("a6", 0x1234), ("a6", 0x0400), ("read", 0x0FFF), ("a6", 0x0800), ("write", 0x0FFF), ("a6", 0x0000),
           ("a6", 0x1C00), ("a6", 0x1000), ("a6", 0x0FFF), ("read", 0x0FFF), ("a6", 0x0000), ("a6", 0x1000),
           ("a6", 0x0000), ("a6", 0x1000)]
    for k, (how, a) in enumerate(seq):
        if how == "a6":
            p.a6(a)
        elif how == "read":
            p.ppu_read(a, "a $2007 read from $0FFF")
        else:
            p.ppu_write(a, 0x5A)
        p.query(f"after {how} ${a:04X}")
        p.write(0xE001, 0)
    p.end()
    run(p, tmp_path, "MMC3 A12 clocking")


def test_with_rendering_off_the_counter_doesnt_move(tmp_path):
    """purpose: the counter counts PPU A12 rises, and with rendering off the PPU makes no fetches: frames
    that pass with $2001 = 0 (waited for by $2002's vblank flag) leave the counter where it was, so the
    IRQ comes after exactly the clocks the latch asks for, however many frames come between them.
    origin: design"""
    p = irq_probe()
    p.write(0xC000, 3)
    p.write(0xC001, 0)
    p.write(0xE001, 0)
    for k in range(9):
        p.wait(1 + k % 3)
        p.query(f"after {1 + k % 3} frames with rendering off")
        p.clock()
        p.query(f"after clock {k + 1}")
        p.write(0xE001, 0)
    p.end()
    run(p, tmp_path, "MMC3 counter with rendering off", frames=120)


# ---------------------------------------------------------------- the IRQ split, rendering on

def split_run(p: Mmc3Probe, tmp_path, frames=3, name="split.nes", timeout=180) -> dict:
    extra = [["frames", 70]]
    for _ in range(frames):
        extra += [["frame"], ["frames", 1]]
    extra += [["peek", RING, 64], ["peek", TOTALS, 6]]
    return run_probe(p.write_to(tmp_path, name), frames=60, extra=extra, timeout=timeout)


def ring(res: dict) -> list:
    return [o for o in res["out"] if isinstance(o, list) and len(o) == 64][-1]


def check_rows(p: Mmc3Probe, res: dict, what: str, frames=3):
    assert res.get("ok"), loads(res, what)
    want = p.irq_frame()
    for k in range(frames):
        bad = row_mismatches(frame_of(res, k), want)
        assert not bad, f"{what}: frame {k}: {len(bad)} lines differ from the spec's, first {bad[:8]}"


@pytest.mark.parametrize("latch", [0, 1, 2, 47, 100, 160, 238])
def test_the_irq_split_starts_on_the_line_the_latch_gives(tmp_path, latch):
    """purpose: J4's status bar on MMC3: with the background at $0000 and sprites at $1000, the counter
    is clocked at dot 260 of the pre-render line and of every visible line; the NMI's $C000 = N, $C001,
    $E001 reloads it on the pre-render line, so the IRQ comes at the end of line N - 1 (latch 0: on the
    pre-render line itself, on the MMC3B/C), and the handler's $2005 write is copied into the scroll at
    dot 257 of the next line: lines 0 to N show the top scroll and line N + 1 down the new one (the
    latch's ends: 0, 1, and 238, whose split is the last line). Three frames compared row for row with
    the spec renderer, and exactly one IRQ in each of the last 64 frames.
    origin: design"""
    p = split_probe(IrqSplit(latch, xa=0, xb=136))
    res = split_run(p, tmp_path)
    check_rows(p, res, f"latch {latch}")
    assert ring(res) == [1] * 64, f"IRQs per frame: {ring(res)}"


@pytest.mark.parametrize("latch,count", [(239, 1), (240, 1), (241, 0), (255, 0)])
def test_latches_past_the_visible_lines_show_no_split_and_241_clocks_bound_the_irq(tmp_path, latch, count):
    """purpose: 241 clocks a frame (the pre-render line and lines 0-239: "241 times per frame") bound what
    a latch can do: latch 239 and 240 still raise one IRQ a frame (at the end of lines 238 and 239) but
    their split falls past the last line, so the whole frame shows the top scroll; latch 241 and 255 never
    reach 0 before the next NMI reloads them, so there's no IRQ at all.
    origin: design"""
    p = split_probe(IrqSplit(latch, xa=32, xb=200))
    res = split_run(p, tmp_path)
    check_rows(p, res, f"latch {latch}")
    assert ring(res) == [count] * 64, f"IRQs per frame: {ring(res)}"


@pytest.mark.parametrize("latch", [0, 1, 2, 63, 64, 120, 240, 255])
def test_reenabled_every_time_the_irq_comes_every_latch_plus_1_lines(tmp_path, latch):
    """purpose: the counter reloads itself at zero: with the handler acknowledging ($E000) and re-enabling
    ($E001) each IRQ, a frame's 241 clocks give an IRQ every latch + 1 of them, floor(241 / (latch + 1))
    a frame: 241 with latch 0 (every line, the MMC3B/C), 120 with 1, 3 with 63 and 64 (Holy Mapperel's
    check), 1 with 240, none with 255. Counted by the handler in each of the last 64 frames.
    origin: design"""
    p = split_probe(IrqSplit(latch, xa=0, xb=0, xc=0, every=True))
    res = split_run(p, tmp_path, frames=1)
    assert res.get("ok"), loads(res, f"latch {latch}")
    want = 241 // (latch + 1)
    assert ring(res) == [want] * 64, f"latch {latch}: IRQs per frame {sorted(set(ring(res)))}, the spec says {want}"


@pytest.mark.parametrize("second", [30, 0])
def test_a_latch_written_in_the_handler_applies_at_the_next_reload(tmp_path, second):
    """purpose: "updating only $C000 to be applied at start of the next IRQ": the handler of the IRQ at the
    end of line 46 (latch 47) writes $C000 = second and $E001; the counter, at 0, reloads it at the next
    clock (line 47), so the second IRQ comes `second` lines later, at the end of line 47 + second, and the
    frame shows three scroll bands: lines 0-47, 48-(48 + second), and below. With second = 0 the reload
    to 0 is itself an IRQ on the MMC3B/C, so the middle band is the single line 48.
    origin: design"""
    p = split_probe(IrqSplit(47, xa=0, xb=64, xc=160, second=second))
    res = split_run(p, tmp_path)
    check_rows(p, res, f"second latch {second}")
    assert ring(res) == [2] * 64, f"IRQs per frame: {ring(res)}"


def test_a_chr_bank_switched_by_the_irq_shows_from_the_second_line_after(tmp_path):
    """purpose: the status bar drawn from other tiles: the handler of the IRQ at the end of line 99 (latch
    100) writes $2005 and then R0 ($8000 = 0, $8001), the background's first 2 KiB; the bank write lands
    in line 100 while it is being fetched, so line 100 isn't pinned, lines 0-99 show the NMI's bank (and
    the top scroll), and lines 101 down the handler's bank (and the new scroll).
    origin: design"""
    p = split_probe(IrqSplit(100, xa=0, xb=80, chr=(0x00, 8, 24)))
    want = p.irq_frame()
    scrolled_only = rows_of(p.expected_frame(80, [(0x8000, 0), (0x8001, 8)]))
    assert want[150] != scrolled_only[150], "a vacuous case: the two banks draw the same"
    res = split_run(p, tmp_path)
    check_rows(p, res, "the CHR split")
    assert ring(res) == [1] * 64


@pytest.mark.parametrize("mask", [0x0A, 0x14, 0x1E], ids=["background", "sprites", "both"])
def test_the_counter_is_clocked_whenever_rendering_is_on(tmp_path, mask):
    """purpose: "whenever rendering is turned on in the PPU, it fetches ... sprite patterns from dots
    256-319, even if no sprites are visible": with the background alone ($2001 = $0A), the sprites alone
    ($14) or both ($1E) on, the split comes on its line (latch 64: lines 0-64 top, 65 down the new scroll)
    and once a frame.
    origin: design"""
    p = split_probe(IrqSplit(64, xa=8, xb=96), mask=mask)
    res = split_run(p, tmp_path)
    check_rows(p, res, f"$2001 = ${mask:02X}")
    assert ring(res) == [1] * 64


def test_a_frame_drawn_with_rendering_off_has_no_irq_and_the_next_splits_on_its_line(tmp_path):
    """purpose: J4v: a game that blanks the screen for a frame (a loading frame): every other frame is drawn
    with $2001 = 0, so its counter isn't clocked and it has no IRQ; the frames between, rendered, split on
    their line (latch 90), each one row for row the spec's, and the blank ones are the backdrop alone.
    origin: design"""
    p = split_probe(IrqSplit(90, xa=0, xb=48, blank=True))
    res = split_run(p, tmp_path, frames=4)
    assert res.get("ok"), loads(res, "the blanking split")
    want = p.irq_frame()
    shown = [k for k in range(4) if not row_mismatches(frame_of(res, k), want)]
    blank = [k for k in range(4) if len(set(frame_of(res, k))) == 1]
    assert sorted(shown + blank) == [0, 1, 2, 3] and len(shown) == 2, f"split frames {shown}, blank frames {blank}"
    assert ring(res) == [1, 0] * 32, f"IRQs per frame: {ring(res)}"


def test_8x16_sprites_from_the_right_table_clock_the_counter_once_a_line(tmp_path):
    """purpose: "When using 8x16 sprites PPU A12 must be explicitly tracked": an 8x16 sprite takes its
    table from its tile's bit 0, and "the PPU fetches tile $FF ($1FE0-$1FFF) for each leftover sprite", so
    with the background at $0000 and every sprite on an odd tile (the right table) A12 still rises once a
    line: a visible 8x16 sprite on line 60 doesn't move the split (latch 120: lines 0-120 top, 121 down).
    origin: design"""
    p = split_probe(IrqSplit(120, xa=0, xb=112), ctrl=0x20, mask=0x1E, sprite=(60, 0x41, 0x00, 100))
    res = split_run(p, tmp_path)
    check_rows(p, res, "8x16 sprites")
    assert ring(res) == [1] * 64


def test_the_irq_split_row_holds_for_600_frames(tmp_path):
    """purpose: J4's "across 600 consecutive frames of scripted play, the status bar's split scanline sits
    on the same row in every frame", on MMC3: frames 40-640 of the latch-150 split all hash to the spec's
    frame (lines 0-150 top scroll, 151 down the new one).
    origin: design"""
    p = split_probe(IrqSplit(150, xa=16, xb=176))
    want = frame_hash([v for row in p.irq_frame() for v in row])
    res = drive_ppu({"mode": "frames", "rom": p.write_to(tmp_path, "split.nes"), "frames": 640, "hashFrom": 40,
                     "frame": False}, timeout=240)
    assert res.get("ok"), loads(res, "the MMC3 split")
    off = [i + 40 for i, h in enumerate(res["hashes"]) if h != want]
    assert len(res["hashes"]) == 601 and not off, f"{len(off)} of 601 frames differ from the spec's, first {off[:8]}"


# ---------------------------------------------------------------- the IRQ against the features it meets

def test_the_apu_frame_irq_and_the_mmc3_irq_share_the_irq_line(tmp_path):
    """purpose: the APU's frame IRQ ($4017 = $00: 4-step, IRQ on) and the MMC3's both pull the CPU's /IRQ
    line; the handler takes the APU's first when $4015 bit 6 says so (reading $4015 acknowledges it) and
    the MMC3's otherwise ($E000). Over 600 frames the split (latch 110) stays on its line in every frame,
    the MMC3 IRQ comes once a frame, and the APU's every 29,830 CPU cycles (NESdev "APU Frame Counter"),
    about 0.998 a frame: neither is lost or doubled.
    origin: design"""
    p = split_probe(IrqSplit(110, xa=0, xb=104, apu=True))
    want = frame_hash([v for row in p.irq_frame() for v in row])
    res = drive_ppu({"mode": "frames", "rom": p.write_to(tmp_path, "split.nes"), "frames": 640, "hashFrom": 40,
                     "frame": False, "peek": [[TOTALS, 6]]}, timeout=240)
    assert res.get("ok"), loads(res, "the MMC3 split with the APU frame IRQ")
    off = [i + 40 for i, h in enumerate(res["hashes"]) if h != want]
    assert not off, f"{len(off)} of 601 frames differ from the spec's, first {off[:8]}"
    t = res["peek"][f"{TOTALS:04X}"]
    mmc3, apu, nmis = t[0] | t[1] << 8, t[2] | t[3] << 8, t[4] | t[5] << 8
    assert mmc3 == nmis - 1, f"{mmc3} MMC3 IRQs in {nmis} frames"
    assert abs(apu - nmis * 29780.5 / 29830) <= 3, f"{apu} APU frame IRQs in {nmis} frames"


def test_an_irq_during_oam_dma_is_taken_after_it_and_never_lost(tmp_path):
    """purpose: the main loop runs OAM DMA back to back ($4014, 513-514 cycles each, the CPU halted), so
    the MMC3's IRQ (latch 100, at the end of line 99) is asserted during a DMA nearly every frame; the CPU
    takes it when the DMA ends, never before line 99 and never lost: in each of 30 frames lines 0-100 show
    the top scroll, lines 107 down the new one (a DMA delays the handler by at most about 4.8 lines), the
    lines between change over once, and each of the last 64 frames has exactly one IRQ.
    origin: design"""
    p = split_probe(IrqSplit(100, xa=0, xb=120, dma=True))
    res = split_run(p, tmp_path, frames=30, timeout=240)
    assert res.get("ok"), loads(res, "the MMC3 split with OAM DMA")
    want = p.irq_frame()
    top = p.expected_frame(0)
    for k in range(30):
        got = rows_of(frame_of(res, k))
        assert all(got[y] == rows_of(top)[y] for y in range(0, 101)), f"frame {k}: the split came early"
        assert all(got[y] == want[y] for y in range(107, H)), f"frame {k}: the split came late, or not at all"
        middle = [got[y] == want[y] for y in range(101, 107)]
        assert all(got[y] == want[y] or got[y] == rows_of(top)[y] for y in range(101, 107)), f"frame {k}: a torn line"
        assert middle == sorted(middle), f"frame {k}: the scroll went back and forth: {middle}"
    assert ring(res) == [1] * 64, f"IRQs per frame: {ring(res)}"


SPLIT_X = 248      # sprite 0's X: the hit lands at dots 249-255, so the main loop's write lands in the hblank
SOLID = 0xFE       # the sprite tile, solid colour 1 in every 1 KiB CHR bank


def test_a_chr_bank_switched_after_sprite_0_hits_shows_from_the_next_line(tmp_path):
    """purpose: J4p/J4q on MMC3: a status bar made with sprite 0 and one bank data write: R0 (the
    background's first 2 KiB, bank select left at 0) is written with the playfield's bank in the NMI and
    with the bar's right after sprite 0 hits (on line y + 1 at x 248-254: the write lands in that line's
    hblank, after its last background fetch); lines 0 to y + 1 show the NMI's bank and y + 2 down the
    bar's, on frames 10, 11 and 12, index for index with the spec renderer's two frames spliced.
    origin: design"""
    p = Mmc3Probe(128, chr_kib=128, seed=3)
    chr_rom = bytearray(p.chr_rom)
    for b in range(128):
        chr_rom[b * 0x400 + 0x3E0:b * 0x400 + 0x3F0] = b"\xff" * 8 + bytes(8)
    p.chr_rom = bytes(chr_rom)
    p.plan = p.model()
    top, bottom = 18, 50
    p.banks(r=(top, 11, 4, 5, 6, 7, 0, 3))
    setup_picture(p, 1)
    p.write(0x8000, 0x00)
    p.end(ctrl=0x08, mask=0x0A)
    plain = p.expected_frame()
    y = next(y for y in range(100, 200) if any(plain[(y + 1) * 256 + x] & 0x3F != 0x0F for x in range(248, 255)))
    p.sprite0(y, SOLID, 0x00, SPLIT_X)
    p.end(ctrl=0x08, mask=0x1E, split=((0x8001, top), (0x8001, bottom)))
    first, second = p.split_frames()
    row = y + 2
    assert first[row * 256:] != second[row * 256:] and first[:row * 256] != second[:row * 256], "a vacuous split"
    want = first[:row * 256] + second[row * 256:]
    res = run_probe(p.write_to(tmp_path, "split.nes"), frames=60,
                    extra=[["frames", 10], ["frame"], ["frames", 1], ["frame"], ["frames", 1], ["frame"]])
    assert res.get("ok"), loads(res, "the MMC3 sprite-0 split")
    for k in range(3):
        got = frame_of(res, k)
        bad = sorted({i // 256 for i, (g, w) in enumerate(zip(got, want)) if g != w})
        assert not bad, f"sprite 0 at Y={y}: frame {k}: {len(bad)} lines differ, first {bad[:8]}"


# ---------------------------------------------------------------- Reset, power, and the loader

def test_reset_and_power_rerun_an_mmc3_game_that_sets_its_own_banks(tmp_path):
    """purpose: "the values in R6, R7, and $8000 are unspecified at power on, the reset vector must point
    into $E000-$FFFF, and code must initialize these": a probe whose reset code does so, run to its end
    (leaving PRG mode 1, CHR inversion, other banks, horizontal arrangement and an enabled IRQ counter
    behind), then Reset, then power-cycled, logs exactly the spec's reads each time, PRG-RAM included.
    origin: design"""
    p = Mmc3Probe(256, chr_kib=128)
    p.banks(r=(4, 9, 20, 21, 22, 23, 5, 6))
    p.write(0xA001, 0x80)
    look(p, "first")
    look_chr(p, "first")
    p.write(0x6000, 0x42)
    p.read(0x6000, "PRG-RAM")
    p.banks(prg_mode=1, chr_inv=1, r=(40, 43, 70, 71, 99, 127, 17, 30))
    p.write(0xA000, 1)
    look(p, "second")
    look_chr(p, "second")
    p.write(0xC000, 5)
    p.write(0xC001, 0)
    p.write(0xE001, 0)
    p.end()
    path = p.write_to(tmp_path, "probe.nes")
    want = p.expected_log()
    steps = [["until", 0x200, 0xAA, 60], ["peek", 0x300, len(want)],
             ["reset"], ["until", 0x200, 0xAA, 60], ["peek", 0x300, len(want)],
             ["power"], ["until", 0x200, 0xAA, 60], ["peek", 0x300, len(want)]]
    res = drive_i6(path, steps)
    assert res.get("ok"), loads(res, "MMC3")
    o = res["out"]
    for i, what in enumerate(["power-on", "Reset", "a power cycle"]):
        assert o[2 * i] > 0, f"after {what} the probe never finished: {short(res)}"
        assert o[2 * i + 1] == want, f"after {what} the log differs from the spec's"


def test_power_on_is_the_same_every_time(tmp_path):
    """purpose: what the MMC3's registers hold at power-on is unspecified and isn't pinned here, but runs
    are deterministic (Decided): a probe that reads the three switchable or swappable windows ($8000,
    $A000, $C000) and CHR before setting any register reads the same bytes on three power-ons, and each
    window's reads are one 8 KiB bank's bytes; $E000-$FFFF is the last bank from the start (the probe
    runs from it).
    origin: design"""
    p = Mmc3Probe(128, chr_kib=32)
    reads = [0x8000, 0x8001, 0x9234, 0xA000, 0xA001, 0xB234, 0xC000, 0xC001, 0xD234]
    p.ops += [("R", (a,)) for a in reads] + [("PR", (a,)) for a in (0x0000, 0x1FFF)]
    p.end()
    path = p.write_to(tmp_path, "probe.nes")
    logs = []
    for _ in range(3):
        res = drive_i6(path, [["until", 0x200, 0xAA, 60], ["peek", 0x300, len(reads) + 2]])
        assert res.get("ok") and res["out"][0] > 0, loads(res, "MMC3")
        logs.append(res["out"][1])
    assert logs[0] == logs[1] == logs[2], f"power-on reads differ between runs: {logs}"
    data = bytes(p.prg)
    for w in range(3):
        rs = reads[w * 3:w * 3 + 3]
        vs = logs[0][w * 3:w * 3 + 3]
        banks = [b for b in range(len(data) // 0x2000) if all(data[b * 0x2000 + (a & 0x1FFF)] == v for a, v in zip(rs, vs))]
        assert banks, f"the power-on reads at ${rs[0]:04X} {vs} aren't any one bank's bytes"


REFUSED = [20, 68, 74, 118, 119, 206, 260, 516]


@pytest.mark.parametrize("number", REFUSED)
def test_a_mapper_still_unsupported_is_refused_by_its_whole_number(tmp_path, number):
    """purpose: adding mapper 4 doesn't make others run as it: a file for a mapper whose low nibble or low
    byte is 4 (20, 68; NES 2.0 260, 516), or for an MMC3 relative on other wiring (74, a CHR-RAM clone;
    118, TxSROM; 119, TQROM; 206, the Namco 108 the MMC3 grew from), is refused with a RomError that names
    its whole number, never run as MMC3.
    origin: design"""
    p = Mmc3Probe(128, chr_kib=32)
    p.end()
    data = header(0x20000, 0x8000, mapper=number, nes2=number > 255) + p.image()[16:]
    path = tmp_path / f"mapper{number}.nes"
    path.write_bytes(data)
    res = drive_i6(path, [])
    assert res.get("ok") is False and res.get("stage") == "load", f"mapper {number} was run: {short(res)}"
    assert res["romError"] is True, res
    assert re.search(rf"\b{number}\b", res["error"]), f"the refusal doesn't name mapper {number}: {res['error']!r}"
