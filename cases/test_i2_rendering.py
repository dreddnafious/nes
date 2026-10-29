"""I2: rendering, against pictures the cases put up themselves through the PPU's registers (i2_scenes),
compared index-exact with what the NESdev wiki says the 2C02 draws (i2_support.reference_frame). The
frame is read through the core API (cases/drive_ppu.ts), by palette index, never as RGB (Decided, Q5)."""
import pytest

from i2_scenes import (SPLIT_ROW, SPLIT_SPRITE_Y, scene_background, scene_backdrop_mirror, scene_clip,
                       scene_eight, scene_four, scene_horizontal, scene_mixed, scene_priority, scene_scroll,
                       scene_split, scene_sprites, scene_tall)
from i2_support import W, decode_frame, diff_count, drive_ppu, first_diffs, reference_frame, scene_rom, short, write_rom

DELTA = pytest.mark.xfail(strict=True, reason="delta")
FRAMES = 10   # the scene programs have rendering on by about frame 5; frame 10 is a whole, settled frame


def drawn(tmp_path, scene, name, frames=FRAMES):
    res = drive_ppu({"mode": "frames", "rom": write_rom(tmp_path, f"{name}.nes", scene_rom(scene)),
                     "frames": frames})
    assert res.get("ok"), f"{name}: no frame: {short(res)}"
    return decode_frame(res["frame"])


def same(got, want, what):
    n = diff_count(got, want)
    assert n == 0, f"{what}: {n} pixels differ from the spec; first (x, y, got, want): {first_diffs(got, want)}"


@DELTA
def test_background_tiles_and_attributes_are_drawn_as_the_spec_says(tmp_path):
    """purpose: an unscrolled background of random tiles and attributes is drawn index-exact: each 8x8
    cell is its nametable tile's two planes, coloured by the 2-bit palette its attribute byte gives
    its 16x16 quadrant, and colour 0 is the backdrop ($3F00), over all 240 lines.
    origin: design"""
    s = scene_background()
    same(drawn(tmp_path, s, "bg"), reference_frame(s), "background")


@DELTA
def test_fine_and_coarse_scroll_shift_the_background(tmp_path):
    """purpose: $2005 = (77, 45) shifts the picture left 77 and up 45 pixels, fine and coarse parts both,
    wrapping into the nametable to the right at X 256 and below at Y 240 (vertical mirroring), with
    no line or column lost or doubled at either seam.
    origin: design"""
    s = scene_scroll()
    same(drawn(tmp_path, s, "scroll"), reference_frame(s), "scroll (77, 45)")


@DELTA
def test_horizontal_mirroring_maps_the_nametables(tmp_path):
    """purpose: with the header's horizontal mirroring, $2400 shows what was written to $2000 and $2800
    what was written to $2C00, so a picture scrolled across all four shows two tables, each twice.
    origin: design"""
    s = scene_horizontal()
    same(drawn(tmp_path, s, "hmirror"), reference_frame(s), "horizontal mirroring")


@DELTA
def test_four_screen_has_four_nametables(tmp_path):
    """purpose: with the header's four-screen bit, $2000, $2400, $2800 and $2C00 are four separate
    tables, and a picture scrolled by (128, 120) shows a quarter of each.
    origin: design"""
    s = scene_four()
    same(drawn(tmp_path, s, "four"), reference_frame(s), "four-screen")


@DELTA
def test_sprites_show_one_line_below_their_y_and_flip(tmp_path):
    """purpose: an 8x8 sprite with OAM Y = y covers lines y+1 to y+8 (Y = 0 starts on line 1, Y = 238
    shows only on line 239, Y = 239 and 255 not at all), is clipped at the right edge, and attribute
    bits 6 and 7 flip it horizontally and vertically, in the sprite palette bits 0-1 choose.
    origin: design"""
    s = scene_sprites()
    same(drawn(tmp_path, s, "sprites"), reference_frame(s), "8x8 sprites")


@DELTA
def test_sprite_priority_bit_and_the_lower_index_rule(tmp_path):
    """purpose: a sprite with attribute bit 5 shows only where the background is transparent; and where
    sprites overlap, the lowest-indexed opaque one is chosen first and only then weighed against the
    background, so a behind-background sprite hides a higher-indexed front one wherever the
    background is opaque.
    origin: design"""
    s = scene_priority()
    same(drawn(tmp_path, s, "priority"), reference_frame(s), "sprite priority")


@DELTA
def test_8x16_sprites_take_their_table_from_bit_0_and_flip_whole(tmp_path):
    """purpose: with $2000 bit 5, a sprite is 8x16: tile number bit 0 picks $0000 or $1000, the top half
    is tile N & $FE and the bottom the next one, vertical flip turns all 16 rows over, and one at
    Y = 230 shows its first 9 rows (lines 231-239).
    origin: design"""
    s = scene_tall()
    same(drawn(tmp_path, s, "tall"), reference_frame(s), "8x16 sprites")


