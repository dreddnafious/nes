"""I2: the PPU test ROMs (data: i2_ppu_roms.yaml; each case there carries its own promise and delta mark).

The $6000 ROMs run through the frozen I1 driver (cases/drive.ts), which reads blargg's protocol itself.
The screen-only ROMs run through cases/drive_ppu.ts, and the case reads their result off the core's
frame, by palette index, with the ROM's own font: the build never grades its own test results (J4d)."""
from pathlib import Path

import pytest
import yaml

from conftest import drive
from i2_support import decode_frame, drive_ppu, font_2005, i2_oracle, read_screen, short, text_lines

DELTA = pytest.mark.xfail(strict=True, reason="delta")
DATA = yaml.safe_load((Path(__file__).parent / "i2_ppu_roms.yaml").read_text())["cases"]
MAX_FRAMES = 3600            # 60 s of emulated time; the $6000 ROMs here report within about 25 s
SCREEN_MAX_FRAMES = 1800     # the 2005 ROMs finish in a few seconds, beeps included
CONSOLE_READY = 0x07F1       # runtime_rom_common.a: console_ready = $7f1
RESULT = 0x00F8              # validation.a: result = $f8 (1 = passed, 2+ = the failing test's number)
WHITE = 0x30                 # console.a loads its palette as "lda #15 ; bg" and "ldx #48 ; fg"


def params(kind):
    return [pytest.param(c, id=c["id"], marks=[DELTA] if c.get("delta") is True else [])
            for c in DATA if c["kind"] == kind]


@pytest.mark.parametrize("case", params("blargg"))
def test_blargg_ppu_rom_reports_passed(case):
    """Each blargg PPU ROM, run headless until it reports, signs $6001-$6003 with DE B0 61, ends with
    status $00, and its text at $6004 says "Passed".
    origin: design"""
    res = drive({"mode": "blargg", "rom": str(i2_oracle(case["id"])),
                 "maxFrames": case.get("max_frames", MAX_FRAMES)}, timeout=240)
    assert res.get("ok") and res.get("done"), f"{case['id']}: no result: {res}"
    assert res["signature"] is True
    assert res["status"] == 0, f"{case['id']}: status {res['status']}: {res['text']!r}"
    assert "passed" in res["text"].lower(), res["text"]


@pytest.mark.parametrize("case", params("screen"))
def test_screen_only_ppu_rom_shows_passed(case):
    """Each screen-only blargg PPU ROM (2005 runtime), run until its console has printed the result,
    shows the line PASSED on screen: read off the core's 256x240 frame cell by cell, by palette index
    (a pixel is lit when it is the console's white, $30), matched against the glyphs the ROM loads.
    origin: design"""
    path = i2_oracle(case["id"])
    font = font_2005(path.read_bytes())
    res = drive_ppu({"mode": "screen", "rom": str(path), "maxFrames": SCREEN_MAX_FRAMES,
                     "until": {"addr": CONSOLE_READY, "seq": [0xA5, 0x00]}, "settle": 2,
                     "peek": [[RESULT, 1]]}, timeout=180)
    assert res.get("ok"), f"{case['id']}: no frame: {short(res)}"
    assert res["done"], f"{case['id']}: the console never finished printing: {short(res)}"
    pixels = decode_frame(res["frame"])
    screen = text_lines(read_screen(pixels, font, lambda v: (v & 0x3F) == WHITE))
    result = res["peek"]["00F8"][0]
    assert "PASSED" in [ln.strip() for ln in screen], \
        f"{case['id']}: screen reads {screen!r} (result byte ${result:02X})"
