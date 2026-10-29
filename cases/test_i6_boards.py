"""I6: the discrete mappers (UxROM 2, CNROM 3, AxROM 7) through the core API, on probe cartridges built in
the case from the NESdev wiki ("UxROM", "CNROM", "AxROM", "Mirroring", "NES 2.0 submappers", "INES").

Every PRG bank of a probe is filled with seeded random bytes, so each bank, and each offset in it, reads
apart; CNROM's CHR banks are random too. The probe's program runs a script of bank writes and CPU/PPU
reads and logs every read in RAM; the case compares the log, read for read, with what `i6_support.Board`
(the wiki's boards in miniature) says each read gives. The picture cases compare the frame with I2's
spec renderer. Where the wiki leaves a fact unknown (any register at power-on; bus conflicts on a
submapper-0 file of UxROM or CNROM), the probes never depend on it: their bank writes there carry the
same value as the ROM byte they land on, as games do."""
import re

import pytest

from i2_support import diff_count, first_diffs
from i6_support import (BLOCK, TABLE, Probe, check_probe, drive_i6, frame_of, header, loads, look,
                        picture_probe, run_probe, short)

DELTA = pytest.mark.xfail(strict=True, reason="delta")


def probe(mapper: int, kib: int, **kw) -> Probe:
    if mapper == 3:
        return Probe(3, kw.pop("prg_kib", 32), chr_kib=kib, **kw)
    return Probe(mapper, kib, **kw)


# ---------------------------------------------------------------- bank switching, every bank, each size

SIZES = [(2, 64), (2, 128), (2, 256), (3, 16), (3, 32), (7, 64), (7, 128), (7, 256)]


@pytest.mark.parametrize("mapper,kib", [pytest.param(m, k, id=f"mapper{m}-{k}k") for m, k in SIZES])
def test_every_bank_is_selected_by_its_number_and_the_fixed_bank_stays(tmp_path, mapper, kib):
    """purpose: on each board at each size the wiki gives (UxROM 64/128/256 KiB PRG; CNROM 16/32 KiB CHR;
    AxROM 64/128/256 KiB PRG), writing bank number n to $8000-$FFFF maps bank n: UxROM's 16 KiB at
    $8000-$BFFF with the last bank fixed at $C000-$FFFF; CNROM's 8 KiB CHR at PPU $0000-$1FFF with its PRG
    fixed; AxROM's 32 KiB at $8000-$FFFF. Every bank is visited (in a scrambled order, each twice), and
    each visit reads bytes across the window, which must be that bank's bytes at those offsets.
    origin: design"""
    p = probe(mapper, kib)
    n = p.plan.count
    order = [(k * 5 + 3) % n for k in range(n)] + list(range(n - 1, -1, -1))
    for k, b in enumerate(order):
        p.select(b, TABLE, TABLE + 256) if k == 0 else p.select(b, salt=k)
        look(p, f"after selecting bank {b} of {n}")
    p.end()
    check_probe(p, run_probe(p.write_to(tmp_path, "probe.nes"), frames=60), f"mapper {mapper}, {kib} KiB")


@pytest.mark.parametrize("mapper,kib", [pytest.param(m, k, id=f"mapper{m}-{k}k")
                                        for m, k in [(2, 128), (3, 32), (7, 128)]])
def test_a_write_anywhere_in_8000_ffff_switches_the_bank(tmp_path, mapper, kib):
    """purpose: the bank register answers a write at any address in $8000-$FFFF (NESdev: "Bank select
    ($8000-$FFFF)"): in each 1 KiB of $8000-$EFFF, and in the identity table at $F300-$F3FF, one write
    of a bank number (onto a ROM byte equal to it, so there is no bus conflict) selects that bank, as the
    reads after it show.
    origin: design"""
    p = probe(mapper, kib)
    n = p.plan.count
    p.select(0, TABLE, TABLE + 256)
    for j, lo in enumerate(range(0x8000, BLOCK, 0x400)):
        b = (j * 3 + 1) % n
        a = p.select(b, lo, lo + 0x400, salt=j)
        look(p, f"after a write of {b} at ${a:04X}", offsets=(0x0000, 0x2FFF))
    p.select(n - 1, TABLE, TABLE + 256)
    look(p, "after a write in $F300-$F3FF")
    p.end()
    check_probe(p, run_probe(p.write_to(tmp_path, "probe.nes"), frames=60), f"mapper {mapper}")


