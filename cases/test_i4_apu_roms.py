"""I4: the APU test ROMs (data: i4_apu_roms.yaml; each case there carries its own promise and delta mark).

The $6000 ROMs run through the frozen I1 driver (cases/drive.ts), which reads blargg's protocol itself
(and presses Reset when a ROM asks). The screen-only ROMs run through I2's frozen cases/drive_ppu.ts for a
fixed number of frames, and the case reads their result off the core's frame, by palette index, with the
font the ROM itself loads: the build never grades its own test results (J4d)."""
from pathlib import Path

import pytest
import yaml

from conftest import drive
from i2_support import decode_frame, drive_ppu, read_screen, short, text_lines
from i4_support import font_apu_2005, font_console, i4_oracle

DELTA = pytest.mark.xfail(strict=True, reason="delta")
DATA = yaml.safe_load((Path(__file__).parent / "i4_apu_roms.yaml").read_text())["cases"]
MAX_FRAMES = 3600            # 60 s of emulated time; these ROMs report within a few seconds
SCREEN_FRAMES = 900          # 15 s: the screen-only ROMs finish well inside it
WHITE = 0x30                 # both shells' palettes: $0F behind, $30 text


def params(kind):
    return [pytest.param(c, id=c["id"], marks=[DELTA] if c.get("delta") is True else [])
            for c in DATA if c["kind"] == kind]


@pytest.mark.parametrize("case", params("blargg"))
def test_blargg_apu_rom_reports_passed(case):
    """purpose: each blargg APU, interrupt or DMA ROM, run headless until it reports, signs
    $6001-$6003 with DE B0 61, ends with status $00, and its text at $6004 says "Passed". A $81 status
    is answered with a Reset press after at least 6 frames, as the ROM asks.
    origin: design"""
    res = drive({"mode": "blargg", "rom": str(i4_oracle(case["id"])), "maxFrames": MAX_FRAMES},
                timeout=240)
    assert res.get("ok") and res.get("done"), f"{case['id']}: no result: {res}"
    assert res["signature"] is True
    assert res["status"] == 0, f"{case['id']}: status {res['status']}: {res['text']!r}"
    assert "passed" in res["text"].lower(), res["text"]


def screen(case, font_of) -> list:
    path = i4_oracle(case["id"])
    font = font_of(path.read_bytes())
    res = drive_ppu({"mode": "frames", "rom": str(path), "frames": SCREEN_FRAMES}, timeout=180)
    assert res.get("ok"), f"{case['id']}: no frame: {short(res)}"
    pixels = decode_frame(res["frame"])
    return [ln.strip() for ln in text_lines(read_screen(pixels, font, lambda v: (v & 0x3F) == WHITE))]


@pytest.mark.parametrize("case", params("apu2005"))
def test_blargg_apu_2005_rom_shows_result_code_1(case):
    """purpose: each blargg_apu_2005.07.30 ROM, run for 900 frames, shows its result code as "$01"
    ("A result code of 1 always indicates that all tests were passed", tests.txt), read off the core's
    frame cell by cell with the glyphs the ROM loads into CHR-RAM (a pixel is lit when it is $30).
    origin: design"""
    lines = screen(case, font_apu_2005)
    assert "$01" in lines, f"{case['id']}: the screen reads {lines!r}"


@pytest.mark.parametrize("case", params("console"))
def test_dmc_dma_rom_prints_its_hardware_result(case):
    """purpose: each dmc_dma_during_read4 ROM, run for 900 frames, prints its verdict on screen (read
    with the ROM's own ascii.chr font): "Passed" for the tests that check their own CRC, or, for the two
    whose output depends on CPU-PPU synchronization, a final CRC that is one of those the source lists
    as a hardware result.
    origin: design"""
    lines = screen(case, font_console)
    if "accept" in case:
        assert lines and lines[-1] in case["accept"], \
            f"{case['id']}: the last line is {lines[-1:]!r}, not one of {case['accept']}: {lines!r}"
    else:
        assert "Passed" in lines, f"{case['id']}: the screen reads {lines!r}"