@DELTA
def test_only_eight_sprites_are_drawn_on_a_line(tmp_path):
    """purpose: with nine sprites on lines 101-108, the first eight in OAM order are drawn and the ninth
    (sprite 8, at X 202) isn't; on lines 151-158, eight sprites are all drawn.
    origin: design"""
    s = scene_eight()
    got = drawn(tmp_path, s, "eight")
    want = reference_frame(s)
    backdrop = want[0]
    assert all(want[y * W + x] == backdrop for y in range(101, 109) for x in range(202, 210))
    same(got, want, "eight sprites per line")


@pytest.mark.parametrize("mask", [0x18, 0x1A, 0x1C], ids=["both-clipped", "bg-shown", "sprites-shown"])
@DELTA
def test_left_column_clipping_follows_the_mask_bits(tmp_path, mask):
    """purpose: in the leftmost 8 pixels, the background shows only when $2001 bit 1 is set and sprites
    only when bit 2 is set; a pixel with neither shows the backdrop; from X 8 on, both show.
    origin: design"""
    s = scene_clip(mask)
    same(drawn(tmp_path, s, f"clip{mask:02x}"), reference_frame(s), f"left clip, $2001 = ${mask:02X}")


@DELTA
def test_greyscale_ands_every_colour_with_30(tmp_path):
    """purpose: with $2001 bit 0 set, every pixel's colour index, background, sprite and backdrop alike,
    is its palette entry AND $30.
    origin: design"""
    s = scene_mixed(mask=0x1F)
    got = drawn(tmp_path, s, "grey")
    assert {v & 0x0F for v in got} == {0}, sorted({f"{v:02X}" for v in got})[:12]
    same(got, reference_frame(s), "greyscale")


@DELTA
def test_emphasis_bits_ride_along_with_every_pixel(tmp_path):
    """purpose: with $2001 = $BE (emphasis bits 5 and 7), every pixel's frame value carries %101 in bits
    6-8 over the colour index it would have without emphasis.
    origin: design"""
    s = scene_mixed(mask=0xBE)
    got = drawn(tmp_path, s, "emphasis")
    assert {v >> 6 for v in got} == {0b101}, sorted({v >> 6 for v in got})
    same(got, reference_frame(s), "emphasis")


@DELTA
def test_3f10_writes_the_backdrop(tmp_path):
    """purpose: $3F10 is the same palette byte as $3F00: writing $15 there after $21 at $3F00 makes the
    backdrop $15 wherever the background is transparent.
    origin: design"""
    s = scene_backdrop_mirror()
    got = drawn(tmp_path, s, "backdrop")
    assert got[0] & 0x3F == 0x15, f"backdrop is ${got[0] & 0x3F:02X}"
    same(got, reference_frame(s), "backdrop mirror")


def split_rows(got, s):
    """Which rows of the drawn frame match the unscrolled picture, and which the scrolled one."""
    plain, moved = reference_frame(s), reference_frame(s, split_row=0)
    rows = []
    for y in range(240):
        g = got[y * W:(y + 1) * W]
        rows.append("0" if g == plain[y * W:(y + 1) * W] else "S" if g == moved[y * W:(y + 1) * W] else "?")
    return "".join(rows)


@DELTA
def test_mid_frame_scroll_split_starts_on_the_line_after_sprite_0_hits(tmp_path):
    """purpose: a game that waits for sprite-0 hit and then writes a new X scroll (a status bar) gets
    the split on the row the spec gives: with sprite 0's OAM Y = 99 the hit is on line 100 and the
    write lands before dot 257, so lines 0-100 are drawn unscrolled and lines 101-239 with the new
    scroll (J4a).
    origin: design"""
    s = scene_split()
    got = drawn(tmp_path, s, "split", frames=20)
    assert SPLIT_ROW == SPLIT_SPRITE_Y + 2 == 101
    want = reference_frame(s, split_row=SPLIT_ROW)
    assert diff_count(got, want) == 0, f"rows (0 = unscrolled, S = scrolled): {split_rows(got, s)}"


@DELTA
def test_the_split_row_holds_for_600_frames(tmp_path):
    """purpose: over 600 consecutive frames of the status-bar loop (frames 21-620), every frame is the
    same picture, the split on row 101 each time (J4: the split doesn't jitter).
    origin: design"""
    s = scene_split()
    res = drive_ppu({"mode": "frames", "rom": write_rom(tmp_path, "split.nes", scene_rom(s)),
                     "frames": 620, "hashFrom": 21}, timeout=180)
    assert res.get("ok"), short(res)
    got = decode_frame(res["frame"])
    distinct = sorted(set(res["hashes"]))
    assert len(res["hashes"]) == 600 and len(distinct) == 1 and \
        diff_count(got, reference_frame(s, split_row=SPLIT_ROW)) == 0, \
        f"{len(distinct)} different frames; the last one's rows: {split_rows(got, s)}"