@pytest.mark.parametrize("mapper,kib", [pytest.param(m, k, id=f"mapper{m}-{k}k")
                                        for m, k in [(2, 64), (2, 128), (3, 16), (3, 32), (7, 64), (7, 128)]])
def test_bank_numbers_past_the_last_bank_wrap(tmp_path, mapper, kib):
    """purpose: a bank number at or past the board's bank count selects that number modulo the count
    (the register's high bits drive no ROM address line: UNROM uses bits 2-0, CNROM bits 1-0, AxROM bits
    2-0, and a smaller ROM leaves the top ones unconnected): every value from the count to 255 in steps
    of 7, and 255, selects bank value % count.
    origin: design"""
    p = probe(mapper, kib)
    n = p.plan.count
    for k, v in enumerate(list(range(n, 256, 7)) + [255]):
        p.select(v, salt=k) if k else p.select(v, TABLE, TABLE + 256)
        look(p, f"after writing {v} (bank {v % n} of {n})", offsets=(0x0000, 0x3FFF))
    p.end()
    check_probe(p, run_probe(p.write_to(tmp_path, "probe.nes"), frames=60), f"mapper {mapper}, {kib} KiB")


def test_a_4_mib_uxrom_reaches_all_256_banks(tmp_path):
    """purpose: the largest UxROM the wiki allows ("256K/4096K"; "to make use of all 8 bits for a 4 MB
    PRG ROM, an NES 2.0 header must be used") loads from an NES 2.0 header (PRG size 256 x 16 KiB: byte 4
    = 0, byte 9 = 1) and its 8-bit bank register reaches every one of its 256 banks, the first, the
    middle and the last included, with the last bank fixed at $C000.
    origin: design"""
    p = Probe(2, 4096, nes2=True)
    p.select(0, TABLE, TABLE + 256)
    for k, b in enumerate([0, 1, 2, 15, 16, 127, 128, 129, 200, 254, 255, 77, 0]):
        p.select(b, salt=k)
        look(p, f"after selecting bank {b} of 256", offsets=(0x0000, 0x1FFF, 0x3FFF))
    p.end()
    path = p.write_to(tmp_path, "uxrom-4m.nes")
    assert (tmp_path / "uxrom-4m.nes").stat().st_size == 16 + 4 * 1024 * 1024
    check_probe(p, run_probe(path, frames=60, timeout=300), "UxROM 4 MiB")


# ---------------------------------------------------------------- what a write doesn't do

@pytest.mark.parametrize("mapper", [pytest.param(2), 3, pytest.param(7)])
def test_writes_below_8000_never_switch_a_bank(tmp_path, mapper):
    """purpose: the register is enabled only by /ROMSEL ($8000-$FFFF): writes to $4020, $5000, $5FFF,
    $6000 and $7FFF, of values that are other bank numbers, leave the selected bank (and AxROM's
    nametable page) as they were.
    origin: design"""
    p = probe(mapper, {2: 128, 3: 32, 7: 128}[mapper])
    p.select(1, TABLE, TABLE + 256)
    if mapper == 7:
        p.ppu_write(0x2000, 0x5A)
    for a in (0x4020, 0x5000, 0x5FFF, 0x6000, 0x7FFF):
        p.write(a, 0x12)
        look(p, f"after a write of $12 at ${a:04X}", offsets=(0x0000,))
    if mapper == 7:
        p.ppu_read(0x2400, "the nametable page after them")
    p.end()
    check_probe(p, run_probe(p.write_to(tmp_path, "probe.nes"), frames=60), f"mapper {mapper}")


# ---------------------------------------------------------------- bus conflicts, as the submapper says

