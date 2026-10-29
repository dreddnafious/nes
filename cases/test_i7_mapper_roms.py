"""I7: the MMC1 test ROMs and the homebrew games on MMC1, headless through the core API.

- Holy Mapperel's MMC1 builds and blargg's MMC1 multi-test builds (data: i7_mapper_roms.yaml; each case
  there carries its own promise and delta mark): Holy Mapperel's result screen is read with the font from
  its own CHR-ROM build, and blargg's $6000 protocol is read by the frozen cases/drive.ts, so the build
  never grades itself.
- Nova the Squirrel (NovaSquirrel, 256 KiB PRG, 8 KiB CHR-RAM, 32 KiB battery-backed PRG-RAM: SXROM;
  fetched by hash): what it does comes from its own source (github.com/NovaSquirrel/NovaTheSquirrel at
  v1.0.6a: src/boot.s, src/screens.s, src/options.s, src/memory.s, src/nova.x). Its text is drawn in a
  variable-width font rendered into CHR-RAM, which a fixed-cell reader can't read, so its screens are
  pinned by what the source says they do (the save tag it writes, the No-WRAM warning, the menu's round
  trips), not by their words.
- Minekart Madness (Matt Hughson, MMC1 build: 128 KiB PRG, 128 KiB CHR-ROM, 8 KiB battery PRG-RAM:
  SKROM; fetched by hash): its screens' text and tiles come from its own source (github.com/mhughson/
  mbh-cart at v1.0: game/main.c, game/PRG0.c, game/NES_ST/screen_*.h), read off the frame with its own
  font (tile = ASCII code, CHR bank 0) and, for the logo and the game-over picture, checked cell by cell
  against the tiles of the CHR bank its source selects (set_chr_bank_0(2) on the title and game over).
J1/J2 reach: "homebrew on MMC1" (roadmap I7)."""
import re
from pathlib import Path

import pytest
import yaml

from conftest import drive
from i2_support import decode_frame, diff_count
from i6_support import drive_i6, loads, mapperel_font, screen_text
from i7_support import A, B, DOWN, START, UP, ascii_font, cell_mismatches, chr_of, i7_oracle

DELTA = pytest.mark.xfail(strict=True, reason="delta")
DATA = yaml.safe_load((Path(__file__).parent / "i7_mapper_roms.yaml").read_text())["cases"]
MAPPEREL_FRAMES = 900          # 15 s: its CHR-RAM test buzzes for a few seconds, then the screen stays
MAX_FRAMES = 3600              # 60 s of emulated time; all_instrs, the longest, needs about 40 s


def params(kind):
    return [pytest.param(c, id=c["id"], marks=[DELTA] if c.get("delta") is True else [])
            for c in DATA if c["kind"] == kind]


def frames_of(res) -> list:
    return [decode_frame(o) for o in res["out"] if isinstance(o, str)]


def mapperel_lines(pixels) -> list:
    return [ln.strip() for ln in screen_text(pixels, mapperel_font()) if ln.strip()]


@pytest.mark.parametrize("case", params("mapperel"))
def test_holy_mapperel_detects_the_mmc1_board_and_every_bank_reads_back(case):
    """purpose: each Holy Mapperel MMC1 build, run for 900 frames, shows its result screen with: mapper
    001 and the board its NES 2.0 sizes make (SLROM, SKROM, SFROM, SJROM, SGROM, SUROM or SXROM), the PRG
    ROM size it read back from the bank tags, its PRG-RAM line (missing, or the size it found and "OK"),
    the CHR line ending "OK", and a detailed result of 0000 (with PRG-RAM, 0000 or 1000: the WRAM digit
    is $E000 bit 4, which MMC1 revisions answer differently). Each line is the case's item in
    i7_mapper_roms.yaml.
    origin: design"""
    res = drive_i6(i7_oracle(case["id"]), [["frames", MAPPEREL_FRAMES], ["frame"]], timeout=240)
    assert res.get("ok"), loads(res, case["id"])
    lines = mapperel_lines(decode_frame(res["out"][0]))
    assert any(re.fullmatch(case["board"], ln) for ln in lines), f"{case['id']}: no board line; screen {lines[:6]}"
    assert case["prg"] in lines, f"{case['id']}: no {case['prg']!r} line; screen {lines[:6]}"
    assert case["wram"] in lines, f"{case['id']}: no {case['wram']!r} line; screen {lines[:6]}"
    assert case["chr"] in lines, f"{case['id']}: no {case['chr']!r} line; screen {lines[:6]}"
    assert any(re.fullmatch(case["detail"], ln) for ln in lines), f"{case['id']}: screen {lines[:6]}"


