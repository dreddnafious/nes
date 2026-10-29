"""I4: the core's sound, headless, measured from the sample stream (CONTRACT-I4.md: setSampleRate,
audioSamples, the mixer's scale), through the frozen driver cases/drive_apu.ts; and the CLI's `audio`
command.

Most cases play a probe built in the case (i4_support.apu_probe): a script of APU register writes made
at stated frames, so each promise is a tone or a level whose value comes from the NESdev APU pages, not
from any emulator. nes15 (BSD, fetched by hash) is the real game: silent on its title, music once
started, its pause sound, and silence while paused (its sound engine, nes-lib/muse.s, mutes every
channel when paused and holds the triangle, which leaves a DC level).

Levels are in dBFS with the window's DC removed (J1h): 20 log10 of the RMS about the mean, a
full-scale square being 0 dBFS. The floor is -60 dBFS."""
import struct

import pytest

from conftest import cli
from i3_support import nes15
from i4_support import (CPU_HZ, DMC_RATES, crossings, FLOOR_DBFS, NOISE_PERIODS, apu_probe, audio, dbfs, dmc_length, duty,
                        enable, level_mode, lfsr_bits, noise_tone, peak_to_peak, pitch, pulse_hz, pulse_level,
                        pulse_tone, raw_dbfs, repeat, rest_level, script_sample, swing, tnd_level, triangle_hz,
                        triangle_tone, why, write)

DELTA = pytest.mark.xfail(strict=True, reason="delta")
RATE = 48000
PEEKS = [[0x10, 1], [0x0300, 2]]          # the probe's NMI count, and its idle-loop count per frame
OFF_FRAME_CYCLES = 341 * 262 / 3          # a frame with rendering off: 89,342 dots, 29,780.67 CPU cycles
A440 = 253                                # pulse period for 440.4 Hz: CPU / (16 * 254)


def play(tmp_path, name, script, frames, rate=RATE, samples=None) -> dict:
    """The probe's sound over `frames` frames; the core must give it (every promise here needs it)."""
    rom = write(tmp_path, f"{name}.nes", apu_probe(script, samples))
    res = audio(rom, frames, rate, peekEach=PEEKS)
    assert res.get("ok"), why(res)
    return res


def after(res, frame, t0, t1, rate=RATE):
    """The samples from t0 to t1 seconds after the probe played script frame `frame`."""
    s = script_sample(res, frame, rate)
    return res["x"][s + int(t0 * rate):s + int(t1 * rate)]


def loop_counts(res) -> dict:
    """The probe's idle-loop iterations in each script frame's interval (NMI k to NMI k+1)."""
    out = {}
    for row in res["peeks"]:
        out.setdefault(row[0] - 2, row[1] | row[2] << 8)
    return out


