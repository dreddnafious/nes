"""I7: mapper 1 where its consumers meet it: the CLI's `run` and `screenshot` (CONTRACT.md,
CONTRACT-I2.md), the page (CONTRACT-I3.md: the file input, the canvas, the Reset and Power buttons,
#message, the nesTest hooks) in headless Chromium, and the headless speed bound (J3). The cartridges are
I7's probes (i7_support.picture_probe, whose pictures come from I2's spec renderer), I6's probes for the
discrete boards, and the MMC1 oracles (Holy Mapperel's SKROM builds, Minekart Madness, Nova the Squirrel),
read off the canvas with their own fonts. The canvas is compared by palette index (Decided Q5)."""
import re

import pytest

from conftest import cli
from i2_support import decode_frame, drive_ppu, read_png
from i3_support import Tab, browser, compare_by_index, same_picture, site   # noqa: F401 (fixtures)
from i6_support import LOG, mapperel_font, screen_text
from i6_support import picture_probe as discrete_picture
from i7_support import PRG, Mmc1Probe, ascii_font, chr_of, header, i7_oracle, look, picture_probe, setup

DELTA = pytest.mark.xfail(strict=True, reason="delta")
STEP = 60                 # frames the page runs a probe before its canvas is read (it's done in under 10)
MAPPEREL_FRAMES = 900


def mem_lines(stdout: str) -> dict:
    out = {}
    for m in re.finditer(r"^MEM ([0-9A-Fa-f]{4}): ?((?:[0-9A-Fa-f]{2} ?)*)$", stdout or "", re.M):
        out[int(m.group(1), 16)] = [int(x, 16) for x in m.group(2).split()]
    return out


def test_cli_run_reads_memory_after_mmc1_bank_switching(tmp_path):
    """purpose: `node src/cli/nes.ts run <probe> --frames 30 --mem 0200:1 --mem 0300:n --mem 6000:4` runs
    an MMC1 probe (it loads PRG banks through the serial port, logs reads, and writes its PRG-RAM) and
    prints, for each --mem, the bytes the CPU bus holds: $0200 = $AA (the script finished), the log the
    spec says, and the four PRG-RAM bytes it wrote.
    origin: design"""
    p = Mmc1Probe(256)
    setup(p, 0x0E, prg=15)
    for k, b in enumerate([0, 9, 15, 4]):
        p.load(PRG, b)
        look(p, f"bank {b}", offsets=(0x0000, 0x2FFF))
    for i, v in enumerate([0x12, 0x34, 0x56, 0x78]):
        p.write(0x6000 + i, v)
    p.end()
    want = p.expected_log()
    path = p.write_to(tmp_path, "probe.nes")
    r = cli("run", path, "--frames", 30, "--mem", "0200:1", "--mem", f"{LOG:04X}:{len(want)}", "--mem", "6000:4")
    assert r.returncode == 0, f"`run` refused or crashed on a mapper-1 file: {r!r}"
    mem = mem_lines(r.stdout)
    assert mem.get(0x0200) == [0xAA], f"the probe didn't finish: {r.stdout[:300]!r}"
    assert mem.get(LOG) == want, f"the log differs from the spec's: {mem.get(LOG)} vs {want}"
    assert mem.get(0x6000) == [0x12, 0x34, 0x56, 0x78], f"PRG-RAM reads {mem.get(0x6000)}"


def test_cli_screenshot_shows_the_mmc1_picture(tmp_path):
    """purpose: `node src/cli/nes.ts screenshot <probe> --frames 30 --out f.png` on an MMC1 picture probe
    (background from 4 KiB CHR-ROM bank 22, one-screen mirroring on page 1) writes a 256x240 PNG that is
    the spec's picture through one palette: each palette index one colour, distinct indices distinct
    colours, on all 240 lines.
    origin: design"""
    p = picture_probe(22, 1)
    want = p.expected_frame()
    out = tmp_path / "shot.png"
    r = cli("screenshot", p.write_to(tmp_path, "picture.nes"), "--frames", 30, "--out", out)
    assert r.returncode == 0 and out.exists(), f"`screenshot` refused or crashed on a mapper-1 file: {r!r}"
    w, h, px = read_png(out)
    rgb = [(a << 16) | (b << 8) | c for a, b, c in px]
    assert (w, h) == (256, 240)
    c = compare_by_index(rgb, want)
    assert c["mismatched"] == 0 and not c["merged_indices"], c


