"""I8: the MMC3 test ROMs and homebrew on MMC3, headless through the core API.

- blargg's mmc3_test_2 singles and Holy Mapperel's MMC3 builds (data: i8_mapper_roms.yaml; each case there
  carries its own promise and delta mark; 6-MMC3_alt, which can't pass on the MMC3B/C, is pinned by
  where it fails): blargg's $6000
  protocol is read by the frozen cases/drive.ts, so the build never grades itself; Holy Mapperel's result
  screen is read with the font from its own CHR-ROM build.
- nesdoug's 33_MMC3 demo (Doug Fraker, MIT; 128 KiB PRG, 128 KiB CHR-ROM, 8 KiB PRG-RAM, iNES 1.0; fetched
  by hash): what it shows comes from its own source (github.com/nesdoug/33_MMC3 at d03805b: hello_mmc3.c,
  MMC3/mmc3_code.asm, LIB/neslib.s, crt0.s). Its main loop fills an IRQ "array" each frame: in the NMI,
  CHR mode 5 = 8 and a scanline count of 47 ($C000 = 47, $C001, $E001); at each IRQ a new X scroll ($2005
  twice, after a short delay) and the next count, 30, 30 and 30, with $2001 = $FE (all three emphasis
  bits) at the second; at the fourth, $2005 = 0 and $2006 = $20, $00. By the wiki's counter the IRQs come
  at the end of lines 46, 77, 108 and 139, so its screen has four X-scroll bands, each moving at its own
  speed while B is held (scroll_top += $80, scroll2 += $100, scroll3 += $180, scroll4 += $200 a frame, the
  top byte each band's X), and lines from 79-80 down are emphasised. The handler's writes land some cycles
  after each IRQ (its parser, its delay loop), so the cases leave the two lines after each IRQ's line
  unpinned and pin everything else. Its text ("BANK0" to "BANK3  G4", "BANK6  AC", "BACK IN FIXED BANK") is
  drawn by functions in five PRG banks called through banked_call (R6 at $8000), and "AC" is read back
  from its PRG-RAM. It's a demo, not a game (it has no end), so it isn't counted in Reach."""
import re
from pathlib import Path

import pytest
import yaml

from conftest import drive
from i2_support import decode_frame
from i6_support import drive_i6, loads, mapperel_font, screen_text
from i8_support import BANDS, EMPHASISED, PLAIN, TEXT, B, W, band_of, chr_of, i8_oracle, line_text_matches

DELTA = pytest.mark.xfail(strict=True, reason="delta")
DATA = yaml.safe_load((Path(__file__).parent / "i8_mapper_roms.yaml").read_text())["cases"]
MAPPEREL_FRAMES = 900
MAX_FRAMES = 3600


def params(kind):
    out = []
    for c in DATA:
        if c["kind"] != kind:
            continue
        out.append(pytest.param(c, id=c["id"], marks=[DELTA] if c.get("delta") is True else []))
    return out


def mapperel_lines(pixels) -> list:
    return [ln.strip() for ln in screen_text(pixels, mapperel_font()) if ln.strip()]


@pytest.mark.parametrize("case", params("blargg"))
def test_blargg_mmc3_test_2_rom_reports_passed(case):
    """purpose: each of blargg's mmc3_test_2 singles, run headless until it reports, signs $6001-$6003 with
    DE B0 61 and ends with status $00 (passed); its item in i8_mapper_roms.yaml says what it tests.
    origin: design"""
    res = drive({"mode": "blargg", "rom": str(i8_oracle(case["id"])), "maxFrames": MAX_FRAMES}, timeout=240)
    assert res.get("ok") and res.get("done"), f"{case['id']}: no result: {res}"
    assert res["signature"] is True
    assert res["status"] == 0, f"{case['id']}: status {res['status']}: {res['text']!r}"


@pytest.mark.parametrize("case", params("blargg_status"))
def test_blargg_mmc3_alt_fails_at_the_alternate_revisions_first_difference(case):
    """purpose: on the MMC3B/C (Decided Q2), blargg's 6-MMC3_alt fails for its revision's reason and no
    other: it reports the status and the sub-test text its item gives.
    origin: design"""
    res = drive({"mode": "blargg", "rom": str(i8_oracle(case["id"])), "maxFrames": MAX_FRAMES}, timeout=240)
    assert res.get("ok") and res.get("done"), f"{case['id']}: no result: {res}"
    assert res["signature"] is True
    assert res["status"] == case["status"] and case["text"] in res["text"], \
        f"{case['id']}: status {res['status']}: {res['text']!r}"


@pytest.mark.parametrize("case", params("mapperel"))
def test_holy_mapperel_detects_the_mmc3_board_and_every_bank_reads_back(case):
    """purpose: each Holy Mapperel MMC3 build, run for 900 frames, shows its result screen with: mapper 004
    and the board its NES 2.0 sizes make (TGROM, TFROM or TLROM), the PRG ROM size it read back from the
    bank tags, "PRG RAM MISSING" (the headers give none), the CHR line ending "OK", and a detailed result
    of 0000 (every PRG window, every CHR window, the IRQ). Each line is the case's item in
    i8_mapper_roms.yaml.
    origin: design"""
    res = drive_i6(i8_oracle(case["id"]), [["frames", MAPPEREL_FRAMES], ["frame"]], timeout=240)
    assert res.get("ok"), loads(res, case["id"])
    lines = mapperel_lines(decode_frame(res["out"][0]))
    assert any(re.fullmatch(case["board"], ln) for ln in lines), f"{case['id']}: no board line; screen {lines[:6]}"
    assert case["prg"] in lines, f"{case['id']}: no {case['prg']!r} line; screen {lines[:6]}"
    assert case["wram"] in lines, f"{case['id']}: no {case['wram']!r} line; screen {lines[:6]}"
    assert case["chr"] in lines, f"{case['id']}: no {case['chr']!r} line; screen {lines[:6]}"
    assert case["detail"] in lines, f"{case['id']}: screen {lines[:8]}"