@pytest.mark.parametrize("case", params("blargg"))
def test_blargg_multi_test_rom_on_mmc1_reports_all_passed(case):
    """purpose: each blargg multi-test build (an MMC1 cartridge holding every test of its suite, one per
    PRG bank, reporting through PRG-RAM at $6000), run headless until it reports, signs $6001-$6003 with
    DE B0 61, ends with status $00, and its text ends "All N tests passed".
    origin: design"""
    res = drive({"mode": "blargg", "rom": str(i7_oracle(case["id"])), "maxFrames": MAX_FRAMES}, timeout=240)
    assert res.get("ok") and res.get("done"), f"{case['id']}: no result: {res}"
    assert res["signature"] is True
    assert res["status"] == 0, f"{case['id']}: status {res['status']}: {res['text']!r}"
    assert f"All {case['tests']} tests passed" in res["text"], res["text"]


# ---------------------------------------------------------------- Nova the Squirrel (SXROM)

SAVE_TAG = 0x7F00                       # memory.s: segment "SAVE" at $7F00 (nova.x); SaveTag is its first 9 bytes
SAVE_TAG_STRING = list(b"squirrel") + [2]  # boot.s: SaveTagString: .byt "squirrel", 2


def nova(steps: list, rom=None) -> dict:
    return drive_i6(rom or i7_oracle("nova-the-squirrel"), steps, timeout=240)


def press(button: int, after=40) -> list:
    return [["buttons", button], ["frames", 6], ["buttons", 0], ["frames", after], ["frame"]]


@DELTA
def test_nova_the_squirrel_boots_to_its_title_and_keeps_its_save_tag_in_prg_ram():
    """purpose: J1 steps 2-3 on an SXROM game: Nova the Squirrel's reset code (boot.s) resets the MMC1,
    loads its control and CHR registers (InitMapper), clears $6000-$7DFF of its PRG-RAM, and writes its
    save tag "squirrel", 2 at $7F00 (SaveTag, in the battery-backed "SAVE" segment) when it isn't there;
    then ShowTitle puts up a still title. By frame 60 the tag reads back at $7F00 through the core's
    peek, and the title is up (5+ colours) and the same at frames 60, 120 and 180; after Reset the tag is
    still there and 120 frames later the title is the same picture again.
    origin: design"""
    res = nova([["frames", 60], ["frame"], ["peek", SAVE_TAG, 9], ["frames", 60], ["frame"], ["frames", 60], ["frame"],
                ["reset"], ["peek", SAVE_TAG, 9], ["frames", 120], ["frame"], ["peek", SAVE_TAG, 9]])
    assert res.get("ok"), loads(res, "Nova the Squirrel")
    t60, t120, t180, again = frames_of(res)
    tags = [o for o in res["out"] if isinstance(o, list)]
    assert tags[0] == SAVE_TAG_STRING, f"$7F00 holds {bytes(tags[0])!r}, not the game's save tag"
    assert len(set(t60)) >= 5 and t60 == t120 == t180, f"no still title by frame 60 ({len(set(t60))} colours)"
    assert tags[1] == tags[2] == SAVE_TAG_STRING, f"Reset lost the save tag: {tags[1:]}"
    assert again == t60, f"after Reset the title differs by {diff_count(again, t60)} pixels"


