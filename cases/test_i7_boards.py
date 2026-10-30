"""I7: MMC1 (mapper 1) through the core API, on probe cartridges built in the case from the NESdev wiki
("MMC1", "NES 2.0", "Mirroring").

Every PRG bank of a probe is filled with seeded random bytes, CHR-ROM too, so each bank, and each offset
in it, reads apart. The probe's program (in the top 4 KiB of every 16 KiB bank) writes $80 to $8000
first, then runs a script of single CPU writes (a register load is five of them, as the wiki's examples
make it), read-modify-write instructions, and CPU and PPU reads; it logs every read in RAM, and the case
compares the log, read for read, with what `i7_support.Mmc1` (the wiki's MMC1 in miniature) says each
read gives. The picture cases compare the frame with I2's spec renderer. Where the wiki leaves a fact
unknown or revision-dependent (every register at power-on; PRG-RAM with $E000 bit 4 set; SNROM's
PRG-RAM disable), the model refuses to answer, so no probe depends on it."""
import re

import pytest

from i2_support import diff_count, first_diffs
from i6_support import check_probe, drive_i6, frame_of, loads, run_probe, short
from i7_support import (CHR0, CHR1, CONTROL, PRG, Mmc1Probe, header, look, look_chr, picture_probe, setup)

DELTA = pytest.mark.xfail(strict=True, reason="delta")


def run(p: Mmc1Probe, tmp_path, what: str, frames=60, timeout=180):
    check_probe(p, run_probe(p.write_to(tmp_path, "probe.nes"), frames=frames, timeout=timeout), what)


# ---------------------------------------------------------------- the serial port

@pytest.mark.parametrize("kib", [128, 256])
def test_a_register_takes_five_writes_low_bit_first_and_the_fifth_address_picks_it(tmp_path, kib):
    """purpose: the MMC1's registers are loaded through its serial port (NESdev "MMC1", Interface): five
    writes to $8000-$FFFF, each carrying one bit in bit 0, the low bit first; four writes change nothing
    that can be seen, and the fifth copies the five bits into the register its address's bits 14-13 pick
    ($8000 control, $A000 CHR bank 0, $C000 CHR bank 1, $E000 PRG bank), whatever addresses the first
    four went to ("Only on the fifth write does the address matter"). Shown on the PRG bank (16 KiB at
    $8000 in PRG mode 3) with one-bit values that expose the bit order, scattered addresses, and a fifth
    write at $A000 after four at $E000 (which loads CHR bank 0, not the PRG bank).
    origin: design"""
    p = Mmc1Probe(kib, chr_kib=128)
    n = kib // 16
    setup(p, 0x0E, chr0=0, prg=n - 2)
    look(p, "after setup")
    for k, v in enumerate([0b00001, 0b00010, 0b00100, 0b01000, 0b00011, 0b00110]):
        v %= n
        for i in range(4):
            p.write(PRG, (v >> i) & 1)
        look(p, f"after four of the five writes of {v}", offsets=(0x0000, 0x2FFF))
        p.write(PRG | 0x1FFF, (v >> 4) & 1)
        look(p, f"after the fifth write of {v}")
    for k, v in enumerate([5 % n, 1, n - 1]):
        p.load(PRG, v, addrs=[0x8000 + 0x0765 * (k + 1), 0xFACE - k, 0xBA11 + k, 0xAD2E, 0xEAD5 - 0x111 * k])
        look(p, f"after a load of {v} at scattered addresses")
    p.load(CHR0, 0b10110, addrs=[PRG, PRG + 1, 0xFFFF, 0xE123, 0xBFFF])
    look(p, "after four writes at $E000-$FFFF and the fifth at $BFFF")
    look_chr(p, "after four writes at $E000-$FFFF and the fifth at $BFFF")
    p.end()
    run(p, tmp_path, f"MMC1 {kib} KiB")


