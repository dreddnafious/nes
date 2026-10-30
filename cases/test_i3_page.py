"""I3: the page, served locally and driven the way a person meets it (J1 steps 1-4 and 7): the file input,
the canvas, the Reset and Power buttons, and what a refused file does to a game that's running. Run in
headless Chromium (Playwright) against `node src/web/serve.ts` on a free port (CONTRACT-I3.md).

The game is nes15 (NROM-128, BSD, fetched by hash: see roms/LICENSES.md). Its title is checked against the picture
built from the author's own published name table and palettes (i3_support.nes15_title), by palette index.
The Reset and Power promises are checked on boot_probe, a ROM the case builds from the spec."""
import pytest

from conftest import cli
from i2_support import read_png
from i3_support import (NES15_TITLE_FRAME, Tab, boot_probe, boot_read, browser, compare_by_index, nes15,
                        nes15_title, same_picture, site, write)

DELTA = pytest.mark.xfail(strict=True, reason="delta")
TITLE_SECONDS = 5.0        # J1: "the title screen renders within 5 s of loading"



def is_title(title):
    return lambda rgb: same_picture(rgb, title)


def left_title(title):
    """A picture that isn't the title and isn't blank (4+ colours): the game has moved on."""
    return lambda rgb: not same_picture(rgb, title) and len(set(rgb)) >= 4


def test_the_page_is_served_with_its_screen_file_input_and_buttons(site, browser):
    """purpose: J1 step 1, "open the page (served locally)": `node src/web/serve.ts --port N` answers
    GET / with the page, which loads without a page error and has a file input #rom-file, a 256x240
    canvas #screen (every line, Q5), and the two buttons Decided for step 7, #reset and #power.
    origin: design"""
    with Tab(browser, site) as tab:
        assert tab.has("input#rom-file[type=file]"), "no <input type=file id=rom-file>"
        size = tab.page.evaluate("() => { const c = document.querySelector('canvas#screen');"
                                 " return c ? [c.width, c.height] : null; }")
        assert size == [256, 240], f"canvas#screen's backing store is {size}, not 256x240"
        assert tab.has("button#reset") and tab.has("button#power"), "no <button id=reset> and <button id=power>"
        assert not tab.errors, tab.errors


def test_j1_a_game_boots_to_its_title_starts_and_restarts_without_a_reload(site, browser):
    """purpose: J1 steps 1-4 and 7 in one sitting, through the page's real entry points only: open the
    page; give nes15 to the file input; its title (the author's own picture, by palette index) is on the
    canvas within 5 s; Enter (Start) leaves the title for the game; Reset brings the title back within
    5 s; Enter starts it again; Power brings the title back within 5 s; and the page has loaded exactly
    once, with no page error.
    origin: design"""
    title = nes15_title()
    with Tab(browser, site) as tab:
        tab.load(str(nes15()))
        took, rgb = tab.wait_for(is_title(title), TITLE_SECONDS)
        assert took is not None, \
            f"no title within {TITLE_SECONDS} s of loading: {rgb and compare_by_index(rgb, title)}"
        tab.press("Enter")
        assert tab.wait_for(left_title(title), 3.0)[0] is not None, "Enter (Start) didn't leave the title"
        tab.click("reset")
        assert tab.wait_for(is_title(title), TITLE_SECONDS)[0] is not None, \
            f"Reset didn't bring the title back within {TITLE_SECONDS} s"
        tab.press("Enter")
        assert tab.wait_for(left_title(title), 3.0)[0] is not None, "Enter didn't start the game after Reset"
        tab.click("power")
        assert tab.wait_for(is_title(title), TITLE_SECONDS)[0] is not None, \
            f"Power didn't bring the title back within {TITLE_SECONDS} s"
        assert tab.loaded_once(), f"the page navigated {tab.navigations} times, or lost its state"
        assert not tab.errors, tab.errors


def test_the_title_is_on_the_canvas_by_its_stated_frame_and_not_before_the_game_draws_it(site, browser):
    """purpose: J1c/J1d: at nes15's stated title frame (60, well inside the 5 s, about 300 frames) the
    canvas is the author's title, index for index on all 240 lines; at frame 1, before the game has
    turned rendering on, it is not. So the canvas shows the emulated frame, not a stale picture.
    origin: design"""
    title = nes15_title()
    with Tab(browser, site) as tab:
        tab.pause()
        tab.load(str(nes15()))
        assert tab.step(1) == 1
        early = tab.canvas()
        assert not same_picture(early, title), "the title is up at frame 1, before the game has drawn it"
        assert tab.step(NES15_TITLE_FRAME - 1) == NES15_TITLE_FRAME
        rgb = tab.canvas()
        assert same_picture(rgb, title), f"frame {NES15_TITLE_FRAME} isn't the title: {compare_by_index(rgb, title)}"