@DELTA
def test_nova_the_squirrel_warns_on_its_title_only_when_the_header_gives_no_prg_ram(tmp_path):
    """purpose: the NES 2.0 header's PRG-RAM size is the cartridge's (NESdev "MMC1": "All other SxROM
    variants are denoted by their functional PRG/CHR-ROM/RAM sizes in the NES 2.0 header"): ShowTitle
    checks the save tag at $7F00 and, if it isn't there, draws "No WRAM, so game won't work!" over the
    title (screens.s). The same file with header byte 10 set to 0 (no PRG-RAM) shows a title that
    differs from the real file's, where the tag is found and no warning is drawn.
    origin: design"""
    data = bytearray(i7_oracle("nova-the-squirrel").read_bytes())
    assert data[10] == 0x90, "the pinned file's header gives 32 KiB of PRG-NVRAM"
    data[10] = 0
    bare = tmp_path / "nova-no-prg-ram.nes"
    bare.write_bytes(bytes(data))
    real = nova([["frames", 120], ["frame"], ["peek", SAVE_TAG, 9]])
    without = nova([["frames", 120], ["frame"]], rom=bare)
    assert real.get("ok") and without.get("ok"), loads(without if real.get("ok") else real, "Nova the Squirrel")
    assert real["out"][1] == SAVE_TAG_STRING, f"$7F00 holds {bytes(real['out'][1])!r}"
    a, b = frames_of(real)[0], frames_of(without)[0]
    assert diff_count(a, b) >= 100, \
        f"the title with no PRG-RAM differs by only {diff_count(a, b)} pixels: no No-WRAM warning, or one on both"


@DELTA
def test_nova_the_squirrel_start_opens_its_menu_and_the_pad_moves_through_it():
    """purpose: J1 step 4 on an SXROM game: Start on the title opens the main menu (ShowMainMenu, in
    another PRG bank: "Start the game!", "View controls", "Game options", "Extra features"); Down moves
    the cursor twice (each a different picture) and Up twice brings back exactly the first menu picture;
    A on "Start the game!" opens the level select (a different picture, drawn from the save's
    LevelAvailable bits) and B brings back exactly the menu. Each round trip only holds if the code and
    the tiles come from the right PRG banks and the save is read from the PRG-RAM.
    origin: design"""
    res = nova([["frames", 120], ["frame"]] + press(START, 60) + press(DOWN) + press(DOWN) + press(UP) + press(UP)
               + press(A, 90) + press(B, 60))
    assert res.get("ok"), loads(res, "Nova the Squirrel")
    title, menu, down1, down2, up1, up2, levels, back = frames_of(res)
    assert menu != title, "Start didn't leave the title"
    assert down1 != menu and down2 != down1, "Down didn't move the cursor"
    assert up1 == down1, f"Up didn't step back ({diff_count(up1, down1)} pixels differ)"
    assert up2 == menu, f"Up twice didn't bring the menu back exactly ({diff_count(up2, menu)} pixels differ)"
    assert levels != menu and len(set(levels)) >= 5, "A didn't open the level select"
    assert back == menu, f"B didn't return to the menu exactly ({diff_count(back, menu)} pixels differ)"


# ---------------------------------------------------------------- Minekart Madness (SKROM)

CREDITS = {9: "           CREATED BY", 12: "    DESIGN,CODE  MATT HUGHSON", 14: "            ART  HALLER ZOLTAN",
           16: "      MUSIC,SFX  TUI", 18: "   MAPPER28 API  NORILL"}                       # screen_boot.h
TITLE = {27: "            PUSH START"}                                                      # screen_title.h row 27
RULES = {8: "         YOU HAVE 2:30!", 13: "             GATHER", 18: "       UNTIL TIME EXPIRES",
         23: "           GOOD LUCK!"}                                                       # screen_rules.h