def median(v):
    s = sorted(v)
    return s[len(s) // 2]


# ---------------------------------------------------------------- the stream

@DELTA
@pytest.mark.parametrize("rate", [44100, 48000])
def test_the_sample_stream_runs_at_the_stated_rate_against_the_cpu_clock(tmp_path, rate):
    """purpose: the core's stream is `rate` samples per second of emulated time (the CPU's 1,789,772.7
    Hz), whatever the rate: over 600 frames with rendering off (29,780.67 cycles each) the samples
    number 600 * 29,780.67 * rate / 1,789,772.7 within 0.02 % (a stream made per 1/60 s, at 60.00 fps,
    is 0.16 % long: outside); each frame after the first gives that frame's share (within 16
    samples, so nothing is held back and released in bursts); and every sample is finite and within
    the mixer's range, 0.0 to 1.0 (with 0.1 for a band-limiting filter's overshoot).
    origin: design"""
    res = play(tmp_path, "tone", enable(0, 0x01) + pulse_tone(0, A440), 600, rate)
    x, counts = res["x"], res["counts"]
    want = 600 * OFF_FRAME_CYCLES * rate / CPU_HZ
    assert abs(len(x) - want) <= want * 0.0002, f"{len(x)} samples over 600 frames, want {want:.1f} +- 0.02 %"
    per = OFF_FRAME_CYCLES * rate / CPU_HZ
    lumpy = [(i + 1, c) for i, c in enumerate(counts) if i > 0 and abs(c - per) > 16]
    assert len(counts) == 600 and not lumpy, f"frames whose share isn't {per:.1f} +- 16: {lumpy[:8]}"
    bad = [(i, v) for i, v in enumerate(x) if not (-0.1 <= v <= 1.1) or v != v]
    assert not bad, f"samples outside 0.0-1.0: {bad[:8]}"


@DELTA
def test_dc_is_not_sound_the_floor_is_minus_60_dbfs_and_the_quietest_note_is_above_it(tmp_path):
    """purpose: J1h: the core's stream is the mixer's output with its DC left in, so a ROM that only
    sets the DMC's DAC to 64 ($4011, a constant level) measures at -9 dBFS with the DC in (above -20)
    but below the -60 dBFS floor with it removed; a pulse at volume 1, the quietest note there is,
    measures -44.7 dBFS (the mixer formula's 95.88 / (8128 + 100) as a 50 % square; within 3 dB), above
    the floor; and at volume 15, -22.5 dBFS (within 1.5 dB).
    origin: design"""
    dc = play(tmp_path, "dc", [(0, 0x4011, 64)], 120)
    w = after(dc, 0, 0.5, 1.5)
    assert raw_dbfs(w) > -20, f"with the DC in, the DAC at 64 measures {raw_dbfs(w):.1f} dBFS: the DC isn't in the stream"
    assert dbfs(w) < FLOOR_DBFS, f"a constant level measures {dbfs(w):.1f} dBFS with the DC removed"
    for vol, lo, hi in ((1, -47.7, -41.7), (15, -24.0, -21.0)):
        res = play(tmp_path, f"vol{vol}", enable(0, 0x01) + pulse_tone(0, A440, vol=vol), 120)
        level = dbfs(after(res, 0, 0.5, 1.5))
        assert level > FLOOR_DBFS and lo <= level <= hi, f"volume {vol} measures {level:.1f} dBFS, want {lo}..{hi}"


# ---------------------------------------------------------------- the pulses

@DELTA
@pytest.mark.parametrize("t", [253, 126, 400, 1000, 2000])
def test_pulse_pitch_follows_its_period_register(tmp_path, t):
    """purpose: a pulse with period register t sounds at CPU / (16 (t + 1)) Hz (NESdev "APU Pulse"),
    within 0.1 %, which tells t from t - 1 or t + 1 (0.39 % apart at t = 253, 440.4 Hz); measured over
    1 s, on pulse 1 for t = 253, 126, 400, 1000 and 2000 (55.9-880.8 Hz).
    origin: design"""
    res = play(tmp_path, f"p{t}", enable(0, 0x01) + pulse_tone(0, t), 100)
    got, want = pitch(after(res, 0, 0.2, 1.2), RATE), pulse_hz(t)
    assert abs(got - want) <= want * 0.001, f"period {t}: {got:.2f} Hz, want {want:.2f} Hz +- 0.1 %"


@DELTA
@pytest.mark.parametrize("d,want", [(0, 0.125), (1, 0.25), (2, 0.5), (3, 0.75)])
def test_pulse_duty_is_one_of_the_four_sequences(tmp_path, d, want):
    """purpose: duty d of $4000 bits 6-7 holds the pulse high for 1, 2, 4 or 6 of its 8 steps
    (12.5 %, 25 %, 50 %, 75 %: NESdev "APU Pulse" sequencer), within 2 points, at 440.4 Hz on pulse 2.
    origin: design"""
    res = play(tmp_path, f"duty{d}", enable(0, 0x02) + pulse_tone(0, A440, duty=d, ch=2), 100)
    got = duty(after(res, 0, 0.2, 1.2))
    assert abs(got - want) <= 0.02, f"duty {d}: high {got:.3f} of each period, want {want}"


@DELTA
@pytest.mark.parametrize("t,sweep,sounds", [(7, 0x08, False), (8, 0x08, True), (0x3FF, 0x00, True),
                                            (0x400, 0x00, False), (0x400, 0x08, True)],
                         ids=["period7-muted", "period8-sounds", "3FF-sweep00-sounds", "400-sweep00-muted",
                              "400-negate-sounds"])
def test_the_sweep_unit_mutes_below_8_and_on_overflow_even_when_disabled(tmp_path, t, sweep, sounds):
    """purpose: NESdev "APU Sweep": a pulse whose period is under 8 is muted (7 silent, 8 sounds at
    12.4 kHz); and the sweep's target (period + period >> shift) is computed even with the sweep
    disabled, so with $4005 = $00 (shift 0, negate off) a period of $400 (target $800 > $7FF) is
    muted while $3FF (target $7FE) sounds, and writing $08 (negate on) lets $400 sound. Sounding is
    above -30 dBFS, muted below the -60 floor, over 1 s on pulse 2.
    origin: design"""
    res = play(tmp_path, f"sweep{t:x}", enable(0, 0x02) + pulse_tone(0, t, ch=2, sweep=sweep), 100)
    level = dbfs(after(res, 0, 0.2, 1.2))
    if sounds:
        assert level > -30, f"period ${t:X} with $4005 = ${sweep:02X} measures {level:.1f} dBFS: it should sound"
    else:
        assert level < FLOOR_DBFS, f"period ${t:X} with $4005 = ${sweep:02X} measures {level:.1f} dBFS: it should be muted"


# ---------------------------------------------------------------- the triangle, the noise, the DMC

@DELTA
@pytest.mark.parametrize("t", [126, 253, 63])
def test_triangle_pitch_and_shape(tmp_path, t):
    """purpose: the triangle with period register t sounds at CPU / (32 (t + 1)) Hz (its 32-step
    sequence, NESdev "APU Triangle") within 0.1 %; it is a triangle (half its samples lie in the middle
    half of its range, within 0.4-0.6, where a square has none); and its swing is the mixer's
    tnd(15) - tnd(0) = 0.2464 within 5 % (NESdev "APU Mixer").
    origin: design"""
    res = play(tmp_path, f"tri{t}", enable(0, 0x04) + triangle_tone(0, t), 100)
    w = after(res, 0, 0.2, 1.2)
    got, want = pitch(w, RATE), triangle_hz(t)
    assert abs(got - want) <= want * 0.001, f"period {t}: {got:.2f} Hz, want {want:.2f} Hz +- 0.1 %"
    lo, hi = min(w), max(w)
    middle = sum(1 for v in w if lo + (hi - lo) / 4 <= v <= hi - (hi - lo) / 4) / len(w)
    assert 0.4 <= middle <= 0.6, f"{middle:.2f} of the samples in the middle half: not a triangle"
    got_swing, want_swing = peak_to_peak(w), tnd_level(t=15) - tnd_level(t=0)
    assert abs(got_swing - want_swing) <= want_swing * 0.05, f"swing {got_swing:.4f}, want {want_swing:.4f} +- 5 %"


def noise_bits(x, period: int, rate=RATE) -> str:
    """The noise channel's output bit at each clock of its timer, read from the stream: from the first
    edge (a crossing of the mid level, which is a clock boundary), sampled at each clock's middle."""
    lo, hi = min(x), max(x)
    mid = (lo + hi) / 2
    per = rate * period / CPU_HZ
    first = x[0] > mid
    e = next((i for i in range(1, len(x)) if (x[i] > mid) != first), None)
    if e is None:
        return ""
    edge = e - 1 + (mid - x[e - 1]) / (x[e] - x[e - 1])
    out, k = [], 0
    while edge + (k + 0.5) * per < len(x) - 1:
        out.append("1" if x[int(edge + (k + 0.5) * per)] > mid else "0")
        k += 1
    return "".join(out)


@DELTA
def test_noise_is_the_15_bit_shift_register_at_its_timer_period(tmp_path):
    """purpose: NESdev "APU Noise": the noise channel sounds its volume while bit 0 of a 15-bit shift
    register is 0, clocked at the period table's rate (index 15: every 4068 CPU cycles; index 13: every
    1016); with feedback from bits 0 and 1 the 600-odd bits read off 1.4 s of the stream are a run of
    the register's one 32,767-step sequence, and with mode 1 (bits 0 and 6) the bits repeat every 93 (or
    31) clocks; and it steps by the mixer's tnd(t, 15, 0) - tnd(t, 0, 0) within 5 %, where t is the
    step the stopped triangle holds (0.1744 with the triangle at 0).
    origin: design"""
    long_mode = play(tmp_path, "noise-long", enable(0, 0x08) + noise_tone(0, 15), 100)
    w = after(long_mode, 0, 0.05, 1.45)
    bits = noise_bits(w, NOISE_PERIODS[15])
    seq = lfsr_bits(False)
    assert len(bits) >= 500 and bits in seq + seq, f"{len(bits)} bits read; they aren't a run of the sequence: {bits[:64]}"
    _, t0 = rest_level(long_mode, RATE)
    level, want = swing(w), tnd_level(t=t0, n=15) - tnd_level(t=t0)
    assert abs(level - want) <= want * 0.05, f"the noise steps by {level:.4f}, want {want:.4f} +- 5 %"
    short_mode = play(tmp_path, "noise-short", enable(0, 0x08) + noise_tone(0, 13, short_mode=True), 100)
    bits = noise_bits(after(short_mode, 0, 0.05, 1.45), NOISE_PERIODS[13])
    periodic = [p for p in (31, 93) if len(bits) > 3 * p and all(bits[i] == bits[i + p] for i in range(len(bits) - p))]
    assert len(set(bits)) == 2 and periodic, f"mode 1's {len(bits)} bits don't repeat every 93 or 31: {bits[:120]}"


SAMPLE_A, SAMPLE_B = 0xE000, 0xF000       # $4012 = $80 and $C0


def dmc_rom_samples():
    """At $E000, bytes FF FF 00 00 repeated: 16 one-bits (+2 each), 16 zero-bits (-2 each): a triangle
    from the starting level up 32 and back, every 32 bits. At $F000, FF 00 repeated: up 16 and back,
    every 16 bits."""
    n = dmc_length(0x7F)
    return {SAMPLE_A: repeat(b"\xff\xff\x00\x00", n), SAMPLE_B: repeat(b"\xff\x00", n)}


@DELTA
@pytest.mark.parametrize("address,bits_per_cycle,steps", [(SAMPLE_A, 32, 32), (SAMPLE_B, 16, 16)],
                         ids=["E000", "F000"])
def test_dmc_plays_the_sample_at_its_address_by_dma_and_falls_silent_after(tmp_path, address, bits_per_cycle, steps):
    """purpose: J4c: with the DAC at 64, a DMC sample started at $4012's address ($C000 + A * 64),
    rate 15 (54 CPU cycles a bit), 2033 bytes ($4013 = $7F: L * 16 + 1), plays that address's data: the
    $E000 bytes give a 1035.7 Hz wave swinging tnd(d = 96) - tnd(64), the $F000 bytes a 2071.5 Hz one
    swinging tnd(80) - tnd(64) (pitch within 0.5 %, swing within 10 %, with the stopped triangle's
    held step in the tnd mix); while it plays, DMA steals
    cycles from the CPU (the probe's idle loop counts 15 or more fewer iterations a frame than before
    the sample: about 69 fetches a frame); after its 0.49 s it stops: the DAC holds its last level, so
    the stream is below the -60 dBFS floor, and the loop count is back within 2 of the count before
    (no more fetches).
    origin: design"""
    script = [(0, 0x4011, 64), (60, 0x4010, 0x0F), (60, 0x4012, (address - 0xC000) // 64),
              (60, 0x4013, 0x7F), (60, 0x4015, 0x10)]
    res = play(tmp_path, f"dmc{address:x}", script, 160, samples=dmc_rom_samples())
    w = after(res, 60, 0.05, 0.44)
    want = CPU_HZ / (DMC_RATES[15] * bits_per_cycle)
    got = pitch(w, RATE)
    assert abs(got - want) <= want * 0.005, f"the sample at ${address:X} plays at {got:.1f} Hz, want {want:.1f} Hz"
    _, t0 = rest_level(res, RATE)
    want_swing = tnd_level(t=t0, d=64 + steps) - tnd_level(t=t0, d=64)
    assert abs(peak_to_peak(w) - want_swing) <= want_swing * 0.10, f"swing {peak_to_peak(w):.4f}, want {want_swing:.4f}"
    tail = after(res, 60, 0.55, 1.4)
    assert dbfs(tail) < FLOOR_DBFS, f"after the sample ends the stream measures {dbfs(tail):.1f} dBFS"
    loops = loop_counts(res)
    before = median([loops[k] for k in range(10, 58)])
    during = median([loops[k] for k in range(62, 87)])
    later = median([loops[k] for k in range(95, 140)])
    assert during <= before - 15, f"idle-loop count {during} while the sample plays vs {before} before: no DMA"
    assert abs(later - before) <= 2, f"idle-loop count {later} after the sample vs {before} before: DMA goes on"


@DELTA
@pytest.mark.parametrize("r", [0, 8, 15])
def test_dmc_rate_table(tmp_path, r):
    """purpose: the DMC's rate index r ($4010 bits 0-3) plays a bit every DMC_RATES[r] CPU cycles
    (NESdev "APU DMC", NTSC: 428 for 0, 190 for 8, 54 for 15), so the $E000 sample's 32-bit cycle
    sounds at CPU / (32 * period) Hz: 130.7, 294.4 and 1035.7 Hz, within 0.5 %.
    origin: design"""
    script = [(0, 0x4011, 64), (10, 0x4010, r), (10, 0x4012, 0x80), (10, 0x4013, 0xFF), (10, 0x4015, 0x10)]
    samples = {SAMPLE_A: repeat(b"\xff\xff\x00\x00", dmc_length(0xFF))}
    res = play(tmp_path, f"rate{r}", script, 80, samples=samples)
    want = CPU_HZ / (DMC_RATES[r] * 32)
    got = pitch(after(res, 10, 0.05, 0.45 if r == 15 else 1.0), RATE)
    assert abs(got - want) <= want * 0.005, f"rate {r}: {got:.2f} Hz, want {want:.2f} Hz"


# ---------------------------------------------------------------- the mixer

@DELTA
def test_the_mixer_is_the_specs_nonlinear_sum(tmp_path):
    """purpose: NESdev "APU Mixer", whose values the stream carries (CONTRACT-I4): output = pulse_out +
    tnd_out, pulse_out = 95.88 / (8128 / (p1 + p2) + 100), tnd_out = 159.79 / (1 / (t / 8227 + n / 12241
    + d / 22638) + 100). With t the step the stopped triangle holds at rest (which the rest level must
    be one of), the DAC ($4011) at 32, 64 and 127 gives tnd(t, 0, d) within 5 % (0.198, 0.352 and 0.574
    with t = 0; the linear approximation 0.00335 d gives 0.107, 0.214, 0.425), and the step from 64 to
    127 is under 0.8x the step from 0 to 64 (0.63-0.67x for any t; linear: 0.98x); pulse 1 alone at
    volume 15 (duty 75 %) steps by pulse(15) = 0.1494, both pulses high together by pulse(30) = 0.2585,
    each within 3 %, so two are 1.73x one within 2 % (linear: 2.00x).
    origin: design"""
    script = ([(0, 0x4011, 32), (30, 0x4011, 64), (60, 0x4011, 127), (90, 0x4011, 0)]
              + enable(90, 0x01) + pulse_tone(90, A440, duty=3)
              + enable(150, 0x03) + pulse_tone(150, 380, duty=3, ch=2))
    res = play(tmp_path, "mixer", script, 220)
    rest, t0 = rest_level(res, RATE)
    dac = {d: level_mode(after(res, f, 0.1, 0.4)) for f, d in ((0, 32), (30, 64), (60, 127))}
    for d, got in dac.items():
        want = tnd_level(t=t0, d=d)
        assert abs(got - want) <= want * 0.05, f"DAC {d} (triangle resting at {t0}): {got:.4f}, want {want:.4f}"
    bend = (dac[127] - dac[64]) / (dac[64] - rest)
    assert bend < 0.8, f"DAC 64->127 is {bend:.3f}x DAC 0->64: the mix is linear (0.98x), not the spec's curve"
    one = swing(after(res, 90, 0.2, 0.9))
    two = swing(after(res, 150, 0.2, 0.9))
    assert abs(one - pulse_level(15)) <= pulse_level(15) * 0.03, f"one pulse at 15: {one:.4f}, want {pulse_level(15):.4f}"
    assert abs(two - pulse_level(30)) <= pulse_level(30) * 0.03, f"two pulses at 15: {two:.4f}, want {pulse_level(30):.4f}"
    assert abs(two / one - 1.7303) <= 1.7303 * 0.02, f"two / one = {two / one:.3f}, want 1.730 (linear 2.0)"


# ---------------------------------------------------------------- notes end on time

@DELTA
def test_envelope_length_counter_and_linear_counter_end_notes_on_time(tmp_path):
    """purpose: the APU's note-ending units, each on both sides of its time (NESdev "APU Envelope",
    "APU Length Counter", "APU Triangle", "APU Frame Counter": quarter frames every 7457 CPU cycles,
    half frames every 14913):
    - an envelope with period 3 and no loop starts at volume 15 (the first 10 ms step by pulse(15)
      within 5 %), is at volume 7-9 at 125 ms, still sounds at 200-240 ms, and is silent from 280 ms (15 steps
      of 4 quarter frames, 0.25 s);
    - a constant-volume note with length index 0 (10 half frames, 83 ms) sounds for its first 70 ms
      and is silent from 95 ms;
    - the triangle with its linear counter at 30 (control clear) sounds for its first 110 ms and is
      silent (holding its level, so below the floor with DC removed) from 140 ms (30 quarter frames).
    origin: design"""
    script = (enable(0, 0x07)
              + [(0, 0x4000, 0x83), (0, 0x4001, 0x08), (0, 0x4002, A440), (0, 0x4003, 0x08)]
              + [(60, 0x4004, 0x9F), (60, 0x4005, 0x08), (60, 0x4006, A440), (60, 0x4007, 0x00)]
              + [(120, 0x4008, 0x1E), (120, 0x400A, 126), (120, 0x400B, 0x08)])
    res = play(tmp_path, "endings", script, 200)

    start = swing(after(res, 0, 0.0, 0.010))
    assert abs(start - pulse_level(15)) <= pulse_level(15) * 0.05, f"the envelope starts at {start:.4f}, not volume 15"
    mid = swing(after(res, 0, 0.120, 0.130))
    assert pulse_level(7) * 0.97 <= mid <= pulse_level(9) * 1.03, f"at 125 ms the envelope is at {mid:.4f}, not volume 7-9"
    checks = [("envelope", 0, 0.200, 0.240, True), ("envelope", 0, 0.280, 0.900, False),
              ("length counter", 60, 0.005, 0.070, True), ("length counter", 60, 0.095, 0.900, False),
              ("linear counter", 120, 0.005, 0.110, True), ("linear counter", 120, 0.140, 0.900, False)]
    for unit, frame, t0, t1, sounding in checks:
        level = dbfs(after(res, frame, t0, t1))
        if sounding:
            assert level > -50, f"{unit}: {level:.1f} dBFS at {t0 * 1000:.0f}-{t1 * 1000:.0f} ms: the note ended early"
        else:
            assert level < FLOOR_DBFS, f"{unit}: {level:.1f} dBFS at {t0 * 1000:.0f}-{t1 * 1000:.0f} ms: the note didn't end"


# ---------------------------------------------------------------- a real game

START = 0x08
NES15_SCRIPT = [[150, START], [154, 0], [500, START], [504, 0], [900, START], [904, 0]]


@DELTA
def test_nes15_is_silent_on_its_title_has_music_and_effects_and_falls_silent_when_paused():
    """purpose: J1 step 5 and J1h/J1i on a real game, headless: nes15 (its sound engine is nes-lib/
    muse.s): on its title (frames 60-150) it makes no sound (below -60 dBFS); Start at frame 150 starts
    a puzzle and its background music (play.s: "Start the background music"): the next 5 s are above
    the floor; Start at frame 500 pauses: its pause sound effect (sfx1) plays over the first 0.5 s,
    above the floor, and then, with every channel muted and the triangle held (a DC level), frames
    740-860 are below it; Start at frame 900 resumes the music: frames 910-1100 are above it.
    origin: design"""
    res = audio(nes15(), 1100, RATE, buttons=NES15_SCRIPT)
    assert res.get("ok"), why(res)
    x, counts = res["x"], res["counts"]

    def frames(a, b):
        return x[sum(counts[:a - 1]):sum(counts[:b - 1])]
    for what, a, b, sounding in (("the title", 60, 150, False), ("the music", 160, 460, True),
                                 ("the pause effect", 502, 532, True), ("paused", 740, 860, False),
                                 ("the music again", 910, 1100, True)):
        level = dbfs(frames(a, b))
        assert (level > FLOOR_DBFS) == sounding, f"{what} (frames {a}-{b}) measures {level:.1f} dBFS"


# ---------------------------------------------------------------- the CLI

def read_wav(path):
    """(format, channels, rate, bits, samples as floats) for a RIFF WAVE file: format 1 (PCM, 16-bit,
    returned as value / 32767) or 3 (IEEE float, 32-bit)."""
    data = path.read_bytes()
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE", "not a RIFF WAVE file"
    pos, fmt, samples = 12, None, None
    while pos + 8 <= len(data):
        kind, n = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + n]
        if kind == b"fmt ":
            fmt = struct.unpack("<HHIIHH", body[:16])
        elif kind == b"data":
            samples = body
        pos += 8 + n + (n & 1)
    assert fmt and samples is not None, "no fmt or data chunk"
    tag, channels, rate, _, _, bits = fmt
    if tag == 3 and bits == 32:
        return tag, channels, rate, bits, list(struct.unpack(f"<{len(samples) // 4}f", samples))
    if tag == 1 and bits == 16:
        return tag, channels, rate, bits, [v / 32767 for v in struct.unpack(f"<{len(samples) // 2}h", samples)]
    return tag, channels, rate, bits, None


@DELTA
def test_the_cli_writes_the_cores_sound_as_a_wav(tmp_path):
    """purpose: `node src/cli/nes.ts audio <rom> --frames 120 --rate 44100 --out f.wav` (Decided: the
    CLI gives "audio samples" headless) exits 0 and writes a mono WAV at 44,100 Hz, 32-bit float or
    16-bit PCM, holding the core's stream for those 120 frames sample for sample (the same count as the
    core's, each within 1e-6, or 1.5 / 32767 for PCM); without --out it exits non-zero with a message
    naming --out and no stack trace, and --rate 0 is refused with a message naming --rate.
    origin: design"""
    rom = write(tmp_path, "tone.nes", apu_probe(enable(0, 0x01) + pulse_tone(0, A440)))
    out = tmp_path / "tone.wav"
    r = cli("audio", rom, "--frames", 120, "--rate", 44100, "--out", out)
    assert r.returncode == 0 and out.is_file(), repr(r)
    tag, channels, rate, bits, got = read_wav(out)
    assert (channels, rate) == (1, 44100) and got is not None, f"format {tag}, {channels} channels, {rate} Hz, {bits} bits"
    core = audio(rom, 120, 44100)
    assert core.get("ok"), why(core)
    core = core["x"]
    tol = 1e-6 if tag == 3 else 1.5 / 32767
    assert len(got) == len(core), f"the WAV has {len(got)} samples, the core made {len(core)}"
    worst = max(abs(a - b) for a, b in zip(got, core))
    assert worst <= tol, f"the WAV differs from the core's stream by up to {worst}"
    for args, flag in ((("audio", rom, "--frames", 10), "--out"),
                       (("audio", rom, "--rate", 0, "--out", tmp_path / "x.wav"), "--rate")):
        r = cli(*args)
        text = (r.stderr or "") + (r.stdout or "")
        assert r.returncode not in (0, None) and flag in text and "    at " not in text, f"{args}: {r!r}"


# ---------------------------------------------------------------- the sweep, moving the pitch

HALF_FRAME_S = 14914.5 / CPU_HZ        # the frame counter's half-frame clock, 4-step mode: about 120 Hz


def sweep_target(t: int, shift: int, negate: bool, ch: int) -> int:
    """NESdev "APU Sweep": the change is t >> shift; negated, pulse 1 adds its ones' complement (one
    lower than pulse 2's two's complement)."""
    change = t >> shift
    if not negate:
        return t + change
    return t - change - (1 if ch == 1 else 0)


def plateaus(x, rate) -> list:
    """The pulse's period register over time, read off the stream: each rising-to-rising cycle's length
    gives t = CPU * length / 16 - 1; runs of cycles within 3 % of their run's median are one register
    value (sweep steps are 1/8 or more apart, at shift 3), and each run's t is read from its whole span
    (its first crossing to its last, over its cycle count). Returns (start s, end s, t, cycles) for each
    run of 4 or more cycles."""
    c, _ = crossings(x)
    runs = []
    for a, b in zip(c, c[1:]):
        t = CPU_HZ * (b - a) / rate / 16 - 1
        if runs and abs(t - median(runs[-1][2])) <= 0.03 * median(runs[-1][2]):
            runs[-1][1] = b
            runs[-1][2].append(t)
        else:
            runs.append([a, b, [t]])
    return [(a / rate, b / rate, CPU_HZ * (b - a) / rate / len(ts) / 16 - 1, len(ts))
            for a, b, ts in runs if len(ts) >= 4]


@DELTA
@pytest.mark.parametrize("ch,start,negate,steps", [(2, 400, True, 8), (1, 400, True, 8), (2, 200, False, 10)],
                         ids=["pulse2-down", "pulse1-down-ones-complement", "pulse2-up-to-the-mute"])
def test_the_sweep_unit_moves_the_pitch_by_its_shift_every_divider_period(tmp_path, ch, start, negate, steps):
    """purpose: NESdev "APU Sweep": with the sweep enabled ($4001/$4005 = $F3 or $FB: divider period 7,
    shift 3), every 8 half-frames (66.7 ms) the pulse's period register becomes its target, t + (t >> 3)
    or, negated, t - (t >> 3) on pulse 2 and t - (t >> 3) - 1 on pulse 1 (its ones' complement): so the
    pitch read off the stream steps through exactly that sequence of periods (400, 350, 307, ... on
    pulse 2; 400, 349, 305, ... on pulse 1; 200, 225, 253, ... upward), each step after the first lasting
    8 half-frames within 5 ms; and upward, once the target passes $7FF the channel is muted (below the
    -60 dBFS floor), with no further step.
    origin: design"""
    sweep = 0x80 | (7 << 4) | (0x08 if negate else 0) | 3
    res = play(tmp_path, f"sweep{ch}{start}{int(negate)}", enable(0, ch) + pulse_tone(0, start, ch=ch, sweep=sweep), 170)
    want, t = [start], start
    while len(want) < 40:
        nxt = sweep_target(t, 3, negate, ch)
        if not negate and nxt > 0x7FF:
            break
        want.append(nxt)
        t = nxt
    got = plateaus(after(res, 0, 0.0, 2.6), RATE)
    seen = [round(p[2]) for p in got[:steps]]
    assert seen == want[:steps], f"periods read off the stream {seen}, want {want[:steps]}"
    lengths = [p[1] - p[0] + (16 * (p[2] + 1) / CPU_HZ) for p in got[1:steps - 1]]
    off = [round(L * 1000, 1) for L in lengths if abs(L - 8 * HALF_FRAME_S) > 0.005]
    assert not off, f"steps lasting {off} ms, not {8 * HALF_FRAME_S * 1000:.1f} ms (8 half-frames)"
    if not negate:
        end = len(want) * 8 * HALF_FRAME_S + 0.1      # the last sounding step's end, with a step to spare
        tail = after(res, 0, end, end + 0.4)
        assert dbfs(tail) < FLOOR_DBFS, \
            f"{len(want)} steps in, the target passes $7FF, but the stream measures {dbfs(tail):.1f} dBFS"