def test_holy_mapperels_mapper_118_build_is_refused_by_its_number():
    """purpose: an MMC3 board on other wiring isn't run as mapper 4: Holy Mapperel's TLSROM build (iNES
    mapper 118, whose CHR bank lines also drive the nametables) is refused with a RomError naming 118.
    origin: design"""
    res = drive_i6(i8_oracle("holy-mapperel/M118_P128K_C64K"), [])
    assert res.get("ok") is False and res.get("stage") == "load", f"mapper 118 was run: {res}"
    assert res["romError"] is True and re.search(r"\b118\b", res["error"]), res


# ---------------------------------------------------------------- nesdoug's 33_MMC3 demo

def demo_chr() -> bytes:
    return chr_of(i8_oracle("nesdoug-33_MMC3").read_bytes())


def text_lines_shown(pixels, shifts: dict, mask=0x3F) -> list:
    """The pinned lines of the demo's text rows that don't show its text, each band read at its X
    scroll (shifts: band -> X). Horizontal arrangement ($A000 = 1), so X wraps at 256."""
    chr_bytes = demo_chr()
    bad = []
    for row, text in TEXT.items():
        for y in range(row * 8, row * 8 + 8):
            if not any(y in r for r in BANDS.values()):
                continue
            if not line_text_matches(pixels, y, 1, text, chr_bytes, mask=mask, shift=shifts[band_of(y)]):
                bad.append((y, text))
    return bad


def demo(steps: list, timeout=240) -> dict:
    return drive_i6(i8_oracle("nesdoug-33_MMC3"), steps, timeout=timeout)


@DELTA
def test_nesdoug_mmc3_demo_draws_its_text_from_its_banks_and_prg_ram_and_emphasises_below_its_second_split():
    """purpose: J1 steps 2-3 on an MMC3 program: by frame 120 nesdoug's demo shows every line of its six
    text rows at column 1, read with its own font ("BANK0", "BANK1", "BANK2  HI", "BANK3  G4" drawn by
    functions in PRG banks 0-3 through R6 at $8000 in PRG mode 0; "BANK6  AC", from bank 6, with "AC" read
    back from $6000 and $6002 of its PRG-RAM; "BACK IN FIXED BANK"), and its second IRQ's $2001 = $FE
    puts all three emphasis bits on every pixel of lines 81-239 while lines 0-77 have none: the IRQs came
    on their lines (47, then 30 + 1 lines later). Frames 120 and 121 alike.
    origin: design"""
    res = demo([["frames", 120], ["frame"], ["frames", 1], ["frame"]])
    assert res.get("ok"), loads(res, "nesdoug's 33_MMC3")
    for f in (decode_frame(res["out"][0]), decode_frame(res["out"][1])):
        bad = text_lines_shown(f, {1: 0, 2: 0, 3: 0, 4: 0})
        assert not bad, f"{len(bad)} text lines don't show the demo's text, first {bad[:4]}"
        loud = [y for y in EMPHASISED if any(v >> 6 != 7 for v in f[y * W:(y + 1) * W])]
        quiet = [y for y in PLAIN if any(v >> 6 for v in f[y * W:(y + 1) * W])]
        assert not loud and not quiet, f"emphasis wrong on lines {loud[:6]} (want all three) and {quiet[:6]} (want none)"


@DELTA
def test_nesdoug_mmc3_demo_scrolls_its_four_bands_at_their_own_speeds_while_b_is_held():
    """purpose: J4 on an MMC3 program: holding B for 64 frames scrolls the demo's four bands, each by its
    own X: if band 2 (lines 50-77) moved n pixels (n frames' worth, 60-64 of them), band 1 (lines 0-46)
    moved n / 2, band 3 (81-108) 1.5 n and band 4 (112-139) 2 n (rounded down, mod 256), every pinned line
    of the text rows showing its text at that band's scroll. So each of the four IRQs came on its own
    line, frame after frame, and each band got its own handler's scroll.
    origin: design"""
    res = demo([["frames", 120], ["buttons", B], ["frames", 64], ["frame"]])
    assert res.get("ok"), loads(res, "nesdoug's 33_MMC3")
    f = decode_frame(res["out"][0])
    chr_bytes = demo_chr()
    found = [n for n in range(256)
             if all(line_text_matches(f, y, 1, "BANK1", chr_bytes, shift=n) for y in range(50, 56))]
    assert found and 60 <= found[0] <= 64, f"band 2 (row 6, 'BANK1') isn't scrolled 60-64 pixels: {found[:4]}"
    n = found[0]
    shifts = {1: (n * 0x80 >> 8) & 0xFF, 2: n & 0xFF, 3: (n * 0x180 >> 8) & 0xFF, 4: (n * 0x200 >> 8) & 0xFF}
    bad = text_lines_shown(f, shifts)
    assert not bad, f"with band scrolls {shifts}: {len(bad)} text lines don't show their text, first {bad[:4]}"