BAR = {2: "   SCORE    HISCORE     TIME"}                                                   # draw_statusbar_ppu_off()
# The title's logo, screen_title.h rows 4-7 from column 7: tiles from CHR bank 2's first 4 KiB.
LOGO = {r: (7, list(range(0x20 + 16 * (r - 4), 0x30 + 16 * (r - 4))) + [0x80 + 16 * (r - 4), 0x81 + 16 * (r - 4)])
        for r in range(4, 8)}
# The game-over picture, screen_gameover.h rows 4-8 from column 7 (bank_bg(1): CHR bank 2's second 4 KiB).
GAME_OVER = {4: (9, list(range(0x01, 0x0E))), 5: (9, list(range(0x11, 0x1E))),
             6: (7, [0x0D, 0x0E, 0x0F] + list(range(0x22, 0x2C)) + [0x5D, 0x5E, 0x5F]),
             7: (7, [0x1D, 0x1E, 0x1F] + list(range(0x32, 0x3C)) + [0x6D, 0x6E, 0x6F]),
             8: (7, [0x2D, 0x2E, 0x2F] + list(range(0x42, 0x4C)) + [0x7D, 0x7E, 0x7F])}
TIME = re.compile(r"(\d\d):(\d\d):(\d\d)$")


def minekart(steps: list, timeout=240) -> dict:
    return drive_i6(i7_oracle("minekart-madness"), steps, timeout=timeout)


def mk_chr() -> bytes:
    return chr_of(i7_oracle("minekart-madness").read_bytes())


def rows_read(pixels, want: dict) -> dict:
    lines = screen_text(pixels, ascii_font(mk_chr(), 0x0000))
    return {r: lines[r] for r in want}


def shows(frames, want: dict) -> bool:
    return any(rows_read(f, want) == want for f in frames)


def to_gameplay() -> list:
    """Power on; the credits give way to the title after 2 s (main.c, STATE_BOOT); Start on the title
    (frame 240), Start on the rules (frame 340), then 120 frames into play."""
    return [["frames", 240], ["buttons", START], ["frames", 5], ["buttons", 0], ["frames", 95],
            ["buttons", START], ["frames", 5], ["buttons", 0], ["frames", 120]]


@DELTA
def test_minekart_madness_boots_to_its_credits_and_its_title_from_chr_bank_2():
    """purpose: J1 steps 2-3 on an SKROM game (CHR-ROM in 4 KiB banks): Minekart Madness powers on to its
    credits (load_screen_boot: screen_boot's "CREATED BY", "DESIGN,CODE  MATT HUGHSON" and three more
    rows, in the font of CHR bank 0, tile = ASCII), then after 2 s its title (go_to_state(STATE_TITLE):
    set_chr_bank_0(2), screen_title): the logo's 72 cells (rows 4-7) are CHR bank 2's tiles in the
    places screen_title.h gives, cell for cell, and row 27 reads "PUSH START". The credits read by frame
    90, the title by frame 240. A board that left CHR bank 0 in place draws letters where the logo is.
    origin: design"""
    res = minekart([["frames", 60], ["frame"], ["frames", 30], ["frame"], ["frames", 150], ["frame"]])
    assert res.get("ok"), loads(res, "Minekart Madness")
    f60, f90, f240 = frames_of(res)
    assert shows([f60, f90], CREDITS), f"no credits by frame 90: {rows_read(f90, CREDITS)}"
    assert rows_read(f240, TITLE) == TITLE, f"no title at frame 240: row 27 reads {rows_read(f240, TITLE)}"
    bad = cell_mismatches(f240, mk_chr(), 2 * 0x2000, LOGO)
    assert not bad, f"{len(bad)} of 72 logo cells aren't CHR bank 2's tiles, first {bad[:6]}"