@pytest.mark.parametrize("mode", [0, 1, 2, 3])
@pytest.mark.parametrize("kib", [64, 128, 256])
def test_every_prg_bank_in_each_prg_mode(tmp_path, kib, mode):
    """purpose: the control register's PRG mode (bits 3-2) and the PRG bank register (bits 3-0) map the
    16 KiB banks as the wiki's table says: modes 0 and 1, 32 KiB at $8000 (the bank number's low bit
    ignored); mode 2, the first bank fixed at $8000 and the selected one at $C000; mode 3, the selected
    bank at $8000 and the last fixed at $C000. All 16 bank numbers are loaded (in a scrambled order),
    each read across both windows; on 64 and 128 KiB the numbers past the last bank wrap (the unused
    high bits drive no ROM address line).
    origin: design"""
    p = Mmc1Probe(kib, chr_kib=0)
    setup(p, (mode << 2) | 0x02, prg=0)
    for k in range(16):
        v = (k * 7 + 3) % 16
        p.load(PRG, v)
        look(p, f"mode {mode}, PRG bank {v}")
    p.end()
    run(p, tmp_path, f"MMC1 {kib} KiB, PRG mode {mode}")


@pytest.mark.parametrize("start_mode", [0, 2])
def test_a_bit_7_write_clears_the_shift_register_and_fixes_the_last_bank(tmp_path, start_mode):
    """purpose: a write with bit 7 set, anywhere in $8000-$FFFF, clears the shift register and ORs the
    control register with $0C (PRG mode 3: the last bank fixed at $C000, the 16 KiB bank at $8000
    switched); "The CHR bank mode and nametable arrangement are not altered." Made in the middle of a
    load (after one, two, three and four of its writes), with $80 and $FF at different addresses: the
    bits before it are dropped, so the next five writes load exactly their value; and the mode change,
    the 4 KiB CHR mode and one-screen B mirroring set before it are read back after it.
    origin: design"""
    p = Mmc1Probe(256, chr_kib=128)
    setup(p, 0x11 | (start_mode << 2), chr0=3, chr1=9, prg=6)
    p.ppu_write(0x2000, 0x5A)
    look(p, f"in PRG mode {start_mode}")
    for k, (addr, val) in enumerate([(0x8000, 0x80), (0xBFFF, 0xFF), (0xC123, 0x80), (0xFFFF, 0xC1)]):
        for i in range(k + 1):
            p.write(0xE000, (0b10101 >> i) & 1)
        p.reset_write(addr, val)
        look(p, f"after a reset write of ${val:02X} at ${addr:04X} mid-load")
        b = (k * 5 + 2) % 16
        p.load(PRG, b)
        look(p, f"after a clean load of PRG bank {b}")
        look_chr(p, "CHR after the reset write (4 KiB mode kept)", offsets=(0x0000, 0x1000))
        for t in range(4):
            p.ppu_read(0x2000 + t * 0x400, "nametables after the reset write (one-screen B kept)")
        p.load(CONTROL, 0x11 | (start_mode << 2))
    p.end()
    run(p, tmp_path, f"MMC1 reset from PRG mode {start_mode}")