def test_the_canvas_is_the_cli_screenshot_pixel_for_pixel(site, browser, tmp_path):
    """purpose: Q5's "one standard 2C02 palette" across the two pictures out: the canvas at nes15's
    frame 60 is, RGB for RGB on all 61,440 pixels, the PNG `node src/cli/nes.ts screenshot --frames 60`
    writes: the same core, the same frame numbering, the same palette, nothing cropped or smoothed.
    origin: design"""
    out = tmp_path / "title.png"
    r = cli("screenshot", nes15(), "--frames", NES15_TITLE_FRAME, "--out", out)
    assert r.returncode == 0, repr(r)
    w, h, png = read_png(out)
    want = [(p[0] << 16) | (p[1] << 8) | p[2] for p in png]
    with Tab(browser, site) as tab:
        tab.pause()
        tab.load(str(nes15()))
        tab.step(NES15_TITLE_FRAME)
        rgb = tab.canvas()
    diffs = [(i % 256, i // 256, f"{g:06X}", f"{e:06X}") for i, (g, e) in enumerate(zip(rgb, want)) if g != e]
    assert (w, h) == (256, 240) and not diffs, f"{len(diffs)} pixels differ, first {diffs[:6]}"


def bad_files(tmp_path) -> dict:
    """Files the core refuses (built from the iNES spec, as I1's loader cases are), and a cancelled pick."""
    rom = nes15().read_bytes()
    return {
        "not-nes": write(tmp_path, "readme.txt", b"This is a text file, not an NES ROM.\n" * 40),
        "truncated": write(tmp_path, "truncated.nes", rom[:16 + 0x2000]),
        "mapper-30": write(tmp_path, "mapper30.nes", rom[:6] + bytes([0xE1, 0x10]) + rom[8:]),
        "cancelled": None,
    }


@pytest.mark.parametrize("kind", ["not-nes", "truncated", "mapper-30", "cancelled"])
def test_a_refused_file_leaves_the_running_game_running(site, browser, tmp_path, kind):
    """purpose: the axes choice for a bad file, or a cancelled picker, while a game plays (J1a, J1b):
    with nes15 on its title, picking a non-NES file, a truncated one or a mapper-30 one (or picking
    nothing) leaves nes15 running: its frame count keeps climbing (by 45+ over 1.5 s, and never back
    to 0), its title stays on the canvas, and for a refused file #message is visible with a reason in it.
    No page error.
    origin: design"""
    title = nes15_title()
    path = bad_files(tmp_path)[kind]
    with Tab(browser, site) as tab:
        tab.load(str(nes15()))
        assert tab.wait_for(is_title(title), TITLE_SECONDS)[0] is not None, "nes15 never showed its title"
        before = tab.frames()
        if path is None:
            tab.page.set_input_files("#rom-file", [])
        else:
            tab.load(path)
        tab.page.wait_for_timeout(1500)
        after = tab.frames()
        assert after >= before + 45, f"frames went from {before} to {after} in 1.5 s: the game stopped or restarted"
        rgb = tab.canvas()
        assert same_picture(rgb, title), f"the title is gone: {compare_by_index(rgb, title)}"
        if path is not None:
            assert tab.message(), "no visible #message saying why the file was refused"
        assert not tab.errors, tab.errors


def test_a_refused_file_on_a_fresh_page_starts_nothing_and_names_the_mapper(site, browser, tmp_path):
    """purpose: J1a, "nothing starts": on a fresh page, a mapper-30 file runs no frame (frames stays 0
    and the canvas is unchanged after 1 s), and the visible #message carries the core's reason, which
    names the mapper number 30 (I1 pins the RomError's wording; this only checks that the page passes
    it on: a loose word pin).
    origin: design"""
    path = bad_files(tmp_path)["mapper-30"]
    with Tab(browser, site) as tab:
        tab.hooks()
        before = tab.canvas()
        tab.load(path)
        tab.page.wait_for_timeout(1000)
        assert tab.frames() == 0, f"{tab.frames()} frames ran for a refused file"
        assert tab.canvas() == before, "the canvas changed for a refused file"
        msg = tab.message()
        assert "30" in msg, f"#message is {msg!r}: it doesn't name mapper 30"
        assert not tab.errors, tab.errors


def test_reset_keeps_ram_and_power_cycles_it_but_keeps_the_battery_save(site, browser, tmp_path):
    """purpose: J1 step 7's two buttons (Decided at approval) and Q3: on boot_probe, a load boots with
    RAM clean and both counters at 1; each Reset re-runs the game's reset path with RAM and battery
    PRG-RAM kept (RAM counter 2, 3; battery 2, 3; RAM not clean) and leaves the frame count running;
    Power starts a fresh console at frame 0 (RAM clean, RAM counter 1) whose battery save survives
    (battery counter 4).
    origin: design"""
    rom = write(tmp_path, "boot.nes", boot_probe())
    with Tab(browser, site) as tab:
        tab.pause()
        tab.load(rom)
        tab.step(10)
        assert boot_read(tab.canvas()) == {"ram": 1, "battery": 1, "clean": True}
        for n in (2, 3):
            tab.click("reset")
            frames = tab.frames()
            assert frames == 10 * (n - 1), f"Reset changed the frame count to {frames}"
            tab.step(10)
            got = boot_read(tab.canvas())
            assert got == {"ram": n, "battery": n, "clean": False}, f"after Reset {n - 1}: {got}"
        tab.click("power")
        assert tab.frames() == 0, f"Power left the frame count at {tab.frames()}, not 0"
        tab.step(10)
        got = boot_read(tab.canvas())
        assert got == {"ram": 1, "battery": 4, "clean": True}, f"after Power: {got}"
        assert not tab.errors, tab.errors


def test_a_second_game_loaded_over_a_running_one_starts_clean(site, browser, tmp_path):
    """purpose: J1q (and J2's switch, as far as I3 goes): with nes15 200 frames in, loading boot_probe
    through the same file input powers boot_probe on at frame 0 with clean RAM (none of nes15's RAM
    leaks), a fresh battery save, and a canvas at frame 10 identical to boot_probe's frame 10 on a
    fresh page; the page doesn't reload.
    origin: design"""
    rom = write(tmp_path, "boot.nes", boot_probe())
    with Tab(browser, site) as fresh:
        fresh.pause()
        fresh.load(rom)
        fresh.step(10)
        want = fresh.canvas()
    with Tab(browser, site) as tab:
        tab.pause()
        tab.load(str(nes15()))
        tab.step(200)
        tab.load(rom)
        assert tab.frames() == 0, f"the new game starts at frame {tab.frames()}, not 0"
        tab.step(10)
        rgb = tab.canvas()
        assert boot_read(rgb) == {"ram": 1, "battery": 1, "clean": True}, boot_read(rgb)
        assert rgb == want, "boot_probe's frame 10 after nes15 differs from a fresh page's"
        assert tab.loaded_once() and not tab.errors, (tab.navigations, tab.errors)


@pytest.mark.parametrize("button", ["reset", "power"])
def test_enter_after_clicking_a_button_is_start_not_another_click(site, browser, button):
    """purpose: J1f: in the middle of a game, a click on #reset or #power restarts nes15 at its title;
    pressing Enter then starts the game (the canvas leaves the title within 3 s, and stays off it)
    instead of clicking the focused button again (which would put the title back).
    origin: design"""
    title = nes15_title()
    with Tab(browser, site) as tab:
        tab.load(str(nes15()))
        assert tab.wait_for(is_title(title), TITLE_SECONDS)[0] is not None, "nes15 never showed its title"
        tab.press("Enter")
        assert tab.wait_for(left_title(title), 3.0)[0] is not None, "Enter (Start) didn't leave the title"
        tab.click(button)
        assert tab.wait_for(is_title(title), TITLE_SECONDS)[0] is not None, f"no title after {button}"
        tab.page.wait_for_timeout(300)
        tab.press("Enter")
        took, _ = tab.wait_for(left_title(title), 3.0)
        assert took is not None, f"Enter after clicking {button} didn't start the game"
        tab.page.wait_for_timeout(1000)
        assert not same_picture(tab.canvas(), title), f"Enter clicked {button} again: the title came back"


def test_a_pal_flagged_rom_runs_at_ntsc_behind_a_notice(site, browser, tmp_path):
    """purpose: Q4 (Decided): "NTSC only. PAL-flagged ROMs run at NTSC timing behind a notice": nes15
    with an NES 2.0 header whose byte 12 says PAL (1) still boots to its title within 5 s, and #message
    is visible with a notice while it runs; the same game with byte 12 NTSC (0) shows no message (a
    loose pin: the notice's words are the builder's).
    origin: design"""
    title = nes15_title()
    rom = bytearray(nes15().read_bytes())
    rom[7] = (rom[7] & 0xF3) | 0x08          # NES 2.0
    for region, flagged in ((0, False), (1, True)):
        rom[12] = region
        path = write(tmp_path, f"nes15-region{region}.nes", bytes(rom))
        with Tab(browser, site) as tab:
            tab.load(path)
            assert tab.wait_for(is_title(title), TITLE_SECONDS)[0] is not None, \
                f"region {region}: no title within {TITLE_SECONDS} s"
            notice = tab.message()
            assert bool(notice) == flagged, f"region {region}: #message is {notice!r}"
            assert not tab.errors, tab.errors