def conflict_probe(mapper: int, submapper: int) -> Probe:
    """An NES 2.0 probe that writes bank numbers onto ROM bytes that differ from them, where the AND of
    the two is another bank: so what's latched shows the rule."""
    p = probe(mapper, {2: 128, 3: 32, 7: 128}[mapper], nes2=True, submapper=submapper)
    n = p.plan.count
    p.select(0, TABLE, TABLE + 256)
    region = {2: (0xC000, BLOCK), 3: (0x8000, BLOCK), 7: (0x8000, BLOCK)}[mapper]
    for k, v in enumerate([n - 1, 0x0F, 0x1B, 0x2D, 0x37, 0xFF, 0x05, 0x06]):
        a = p.addr_where(lambda r, v=v: (v & r) % n != v % n, *region, salt=k)
        p.write(a, v)
        look(p, f"after writing ${v:02X} over ${p.plan.cpu_read(a) if mapper != 7 else 0:02X} at ${a:04X}",
             offsets=(0x0000, 0x2222))
    p.end()
    return p


@pytest.mark.parametrize("mapper,submapper", [
    pytest.param(m, s, id=f"mapper{m}-submapper{s}")
    for m in (2, 3, 7) for s in (1, 2)])
def test_bus_conflicts_follow_the_nes2_submapper(tmp_path, mapper, submapper):
    """purpose: NES 2.0 submappers of 2, 3 and 7 say how a write over a different ROM byte lands
    (NESdev "UxROM", "CNROM", "AxROM": submapper 1 "No bus conflicts", submapper 2 "AND-type bus
    conflicts"): with submapper 1 the written value is latched; with submapper 2 the written value ANDed
    with the ROM byte at that address is. Eight such writes, each onto a byte whose AND gives another
    bank.
    origin: design"""
    p = conflict_probe(mapper, submapper)
    check_probe(p, run_probe(p.write_to(tmp_path, "probe.nes"), frames=60),
                f"mapper {mapper} submapper {submapper}")


def test_axrom_on_an_ines_1_header_has_no_bus_conflicts(tmp_path):
    """purpose: a plain iNES mapper-7 file (no submapper) latches the value written even over a different
    ROM byte, as the wiki recommends ("Bus conflict behavior undefined, but emulators shouldn't emulate
    bus conflicts"; "every retail AOROM game could be emulated correctly without emulating bus
    conflicts").
    origin: design"""
    p = Probe(7, 128)
    n = p.plan.count
    p.select(0, TABLE, TABLE + 256)
    for k, v in enumerate([3, 0x11, 0x1E, 0x02, 0x13]):
        a = p.addr_where(lambda r, v=v: (v & r) % n != v % n or (v & r) & 0x10 != v & 0x10, 0x8000, BLOCK, salt=k)
        p.write(a, v)
        look(p, f"after writing ${v:02X} at ${a:04X}")
        p.ppu_write(0x2000 + k, 0xC0 | k)
        p.ppu_read(0x2C00 + k, "(the page bit 4 chose)")
    p.end()
    check_probe(p, run_probe(p.write_to(tmp_path, "probe.nes"), frames=60), "mapper 7, iNES 1.0")


# ---------------------------------------------------------------- nametables

@pytest.mark.parametrize("mapper,mirroring", [
    pytest.param(m, mi, id=f"mapper{m}-{mi}")
    for m in (2, 3) for mi in ("v", "h")])
def test_uxrom_and_cnrom_mirroring_is_the_headers_and_bank_writes_dont_change_it(tmp_path, mapper, mirroring):
    """purpose: UxROM and CNROM mirroring is fixed ("Fixed H or V, controlled by solder pads"): the
    header's byte 6 bit 0 picks vertical ($2000 = $2800, $2400 = $2C00) or horizontal ($2000 = $2400,
    $2800 = $2C00), and bank writes whose values have bit 4 set (AxROM's page bit) change nothing.
    Checked by writing each of the four nametables through $2007 and reading all four back.
    origin: design"""
    p = probe(mapper, {2: 128, 3: 32}[mapper], mirroring=mirroring)
    p.select(0x10, TABLE, TABLE + 256)
    for t in range(4):
        p.ppu_write(0x2000 + t * 0x400 + 0x123, 0x40 + t)
    for k, v in enumerate([0x11, 0x13, 0x1F, 0x10]):
        p.select(v, salt=k)
        for t in range(4):
            p.ppu_read(0x2000 + t * 0x400 + 0x123, f"nametable {t} after writing ${v:02X}")
    p.end()
    check_probe(p, run_probe(p.write_to(tmp_path, "probe.nes"), frames=60), f"mapper {mapper} {mirroring}")


