"""I2's hand-built scenes: small NROM programs that put a known picture up through the PPU registers,
the way a game does (see i2_support.Scene). Each is seeded, so its picture is the same on every run.
Sprite tables: with $2000 bit 3 set, 8x8 sprites come from $1000, where tile 1 is solid and tile 2 a
single top-left pixel (i2_support.make_chr)."""
import random

from i2_support import Scene, make_chr, oam_of, random_nametable, random_palette

CHR = make_chr(2002)
SOLID_BG = 1                 # background tile 1 is solid colour 3
SOLID_SP, DOT_SP = 1, 2      # sprite tiles at $1000: solid colour 3; one pixel at the top-left corner


def _palette(seed):
    return (0x3F00, random_palette(random.Random(seed)))


def _clear_nametable():
    """A nametable of tile 0 (transparent), so only the backdrop and the sprites show."""
    return bytes(960) + bytes(random.Random(7).randrange(256) for _ in range(64))


def scene_background():
    """Two random nametables (vertical mirroring), unscrolled, background only, left column shown."""
    rng = random.Random(11)
    return Scene(chr=CHR, uploads=[(0x2000, random_nametable(rng)), (0x2400, random_nametable(rng)),
                                   _palette(12)], mask=0x0A)


def scene_scroll():
    """The same two nametables scrolled by (77, 45): fine X 5, coarse X 9, fine Y 5, coarse Y 5, so the
    picture wraps into $2400 on the right and (vertical mirroring) back into $2000 at the bottom."""
    s = scene_background()
    s.scroll = (77, 45)
    return s


def scene_horizontal():
    """Horizontal mirroring: $2000 and $2C00 hold different tables ($2400 is $2000, $2800 is $2C00);
    the picture starts in $2400 ($2000 bits 0-1 = 1) scrolled by (100, 200), so it crosses all four."""
    rng = random.Random(21)
    return Scene(chr=CHR, uploads=[(0x2000, random_nametable(rng)), (0x2C00, random_nametable(rng)),
                                   _palette(22)], ctrl=0x01, scroll=(100, 200), mask=0x0A,
                 mirroring="horizontal")


def scene_four():
    """Four-screen (header byte 6 bit 3): four different nametables, scrolled by (128, 120) so a quarter
    of each is on screen."""
    rng = random.Random(31)
    return Scene(chr=CHR, uploads=[(a, random_nametable(rng)) for a in (0x2000, 0x2400, 0x2800, 0x2C00)]
                 + [_palette(32)], scroll=(128, 120), mask=0x0A, mirroring="four")