@pytest.mark.parametrize("name,byte", [("inc", "ff"), ("inc", "low"), ("dec", "low"), ("ror", "low"), ("rra,x", "low")])
def test_a_read_modify_write_lands_only_its_first_write_but_a_reset_always(tmp_path, name, byte):
    """purpose: "When the serial port is written to on consecutive cycles, it ignores every write after
    the first ... the bit 7 reset is never ignored." A read-modify-write instruction on a ROM address
    writes the byte it read and then, on the very next cycle, the modified byte: INC on a byte of $FF
    resets the MMC1 with its first write and the $00 after it is ignored (Bill & Ted's Excellent
    Adventure); INC, DEC and ROR on a byte with bit 7 clear shift in only the first write's bit 0, so
    four more single writes complete the load; ROR and the unofficial RRA abs,X ($7F, as Shinsenden
    uses it) with the carry set write bit 7 on the second write, and that reset still lands (the shift
    register is cleared and the PRG mode becomes 3). Each is checked by the bank the next load selects.
    origin: design"""
    p = Mmc1Probe(256)
    setup(p, 0x02, prg=4)                           # PRG mode 0 (32 KiB), so a reset to mode 3 shows
    look(p, "mode 0, bank 4")
    for k in range(6):
        if byte == "ff":
            a = p.addr_where(lambda b: b == 0xFF, 0x8000, 0xF000, salt=k)
            p.rmw(name, a)
            look(p, f"after {name.upper()} on $FF at ${a:04X} (a reset, then an ignored $00)")
            p.load(PRG, (k * 3 + 1) % 16)
        else:
            carry = 1 if name in ("ror", "rra,x") else 0
            if carry:
                for i in range(k % 3 + 1):
                    p.write(PRG, (0b11011 >> i) & 1)
                a = p.addr_where(lambda b: b < 0x80, 0x8000, 0xF000, salt=k)
                p.rmw(name, a, carry=1)
                look(p, f"after {name.upper()} with the carry set at ${a:04X} (its second write resets)")
                p.load(PRG, (k * 3 + 1) % 16)
            else:
                a = p.addr_where(lambda b: b < 0x7F and (b & 1) == k % 2 and b > 0, 0x8000, 0xF000, salt=k)
                p.rmw(name, a)
                v = k % 2 | (((k * 5 + 2) % 8) << 1)
                for i in range(1, 5):
                    p.write(PRG, (v >> i) & 1)
                look(p, f"after {name.upper()} at ${a:04X} and four single writes")
        p.load(CONTROL, 0x02)
        look(p, "mode 0 again")
    p.end()
    run(p, tmp_path, f"{name.upper()} on the serial port")


def test_writes_below_8000_never_reach_the_mmc1(tmp_path):
    """purpose: the serial port answers only $8000-$FFFF (/ROMSEL): in the middle of a load, writes of
    $80, $FF and $01 to $4020, $5000, $5FFF, $6000 and $7FFF neither reset nor shift the register, so
    the load's remaining writes complete it as if nothing came between; and the $6000-$7FFF writes go to
    the PRG-RAM (read back).
    origin: design"""
    p = Mmc1Probe(128, chr_kib=32)
    setup(p, 0x0E, prg=1)
    for k, v in enumerate([3, 6, 2, 5, 0]):
        for i in range(k % 4 + 1):
            p.write(PRG, (v >> i) & 1)
        for a in (0x4020, 0x5000, 0x5FFF, 0x6000, 0x7FFF):
            for x in (0x80, 0xFF, 0x01 + k):
                p.write(a, x)
        for i in range(k % 4 + 1, 5):
            p.write(PRG, (v >> i) & 1)
        look(p, f"after a load of {v} with writes below $8000 in it")
        p.read(0x6000, "PRG-RAM $6000")
        p.read(0x7FFF, "PRG-RAM $7FFF")
    p.end()
    run(p, tmp_path, "writes below $8000")


# ---------------------------------------------------------------- mirroring

@pytest.mark.parametrize("hdr", ["v", "h"])
def test_mirroring_is_control_bits_0_and_1_whatever_the_header_says(tmp_path, hdr):
    """purpose: the control register's bits 1-0 pick the nametable arrangement ("0: one-screen, screen A;
    1: one-screen, screen B; 2: vertical mirroring; 3: horizontal mirroring"), switchable at any time,
    whatever header byte 6 bit 0 says: under each arrangement, a byte written through each of $2000,
    $2400, $2800 and $2C00 is read back through all four, and each VRAM page keeps its bytes while
    the other is shown.
    origin: design"""
    p = Mmc1Probe(128, mirroring=hdr)
    setup(p, 0x0E)
    p.ppu_fill(0x2000, 0x11, 0x80)
    p.ppu_fill(0x2400, 0x22, 0x80)
    for m in (0, 1, 2, 3, 1, 0, 3, 2):
        p.load(CONTROL, 0x0C | m)
        for t in range(4):
            p.ppu_write(0x2000 + t * 0x400 + 0x40 + m * 4 + t, 0x80 | (m << 4) | t)
        for t in range(4):
            for o in range(4):
                p.ppu_read(0x2000 + t * 0x400 + 0x40 + m * 4 + o, f"arrangement {m}, nametable {t}")
    for m in (2, 3, 0, 1):
        p.load(CONTROL, 0x0C | m)
        for t in range(4):
            p.ppu_read(0x2000 + t * 0x400 + 0x41, f"arrangement {m} afterwards, nametable {t}")
    p.end()
    run(p, tmp_path, f"MMC1 mirroring, header {hdr}")