@pytest.mark.parametrize("mirroring", [pytest.param("v", id="header-vertical"), pytest.param("h", id="header-horizontal")])
def test_axrom_bit_4_picks_one_page_for_all_four_nametables(tmp_path, mirroring):
    """purpose: AxROM's register bit 4 selects the 1 KiB VRAM page "for all 4 nametables" (single-screen,
    NESdev "AxROM"), whatever the header's mirroring bit says: with bit 4 clear, $2000, $2400, $2800 and
    $2C00 are one page; with it set, they are the other; each page keeps its bytes while the other is
    selected; and changing the PRG bank with the same page bit keeps the page.
    origin: design"""
    p = Probe(7, 128, mirroring=mirroring)
    for page in (0, 1):
        p.select(0x10 * page, TABLE, TABLE + 256)
        for t in range(4):
            p.ppu_write(0x2000 + t * 0x400 + 0x10 + t, 0xA0 + 0x20 * page + t)
            p.ppu_write(0x2000 + t * 0x400 + 0x20 + t, 0xB0 + 0x20 * page + t)
    for k, v in enumerate([0x00, 0x10, 0x02, 0x13, 0x01]):
        p.select(v, TABLE, TABLE + 256)
        for t in range(4):
            for o in range(4):
                p.ppu_read(0x2000 + t * 0x400 + 0x10 + o, f"page bit {v >> 4 & 1}, nametable {t}")
                p.ppu_read(0x2000 + t * 0x400 + 0x20 + o, f"page bit {v >> 4 & 1}, nametable {t}")
    p.end()
    check_probe(p, run_probe(p.write_to(tmp_path, "probe.nes"), frames=60), f"mapper 7, header {mirroring}")


# ---------------------------------------------------------------- CHR: RAM on UxROM/AxROM, ROM where the file has it

@pytest.mark.parametrize("mapper", [pytest.param(2), pytest.param(7)])
def test_chr_ram_holds_what_is_written_through_every_prg_switch(tmp_path, mapper):
    """purpose: a UxROM or AxROM file with no CHR-ROM (header byte 5 = 0) has 8 KiB of CHR-RAM at PPU
    $0000-$1FFF: bytes written through $2007 at both ends of both pattern tables, and a 256-byte run
    copied from a switched PRG bank (as games load their tiles), read back the same after every PRG
    bank switch.
    origin: design"""
    p = Probe(mapper, 128)
    p.select(2, TABLE, TABLE + 256)
    p.ppu_copy(0x0800, 0x8100, 256)
    spots = [0x0000, 0x0001, 0x0FFF, 0x1000, 0x17A5, 0x1FFE, 0x1FFF]
    for k, a in enumerate(spots):
        p.ppu_write(a, (0x31 * (k + 1)) & 0xFF)
    for k, b in enumerate([0, 3, 1, 2]):
        p.select(b, salt=k)
        for a in spots + [0x0800, 0x0855, 0x08FF]:
            p.ppu_read(a, f"after PRG bank {b}")
    p.end()
    check_probe(p, run_probe(p.write_to(tmp_path, "probe.nes"), frames=60), f"mapper {mapper} CHR-RAM")


@pytest.mark.parametrize("mapper", [pytest.param(2, marks=DELTA), pytest.param(7, marks=DELTA)])
def test_chr_rom_in_the_file_is_read_and_never_written(tmp_path, mapper):
    """purpose: a UxROM or AxROM file that carries 8 KiB of CHR-ROM (header byte 5 = 1) shows it at PPU
    $0000-$1FFF, and writes through $2007 don't change it (it's ROM), while the nametables still take
    their writes.
    origin: design"""
    p = Probe(mapper, 64, chr_kib=8)
    p.select(1, TABLE, TABLE + 256)
    spots = [0x0000, 0x0123, 0x0FFF, 0x1000, 0x1FFF]
    for a in spots:
        p.ppu_read(a, "CHR-ROM before writes")
    for a in spots:
        p.ppu_write(a, 0xE7)
    p.ppu_write(0x2000, 0x99)
    for a in spots:
        p.ppu_read(a, "CHR-ROM after writes")
    p.ppu_read(0x2000, "nametable")
    p.end()
    check_probe(p, run_probe(p.write_to(tmp_path, "probe.nes"), frames=60), f"mapper {mapper} CHR-ROM")