def scene_sprites():
    """8x8 sprites over a transparent background: at Y = 0 (lines 1-8), Y = 238 (line 239 only), Y = 239
    and Y = 255 (not on screen), X = 252 (half off the right edge), X = 0 (left column shown), each flip,
    each palette; never more than 8 on a line."""
    rng = random.Random(41)
    sprites = [(0, 3, 0x00, 20), (238, 4, 0x01, 40), (239, 5, 0x02, 60), (255, 6, 0x03, 80),
               (50, 7, 0x40, 100), (50, 7, 0x80, 120), (50, 7, 0xC0, 140), (50, 7, 0x00, 160),
               (100, 8, 0x01, 252), (120, DOT_SP, 0x02, 0), (120, DOT_SP, 0xC3, 30)]
    for i in range(12):
        sprites.append((140 + 8 * (i // 4) + rng.randrange(4), rng.randrange(3, 256), rng.randrange(256) & 0xC3,
                        rng.randrange(0, 240)))
    return Scene(chr=CHR, uploads=[(0x2000, _clear_nametable()), _palette(42)], oam=oam_of(sprites),
                 ctrl=0x08, mask=0x1E)


def scene_priority():
    """Sprites over a random background: sprite 1 (solid, behind the background) under sprite 2 (in
    front, lower priority by index); one in front alone; one behind alone. Where 1 and 2 overlap, 1 wins
    the sprite choice by its lower index, so the background shows through 2 wherever the background is
    opaque (NESdev "PPU sprite priority")."""
    rng = random.Random(51)
    sprites = [(150, 5, 0x00, 160), (60, SOLID_SP, 0x21, 40), (60, 3, 0x02, 44), (90, 3, 0x01, 80),
               (90, 4, 0x22, 120), (90, SOLID_SP, 0x23, 200)]
    return Scene(chr=CHR, uploads=[(0x2000, random_nametable(rng)), _palette(52)], oam=oam_of(sprites),
                 ctrl=0x08, mask=0x1E)


def scene_tall():
    """8x16 sprites ($2000 bit 5): even tile numbers from $0000, odd from $1000, the top half tile N & $FE
    and the bottom half the next one; vertical flip swaps the halves as well as flipping each."""
    sprites = [(40, 0x10, 0x00, 30), (40, 0x11, 0x00, 60), (40, 0x23, 0x80, 90), (40, 0x24, 0x40, 120),
               (40, 0x35, 0xC1, 150), (100, 0x47, 0x82, 30), (100, 0x48, 0x03, 60), (230, 0x51, 0x00, 90)]
    return Scene(chr=CHR, uploads=[(0x2000, _clear_nametable()), _palette(62)], oam=oam_of(sprites),
                 ctrl=0x20, mask=0x1E)


def scene_eight():
    """Nine solid sprites on lines 101-108 (sprites 0-8, X = 10, 34, ... 202) and eight on lines 151-158
    (sprites 9-16): only the first eight in OAM order are drawn on a line, so sprite 8 at X = 202 is not."""
    sprites = [(100, SOLID_SP, i & 3, 10 + 24 * i) for i in range(9)] + \
              [(150, SOLID_SP, i & 3, 10 + 24 * i) for i in range(8)]
    return Scene(chr=CHR, uploads=[(0x2000, _clear_nametable()), _palette(72)], oam=oam_of(sprites),
                 ctrl=0x08, mask=0x1E)


def scene_clip(mask):
    """A random background with sprites at X = 0, 4 and 8 down the left edge, under the given $2001."""
    rng = random.Random(81)
    sprites = [(30 + 20 * i, SOLID_SP if i % 2 else rng.randrange(3, 256), i & 3, x)
               for i, x in enumerate((0, 4, 8, 0, 4, 8, 2, 6))]
    return Scene(chr=CHR, uploads=[(0x2000, random_nametable(rng)), _palette(82)], oam=oam_of(sprites),
                 ctrl=0x08, mask=mask)


def scene_mixed(mask=0x1E):
    """A busy picture: a random background (two nametables, scrolled by (13, 7)) and 64 random sprites,
    through a random palette of 31 colours."""
    rng = random.Random(91)
    sprites = [(rng.randrange(0, 240), rng.randrange(256), rng.randrange(256) & 0xE3, rng.randrange(256))
               for _ in range(64)]
    return Scene(chr=CHR, uploads=[(0x2000, random_nametable(rng)), (0x2400, random_nametable(rng)),
                                   _palette(92)], oam=oam_of(sprites), ctrl=0x08, scroll=(13, 7), mask=mask)


def scene_backdrop_mirror():
    """The palette written $3F00-$3F1F with $3F00 = $21, then $3F10 written again with $15: $3F10 is the
    same byte as $3F00, so the backdrop shows $15. The top half is transparent tiles, so the backdrop
    covers it."""
    rng = random.Random(101)
    nt = bytearray(random_nametable(rng))
    nt[:480] = bytes(480)
    pal = bytearray(random_palette(random.Random(102)))
    pal[0] = pal[16] = 0x21
    return Scene(chr=CHR, uploads=[(0x2000, bytes(nt)), (0x3F00, bytes(pal)), (0x3F10, bytes([0x15]))],
                 mask=0x0A)


SPLIT_SPRITE_Y = 99
SPLIT_X = 64
# J4a, from the spec: a sprite with OAM Y = 99 has its top row on line 100 (sprites are drawn one line
# below their Y). Its first pixel there overlaps an opaque background pixel (a solid tile at column 1,
# row 12), so the hit flag sets early on line 100. The game's poll-and-write loop takes well under 100
# CPU cycles (< 300 dots), so its $2005 write lands on line 100 before dot 257, where the PPU copies the
# horizontal scroll from t to v. Line 101 is the first drawn with the new X scroll; line 100 isn't.
SPLIT_ROW = SPLIT_SPRITE_Y + 2


def scene_split():
    """A status bar split by sprite-0 hit: sprite 0 (solid, behind the background, so unseen) at
    (8, 99) over a solid background tile; after it hits, the game sets X scroll 64; its NMI puts 0 back."""
    rng = random.Random(111)
    nt0 = bytearray(random_nametable(rng))
    nt0[12 * 32 + 1] = SOLID_BG
    sprites = [(SPLIT_SPRITE_Y, SOLID_SP, 0x20, 8)]
    return Scene(chr=CHR, uploads=[(0x2000, bytes(nt0)), (0x2400, random_nametable(rng)), _palette(112)],
                 oam=oam_of(sprites), ctrl=0x08, mask=0x1E, split=(SPLIT_X,))


def scene_power_on():
    """Nothing written to the palette or OAM: the program reads all 32 palette bytes ($2007 at $3F00-$3F1F)
    into $0300-$031F and all 256 OAM bytes ($2004, with $2003 set before each) into $0400-$04FF, then
    marks $0500 = $5A."""
    return Scene(chr=CHR, uploads=[], readback=True, mask=0x00)


def same_colour(i: int):
    """Colour indices that standard 2C02 palettes commonly draw alike (the blacks of columns D-F, $20/$30
    white, $2D/$00 and $3D/$10 greys) share a class; any other index is its own."""
    if i in (0x0D, 0x0E, 0x0F, 0x1D, 0x1E, 0x1F, 0x2E, 0x2F, 0x3E, 0x3F):
        return "black"
    return {0x20: 0x30, 0x2D: 0x00, 0x3D: 0x10}.get(i, i)
