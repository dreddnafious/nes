"""I8: mapper 4 where its consumers meet it: the CLI's `run` and `screenshot` (CONTRACT.md,
CONTRACT-I2.md), the page (CONTRACT-I3.md: the file input, the canvas, the keyboard, the Reset and Power
buttons, #message, the nesTest hooks) in headless Chromium, and the headless speed bound (J3). The
cartridges are I8's probes (whose pictures, IRQ splits included, come from I2's spec renderer at the rows
the wiki's counter gives), I6's and I7's probes for the other boards, Holy Mapperel's mapper-118 build for
the refusal, and nesdoug's 33_MMC3 demo, read off the canvas with its own font. The canvas is compared by
palette index (Decided Q5)."""
import re

import pytest

from conftest import cli
from i2_support import decode_frame, drive_ppu, read_png
from i3_support import Tab, browser, compare_by_index, same_picture, site   # noqa: F401 (fixtures)
from i6_support import LOG
from i6_support import picture_probe as discrete_picture
from i7_support import picture_probe as mmc1_picture
from i8_support import (BANDS, EMPHASISED, SAVE, SAVE_BYTES, TEXT, W, IrqSplit, Mmc3Probe, band_of, battery_probe,
                        chr_of, i8_oracle, line_text_matches, picture_probe, split_probe)

DELTA = pytest.mark.xfail(strict=True, reason="delta")
STEP = 90                 # frames the page runs a probe before its canvas is read (it's done in under 10)


def mem_lines(stdout: str) -> dict:
    out = {}
    for m in re.finditer(r"^MEM ([0-9A-Fa-f]{4}): ?((?:[0-9A-Fa-f]{2} ?)*)$", stdout or "", re.M):
        out[int(m.group(1), 16)] = [int(x, 16) for x in m.group(2).split()]
    return out


def flat(p: Mmc3Probe) -> list:
    """The IRQ split's frame, every row pinned (a scroll-only split)."""
    rows = p.irq_frame()
    assert all(r is not None for r in rows)
    return [v for r in rows for v in r]


@DELTA
def test_cli_run_reads_memory_after_mmc3_bank_switching(tmp_path):
    """purpose: `node src/cli/nes.ts run <probe> --frames 30 --mem 0200:1 --mem 0300:n --mem 6000:4` runs
    an MMC3 probe (it switches R6 and R7 in both PRG modes, logs reads, and writes its PRG-RAM after $A001
    = $80) and prints, for each --mem, the bytes the CPU bus holds: $0200 = $AA (the script finished), the
    log the spec says, and the four PRG-RAM bytes it wrote.
    origin: design"""
    p = Mmc3Probe(256, chr_kib=32)
    p.banks()
    p.write(0xA001, 0x80)
    for k, (mode, b6, b7) in enumerate([(0, 0, 9), (1, 31, 4), (0, 17, 30), (1, 2, 3)]):
        p.bank_mode = mode << 6
        p.select(6, b6)
        p.select(7, b7)
        for a in (0x8000, 0xA123, 0xDFFF):
            p.read(a, f"mode {mode}, R6 {b6}, R7 {b7}")
    for i, v in enumerate([0x12, 0x34, 0x56, 0x78]):
        p.write(0x6000 + i, v)
    p.end()
    want = p.expected_log()
    path = p.write_to(tmp_path, "probe.nes")
    r = cli("run", path, "--frames", 30, "--mem", "0200:1", "--mem", f"{LOG:04X}:{len(want)}", "--mem", "6000:4")
    assert r.returncode == 0, f"`run` refused or crashed on a mapper-4 file: {r!r}"
    mem = mem_lines(r.stdout)
    assert mem.get(0x0200) == [0xAA], f"the probe didn't finish: {r.stdout[:300]!r}"
    assert mem.get(LOG) == want, f"the log differs from the spec's: {mem.get(LOG)} vs {want}"
    assert mem.get(0x6000) == [0x12, 0x34, 0x56, 0x78], f"PRG-RAM reads {mem.get(0x6000)}"


