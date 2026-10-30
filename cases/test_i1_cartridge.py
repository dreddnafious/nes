"""I1: the cartridge loader, against ROM files built here from the iNES / NES 2.0 spec.

Each good file carries a tiny hand-assembled program that reports through blargg's $6000 protocol
(signature DE B0 61, status $00 = pass), so "accepted" means the program actually ran from the
right bytes, not just that the constructor didn't throw. Variants J1a and J1b."""
import re

import pytest

from conftest import drive

DELTA = pytest.mark.xfail(strict=True, reason="delta")


def program(check: bytes) -> bytes:
    """A 16 KiB NROM-128 PRG bank: sign $6001-3, run `check` (which leaves A = 0 on pass), write a
    one-character text and a terminator, then the status, then spin. Vectors all point at $C000;
    the bank is mirrored, so $8000 and $C000 hold the same code."""
    code = bytes([
        0x78, 0xD8, 0xA2, 0xFF, 0x9A,              # SEI; CLD; LDX #$FF; TXS
        0xA9, 0xDE, 0x8D, 0x01, 0x60,              # LDA #$DE; STA $6001
        0xA9, 0xB0, 0x8D, 0x02, 0x60,              # LDA #$B0; STA $6002
        0xA9, 0x61, 0x8D, 0x03, 0x60,              # LDA #$61; STA $6003
    ]) + check + bytes([
        0x48,                                      # PHA            (the result)
        0xA9, 0x6B, 0x8D, 0x04, 0x60,              # LDA #'k'; STA $6004
        0xA9, 0x00, 0x8D, 0x05, 0x60,              # LDA #0;   STA $6005
        0x68, 0x8D, 0x00, 0x60,                    # PLA; STA $6000 (status last)
    ])
    spin = 0xC000 + len(code)
    code += bytes([0x4C, spin & 0xFF, spin >> 8])  # JMP *
    prg = bytearray(code + bytes(0x4000 - len(code)))
    prg[0x3FFA:0x4000] = bytes([0x00, 0xC0, 0x00, 0xC0, 0x00, 0xC0])
    return bytes(prg)


# LDA $8000; CMP $C000; BNE fail; LDA $BFFC; CMP $FFFC; BNE fail; LDA #0; BEQ done; fail: LDA #1; done:
MIRROR_CHECK = bytes([0xAD, 0x00, 0x80, 0xCD, 0x00, 0xC0, 0xD0, 0x0C,
                      0xAD, 0xFC, 0xBF, 0xCD, 0xFC, 0xFF, 0xD0, 0x04,
                      0xA9, 0x00, 0xF0, 0x02, 0xA9, 0x01])
PRG = program(MIRROR_CHECK)
CHR = bytes(0x2000)


def ines(prg_banks=1, chr_banks=1, flags6=0x00, rest=b"\x00" * 9) -> bytes:
    return b"NES\x1a" + bytes([prg_banks, chr_banks, flags6]) + rest


def write(tmp_path, name, data) -> str:
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def runs_and_passes(path):
    res = drive({"mode": "blargg", "rom": path, "maxFrames": 60}, timeout=60)
    assert res.get("ok") and res.get("done"), f"the program never reported: {res}"
    assert res["status"] == 0, f"the program ran but its check failed: {res}"
    return res


def test_nrom128_runs_with_its_16k_bank_mirrored(tmp_path):
    """purpose: a plain iNES NROM-128 file (16 KiB PRG, 8 KiB CHR) loads and runs, its single PRG bank
    seen at both $8000 and $C000, and the PRG-RAM at $6000 holds what the program writes there.
    origin: design"""
    runs_and_passes(write(tmp_path, "nrom128.nes", ines() + PRG + CHR))


def test_nes2_header_is_read_as_nes2(tmp_path):
    """purpose: the same program in an NES 2.0 file (byte 7 bits 2-3 = %10, the size MSBs in byte 9
    zero) loads and runs as NROM.
    origin: design"""
    rest = bytes([0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
    runs_and_passes(write(tmp_path, "nes2.nes", ines(rest=rest) + PRG + CHR))


def test_trainer_is_skipped(tmp_path):
    """purpose: a file with the trainer flag (byte 6 bit 2) has 512 bytes before its PRG; the loader
    skips them, so the program still runs from the right bytes.
    origin: design"""
    trainer = bytes([0xEA]) * 512
    runs_and_passes(write(tmp_path, "trainer.nes", ines(flags6=0x04) + trainer + PRG + CHR))


def test_archaic_header_junk_is_ignored(tmp_path):
    """purpose: an old file with "DiskDude!" in header bytes 7-15 is read as mapper 0 (the upper
    mapper nibble in byte 7 is ignored when bytes 12-15 aren't zero), so it runs instead of being
    refused as mapper 64.
    origin: design"""
    data = b"NES\x1a" + bytes([1, 1, 0x00]) + b"DiskDude!" + PRG + CHR
    runs_and_passes(write(tmp_path, "diskdude.nes", data))


def test_power_on_ram_is_zero_filled(tmp_path):
    """purpose: straight after power-on, before any frame runs, CPU RAM $0000-$07FF reads all $00
    (the fixed power-on fill decided at axes, so runs are deterministic).
    origin: design"""
    res = drive({"mode": "power", "rom": write(tmp_path, "nrom128.nes", ines() + PRG + CHR)})
    assert res.get("ok"), res
    assert len(res["ram"]) == 0x800 and set(res["ram"]) == {0}, f"non-zero bytes: {sorted(set(res['ram']))[:8]}"


def test_not_an_nes_file_is_refused_with_a_reason(tmp_path):
    """purpose: a file that isn't an iNES/NES 2.0 ROM (a text file) is refused with a RomError whose
    message says it isn't an NES ROM; nothing runs (variant J1a).
    origin: design"""
    res = drive({"mode": "load", "rom": write(tmp_path, "notes.txt", b"hello, this is not a ROM\n" * 40)})
    assert res.get("stage") == "load" and res.get("ok") is False, res
    assert res["romError"] is True, res
    assert re.search(r"\b(i?NES|header)\b", res["error"], re.I), res["error"]


def test_truncated_file_is_refused_naming_the_mismatch(tmp_path):
    """purpose: a file whose header promises 32 KiB PRG + 8 KiB CHR but holds only 20 KiB of data is
    refused with a RomError that says the file is too short for its header, giving the sizes
    (variant J1b).
    origin: design"""
    data = ines(prg_banks=2) + (PRG + PRG)[:20 * 1024]
    res = drive({"mode": "load", "rom": write(tmp_path, "short.nes", data)})
    assert res.get("stage") == "load" and res.get("ok") is False, res
    assert res["romError"] is True, res
    assert re.search(r"truncat|too short|short|expect|size|length", res["error"], re.I), res["error"]
    assert re.search(r"\d", res["error"]), f"no size given: {res['error']}"


def test_unsupported_mapper_is_refused_by_number(tmp_path):
    """purpose: an NES 2.0 file for mapper 3000 (an unassigned number, spread across bytes 6, 7 and 8)
    is refused with a RomError naming mapper 3000, rather than running garbage (variant J1a).
    origin: design"""
    rest = bytes([0xB8, 0x0B, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
    data = ines(flags6=0x80, rest=rest) + PRG + CHR
    res = drive({"mode": "load", "rom": write(tmp_path, "m3000.nes", data)})
    assert res.get("stage") == "load" and res.get("ok") is False, res
    assert res["romError"] is True, res
    assert re.search(r"mapper", res["error"], re.I) and "3000" in res["error"], res["error"]
