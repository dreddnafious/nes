"""I6: mappers 2, 3 and 7 where their consumers meet them: the CLI's `run` and `screenshot`
(CONTRACT.md, CONTRACT-I2.md), the page (CONTRACT-I3.md: the file input, the canvas, #message, the
nesTest hooks) in headless Chromium, and the headless speed bound (J3). The cartridges are I6's probes
(i6_support.picture_probe), whose pictures come from I2's spec renderer; the canvas is compared by
palette index (Decided Q5)."""
import re

import pytest

from conftest import cli
from i2_support import decode_frame, drive_ppu, read_png
from i3_support import Tab, browser, compare_by_index, same_picture, site   # noqa: F401 (fixtures)
from i6_support import LOG, TABLE, Probe, header, i6_oracle, loads, look, picture_probe

DELTA = pytest.mark.xfail(strict=True, reason="delta")
STEP = 60                 # frames the page runs a probe before its canvas is read (it's done in under 10)


def mem_lines(stdout: str) -> dict:
    out = {}
    for m in re.finditer(r"^MEM ([0-9A-Fa-f]{4}): ?((?:[0-9A-Fa-f]{2} ?)*)$", stdout or "", re.M):
        out[int(m.group(1), 16)] = [int(x, 16) for x in m.group(2).split()]
    return out


@pytest.mark.parametrize("mapper", [pytest.param(2), 3, pytest.param(7)])
def test_cli_run_reads_memory_after_bank_switching(tmp_path, mapper):
    """purpose: `node src/cli/nes.ts run <probe> --frames 30 --mem 0200:3 --mem 0300:n` runs a mapper-2,
    -3 or -7 probe (it switches banks and logs reads) and prints, for each --mem, the bytes the CPU bus
    holds: $0200 = $AA (the script finished), and the log the spec says.
    origin: design"""
    p = Probe(mapper, 32 if mapper == 3 else 128, chr_kib=32 if mapper == 3 else 0)
    n = p.plan.count
    for k, b in enumerate([n - 1, 0, 2, 1]):
        p.select(b, TABLE, TABLE + 256) if k == 0 else p.select(b, salt=k)
        look(p, f"bank {b}", offsets=(0x0000, 0x3FFF))
    p.end()
    want = p.expected_log()
    path = p.write_to(tmp_path, "probe.nes")
    r = cli("run", path, "--frames", 30, "--mem", "0200:1", "--mem", f"{LOG:04X}:{len(want)}")
    assert r.returncode == 0, f"`run` refused or crashed on a mapper-{mapper} file: {r!r}"
    mem = mem_lines(r.stdout)
    assert mem.get(0x0200) == [0xAA], f"the probe didn't finish: {r.stdout[:300]!r}"
    assert mem.get(LOG) == want, f"the log differs from the spec's: {mem.get(LOG)} vs {want}"


@pytest.mark.parametrize("mapper,choice", [pytest.param(2, 3), (3, 2), pytest.param(7, 1)])
def test_cli_screenshot_shows_the_banked_picture(tmp_path, mapper, choice):
    """purpose: `node src/cli/nes.ts screenshot <probe> --frames 30 --out f.png` on a mapper-2, -3 or -7
    picture probe writes a 256x240 PNG that is the spec's picture through one palette: each palette index
    one colour, distinct indices distinct colours, on all 240 lines.
    origin: design"""
    p = picture_probe(mapper, choice)
    want = p.expected_frame()
    out = tmp_path / "shot.png"
    r = cli("screenshot", p.write_to(tmp_path, "picture.nes"), "--frames", 30, "--out", out)
    assert r.returncode == 0 and out.exists(), f"`screenshot` refused or crashed on a mapper-{mapper} file: {r!r}"
    w, h, px = read_png(out)
    rgb = [(a << 16) | (b << 8) | c for a, b, c in px]
    assert (w, h) == (256, 240)
    c = compare_by_index(rgb, want)
    assert c["mismatched"] == 0 and not c["merged_indices"], c