@DELTA
def test_cli_screenshot_shows_the_mmc3_irq_split(tmp_path):
    """purpose: `node src/cli/nes.ts screenshot <probe> --frames 30 --out f.png` on an MMC3 status-bar
    probe (latch 100: lines 0-100 at X scroll 0, lines 101 down at X 144) writes a 256x240 PNG that is the
    spec's split picture through one palette: each palette index one colour, distinct indices distinct
    colours, on all 240 lines.
    origin: design"""
    p = split_probe(IrqSplit(100, xa=0, xb=144))
    want = flat(p)
    out = tmp_path / "shot.png"
    r = cli("screenshot", p.write_to(tmp_path, "split.nes"), "--frames", 30, "--out", out)
    assert r.returncode == 0 and out.exists(), f"`screenshot` refused or crashed on a mapper-4 file: {r!r}"
    w, h, px = read_png(out)
    rgb = [(a << 16) | (b << 8) | c for a, b, c in px]
    assert (w, h) == (256, 240)
    c = compare_by_index(rgb, want)
    assert c["mismatched"] == 0 and not c["merged_indices"], c


def demo_text_bad(rgb, shifts) -> list:
    chr_bytes = chr_of(i8_oracle("nesdoug-33_MMC3").read_bytes())
    bad = []
    for row, text in TEXT.items():
        for y in range(row * 8, row * 8 + 8):
            if any(y in r for r in BANDS.values()) and not line_text_matches(
                    rgb, y, 1, text, chr_bytes, mask=0xFFFFFF, shift=shifts[band_of(y)]):
                bad.append((y, text))
    return bad


@DELTA
def test_the_page_plays_the_mmc3_demo_scrolls_its_bands_with_b_and_restarts_it(site, browser):
    """purpose: J1 steps 1-4 and 7, and J4, for an MMC3 program on the page: nesdoug's 33_MMC3 loads through
    #rom-file instead of being refused (#message hidden or empty); at frame 120 its six text rows show on
    the canvas, read with its own font; holding Z (B) through the keyboard for 64 frames scrolls its four
    IRQ bands, band 2 by n pixels (60-64) and bands 1, 3 and 4 by n / 2, 1.5 n and 2 n, every pinned text
    line showing its text at its band's scroll; Reset, and then Power, bring the unscrolled text back 120
    frames later; the page loaded once and raised no error.
    origin: design"""
    zero = {1: 0, 2: 0, 3: 0, 4: 0}
    with Tab(browser, site) as tab:
        tab.pause()
        tab.load(str(i8_oracle("nesdoug-33_MMC3")))
        assert not tab.message(), f"the page refused the mapper-4 file: {tab.message()!r}"
        tab.step(120)
        bad = demo_text_bad(tab.canvas(), zero)
        assert not bad, f"at frame 120, {len(bad)} text lines don't show the demo's text, first {bad[:4]}"
        tab.down("KeyZ")
        tab.step(64)
        tab.up("KeyZ")
        rgb = tab.canvas()
        chr_bytes = chr_of(i8_oracle("nesdoug-33_MMC3").read_bytes())
        found = [n for n in range(256) if all(line_text_matches(rgb, y, 1, "BANK1", chr_bytes, mask=0xFFFFFF, shift=n)
                                              for y in range(50, 56))]
        assert found and 60 <= found[0] <= 64, f"holding Z didn't scroll band 2 by 60-64 pixels: {found[:4]}"
        n = found[0]
        shifts = {1: (n * 0x80 >> 8) & 0xFF, 2: n, 3: (n * 0x180 >> 8) & 0xFF, 4: (n * 2) & 0xFF}
        bad = demo_text_bad(rgb, shifts)
        assert not bad, f"band scrolls {shifts}: {len(bad)} text lines wrong, first {bad[:4]}"
        for button in ("reset", "power"):
            tab.click(button)
            tab.step(120)
            bad = demo_text_bad(tab.canvas(), zero)
            assert not bad, f"after {button}, {len(bad)} text lines don't show the unscrolled text, first {bad[:4]}"
        assert tab.loaded_once(), "the page reloaded"
        assert not tab.errors, tab.errors