# ---------------------------------------------------------------- the picture

PICTURES = [(2, 0), (2, 5), (3, 0), (3, 1), (3, 2), (3, 3), (7, 0), (7, 1)]


@pytest.mark.parametrize("mapper,choice", [pytest.param(m, c, id=f"mapper{m}-{c}", marks=[] if m == 3 else [DELTA])
                                           for m, c in PICTURES])
def test_the_picture_comes_from_the_banks_and_page_the_game_chose(tmp_path, mapper, choice):
    """purpose: the frame is drawn from what the board maps: UxROM's tiles copied into CHR-RAM from the
    PRG bank the game selected; CNROM's tiles from the selected CHR-ROM bank; AxROM's single-screen page
    from register bit 4 (both pages hold different pictures). Compared with I2's spec renderer, index for
    index on all 240 lines.
    origin: design"""
    p = picture_probe(mapper, choice)
    want = p.expected_frame()
    res = run_probe(p.write_to(tmp_path, "picture.nes"), frames=60, extra=[["frames", 3], ["frame"]])
    assert res.get("ok"), loads(res, f"mapper {mapper}")
    got = frame_of(res)
    assert got == want, f"mapper {mapper} choice {choice}: {diff_count(got, want)} pixels differ, first {first_diffs(got, want)}"


# ---------------------------------------------------------------- a split: a bank or page switched mid-frame

SPLIT_X = 248      # sprite 0's X: the hit lands at dots 249-255, so the write lands after the line's
                   # last background fetch (dot 256) and before the next line's first (dot 321)
SOLID = 0xFE       # the sprite tile, solid colour 1 and the same in every bank


def split_probe(mapper: int) -> tuple:
    """A status-bar split, as games make one with sprite 0: each NMI selects the top's CHR bank (CNROM)
    or VRAM page (AxROM); once sprite 0 hits, the main loop selects the bottom's. Returns (probe, the
    spec's frame: the top rows from the first state, the rest from the second)."""
    if mapper == 3:
        p = Probe(3, 32, chr_kib=32, mirroring="h")
        chr_rom = bytearray(p.chr_rom)
        for b in range(4):
            chr_rom[b * 0x2000 + 0x1000 + SOLID * 16:b * 0x2000 + 0x1000 + SOLID * 16 + 16] = b"\xff" * 8 + bytes(8)
        p.chr_rom = bytes(chr_rom)
        p.plan = p.model()
        p.select(1, TABLE, TABLE + 256)
        top, bottom = 1, 2
    else:
        p = Probe(7, 128)
        p.select(2, TABLE, TABLE + 256)
        for k in range(16):
            p.ppu_copy(k * 0x100, 0x8000 + k * 0x100, 256)
        for i in range(16):
            p.ppu_write(0x1000 + SOLID * 16 + i, 0xFF if i < 8 else 0x00)
        top, bottom = 0x02, 0x12
    pal = [0x0F, 0x16, 0x2A, 0x30, 0x0F, 0x01, 0x11, 0x21, 0x0F, 0x06, 0x17, 0x28, 0x0F, 0x09, 0x1A, 0x3C,
           0x0F, 0x24, 0x24, 0x24] + [0x0F, 0x24, 0x24, 0x24] * 3
    for i, c in enumerate(pal):
        p.ppu_write(0x3F00 + i, c)
    for page in (0, 1):
        if mapper == 7:
            p.select(0x10 * page | 2, TABLE, TABLE + 256)
        for k in range(4):
            p.ppu_copy(0x2000 + page * 0x800 + k * 0x100, 0x9000 + page * 0x400 + k * 0x100, 256)
    if mapper == 7:
        p.select(top, TABLE, TABLE + 256)
    # sprite 0 on a line where the background under x 248-254 is opaque, so it hits on its first line
    p.end(ctrl=0x08, mask=0x0A)
    plain = p.expected_frame()
    y = next(y for y in range(100, 200) if any(plain[(y + 1) * 256 + x] & 0x3F != 0x0F for x in range(248, 255)))
    p.sprite0(y, SOLID, 0x00, SPLIT_X)
    p.end(ctrl=0x08, mask=0x1E, split=((TABLE + top, top), (TABLE + bottom, bottom)))
    first, second = p.split_frames()
    row = y + 2                                    # the hit is on line y + 1; line y + 2 is the first after it
    assert first[row * 256:] != second[row * 256:] and first[:row * 256] != second[:row * 256], "a vacuous split"
    return p, first[:row * 256] + second[row * 256:], y


