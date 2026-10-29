"""I2: the picture out. The core's 256x240 frame (by palette index) and the CLI's PNG screenshot show a
test ROM's on-screen text legibly, checked against the ROM's own data: the font is the ROM's CHR-ROM
(blargg's shell: "Requires ASCII font in CHR", tile number = ASCII code), the colours are the ones its
console.s loads ($0F black behind, CONSOLE_COLOR $30 white), and the words are the ones it writes at
$6004. No reference image from any emulator is used. Variants J1c and J1t."""
import re

import pytest

from conftest import cli
from i2_support import (H, W, decode_frame, drive_ppu, font_from_chr, i2_oracle, read_png, read_screen,
                        scene_rom, short, text_lines, two_colours, write_rom)
from i2_scenes import same_colour, scene_mixed

DELTA = pytest.mark.xfail(strict=True, reason="delta")
ROM = "ppu_vbl_nmi/01-vbl_basics"
FRAME = 200                   # fixed frame N: the ROM reports at about frame 145 and then sits still
BLACK, WHITE = 0x0F, 0x30


def rom_text(peeked: list) -> str:
    return bytes(peeked).split(b"\0", 1)[0].decode("latin-1")


def expected_lines(text: str) -> list:
    return [ln for ln in text.splitlines() if ln.strip()]


def font():
    data = i2_oracle(ROM).read_bytes()
    prg = data[4] * 0x4000
    return font_from_chr(data[16 + prg:16 + prg + 0x2000])


@DELTA
def test_core_frame_shows_the_roms_text_by_palette_index():
    """purpose: at fixed frame 200 of ppu_vbl_nmi 01-vbl_basics, the core's frame holds only the
    console's two colour indices ($0F and $30), and read cell by cell with the ROM's CHR font it shows
    the lines the ROM wrote at $6004 ("01-vbl_basics", "Passed"), each starting in column 1 (the
    console's left margin).
    origin: design"""
    res = drive_ppu({"mode": "frames", "rom": str(i2_oracle(ROM)), "frames": FRAME,
                     "peek": [[0x6004, 512]]})
    assert res.get("ok"), short(res)
    want = expected_lines(rom_text(res["peek"]["6004"]))
    assert want and want[-1] == "Passed", f"the ROM itself didn't finish: {want!r}"
    pixels = decode_frame(res["frame"])
    colours = {v & 0x3F for v in pixels}
    screen = read_screen(pixels, font(), lambda v: (v & 0x3F) == WHITE)
    got = [ln[1:] for ln in text_lines(screen)]
    assert colours == {BLACK, WHITE} and got == want, \
        f"colour indices {sorted(colours)}; screen reads {text_lines(screen)!r}, want {want!r}"
    assert all(ln.startswith(" ") and ln[1] != " " for ln in text_lines(screen)), text_lines(screen)


@DELTA
def test_screenshot_png_is_256x240_and_shows_the_text(tmp_path):
    """purpose: `nes.ts screenshot <rom> --frames 200 --out f.png` writes a 256x240 PNG (all 240
    lines, none cropped) in which the ROM's text reads, with its own CHR font, as the lines it wrote at
    $6004 ("01-vbl_basics", "Passed"): lit pixels are the ones not in the background's colour.
    origin: design"""
    out = tmp_path / "shot.png"
    r = cli("screenshot", i2_oracle(ROM), "--frames", FRAME, "--out", out)
    assert r.returncode == 0 and out.is_file(), repr(r)
    w, h, pixels = read_png(out)
    assert (w, h) == (W, H), f"{w}x{h}"
    bg, _ = two_colours(pixels)
    screen = text_lines(read_screen(pixels, font(), lambda p: p != bg))
    assert [ln.strip() for ln in screen] == ["01-vbl_basics", "Passed"], screen


@DELTA
def test_screenshot_is_the_core_frame_through_one_palette(tmp_path):
    """purpose: the CLI's PNG after N frames is the core's frame after N frames mapped through one
    palette: over a scene drawn with 31 colour indices, every pixel with the same frame value (colour
    index plus emphasis) has the same RGB, and the image has at least as many distinct RGBs as the
    scene has colours that standard 2C02 palettes keep apart.
    origin: design"""
    s = scene_mixed()
    rom = write_rom(tmp_path, "mixed.nes", scene_rom(s))
    res = drive_ppu({"mode": "frames", "rom": rom, "frames": 8})
    assert res.get("ok"), short(res)
    frame = decode_frame(res["frame"])
    out = tmp_path / "mixed.png"
    r = cli("screenshot", rom, "--frames", 8, "--out", out)
    assert r.returncode == 0 and out.is_file(), repr(r)
    w, h, pixels = read_png(out)
    assert (w, h) == (W, H), f"{w}x{h}"
    seen = {}
    for v, rgb in zip(frame, pixels):
        seen.setdefault(v, set()).add(rgb)
    split = {f"{v:03X}": sorted(c) for v, c in seen.items() if len(c) > 1}
    floor = len({same_colour(v & 0x3F) for v in seen})
    distinct = len(set(pixels))
    assert len(seen) >= 24 and not split and distinct >= floor, \
        f"{len(seen)} frame values, {distinct} RGBs (floor {floor}); values with more than one RGB: {split}"


@DELTA
def test_screenshot_without_out_is_refused_with_a_usage_message(tmp_path):
    """purpose: `nes.ts screenshot <rom>` with no --out exits non-zero with a message naming --out on
    stderr, not a stack trace (as the I1 CLI refuses bad options).
    origin: design"""
    r = cli("screenshot", i2_oracle(ROM), "--frames", 2)
    assert r.returncode not in (0, None), repr(r)
    assert "--out" in r.stderr, r.stderr
    assert not re.search(r"^\s+at .+:\d+:\d+\)?$", r.stderr, re.M), r.stderr