# ---------------------------------------------------------------- CHR

@pytest.mark.parametrize("kib", [16, 32, 128])
def test_chr_banks_in_8k_and_4k_mode(tmp_path, kib):
    """purpose: CHR-ROM banking as the wiki gives it: in 8 KiB mode (control bit 4 clear) CHR bank 0
    selects 8 KiB at PPU $0000-$1FFF with its low bit ignored, and CHR bank 1 is ignored; in 4 KiB mode
    CHR bank 0 selects $0000-$0FFF and CHR bank 1 $1000-$1FFF, independently. Every one of the 32
    values is loaded in each mode (numbers past the ROM's last bank wrap), and each half is read at
    both ends and in the middle.
    origin: design"""
    p = Mmc1Probe(64, chr_kib=kib)
    setup(p, 0x0E)
    for k in range(32):
        v = (k * 11 + 5) % 32
        p.load(CHR0, v)
        p.load(CHR1, (v + 13) % 32)
        look_chr(p, f"8 KiB mode, CHR bank 0 = {v}", offsets=(0x0000, 0x0FFF, 0x1000, 0x1FFF))
    p.load(CONTROL, 0x1E)
    for k in range(32):
        v = (k * 7 + 1) % 32
        p.load(CHR0 if k % 2 else CHR1, v)
        look_chr(p, f"4 KiB mode, CHR bank {k % 2 ^ 1} = {v}", offsets=(0x0000, 0x0ABC, 0x1000, 0x1FFF))
    p.end()
    run(p, tmp_path, f"MMC1 {kib} KiB CHR-ROM")


def test_chr_ram_is_two_4k_banks_in_4k_mode_and_is_never_rom(tmp_path):
    """purpose: on a board with 8 KiB of CHR-RAM (header byte 5 = 0), "that makes 2 banks": bytes
    written through $2007 in 8 KiB mode read back there; in 4 KiB mode CHR bank 0 = 1 shows the upper
    half at $0000 and CHR bank 1 = 0 the lower at $1000 (larger numbers wrap: "the bank number is in
    effect ANDed with 1"); and writes in 4 KiB mode land in the half selected.
    origin: design"""
    p = Mmc1Probe(128)
    setup(p, 0x0E, prg=2)
    spots = [0x0000, 0x0123, 0x0FFF, 0x1000, 0x1123, 0x1FFF]
    for k, a in enumerate(spots):
        p.ppu_write(a, 0x30 + k)
    p.ppu_copy(0x0400, 0x8100, 256)
    p.ppu_copy(0x1400, 0x8200, 256)
    p.ppu_fill(0x0800, 0x77, 16)
    p.ppu_fill(0x1800, 0x66, 16)
    look_chr(p, "8 KiB mode", offsets=spots + [0x0400, 0x04FF, 0x14FF])
    p.load(CONTROL, 0x1E)
    for k, (c0, c1) in enumerate([(1, 0), (0, 1), (3, 2), (2, 7), (1, 1)]):
        p.load(CHR0, c0)
        p.load(CHR1, c1)
        look_chr(p, f"4 KiB mode {c0}/{c1}", offsets=spots + [0x0400, 0x14FF])
        p.ppu_write(0x0800 + k, 0xA0 + k)
        p.ppu_read(0x0800 + k, "a write in 4 KiB mode")
    p.load(CONTROL, 0x0E)
    p.load(CHR0, 0)
    look_chr(p, "back in 8 KiB mode", offsets=(0x0800, 0x0801, 0x0802, 0x0803, 0x0804, 0x1800, 0x1801))
    p.end()
    run(p, tmp_path, "MMC1 CHR-RAM")


