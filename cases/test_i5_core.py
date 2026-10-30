"""I5: the core over time (J3's "30 minutes of emulated play ... fast-forwarded headless for the core", and
"headless, the core runs at >= 5x real time"), through cases/drive_i5.ts and cases/drive_apu.ts."""
import pytest

from i2_support import drive_ppu
from i5_support import GAME_IDS, corpus_games, drive_i5, game_path
from i8_support import RING, TOTALS, IrqSplit, frame_hash, split_probe

THIRTY_MIN = 108_000          # frames: 30 minutes of NTSC play


def test_thirty_minutes_of_play_hold_the_picture_the_interrupts_and_the_heap(tmp_path):
    """purpose: J3's long sitting for the core, at its real volume (Data: 30 minutes, 108,000 frames), and the
    long-run counters (J3x, later): I8's MMC3 status bar (latch 150, the APU frame IRQ on beside it) run for
    108,000 frames, past 2^31 CPU cycles and 2^32 PPU dots:
      - frames 41-100 and the last 60 all hash to the spec's split frame (lines 0-150 top, 151 down);
      - each of the last 64 frames had exactly one MMC3 IRQ, and over the whole run the MMC3 IRQs are the
        NMIs less one and the APU frame IRQs are one every 29,830 CPU cycles within 0.01 % (about 11 over
        the run; I8 pins them within 3 over 600 frames): none lost or doubled as the counters grow;
      - the heap after a GC at the end is within 10 % of the heap at frame 3,600 (one minute);
      - the run takes at most 6 minutes (>= 5x real time).
    origin: design"""
    p = split_probe(IrqSplit(150, xa=16, xb=176, apu=True))
    want = frame_hash([v for row in p.irq_frame() for v in row])
    res = drive_i5({"rom": p.write_to(tmp_path, "split.nes"), "frames": THIRTY_MIN, "samples": True,
                    "hash": [[41, 100], [THIRTY_MIN - 59, THIRTY_MIN]], "heapAt": [3600, THIRTY_MIN],
                    "peek": [[TOTALS, 6], [RING, 64]]}, timeout=1200)
    assert res.get("ok"), res
    off = sorted(int(f) for f, h in res["hashes"].items() if h != want)
    assert len(res["hashes"]) == 120 and not off, f"{len(off)} of 120 frames differ from the spec's, first {off[:8]}"
    ring = res["peek"][f"{RING:04X}"]
    assert ring == [1] * 64, f"MMC3 IRQs per frame at the end: {sorted(set(ring))}"
    t = res["peek"][f"{TOTALS:04X}"]
    mmc3, apu, nmis16 = t[0] | t[1] << 8, t[2] | t[3] << 8, t[4] | t[5] << 8
    nmis = next(nmis16 + 65536 * k for k in range(4) if THIRTY_MIN - 300 <= nmis16 + 65536 * k <= THIRTY_MIN)
    assert mmc3 == (nmis - 1) % 65536, f"{mmc3} MMC3 IRQs (mod 65536) in {nmis} frames"
    want_apu = nmis * 29780.5 / 29830
    got_apu = next(apu + 65536 * k for k in range(4) if abs(apu + 65536 * k - want_apu) < 32768)
    assert abs(got_apu - want_apu) <= want_apu * 1e-4, \
        f"{got_apu} APU frame IRQs in {nmis} frames, want {want_apu:.0f} within 0.01 %"
    h1, h2 = res["heap"]["3600"], res["heap"][str(THIRTY_MIN)]
    assert h2 <= h1 * 1.10, f"the core's heap grew from {h1} to {h2} bytes over 30 minutes"
    assert res["secs"] <= 360, f"30 minutes of play took {res['secs']:.0f} s headless (under 5x real time)"


@pytest.mark.parametrize("i", range(len(GAME_IDS)), ids=GAME_IDS)
def test_every_corpus_game_runs_at_least_five_times_real_time_headless(i):
    """purpose: J3's "headless, the core runs at >= 5x real time" on every homebrew game and demo the corpus
    fetches (J3ag, for real: speed depends on the game and its board): 1,800 frames (30 s of play) take at
    most 6 s of wall time.
    origin: design"""
    name = corpus_games()[i][0]
    res = drive_ppu({"mode": "bench", "rom": str(game_path(i)), "frames": 1800}, timeout=120)
    assert res.get("ok"), f"{name}: {res}"
    assert res["ms"] <= 6000, f"{name}: 1,800 frames took {res['ms'] / 1000:.2f} s (under 5x real time)"