MK_CREDITS = {9: "           CREATED BY", 12: "    DESIGN,CODE  MATT HUGHSON"}     # screen_boot.h
MK_TITLE = {27: "            PUSH START"}                                           # screen_title.h
MK_BAR = {2: "   SCORE    HISCORE     TIME"}                                        # draw_statusbar_ppu_off()


def mk_rows(rgb, want: dict) -> dict:
    font = ascii_font(chr_of(i7_oracle("minekart-madness").read_bytes()), 0x0000)
    lines = screen_text(rgb, font)
    return {r: lines[r] for r in want}


def test_the_page_plays_an_mmc1_game_from_its_credits_into_play_and_restarts_it(site, browser):
    """purpose: J1 steps 1-4 and 7 for an MMC1 game: the page loads Minekart Madness (SKROM) through
    #rom-file instead of refusing it (#message hidden or empty); its canvas shows the credits at frame 60
    and the title ("PUSH START") at frame 240, read off the canvas with the game's own font; Enter
    (Start) twice, through the keyboard, gets it into play (the status bar's "SCORE  HISCORE  TIME");
    Reset brings the credits back 60 frames later, and Power does too; the page loaded once and raised
    no error.
    origin: design"""
    with Tab(browser, site) as tab:
        tab.pause()
        tab.load(str(i7_oracle("minekart-madness")))
        msg = tab.message()
        assert not msg, f"the page refused the mapper-1 file: {msg!r}"
        tab.step(60)
        assert mk_rows(tab.canvas(), MK_CREDITS) == MK_CREDITS, f"no credits at frame 60: {mk_rows(tab.canvas(), MK_CREDITS)}"
        tab.step(180)
        assert mk_rows(tab.canvas(), MK_TITLE) == MK_TITLE, f"no title at frame 240: {mk_rows(tab.canvas(), MK_TITLE)}"
        for after in (95, 120):
            tab.down("Enter")
            tab.step(5)
            tab.up("Enter")
            tab.step(after)
        assert mk_rows(tab.canvas(), MK_BAR) == MK_BAR, f"Enter twice didn't start play: {mk_rows(tab.canvas(), MK_BAR)}"
        for button in ("reset", "power"):
            tab.click(button)
            tab.step(60)
            assert mk_rows(tab.canvas(), MK_CREDITS) == MK_CREDITS, f"{button} didn't bring the credits back"
            tab.step(180)
            for after in (95, 120):
                tab.down("Enter")
                tab.step(5)
                tab.up("Enter")
                tab.step(after)
        assert tab.loaded_once(), "the page reloaded"
        assert not tab.errors, tab.errors


def test_switching_between_mmc1_and_the_discrete_boards_leaves_nothing_behind(site, browser, tmp_path):
    """purpose: J2 steps 2-4 across mappers (J2d, J2i): in one page, an MMC1 game (4 KiB CHR bank 5,
    one-screen B), then a vertically mirrored UxROM game (I6), then an MMC1 game showing CHR bank 26 on
    one-screen A, then an AxROM game showing VRAM page 1, then the first MMC1 game again: each shows
    exactly its own spec picture 60 frames after its load, as on a fresh console (no MMC1 register,
    shift-register bit, bank, page or mirroring of the game before), and the page loaded once.
    origin: design"""
    games = [("MMC1", picture_probe(5, 1)), ("UxROM", discrete_picture(2, 5)), ("MMC1", picture_probe(0x1A, 0)),
             ("AxROM", discrete_picture(7, 1)), ("MMC1", picture_probe(5, 1))]
    with Tab(browser, site) as tab:
        tab.pause()
        for k, (name, g) in enumerate(games):
            tab.load(g.write_to(tmp_path, f"game{k}.nes"))
            assert tab.frames() == 0 and not tab.message(), f"game {k} ({name}) didn't load clean: {tab.message()!r}"
            tab.step(STEP)
            rgb = tab.canvas()
            assert same_picture(rgb, g.expected_frame()), f"game {k} ({name}): {compare_by_index(rgb, g.expected_frame())}"
        assert tab.loaded_once(), "the page reloaded"
        assert not tab.errors, tab.errors


def mapperel_wram(rgb) -> str:
    lines = [ln.strip() for ln in screen_text(rgb, mapperel_font()) if ln.strip()]
    return next((ln for ln in lines if "PRG RAM" in ln), f"<no PRG-RAM line: {lines[:6]}>")