@DELTA
def test_switching_between_mmc3_and_the_other_boards_leaves_nothing_behind(site, browser, tmp_path):
    """purpose: J2 steps 2-4 across mappers (J2d, J2i, J2s): in one page, an MMC3 game with its IRQ split
    running (latch 100), then an MMC1 game (one-screen B), a vertically mirrored UxROM game, an MMC3 game
    with no IRQ (CHR inversion, horizontal arrangement), an AxROM game, and an MMC3 split game on another
    line (latch 60): each shows exactly its own spec picture 90 frames after its load, as on a fresh
    console (no MMC3 bank, mode, arrangement, latch or counter of the game before, and no stray IRQ), and
    the page loaded once.
    origin: design"""
    games = [("MMC3 split", split_probe(IrqSplit(100, xa=0, xb=144))), ("MMC1", mmc1_picture(5, 1)),
             ("UxROM", discrete_picture(2, 5)), ("MMC3", picture_probe(r0=20, r1=33, chr_inv=1, mirror=1, nt=2)),
             ("AxROM", discrete_picture(7, 1)), ("MMC3 split", split_probe(IrqSplit(60, xa=24, xb=200), seed=4))]
    with Tab(browser, site) as tab:
        tab.pause()
        for k, (name, g) in enumerate(games):
            tab.load(g.write_to(tmp_path, f"game{k}.nes"))
            assert tab.frames() == 0 and not tab.message(), f"game {k} ({name}) didn't load clean: {tab.message()!r}"
            tab.step(STEP)
            want = flat(g) if "split" in name else g.expected_frame()
            rgb = tab.canvas()
            assert same_picture(rgb, want), f"game {k} ({name}): {compare_by_index(rgb, want)}"
        assert tab.loaded_once(), "the page reloaded"
        assert not tab.errors, tab.errors


@DELTA
def test_battery_prg_ram_on_mmc3_survives_reset_and_power_on_the_page_but_not_a_switch(site, browser, tmp_path):
    """purpose: Decided Q3 on an MMC3 board, on the page: the battery probe shows the 32 bytes it finds at
    $6000 on nametable row 12, then saves its own there. After Power, and then Reset, its canvas is the
    spec's picture with the save on row 12 (kept across a power cycle and a soft reset of the same game);
    after another game is loaded and then it again, it shows what it showed at its first load (the save
    cleared on a ROM switch). The same probe without the battery bit shows the save after Reset (RAM is
    kept) but, after Power, its first load's picture again.
    origin: design"""
    saved = {SAVE + i: v for i, v in enumerate(SAVE_BYTES)}
    other = picture_probe().write_to(tmp_path, "other.nes")
    with Tab(browser, site) as tab:
        tab.pause()
        for battery in (True, False):
            p = battery_probe(battery)
            path = p.write_to(tmp_path, f"save-{battery}.nes")
            with_save = p.expected_frame(ram_preset=saved)
            tab.load(path)
            assert not tab.message(), f"the page refused the mapper-4 file: {tab.message()!r}"
            tab.step(60)
            first = tab.canvas()
            assert not same_picture(first, with_save), "a vacuous case: a fresh load already shows the save"
            if battery:
                tab.click("power")
                tab.step(60)
                assert same_picture(tab.canvas(), with_save), f"after Power: {compare_by_index(tab.canvas(), with_save)}"
                tab.click("reset")
                tab.step(60)
                assert same_picture(tab.canvas(), with_save), f"after Reset: {compare_by_index(tab.canvas(), with_save)}"
                tab.load(other)
                tab.step(30)
                tab.load(path)
                tab.step(60)
                assert tab.canvas() == first, "after a switch and back, the save was still there"
            else:
                tab.click("reset")
                tab.step(60)
                assert same_picture(tab.canvas(), with_save), f"no battery, after Reset: {compare_by_index(tab.canvas(), with_save)}"
                tab.click("power")
                tab.step(60)
                assert tab.canvas() == first, "no battery: Power kept the save"
        assert not tab.errors, tab.errors


