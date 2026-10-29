"""I4: invariants from the Intent and Decided, each stated in words, then its predicate."""
import re

import pytest

from conftest import ROOT
from i3_support import nes15
from i4_support import (A440_PERIOD, FLOOR_DBFS, apu_probe, audio, dbfs, dmc_length, drive_apu, enable,
                        noise_tone, pulse_tone, repeat, triangle_tone, why, write)

DELTA = pytest.mark.xfail(strict=True, reason="delta")
START = 0x08


def test_the_sound_is_deterministic_and_the_same_however_it_is_taken():
    """purpose: "power-on state is fixed, so runs are deterministic" (Decided, axes), for the sound too:
    the base of J2c's "the new game's first 2 s of audio match a fresh page's, sample for sample".
    nes15 with Start pressed at frame 150, run twice for 600 frames, gives the same samples bit for bit;
    and taking them every 7 frames instead of every frame gives the same stream (how often the page
    asks can't change what it hears). The stream has its music in it (the last 5 s above -60 dBFS).
    origin: design"""
    script = [[150, START], [154, 0]]
    a = audio(nes15(), 600, 48000, buttons=script)
    b = audio(nes15(), 600, 48000, buttons=script)
    c = audio(nes15(), 600, 48000, buttons=script, take=7)
    assert a.get("ok") and b.get("ok") and c.get("ok"), why(a if not a.get("ok") else b if not b.get("ok") else c)
    assert len(a["x"]) > 0 and dbfs(a["x"][-5 * 48000:]) > FLOOR_DBFS, "no music in the stream"
    assert a["samples"] == b["samples"], "two runs from power-on made different sound"
    assert c["samples"] == a["samples"], "taking the samples every 7 frames changed the stream"


def all_channels_probe() -> bytes:
    """Every channel at once, for good: both pulses, the triangle, noise, and a looping DMC sample."""
    script = (enable(0, 0x0F) + pulse_tone(0, A440_PERIOD) + pulse_tone(0, 400, ch=2) + triangle_tone(0, 300)
              + noise_tone(0, 6) + [(0, 0x4010, 0x4F), (0, 0x4012, 0x80), (0, 0x4013, 0x7F), (0, 0x4015, 0x1F)])
    return apu_probe(script, {0xE000: repeat(b"\xff\xff\x00\x00", dmc_length(0x7F))})


def test_headless_core_with_every_channel_sounding_runs_at_least_five_times_real_time(tmp_path):
    """purpose: "headless, the core runs at >= 5x real time, so the corpus runs in minutes" (J3) holds
    with the sound being made: 1,800 frames (30 s of NTSC time) of a probe with all five channels
    playing (the DMC sample looping, so its DMA runs throughout), with audioSamples() taken after every
    frame at 48 kHz, take at most 6.0 s of wall time, and give 30 s of samples (within 0.1 %).
    origin: design"""
    rom = write(tmp_path, "all.nes", all_channels_probe())
    res = drive_apu({"mode": "bench", "rom": rom, "frames": 1800, "rate": 48000}, timeout=120)
    assert res.get("ok"), why(res)
    want = 1800 * 341 * 262 / 3 * 48000 / 1789772.7272
    assert res["ms"] <= 6000 and abs(res["samples"] - want) <= want * 0.001, \
        f"{res['ms']:.0f} ms for 1800 frames; {res['samples']} samples (want {want:.0f})"


def test_the_core_makes_no_sound_through_the_browser():
    """purpose: "The core in src/core is pure TypeScript with no DOM ... The front end in src/web is the
    presentation layer: canvas, WebAudio, keyboard" (Decided): the core makes samples and nothing else:
    no file under src/core names a Web Audio type (AudioContext, AudioWorklet..., AudioBuffer...,
    OfflineAudioContext) or reaches for window., document. or navigator.
    origin: design"""
    core = ROOT / "src" / "core"
    banned = re.compile(r"\b(AudioContext|OfflineAudioContext|webkitAudioContext|AudioWorklet\w*|AudioBuffer\w*|"
                        r"AudioNode|GainNode)\b|\b(window|document|navigator)\s*\.")
    hits = [f"{p.relative_to(ROOT)}:{i}" for p in core.rglob("*.ts")
            for i, line in enumerate(p.read_text().splitlines(), 1) if banned.search(line)]
    assert (core / "index.ts").is_file() and not hits, hits