@pytest.mark.parametrize("mapper,choice", [pytest.param(2, 5, marks=DELTA), (3, 3), pytest.param(7, 0, marks=DELTA)])
def test_the_page_plays_a_mapper_2_3_or_7_file(site, browser, tmp_path, mapper, choice):
    """purpose: J1 step 2 for these boards: the page loads a mapper-2, -3 or -7 file through #rom-file
    instead of refusing it (#message hidden or empty), runs it, and after 60 frames its canvas is the
    spec's picture of the banks and page the game chose, index for index. No page error.
    origin: design"""
    p = picture_probe(mapper, choice)
    want = p.expected_frame()
    with Tab(browser, site) as tab:
        tab.pause()
        tab.load(p.write_to(tmp_path, "picture.nes"))
        msg = tab.message()
        assert not msg, f"the page refused the mapper-{mapper} file: {msg!r}"
        assert tab.step(STEP) == STEP
        rgb = tab.canvas()
        assert same_picture(rgb, want), compare_by_index(rgb, want)
        assert not tab.errors, tab.errors


def test_switching_between_boards_leaves_nothing_behind(site, browser, tmp_path):
    """purpose: J2 steps 2-4 across boards (J2d, J2i): in one page, an AxROM game showing VRAM page 1
    (single-screen), then a vertically mirrored UxROM game, then the AxROM game again, then a
    horizontally mirrored CNROM game: each shows exactly its own spec picture 60 frames after its load,
    as on a fresh console (no bank, page or mirroring of the game before), and the page loaded once.
    origin: design"""
    games = [picture_probe(7, 1), picture_probe(2, 5), picture_probe(7, 1), picture_probe(3, 1)]
    with Tab(browser, site) as tab:
        tab.pause()
        for k, g in enumerate(games):
            tab.load(g.write_to(tmp_path, f"game{k}.nes"))
            assert tab.frames() == 0 and not tab.message(), f"game {k} (mapper {g.mapper}) didn't load clean"
            tab.step(STEP)
            rgb = tab.canvas()
            assert same_picture(rgb, g.expected_frame()), \
                f"game {k} (mapper {g.mapper}): {compare_by_index(rgb, g.expected_frame())}"
        assert tab.loaded_once(), "the page reloaded"
        assert not tab.errors, tab.errors


def test_a_still_unsupported_mapper_is_refused_by_number_while_an_axrom_game_runs(site, browser, tmp_path):
    """purpose: J1a after I6: with an AxROM game running on the page, picking a file for mapper 180 (an
    UNROM relative I6 doesn't build) shows #message naming 180, and the AxROM game keeps running (its
    frame count climbs by 45+ over 1.5 s) with its picture on the canvas. No page error.
    origin: design"""
    game = picture_probe(7, 1)
    bad = tmp_path / "mapper180.nes"
    bad.write_bytes(header(180, 0x8000, 0x2000) + Probe(3, 32, chr_kib=8).image()[16:])
    with Tab(browser, site) as tab:
        tab.load(game.write_to(tmp_path, "axrom.nes"))
        tab.page.wait_for_timeout(1000)
        assert not tab.message(), f"the page refused the AxROM game: {tab.message()!r}"
        assert same_picture(tab.canvas(), game.expected_frame()), "the AxROM game isn't showing its picture"
        before = tab.frames()
        tab.load(str(bad))
        tab.page.wait_for_timeout(1500)
        msg = tab.message()
        assert re.search(r"\b180\b", msg), f"#message is {msg!r}: it doesn't name mapper 180"
        assert tab.frames() >= before + 45, "the running game stopped or restarted"
        assert same_picture(tab.canvas(), game.expected_frame()), "the AxROM picture is gone"
        assert not tab.errors, tab.errors


def test_headless_core_on_a_uxrom_program_runs_at_least_five_times_real_time():
    """purpose: J3's ">= 5x real time" holds on a bank-switched cartridge: 1,800 frames (30 s of NTSC time)
    of the 240p Test Suite (UNROM) take at most 6.0 s of wall time, and its last frame shows its About
    page (8+ colours).
    origin: design"""
    res = drive_ppu({"mode": "bench", "rom": str(i6_oracle("240pee")), "frames": 1800}, timeout=120)
    assert res.get("ok"), loads({k: v for k, v in res.items() if k != "frame"}, "240pee")
    colours = len(set(decode_frame(res["frame"])))
    assert res["ms"] <= 6000 and colours >= 8, f"{res['ms']:.0f} ms for 1800 frames; {colours} colours"