def test_chr_rom_is_read_and_never_written(tmp_path):
    """purpose: CHR-ROM in the file is ROM: writes through $2007 in either CHR mode leave every byte as
    the file has it, while the nametables take their writes.
    origin: design"""
    p = Mmc1Probe(64, chr_kib=32)
    setup(p, 0x1E, chr0=2, chr1=5)
    spots = [0x0000, 0x0777, 0x0FFF, 0x1000, 0x1FFF]
    for a in spots:
        p.ppu_write(a, 0xE7)
    p.ppu_write(0x2000, 0x99)
    look_chr(p, "after writes in 4 KiB mode", offsets=spots)
    p.load(CONTROL, 0x0E)
    for a in spots:
        p.ppu_write(a, 0x18)
    look_chr(p, "after writes in 8 KiB mode", offsets=spots)
    p.ppu_read(0x2000, "nametable")
    p.end()
    run(p, tmp_path, "MMC1 CHR-ROM")


# ---------------------------------------------------------------- PRG-RAM

@pytest.mark.parametrize("kind", ["nes2-8k", "nes2-8k-battery", "ines"])
def test_prg_ram_at_6000_holds_what_is_written_through_bank_switching(tmp_path, kind):
    """purpose: an MMC1 cartridge's PRG-RAM at $6000-$7FFF (NES 2.0 byte 10: 8 KiB, volatile or
    battery-backed; or an iNES 1.0 file, whose size "has to be assumed", here only its first 8 KiB is
    asked of) holds bytes written across all of it, read back after PRG bank, PRG mode, CHR mode and
    mirroring changes, with $E000 bit 4 clear (PRG-RAM enabled on every revision).
    origin: design"""
    nes2, battery = kind != "ines", kind.endswith("battery")
    p = Mmc1Probe(128, chr_kib=32, prg_ram_kib=0 if battery else 8, prg_nvram_kib=8 if battery else 0,
                  battery=battery, nes2=nes2)
    setup(p, 0x0E, prg=0)
    spots = [0x6000, 0x6001, 0x63A5, 0x6FFF, 0x7000, 0x7ABC, 0x7FFE, 0x7FFF]
    for k, a in enumerate(spots):
        p.write(a, (0x5B * (k + 3)) & 0xFF)
    for k, (control, prg, chr0) in enumerate([(0x0E, 3, 1), (0x13, 5, 2), (0x1A, 7, 3), (0x0F, 0, 0)]):
        p.load(CONTROL, control)
        p.load(PRG, prg)
        p.load(CHR0, chr0 & 0x03)
        for a in spots:
            p.read(a, f"after control ${control:02X}, PRG bank {prg}")
        p.write(spots[k], 0xC0 | k)
        p.read(spots[k], "rewritten")
    p.end()
    run(p, tmp_path, f"MMC1 PRG-RAM ({kind})")


def test_sxrom_32k_prg_ram_is_four_banks_chosen_by_chr_bank_bits_3_2(tmp_path):
    """purpose: SXROM (NES 2.0: 32 KiB of battery-backed PRG-RAM, 8 KiB CHR-RAM, as Nova the Squirrel's
    header gives) banks its PRG-RAM in 8 KiB at $6000-$7FFF by CHR bank 0's bits 3-2 ("Select 8 KiB
    PRG-RAM bank"): the same addresses written in each of the four banks hold four different bytes, read
    back in a scrambled order; in 4 KiB CHR mode with both CHR registers equal, they choose the same
    way; and the PRG-ROM banking is unaffected by those bits.
    origin: design"""
    p = Mmc1Probe(256, prg_ram_kib=0, prg_nvram_kib=32, battery=True)
    setup(p, 0x0E, prg=2)
    spots = [0x6000, 0x6C35, 0x7F00, 0x7FFF]
    for bank in range(4):
        p.load(CHR0, bank << 2)
        for k, a in enumerate(spots):
            p.write(a, (bank << 6) | (k << 2) | 1)
    for bank in (2, 0, 3, 1, 1, 3):
        p.load(CHR0, (bank << 2) | 1)
        for a in spots:
            p.read(a, f"PRG-RAM bank {bank}")
        look(p, f"PRG-ROM with PRG-RAM bank {bank}", offsets=(0x0000,))
    p.load(CONTROL, 0x1E)
    for bank in (3, 0, 2):
        p.load(CHR0, bank << 2)
        p.load(CHR1, (bank << 2) | 1)
        for a in spots:
            p.read(a, f"4 KiB mode, PRG-RAM bank {bank}")
    p.end()
    run(p, tmp_path, "SXROM PRG-RAM")