def test_battery_prg_ram_survives_power_on_the_page_but_not_a_switch(site, browser, tmp_path):
    """purpose: Decided Q3 on an MMC1 board, read by the cartridge itself: Holy Mapperel writes the tag
    "SAVEDATA" into its PRG-RAM at the end of its test and, on its next start, shows " + BATTERY" if the
    tag survived (README "WRAM test"). On the page: the SKROM build with battery-backed PRG-RAM shows
    "8K PRG RAM 0K" after its first 900 frames, and "8K PRG RAM 0K + BATTERY" 900 frames after Power (the
    save kept across a power cycle of the same game); the same build without the battery bit shows no
    " + BATTERY" after Power (a fresh console, as at load); and after another game is loaded in between,
    the battery build starts with no save again (cleared on a ROM switch).
    origin: design"""
    battery = str(i7_oracle("holy-mapperel/M1_P128K_C128K_S8K"))
    plain = str(i7_oracle("holy-mapperel/M1_P128K_C128K_W8K"))
    other = picture_probe(5, 1).write_to(tmp_path, "other.nes")
    with Tab(browser, site) as tab:
        tab.pause()
        tab.load(battery)
        assert not tab.message(), f"the page refused the mapper-1 file: {tab.message()!r}"
        tab.step(MAPPEREL_FRAMES)
        assert mapperel_wram(tab.canvas()) == "8K PRG RAM 0K", f"first run: {mapperel_wram(tab.canvas())!r}"
        tab.click("power")
        tab.step(MAPPEREL_FRAMES)
        assert mapperel_wram(tab.canvas()) == "8K PRG RAM 0K + BATTERY", f"after Power: {mapperel_wram(tab.canvas())!r}"
        tab.load(plain)
        tab.step(MAPPEREL_FRAMES)
        tab.click("power")
        tab.step(MAPPEREL_FRAMES)
        assert mapperel_wram(tab.canvas()) == "8K PRG RAM 0K", f"no battery, after Power: {mapperel_wram(tab.canvas())!r}"
        tab.load(other)
        tab.step(STEP)
        tab.load(battery)
        tab.step(MAPPEREL_FRAMES)
        assert mapperel_wram(tab.canvas()) == "8K PRG RAM 0K", f"after a switch and back: {mapperel_wram(tab.canvas())!r}"
        assert not tab.errors, tab.errors


def test_a_still_unsupported_mapper_is_refused_by_number_while_an_mmc1_game_runs(site, browser, tmp_path):
    """purpose: J1a after I7: with an MMC1 game running on the page, picking a file for mapper 105
    (NES-EVENT, an MMC1 relative I7 doesn't build) shows #message naming 105, and the MMC1 game keeps
    running (its frame count climbs by 45+ over 1.5 s) with its picture on the canvas. No page error.
    origin: design"""
    game = picture_probe(5, 1)
    bad = tmp_path / "mapper105.nes"
    bad.write_bytes(header(0x20000, 0x8000, mapper=105, nes2=False) + picture_probe(5, 1).image()[16:])
    with Tab(browser, site) as tab:
        tab.load(game.write_to(tmp_path, "mmc1.nes"))
        tab.page.wait_for_timeout(1000)
        assert not tab.message(), f"the page refused the MMC1 game: {tab.message()!r}"
        assert same_picture(tab.canvas(), game.expected_frame()), "the MMC1 game isn't showing its picture"
        before = tab.frames()
        tab.load(str(bad))
        tab.page.wait_for_timeout(1500)
        msg = tab.message()
        assert re.search(r"\b105\b", msg), f"#message is {msg!r}: it doesn't name mapper 105"
        assert tab.frames() >= before + 45, "the running game stopped or restarted"
        assert same_picture(tab.canvas(), game.expected_frame()), "the MMC1 picture is gone"
        assert not tab.errors, tab.errors


def test_headless_core_on_an_mmc1_cartridge_runs_at_least_five_times_real_time():
    """purpose: J3's ">= 5x real time" holds on a real-size MMC1 cartridge: 1,800 frames (30 s of NTSC
    time) of Nova the Squirrel (256 KiB PRG, SXROM; its NMI switches PRG banks every frame for its music)
    take at most 6.0 s of wall time, and its last frame shows its title (5+ colours).
    origin: design"""
    res = drive_ppu({"mode": "bench", "rom": str(i7_oracle("nova-the-squirrel")), "frames": 1800}, timeout=120)
    assert res.get("ok"), f"the core didn't run it: {({k: v for k, v in res.items() if k != 'frame'})}"
    colours = len(set(decode_frame(res["frame"])))
    assert res["ms"] <= 6000 and colours >= 5, f"{res['ms']:.0f} ms for 1800 frames; {colours} colours"
