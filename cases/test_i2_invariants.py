"""I2: invariants from the Intent and Decided, each stated in words, then its predicate; and the
odd-frame boundary, derived from the spec, through the I1 trace surface."""
import re

import pytest

from conftest import ROOT, cli
from i2_scenes import scene_mixed, scene_power_on, scene_split
from i2_support import build_rom, decode_frame, drive_ppu, make_chr, scene_rom, short, write_rom

DELTA = pytest.mark.xfail(strict=True, reason="delta")


def test_frames_are_deterministic(tmp_path):
    """purpose: "power-on state is fixed, so runs are deterministic and title frames can be compared"
    (Decided, axes): the same ROM run twice from power-on draws the same frame, index for index, at
    every one of its first 120 frames; over a picture that isn't blank (J1e, J2a).
    origin: design"""
    rom = write_rom(tmp_path, "split.nes", scene_rom(scene_split()))
    req = {"mode": "frames", "rom": rom, "frames": 120, "hashFrom": 1}
    a, b = drive_ppu(req), drive_ppu(req)
    assert a.get("ok") and b.get("ok"), (short(a), short(b))
    assert len(set(decode_frame(a["frame"]))) >= 8, "the picture is (nearly) blank"
    assert a["hashes"] == b["hashes"] and len(a["hashes"]) == 120, \
        f"first different frame: {next((i + 1 for i, (x, y) in enumerate(zip(a['hashes'], b['hashes'])) if x != y), None)}"


def test_headless_core_with_rendering_on_runs_at_least_five_times_real_time(tmp_path):
    """purpose: "headless, the core runs at >= 5x real time, so the corpus runs in minutes" (J3) holds
    with the picture being drawn: 1,800 frames (30 s of NTSC time) of a busy scene, background and 64
    sprites on, take at most 6.0 s of wall time, and the last frame shows the scene (24+ colours).
    origin: design"""
    rom = write_rom(tmp_path, "mixed.nes", scene_rom(scene_mixed()))
    res = drive_ppu({"mode": "bench", "rom": rom, "frames": 1800}, timeout=120)
    assert res.get("ok"), short(res)
    colours = len(set(decode_frame(res["frame"])))
    assert res["frames"] == 1800 and res["ms"] <= 6000 and colours >= 24, \
        f"{res['ms']:.0f} ms for 1800 frames; {colours} colours in the last frame"


def test_the_core_uses_no_node_built_ins():
    """purpose: "the core in src/core is pure TypeScript ... It runs headless under Node for the tests,
    and in the browser for play" (Decided): so it can't lean on Node either, and the PNG encoder
    (zlib) stays in the CLI: no file under src/core imports a node: module or a Node built-in by
    name, calls require(), or uses Buffer or process.
    origin: design"""
    core = ROOT / "src" / "core"
    banned = re.compile(r"""from\s+['"](node:|fs|zlib|path|crypto|buffer|os|child_process)['"/]|"""
                        r"""import\(\s*['"]node:|\brequire\s*\(|\bBuffer\b|\bprocess\.""")
    hits = [f"{p.relative_to(ROOT)}:{i}" for p in core.rglob("*.ts")
            for i, line in enumerate(p.read_text().splitlines(), 1) if banned.search(line)]
    assert (core / "index.ts").is_file() and not hits, hits


POWER_PEEK = [[0x0300, 32], [0x0400, 256], [0x0500, 1]]


def power_on(tmp_path):
    rom = write_rom(tmp_path, "power.nes", scene_rom(scene_power_on()))
    res = drive_ppu({"mode": "frames", "rom": rom, "frames": 6, "frame": False,
                     "peek": POWER_PEEK})
    assert res.get("ok"), short(res)
    assert res["peek"]["0500"] == [0x5A], f"the read-back program didn't finish: {res['peek']['0500']}"
    return res["peek"]


def test_power_on_palette_is_zero_filled(tmp_path):
    """purpose: "RAM, OAM and palette RAM fill with a fixed pattern (all $00)" (Decided, axes): read
    through $2007 at $3F00-$3F1F before anything writes them, all 32 palette entries are $00.
    origin: design"""
    pal = power_on(tmp_path)["0300"]
    assert [v & 0x3F for v in pal] == [0] * 32, [f"{v:02X}" for v in pal]


def test_power_on_oam_is_zero_filled(tmp_path):
    """purpose: "RAM, OAM and palette RAM fill with a fixed pattern (all $00)" (Decided, axes): read
    through $2004 (with $2003 set to each address) before anything writes it, all 256 OAM bytes are $00.
    origin: design"""
    oam = power_on(tmp_path)["0400"]
    assert oam == [0] * 256, [f"{i:02X}:{v:02X}" for i, v in enumerate(oam) if v][:16]


TRACE = re.compile(r"^([0-9A-F]{4}) .*PPU:\s*(\d+),\s*(\d+) CYC:(\d+)")
DOTS = 341 * 262   # 89,342 dots: an NTSC frame without the skipped dot


def frame_length_rom(mask: int) -> tuple:
    """Wait two vblanks, set $2001 = mask in the third, then loop on 6-cycle INCs."""
    prog = ["reset:", ("sei", None), ("cld", None), ("ldx#", 0xFF), ("txs", None),
            ("lda#", 0), ("sta", 0x2000), ("sta", 0x2001),
            ("bit", 0x2002), "v1:", ("bit", 0x2002), ("bpl", "v1"), "v2:", ("bit", 0x2002), ("bpl", "v2"),
            "v3:", ("bit", 0x2002), ("bpl", "v3"), "enable:", ("lda#", mask), ("sta", 0x2001),
            "loop:"] + [("inc", 0x0300)] * 10 + [("jmp", "loop"), "nmi:", ("rti", None), "irq:", ("rti", None)]
    return build_rom(prog, make_chr(1))


@pytest.mark.parametrize("mask,skips", [(0x08, "alternate"), (0x00, "none")], ids=["bg-on", "rendering-off"])
def test_odd_frames_are_one_dot_short_only_with_rendering_on(tmp_path, mask, skips):
    """purpose: an NTSC frame is 341 x 262 = 89,342 dots, and with rendering on the pre-render line of
    every odd frame is one dot short (NESdev "PPU frame timing"): traced through the CLI, the dots the
    PPU position advances run ahead of 3 per CPU cycle by exactly one dot at every other frame wrap
    with BG on, and by none with rendering off, over 8 frames after $2001 is written.
    origin: design"""
    data, labels = frame_length_rom(mask)
    rom = write_rom(tmp_path, f"frames{mask:02x}.nes", data)
    r = cli("trace", rom, "--lines", 100000, timeout=180)
    assert r.returncode == 0, repr(r)
    enable = labels["enable"]
    frame, last_s, started, ahead = 0, None, False, {}
    for line in r.stdout.splitlines():
        m = TRACE.match(line)
        if not m:
            continue
        pc, s, d, cyc = int(m.group(1), 16), int(m.group(2)), int(m.group(3)), int(m.group(4))
        if last_s is not None and s < last_s:
            frame += 1
        last_s = s
        started = started or pc == enable
        if started:
            ahead.setdefault(frame, frame * DOTS + s * 341 + d - 3 * cyc)
    frames = sorted(ahead)
    steps = [ahead[b] - ahead[a] for a, b in zip(frames, frames[1:])][1:]   # wraps wholly after the write
    assert len(steps) >= 8, f"only {len(steps)} frame wraps traced"
    if skips == "none":
        assert set(steps) == {0}, steps
    else:
        assert set(steps) <= {0, 1} and all(a + b == 1 for a, b in zip(steps, steps[1:])), steps