def test_surom_512k_reaches_both_halves_through_chr_bank_bit_4(tmp_path):
    """purpose: SUROM (512 KiB PRG-ROM, 8 KiB CHR-RAM, 8 KiB PRG-RAM: NES 2.0 sizes) selects the 256 KiB
    half with CHR bank 0's bit 4 ("Select 256 KiB PRG-ROM bank"), and "The 256 KiB outer PRG bank
    selection applies to all the PRG area, including the normally 'fixed' bank": every one of the 32
    16 KiB banks is reached through $8000 in PRG mode 3, with the fixed bank at $C000 the last of the
    half chosen; then in 4 KiB CHR mode with both CHR registers equal. A real-size cartridge (J2q).
    origin: design"""
    p = Mmc1Probe(512, prg_ram_kib=0, prg_nvram_kib=8, battery=True)
    setup(p, 0x0E)
    for k in range(32):
        b = (k * 13 + 7) % 32
        p.load(CHR0, (b >> 4) << 4)
        p.load(PRG, b & 15)
        look(p, f"outer {b >> 4}, bank {b & 15}", offsets=(0x0000, 0x2FFF))
    p.load(CONTROL, 0x1E)
    for k, b in enumerate([20, 3, 31, 16]):
        p.load(CHR0, (b >> 4) << 4)
        p.load(CHR1, ((b >> 4) << 4) | 1)
        p.load(PRG, b & 15)
        look(p, f"4 KiB mode, outer {b >> 4}, bank {b & 15}")
    p.end()
    path = p.write_to(tmp_path, "surom.nes")
    assert (tmp_path / "surom.nes").stat().st_size == 16 + 512 * 1024
    check_probe(p, run_probe(path, frames=60, timeout=240), "SUROM 512 KiB")


# ---------------------------------------------------------------- the picture

@pytest.mark.parametrize("chr_bank,screen", [(5, 1), (0x1A, 0), (31, 1)])
def test_the_picture_comes_from_the_chr_bank_and_screen_mmc1_chose(tmp_path, chr_bank, screen):
    """purpose: the frame is drawn from what the MMC1 maps: the background's tiles from the 4 KiB CHR-ROM
    bank in CHR bank 0, and all four nametables from the VRAM page one-screen mirroring picked (both
    pages hold different nametables, copied from PRG bank 3). Compared with I2's spec renderer, index
    for index on all 240 lines.
    origin: design"""
    p = picture_probe(chr_bank, screen)
    want = p.expected_frame()
    res = run_probe(p.write_to(tmp_path, "picture.nes"), frames=60, extra=[["frames", 3], ["frame"]])
    assert res.get("ok"), loads(res, "MMC1 picture")
    got = frame_of(res)
    assert got == want, f"CHR bank {chr_bank}, screen {screen}: {diff_count(got, want)} pixels differ, first {first_diffs(got, want)}"


SPLIT_X = 248      # sprite 0's X: the hit lands at dots 249-255, so the fifth write lands after the line's
                   # last background fetch (dot 256) and before the next line's first (dot 321)
SOLID = 0xFE       # the sprite tile, solid colour 1 in every bank