@DELTA
def test_a_still_unsupported_mapper_is_refused_by_number_while_an_mmc3_game_runs(site, browser, tmp_path):
    """purpose: J1a after I8: with an MMC3 game running on the page, picking Holy Mapperel's TLSROM build
    (mapper 118, an MMC3 on other wiring I8 doesn't build) shows #message naming 118, and the MMC3 game
    keeps running (its frame count climbs by 45+ over 1.5 s) with its picture on the canvas. No page error.
    origin: design"""
    game = picture_probe(r0=14, r1=3)
    with Tab(browser, site) as tab:
        tab.load(game.write_to(tmp_path, "mmc3.nes"))
        tab.page.wait_for_timeout(1000)
        assert not tab.message(), f"the page refused the MMC3 game: {tab.message()!r}"
        assert same_picture(tab.canvas(), game.expected_frame()), "the MMC3 game isn't showing its picture"
        before = tab.frames()
        tab.load(str(i8_oracle("holy-mapperel/M118_P128K_C64K")))
        tab.page.wait_for_timeout(1500)
        msg = tab.message()
        assert re.search(r"\b118\b", msg), f"#message is {msg!r}: it doesn't name mapper 118"
        assert tab.frames() >= before + 45, "the running game stopped or restarted"
        assert same_picture(tab.canvas(), game.expected_frame()), "the MMC3 picture is gone"
        assert not tab.errors, tab.errors


@DELTA
def test_headless_core_on_the_mmc3_demo_runs_at_least_five_times_real_time():
    """purpose: J3's ">= 5x real time" on an MMC3 program that splits its screen four times a frame: 1,800
    frames (30 s of NTSC time) of nesdoug's 33_MMC3 (128 KiB PRG, 128 KiB CHR-ROM, four IRQs a frame, a
    CHR bank switched in two of them, music from a switched PRG bank in every NMI) take at most 6.0 s of
    wall time, and its last frame shows its text and its emphasised lines 81-239.
    origin: design"""
    res = drive_ppu({"mode": "bench", "rom": str(i8_oracle("nesdoug-33_MMC3")), "frames": 1800}, timeout=120)
    assert res.get("ok"), f"the core didn't run it: {({k: v for k, v in res.items() if k != 'frame'})}"
    f = decode_frame(res["frame"])
    chr_bytes = chr_of(i8_oracle("nesdoug-33_MMC3").read_bytes())
    shown = all(line_text_matches(f, y, 1, "BANK0", chr_bytes) for y in range(32, 40))
    loud = all(v >> 6 == 7 for y in EMPHASISED for v in f[y * W:(y + 1) * W])
    assert res["ms"] <= 6000 and shown and loud, f"{res['ms']:.0f} ms for 1800 frames; text {shown}; emphasis {loud}"


@DELTA
def test_headless_core_on_a_512_kib_mmc3_cartridge_with_an_irq_split_runs_at_least_five_times_real_time(tmp_path):
    """purpose: J3's ">= 5x real time" on a real-size MMC3 cartridge (512 KiB PRG, the MMC3's limit, and
    256 KiB CHR-ROM): 1,800 frames of the probe's status bar (an IRQ every frame, a CHR bank and the scroll
    switched in it, the counter clocked on every line) take at most 6.0 s, and the last frame is the
    spec's split, row for row (the line the bank switch lands in unpinned).
    origin: design"""
    p = split_probe(IrqSplit(100, xa=0, xb=80, chr=(0x00, 8, 200)), prg_kib=512, chr_kib=256)
    want = p.irq_frame()
    res = drive_ppu({"mode": "bench", "rom": p.write_to(tmp_path, "big.nes"), "frames": 1800}, timeout=120)
    assert res.get("ok"), f"the core didn't run it: {({k: v for k, v in res.items() if k != 'frame'})}"
    f = decode_frame(res["frame"])
    bad = [y for y, row in enumerate(want) if row is not None and f[y * W:(y + 1) * W] != row]
    assert res["ms"] <= 6000 and not bad, f"{res['ms']:.0f} ms for 1800 frames; {len(bad)} lines off the spec's, first {bad[:6]}"