@DELTA
def test_minekart_madness_plays_its_rules_its_clock_and_the_pad():
    """purpose: J1 step 4 on an SKROM game: Start on the title shows the rules (screen_rules: "YOU HAVE
    2:30!", "GATHER", "UNTIL TIME EXPIRES", "GOOD LUCK!"); Start again starts play, with the status bar
    "SCORE  HISCORE  TIME" on row 2 (draw_statusbar_ppu_off) and a clock on row 3 that counts down from
    02:30:00 (two readings a second apart, the later one smaller); holding A (the jump) for 30 frames
    changes the picture against the same run with no press, and the run with no press is the same twice
    (frame-indexed input, J1e).
    origin: design"""
    rules = minekart([["frames", 240], ["buttons", START], ["frames", 5], ["buttons", 0], ["frames", 95], ["frame"]])
    assert rules.get("ok"), loads(rules, "Minekart Madness")
    assert rows_read(frames_of(rules)[0], RULES) == RULES, f"Start didn't show the rules: {rows_read(frames_of(rules)[0], RULES)}"
    still1 = minekart(to_gameplay() + [["frame"], ["frames", 60], ["frame"], ["frames", 30], ["frame"]])
    still2 = minekart(to_gameplay() + [["frame"], ["frames", 60], ["frame"], ["frames", 30], ["frame"]])
    jump = minekart(to_gameplay() + [["frame"], ["frames", 60], ["frame"], ["buttons", A], ["frames", 30], ["frame"]])
    assert still1.get("ok") and still2.get("ok") and jump.get("ok"), loads(jump, "Minekart Madness")
    p0, p1, p2 = frames_of(still1)
    assert rows_read(p0, BAR) == BAR, f"no status bar in play: {rows_read(p0, BAR)}"
    clocks = []
    for f in (p0, p1):
        m = TIME.search(screen_text(f, ascii_font(mk_chr(), 0x0000))[3])
        assert m, f"no clock on row 3: {screen_text(f, ascii_font(mk_chr(), 0x0000))[3]!r}"
        clocks.append(tuple(int(x) for x in m.groups()))
    assert clocks[0] <= (2, 30, 0) and clocks[1] < clocks[0], f"the clock didn't count down: {clocks}"
    assert frames_of(still2)[-1] == p2, "two runs with the same input differ"
    assert diff_count(frames_of(jump)[-1], p2) >= 50, f"holding A changed only {diff_count(frames_of(jump)[-1], p2)} pixels"


@DELTA
def test_minekart_madness_ends_when_its_time_runs_out_and_returns_to_its_title():
    """purpose: J1 steps 6-7 on an SKROM game: left alone in play, the clock runs out (2:30, 9,000 frames:
    timer_expired, kill_player), the player falls, and the game ends on its GAME OVER screen
    (load_screen_gameover: set_chr_bank_0(2), bank_bg(1), screen_gameover), whose picture is CHR bank 2's
    upper 4 KiB tiles where screen_gameover.h puts them, cell for cell, within 9,600 frames of play
    starting; A there brings back the title ("PUSH START"); and Reset brings back the credits, as at
    power-on.
    origin: design"""
    steps = (to_gameplay() + [["frames", 8400]] + [s for _ in range(20) for s in (["frames", 60], ["frame"])]
             + [["buttons", A], ["frames", 5], ["buttons", 0], ["frames", 120], ["frame"],
                ["reset"], ["frames", 60], ["frame"]])
    res = minekart(steps, timeout=420)
    assert res.get("ok"), loads(res, "Minekart Madness")
    chr_rom = mk_chr()
    *play, title, credits = frames_of(res)
    over = [k for k, f in enumerate(play) if not cell_mismatches(f, chr_rom, 2 * 0x2000 + 0x1000, GAME_OVER)]
    assert over, "no GAME OVER screen within 9,600 frames of play (the clock didn't run out, or it's drawn from other tiles)"
    assert over[-1] == len(play) - 1, "the GAME OVER screen didn't stay until a button was pressed"
    assert rows_read(title, TITLE) == TITLE, f"A on GAME OVER didn't bring the title back: {rows_read(title, TITLE)}"
    assert rows_read(credits, CREDITS) == CREDITS, f"Reset didn't bring the credits back: {rows_read(credits, CREDITS)}"