def split_probe() -> tuple:
    """A status bar made the way an MMC1 game makes one with sprite 0: 4 KiB CHR mode, background from
    PPU $0000; each NMI loads CHR bank 0 with the playfield's bank; each frame the main loop loads the
    bar's bank, its fifth write right after sprite 0 hits. Returns (probe, the spec's frame, sprite Y)."""
    p = Mmc1Probe(128, chr_kib=128, mirroring="h")
    chr_rom = bytearray(p.chr_rom)
    for b in range(32):
        chr_rom[b * 0x1000 + SOLID * 16:b * 0x1000 + SOLID * 16 + 16] = b"\xff" * 8 + bytes(8)
    p.chr_rom = bytes(chr_rom)
    p.plan = p.model()
    top, bottom = 9, 22
    setup(p, 0x1F, chr0=top, chr1=4, prg=3)
    pal = [0x0F, 0x16, 0x2A, 0x30, 0x0F, 0x01, 0x11, 0x21, 0x0F, 0x06, 0x17, 0x28, 0x0F, 0x09, 0x1A, 0x3C,
           0x0F, 0x24, 0x24, 0x24] + [0x0F, 0x24, 0x24, 0x24] * 3
    for i, c in enumerate(pal):
        p.ppu_write(0x3F00 + i, c)
    for k in range(4):
        p.ppu_copy(0x2000 + k * 0x100, 0x9000 + k * 0x100, 256)
        p.ppu_copy(0x2800 + k * 0x100, 0x9400 + k * 0x100, 256)
    p.end(ctrl=0x08, mask=0x0A)
    plain = p.expected_frame()
    y = next(y for y in range(100, 200) if any(plain[(y + 1) * 256 + x] & 0x3F != 0x0F for x in range(248, 255)))
    p.sprite0(y, SOLID, 0x00, SPLIT_X)
    p.end(ctrl=0x08, mask=0x1E, split=((CHR0, top), (CHR0 | 0x1234, bottom)))
    first, second = p.split_frames()
    row = y + 2
    assert first[row * 256:] != second[row * 256:] and first[:row * 256] != second[:row * 256], "a vacuous split"
    return p, first[:row * 256] + second[row * 256:], y


