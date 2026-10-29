"""I6: the mapper test ROMs and the homebrew on mappers 2 and 3, headless through the core API.

- Holy Mapperel (data: i6_mapper_roms.yaml) detects the board from its behaviour and tests every bank;
  its result screen is read with the font from its own CHR-ROM build, so the build never grades itself.
- Cheril the Goddess (The Mojon Twins, CNROM, fetched by hash): what its screens say comes from its own
  source (MK1_NES examples/05_cheril_the_goddess_v1/dev: pres.h, game.c, assets/cuts_text.h), read off the
  frame with its own font from the CHR bank its source selects (`bankswitch (n)`).
- The 240p Test Suite (PinoBatch, UNROM, GPL-2.0-or-later, fetched by hash): boots to its About page, and
  its menu answers the pad (B leaves the page, Down and Up move the cursor and back, A opens a test
  pattern and B returns), each step a round trip a broken board can't fake.
J1/J2 reach: "vendored homebrew on these mappers boots and plays" (roadmap I6)."""
import hashlib
import re
from pathlib import Path

import pytest
import yaml

from i2_support import decode_frame, diff_count
from i6_support import drive_i6, goddess_font, i6_oracle, loads, mapperel_font, screen_text, short

DELTA = pytest.mark.xfail(strict=True, reason="delta")
DATA = yaml.safe_load((Path(__file__).parent / "i6_mapper_roms.yaml").read_text())["cases"]
MAPPEREL_FRAMES = 900          # 15 s: its CHR-RAM test buzzes for a few seconds, then the screen stays

START, A, B, UP, DOWN, LEFT, RIGHT = 0x08, 0x01, 0x02, 0x10, 0x20, 0x40, 0x80


def params():
    return [pytest.param(c, id=c["id"], marks=[DELTA] if c.get("delta") is True else []) for c in DATA]


@pytest.mark.parametrize("case", params())
def test_holy_mapperel_detects_the_board_and_every_bank_reads_back(case):
    """purpose: each Holy Mapperel ROM, run for 900 frames, shows its result screen with: the mapper number
    and board its header says, the PRG ROM size, the CHR line ending "OK", and "DETAILED TEST RESULT:
    0000" (README: "Zero is normal"). The screen is read with the ROM's own font ('0' and 'O' share one
    glyph, so letters O read as 0). Each line is one of the case's `purpose` items in i6_mapper_roms.yaml.
    origin: design"""
    res = drive_i6(i6_oracle(case["id"]), [["frames", MAPPEREL_FRAMES], ["frame"]], timeout=240)
    assert res.get("ok"), loads(res, case["id"])
    lines = [ln.strip() for ln in screen_text(decode_frame(res["out"][0]), mapperel_font()) if ln.strip()]
    assert any(re.fullmatch(case["board"], ln) for ln in lines), f"{case['id']}: no board line; screen {lines[:6]}"
    assert case["prg"] in lines, f"{case['id']}: no {case['prg']!r} line; screen {lines[:6]}"
    assert case["chr"] in lines, f"{case['id']}: no {case['chr']!r} line; screen {lines[:6]}"
    assert "DETAILED TEST RESULT: 0000" in lines, f"{case['id']}: screen {lines[:6]}"


# ---------------------------------------------------------------- Cheril the Goddess (CNROM)

CREDITS = {22: "       CHERIL THE GODDESS", 24: "COPYLEFT 2015 BY THE MOJON TWINS",
           25: "DESIGN & PORT BY THE MOJON TWINS", 26: "   NESLIB & SFX CODE BY SHIRU"}     # pres.h credits()
TITLE = {21: "          PRESS START!", 27: "    (C) 2015 THE MOJON TWINS"}                  # pres.h title()
STORY = {16: " INSIDE THE OLIMPO CHICO (PRO-"}                    # cuts_text.h intro_text, line 1, typed out


def rows_read(pixels, font, want: dict) -> dict:
    lines = screen_text(pixels, font)
    return {r: lines[r] for r in want}


def frames_of(res) -> list:
    return [decode_frame(o) for o in res["out"] if isinstance(o, str)]


def shows(frames, font, want: dict) -> bool:
    """Every wanted row reads exactly so in at least one of the frames (the game's logo sprite can pass
    over a row for a moment)."""
    return all(any(rows_read(f, font, {r: t})[r] == t for f in frames) for r, t in want.items())


def test_cheril_the_goddess_boots_to_its_credits_and_title_from_chr_bank_1():
    """purpose: J1 steps 2-3 on a CNROM homebrew game: Cheril the Goddess powers on to its credits
    (pres.h credits(): bankswitch (1), then "CHERIL THE GODDESS" on row 22 and three more lines, shown
    for up to 300 frames), then its title (title(): bankswitch (1), "PRESS START!" on row 21 and
    "(C) 2015 THE MOJON TWINS" on row 27). Each row reads exactly, with the font from CHR bank 1 (tile =
    character - 32), by frame 120 and by frame 600. Bank 0's tiles don't spell them, so a board that
    ignored the switch fails.
    origin: design"""
    res = drive_i6(i6_oracle("cheril-the-goddess"),
                   [s for f in range(30, 121, 10) for s in (["frames", 10 if f > 30 else 30], ["frame"])]
                   + [["frames", 480], ["frame"]])
    assert res.get("ok"), loads(res, "Cheril the Goddess")
    fr = frames_of(res)
    font = goddess_font(1)
    assert shows(fr[:-1], font, CREDITS), f"no credits by frame 120: {rows_read(fr[-2], font, CREDITS)}"
    assert shows(fr[-1:], font, TITLE), f"no title at frame 600: {rows_read(fr[-1], font, TITLE)}"