@pytest.mark.parametrize("mapper", [3, pytest.param(7, marks=DELTA)])
def test_a_bank_or_page_switched_mid_frame_takes_effect_from_the_next_line(tmp_path, mapper):
    """purpose: J4's status bar on a discrete board: a CNROM CHR bank or AxROM nametable page selected
    after sprite 0 hits (on line y + 1, at x 248-254, so the write lands in that line's hblank, after its
    last background fetch at dot 256 and before the next line's first at dot 321; NESdev "PPU rendering")
    shows from line y + 2 down, while lines 0 to y + 1 show the bank or page the NMI selected; the same
    on frames 10, 11 and 12. The frame is compared, index for index, with the spec renderer's two
    frames spliced at line y + 2, whatever way the renderer schedules its drawing (the Decided (run)
    item on lazy drawing names a mapper's mid-frame switch as its risk).
    origin: design"""
    p, want, y = split_probe(mapper)
    res = run_probe(p.write_to(tmp_path, "split.nes"), frames=60,
                    extra=[["frames", 10], ["frame"], ["frames", 1], ["frame"], ["frames", 1], ["frame"]])
    assert res.get("ok"), loads(res, f"mapper {mapper}")
    for k in range(3):
        got = frame_of(res, k)
        bad = sorted({i // 256 for i, (g, w) in enumerate(zip(got, want)) if g != w})
        assert not bad, f"mapper {mapper}, sprite 0 at Y={y}: frame {k}: {len(bad)} lines differ, first {bad[:8]}"


# ---------------------------------------------------------------- Reset, power, and the loader

@pytest.mark.parametrize("mapper", [pytest.param(2, marks=DELTA), 3, pytest.param(7, marks=DELTA)])
def test_reset_and_power_rerun_a_game_that_sets_its_own_banks(tmp_path, mapper):
    """purpose: the wiki gives no power-on or reset state for these registers, so games select their
    banks in their reset code (AxROM games keep that code in every bank). A probe that does so, run to
    its end, then Reset, then power-cycled, logs exactly the spec's reads each time: Reset reaches the
    reset vector whatever bank was left selected, and nothing the first run left behind changes the
    second.
    origin: design"""
    p = probe(mapper, {2: 128, 3: 32, 7: 128}[mapper])
    n = p.plan.count
    p.select(n - 1, TABLE, TABLE + 256)
    look(p, "first")
    p.select(1 | (0x10 if mapper == 7 else 0), salt=1)
    look(p, "second")
    if mapper == 7:
        p.ppu_write(0x2000, 0x77)
        p.ppu_read(0x2800, "page 1")
    p.end()
    path = p.write_to(tmp_path, "probe.nes")
    want = p.expected_log()
    steps = [["until", 0x200, 0xAA, 60], ["peek", 0x300, len(want)],
             ["reset"], ["until", 0x200, 0xAA, 60], ["peek", 0x300, len(want)],
             ["power"], ["until", 0x200, 0xAA, 60], ["peek", 0x300, len(want)]]
    res = drive_i6(path, steps)
    assert res.get("ok"), loads(res, f"mapper {mapper}")
    o = res["out"]
    for i, what in enumerate(["power-on", "Reset", "a power cycle"]):
        assert o[2 * i] > 0, f"mapper {mapper}: after {what} the probe never finished: {short(res)}"
        assert o[2 * i + 1] == want, f"mapper {mapper}: after {what} the log differs from the spec's"


@pytest.mark.parametrize("mapper", [pytest.param(2, marks=DELTA), 3, pytest.param(7, marks=DELTA)])
def test_power_on_is_the_same_every_time(tmp_path, mapper):
    """purpose: which bank (and AxROM page) is mapped at power-on is unknown on hardware and isn't pinned
    here, but runs are deterministic (Decided): a probe whose first reads look at the switchable window
    before any bank write reads the same bytes on three power-ons, and they are bytes of the ROM (one
    bank's, the same bank for every read).
    origin: design"""
    p = probe(mapper, {2: 128, 3: 32, 7: 128}[mapper])
    if mapper == 2:
        reads = [("R", a) for a in (0x8000, 0x8001, 0x9234, 0xBFFF)]
        p.ops += [("R", (a,)) for _, a in reads]
        size, base = 0x4000, 0x8000
    elif mapper == 7:
        reads = [("R", a) for a in (0x8000, 0x8001, 0xA5A5, 0xEFFF)]
        p.ops += [("R", (a,)) for _, a in reads]
        size, base = 0x8000, 0x8000
    else:
        reads = [("PR", a) for a in (0x0000, 0x0001, 0x1234, 0x1FFF)]
        p.ops += [("PR", (a,)) for _, a in reads]
        size, base = 0x2000, 0x0000
    p.end()
    path = p.write_to(tmp_path, "probe.nes")
    logs = []
    for _ in range(3):
        res = drive_i6(path, [["until", 0x200, 0xAA, 60], ["peek", 0x300, 4]])
        assert res.get("ok") and res["out"][0] > 0, loads(res, f"mapper {mapper}")
        logs.append(res["out"][1])
    assert logs[0] == logs[1] == logs[2], f"mapper {mapper}: power-on reads differ between runs: {logs}"
    data = p.chr_rom if mapper == 3 else bytes(p.prg)
    banks = [b for b in range(len(data) // size)
             if all(data[b * size + a - base] == v for (_, a), v in zip(reads, logs[0]))]
    assert banks, f"mapper {mapper}: the power-on reads {logs[0]} aren't any one bank's bytes"


def test_a_diskdude_uxrom_file_runs_as_mapper_2(tmp_path):
    """purpose: an old UxROM dump with "DiskDude!" junk in header bytes 7-15 is read as mapper 2 (byte 7's
    upper nibble, here 'D' = $44, is ignored when bytes 12-15 aren't zero; NESdev "INES"), not refused as
    mapper 66, and runs as UxROM.
    origin: design"""
    p = Probe(2, 64)
    p.select(3, TABLE, TABLE + 256)
    look(p, "bank 3")
    p.select(1, salt=1)
    look(p, "bank 1")
    p.end()
    data = p.image()
    data = data[:7] + b"DiskDude!" + data[16:]
    path = tmp_path / "diskdude.nes"
    path.write_bytes(data)
    check_probe(p, run_probe(str(path), frames=60), "a DiskDude! UxROM file")


REFUSED = [18, 35, 39, 180, 185, 258, 259, 263]


@pytest.mark.parametrize("number", REFUSED)
def test_a_mapper_still_unsupported_is_refused_by_its_whole_number(tmp_path, number):
    """purpose: adding mappers 2, 3 and 7 doesn't make others run as them: a file for a mapper whose low
    nibble (or low byte) is 2, 3 or 7 (18, 35, 39; NES 2.0 258, 259, 263), or for a close relative on
    other wiring (180, UNROM with the fixed bank first; 185, CNROM with CHR disabled unless the right bank
    is chosen), is refused with a RomError that names its whole number, never run as 2, 3 or 7.
    origin: design"""
    nes2 = number > 255
    data = header(number, 0x8000, 0x2000, nes2=nes2) + Probe(3, 32, chr_kib=8).image()[16:]
    path = tmp_path / f"mapper{number}.nes"
    path.write_bytes(data)
    res = drive_i6(path, [])
    assert res.get("ok") is False and res.get("stage") == "load", f"mapper {number} was run: {short(res)}"
    assert res["romError"] is True, res
    assert re.search(rf"\b{number}\b", res["error"]), f"the refusal doesn't name mapper {number}: {res['error']!r}"