def test_a_chr_bank_loaded_mid_frame_takes_effect_from_the_next_line(tmp_path):
    """purpose: J4's status bar on MMC1: CHR bank 0 (4 KiB mode, the background's table) reloaded after
    sprite 0 hits (on line y + 1 at x 248-254; the load's fifth write, the one that commits, lands in that
    line's hblank, after its last background fetch at dot 256 and before the next line's first at dot
    321; NESdev "PPU rendering") shows from line y + 2 down, while lines 0 to y + 1 show the bank the NMI
    loaded; the same on frames 10, 11 and 12. Compared index for index with the spec renderer's two
    frames spliced at line y + 2, whatever way the renderer schedules its drawing.
    origin: design"""
    p, want, y = split_probe()
    res = run_probe(p.write_to(tmp_path, "split.nes"), frames=60,
                    extra=[["frames", 10], ["frame"], ["frames", 1], ["frame"], ["frames", 1], ["frame"]])
    assert res.get("ok"), loads(res, "the MMC1 split")
    for k in range(3):
        got = frame_of(res, k)
        bad = sorted({i // 256 for i, (g, w) in enumerate(zip(got, want)) if g != w})
        assert not bad, f"sprite 0 at Y={y}: frame {k}: {len(bad)} lines differ, first {bad[:8]}"


# ---------------------------------------------------------------- Reset, power, and the loader

def test_reset_and_power_rerun_an_mmc1_game_that_sets_its_own_banks(tmp_path):
    """purpose: the wiki gives no reliable power-on state for the MMC1, so games write $80 and load every
    register in their reset code, and keep that code where every 16 KiB bank can reach it. A probe that
    does so, run to its end (leaving PRG mode 2, 4 KiB CHR mode, one-screen B and a half-loaded shift
    register behind), then Reset, then power-cycled, logs exactly the spec's reads each time, PRG-RAM
    included: Reset reaches the reset vector in whatever bank was left mapped, and nothing the first
    run left changes the second.
    origin: design"""
    p = Mmc1Probe(256, chr_kib=128)
    setup(p, 0x0E, chr0=4, prg=9)
    look(p, "first")
    look_chr(p, "first")
    p.write(0x6000, 0x42)
    p.read(0x6000, "PRG-RAM")
    p.load(CONTROL, 0x19)
    p.load(PRG, 3)
    p.load(CHR1, 17)
    look(p, "second")
    look_chr(p, "second")
    p.write(PRG, 1)
    p.write(PRG, 0)
    p.end()
    path = p.write_to(tmp_path, "probe.nes")
    want = p.expected_log()
    steps = [["until", 0x200, 0xAA, 60], ["peek", 0x300, len(want)],
             ["reset"], ["until", 0x200, 0xAA, 60], ["peek", 0x300, len(want)],
             ["power"], ["until", 0x200, 0xAA, 60], ["peek", 0x300, len(want)]]
    res = drive_i6(path, steps)
    assert res.get("ok"), loads(res, "MMC1")
    o = res["out"]
    for i, what in enumerate(["power-on", "Reset", "a power cycle"]):
        assert o[2 * i] > 0, f"after {what} the probe never finished: {short(res)}"
        assert o[2 * i + 1] == want, f"after {what} the log differs from the spec's"


def test_power_on_is_the_same_every_time(tmp_path):
    """purpose: what the MMC1's registers hold at power-on is unknown on hardware ("other tests have found
    that this is fragile") and isn't pinned here, but runs are deterministic (Decided): a probe whose
    first reads, right after its $80 reset write, look at the switchable $8000 window and at CHR before
    any register is loaded reads the same bytes on three power-ons, and the PRG reads are one bank's
    bytes, the same bank for every read.
    origin: design"""
    p = Mmc1Probe(128, chr_kib=32)
    reads = [0x8000, 0x8001, 0x9234, 0xAFFF]
    p.ops += [("R", (a,)) for a in reads] + [("PR", (a,)) for a in (0x0000, 0x1FFF)]
    p.end()
    path = p.write_to(tmp_path, "probe.nes")
    logs = []
    for _ in range(3):
        res = drive_i6(path, [["until", 0x200, 0xAA, 60], ["peek", 0x300, 6]])
        assert res.get("ok") and res["out"][0] > 0, loads(res, "MMC1")
        logs.append(res["out"][1])
    assert logs[0] == logs[1] == logs[2], f"power-on reads differ between runs: {logs}"
    data = bytes(p.prg)
    banks = [b for b in range(len(data) // 0x4000)
             if all(data[b * 0x4000 + a - 0x8000] == v for a, v in zip(reads, logs[0]))]
    assert banks, f"the power-on reads {logs[0][:4]} aren't any one bank's bytes"


REFUSED = [17, 33, 65, 105, 257, 513]


@pytest.mark.parametrize("number", REFUSED)
def test_a_mapper_still_unsupported_is_refused_by_its_whole_number(tmp_path, number):
    """purpose: adding mapper 1 doesn't make others run as it: a file for a mapper whose low nibble or low
    byte is 1 (17, 33, 65; NES 2.0 257, 513), or for MMC1's relative on other wiring (105, NES-EVENT: an
    MMC1 with a timer and a different PRG layout), is refused with a RomError that names its whole
    number, never run as MMC1.
    origin: design"""
    p = Mmc1Probe(128, chr_kib=32)
    p.end()
    data = header(0x20000, 0x8000, mapper=number, nes2=number > 255) + p.image()[16:]
    path = tmp_path / f"mapper{number}.nes"
    path.write_bytes(data)
    res = drive_i6(path, [])
    assert res.get("ok") is False and res.get("stage") == "load", f"mapper {number} was run: {short(res)}"
    assert res["romError"] is True, res
    assert re.search(rf"\b{number}\b", res["error"]), f"the refusal doesn't name mapper {number}: {res['error']!r}"