def goddess_play(extra: list) -> dict:
    """Power on, Start on the title at frame 600, Start again to skip the story, A past the manual,
    then 200 frames into the game; `extra` steps follow."""
    steps = [["frames", 600], ["buttons", START], ["frames", 10], ["buttons", 0], ["frames", 90],
             ["buttons", START], ["frames", 10], ["buttons", 0], ["frames", 90],
             ["buttons", A], ["frames", 10], ["buttons", 0], ["frames", 200], ["frame"]]
    return drive_i6(i6_oracle("cheril-the-goddess"), steps + extra)


def test_cheril_the_goddess_plays_start_tells_the_story_and_the_pad_moves_her():
    """purpose: J1 step 4 on a CNROM game: Start on the title starts the story (main(): bankswitch (2),
    cutscene (intro_text)), whose first line types out "INSIDE THE OLIMPO CHICO (PRO-" on row 16, read
    with CHR bank 2's font; then, past the story and the manual, in play, holding Right for 60 frames
    changes the picture against the same run with no press, and the no-press run is the same twice
    (frame-indexed input, J1e).
    origin: design"""
    story = drive_i6(i6_oracle("cheril-the-goddess"),
                     [["frames", 600], ["buttons", START], ["frames", 10], ["buttons", 0]]
                     + [s for _ in range(12) for s in (["frames", 30], ["frame"])])
    assert story.get("ok"), loads(story, "Cheril the Goddess")
    font = goddess_font(2)
    assert shows(frames_of(story), font, STORY), \
        f"Start didn't lead to the story: row 16 reads {rows_read(frames_of(story)[-1], font, STORY)}"
    still1 = goddess_play([["frames", 60], ["frame"]])
    still2 = goddess_play([["frames", 60], ["frame"]])
    right = goddess_play([["buttons", RIGHT], ["frames", 60], ["frame"]])
    assert still1.get("ok") and still2.get("ok") and right.get("ok"), loads(right, "Cheril the Goddess")
    a, a2, r = frames_of(still1)[-1], frames_of(still2)[-1], frames_of(right)[-1]
    assert a == a2, "two runs with the same input differ"
    assert diff_count(a, r) >= 500, f"holding Right changed only {diff_count(a, r)} pixels"


def test_cheril_the_goddess_restarts_on_reset():
    """purpose: J1 step 7 on a CNROM game: Reset in the middle of play goes back through the game's reset
    code to its credits (main() starts with credits()), drawn from CHR bank 1 again, as at power-on.
    origin: design"""
    res = goddess_play([["reset"]] + [s for _ in range(10) for s in (["frames", 10], ["frame"])])
    assert res.get("ok"), loads(res, "Cheril the Goddess")
    fr = frames_of(res)[1:]
    font = goddess_font(1)
    assert shows(fr, font, CREDITS), f"no credits within 100 frames of Reset: {rows_read(fr[-1], font, CREDITS)}"


# ---------------------------------------------------------------- the 240p Test Suite (UNROM)

def digest(pixels) -> str:
    return hashlib.sha256(bytes(str(pixels), "ascii")).hexdigest()[:12]


def test_240p_test_suite_boots_and_its_menu_answers_the_pad():
    """purpose: J1 steps 2-4 on a UNROM homebrew program (64 KiB in four banks, CHR-RAM): the 240p Test
    Suite powers on to a still About page by frame 60 (8+ colours, unchanged at frame 120); B leaves it
    for the menu (a different still picture); Down moves the cursor (the picture changes) and Up brings
    back exactly the menu picture; A opens the first test pattern (a different picture) and B returns to
    exactly the menu picture. Each round trip only holds if the program's code and graphics come from the
    right banks.
    origin: design"""
    steps = [["frames", 60], ["frame"], ["frames", 60], ["frame"],
             ["buttons", B], ["frames", 6], ["buttons", 0], ["frames", 60], ["frame"], ["frames", 30], ["frame"],
             ["buttons", DOWN], ["frames", 6], ["buttons", 0], ["frames", 30], ["frame"],
             ["buttons", UP], ["frames", 6], ["buttons", 0], ["frames", 30], ["frame"],
             ["buttons", A], ["frames", 6], ["buttons", 0], ["frames", 60], ["frame"],
             ["buttons", B], ["frames", 6], ["buttons", 0], ["frames", 60], ["frame"]]
    res = drive_i6(i6_oracle("240pee"), steps)
    assert res.get("ok"), loads(res, "240pee")
    about, about2, menu, menu2, down, up, pattern, back = frames_of(res)
    assert len(set(about)) >= 8, f"the About page has {len(set(about))} colours"
    assert about == about2, "the About page isn't still"
    assert menu == menu2 and menu != about, "B didn't leave the About page for a still menu"
    assert down != menu, "Down didn't move the cursor"
    assert up == menu, f"Up didn't bring the menu back exactly ({diff_count(up, menu)} pixels differ)"
    assert pattern != menu, "A didn't open a test pattern"
    assert back == menu, f"B didn't return to the menu exactly ({diff_count(back, menu)} pixels differ)"


def test_240p_test_suite_restarts_on_reset_as_at_power_on():
    """purpose: J1 step 7 on UNROM: Reset from the menu goes back through the reset vector in the fixed
    bank to the About page, and 60 frames after Reset the picture is the one 60 frames after power-on.
    origin: design"""
    res = drive_i6(i6_oracle("240pee"),
                   [["frames", 60], ["frame"], ["buttons", B], ["frames", 6], ["buttons", 0], ["frames", 60],
                    ["reset"], ["frames", 60], ["frame"]])
    assert res.get("ok"), loads(res, "240pee")
    first, again = frames_of(res)
    assert again == first, f"60 frames after Reset differs from 60 after power-on by {diff_count(again, first)} pixels"
