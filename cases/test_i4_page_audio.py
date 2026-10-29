"""I4: the page's sound, in headless Chromium through the page's real entry points (the file input, the
keyboard, the Reset and Power buttons), measured where it leaves the page: an init script
(i4_support.TAP_JS) records every render quantum that reaches the AudioContext's destination, whatever
nodes the page connects to it (CONTRACT-I4.md). Headless Chromium has no real audio device, but its
AudioContext renders in real time, so what reaches the destination is what a speaker would get.

Headless Chromium doesn't enforce the browser's autoplay policy, so the cases that need it (J1j, J1r)
simulate Chrome's: a context made before any user activation starts suspended, and resume() does
nothing until a key press or click has happened. The J1k case forces the context's rate to 44.1 or 48
kHz, as a device at that rate would.

Levels are in dBFS with the window's DC removed (J1h); the floor is -60 dBFS. The games: nes15 (BSD,
fetched by hash) and probes built in the case (i4_support.apu_probe)."""
import pytest

from conftest import ROOT
from i3_support import NES15_TITLE_FRAME, browser, nes15, nes15_title, same_picture, site
from i4_support import (CPU_HZ, DMC_RATES, FLOOR_DBFS, AudioTab, apu_probe, crossings, dbfs, dmc_length, enable,
                        nes15_message, noise_tone, peak_to_peak, pitch, pulse_hz, pulse_tone, repeat, triangle_hz,
                        triangle_tone, write)

DELTA = pytest.mark.xfail(strict=True, reason="delta")
A440 = 253
SLACK = 0.030          # J1n/J2c: 30 ms of audio-clock time for a restart or a load to reach the output


def tone_probe():
    """Pulse 1 at 440.4 Hz, 50 %, volume 15, from its first frame on."""
    return apu_probe(enable(0, 0x01) + pulse_tone(0, A440))


def restart_probe():
    """Silent for its first 60 frames (1 s) after every power-on or reset, then pulse 1 at 440.4 Hz."""
    return apu_probe(enable(60, 0x01) + pulse_tone(60, A440))


def held_note_probe():
    """The triangle at 440.4 Hz held from its first frame on (linear counter control set)."""
    return apu_probe(enable(0, 0x04) + triangle_tone(0, 126))


CHANNEL_SEGMENTS = [("pulse 1", pulse_hz(A440)), ("pulse 2", pulse_hz(168)), ("triangle", triangle_hz(253)),
                    ("noise", None), ("DMC", CPU_HZ / (DMC_RATES[15] * 32))]


def channels_probe():
    """Each channel for 0.5 s in turn, a quarter second of silence between: pulse 1 at 440.4 Hz, pulse 2
    at 661.9 Hz, the triangle at 220.2 Hz, noise (period index 8), and a DMC sample (rate 15, the
    FF FF 00 00 bytes at $E000, 2033 bytes: a 1035.7 Hz wave for 0.49 s). It starts after 1 s."""
    script = (enable(60, 0x01) + pulse_tone(60, A440) + enable(90, 0x00)
              + enable(105, 0x02) + pulse_tone(105, 168, ch=2) + enable(135, 0x00)
              + enable(150, 0x04) + triangle_tone(150, 253) + enable(180, 0x00)
              + enable(195, 0x08) + noise_tone(195, 8) + enable(225, 0x00)
              + [(240, 0x4011, 64), (240, 0x4010, 0x0F), (240, 0x4012, 0x80), (240, 0x4013, 0x7F)]
              + enable(240, 0x10))
    return apu_probe(script, {0xE000: repeat(b"\xff\xff\x00\x00", dmc_length(0x7F))})


def is_title(title):
    return lambda rgb: same_picture(rgb, title)


def heard(cap, label):
    assert cap.quanta, f"{label}: nothing reached the page's AudioContext destination ({cap.errors})"


def last_seconds(cap, seconds):
    """The last `seconds` of the latest context's quanta, and its rate."""
    ctx = cap.contexts()[-1]
    end = max(q[1] for q in cap.quanta if q[0] == ctx)
    return cap.samples(ctx, end - seconds, end + 1), cap.rate(ctx)


def windows_after(cap, mark, t0, t1):
    """For each context, the samples from t0 to t1 seconds (its own clock) after the mark; a context
    made after the mark counts from its first quantum."""
    out = []
    for ctx in cap.contexts():
        times = mark[2]
        start = times[ctx] if ctx < len(times) else min(q[1] for q in cap.quanta if q[0] == ctx)
        out.append((ctx, cap.samples(ctx, start + t0, start + t1)))
    return out


# ---------------------------------------------------------------- J1 with sound

@DELTA
def test_j1_nes15_from_load_to_its_end_with_sound_and_a_restart(site, browser):
    """purpose: J1 whole for an NROM game, as a person meets it, under the browser's autoplay policy
    (simulated): open the page; give nes15 to the file input (no click anywhere); its title is on the
    canvas within 5 s, the #sound-off indicator shows, and no sound is out; the first key press, Enter
    (Start), starts the game (J1r: the player only ever presses keys) and the sound: #sound-off is
    hidden within 1 s, and over the 5 s from the press the output is above the -60 dBFS floor (J1
    step 5; J1i: the window starts at the press that starts its music, play.s "Start the background
    music"); then, stepped frame by frame (nesTest), Select turns on the game's auto-solver and the
    puzzle ends: the message line reads "SOLVED!" (gfx.s msg1, read with the game's own CHR) within
    3600 frames (J1 step 6, its end as the game shows it); Power brings the title back at frame 60 (J1
    step 7); the page loaded exactly once, with no page error.
    origin: design"""
    title = nes15_title()
    with AudioTab(browser, site, policy=True) as tab:
        tab.load(str(nes15()))
        assert tab.wait_for(is_title(title), 5.0)[0] is not None, "no title within 5 s of loading"
        tab.wait(0.5)
        assert tab.sound_off_shown(), "no visible #sound-off while the sound can't start (no gesture yet)"
        before = tab.capture()
        loud = [q for q in before.quanta if dbfs(q[3]) > FLOOR_DBFS]
        assert not loud, f"{len(loud)} quanta of sound before any gesture"
        tab.press("Enter")
        shown = True
        for _ in range(20):
            tab.wait(0.05)
            shown = tab.sound_off_shown()
            if not shown:
                break
        assert not shown, "#sound-off still shows 1 s after the first key press"
        tab.wait(5.3)
        cap = tab.capture()
        heard(cap, "after the first key press")
        press = cap.mark("keydown:Enter")
        assert press, "the key press wasn't seen by the page"
        music = [w for _, w in windows_after(cap, press, 0.0, 5.0)]
        level = max(dbfs(w) for w in music)
        assert level > FLOOR_DBFS, f"the 5 s after Start measure {level:.1f} dBFS: no music"
        tab.pause()
        tab.down("ShiftRight")
        tab.step(3)
        tab.up("ShiftRight")
        msg, stepped = None, 0
        while stepped < 3600 and msg != "solved":
            tab.step(60)
            stepped += 60
            msg = nes15_message(tab.canvas())
        assert msg == "solved", f"after {stepped} frames of the auto-solver the message line reads {msg!r}"
        tab.click("power")
        assert tab.frames() == 0, f"Power left the frame count at {tab.frames()}"
        tab.step(NES15_TITLE_FRAME)
        assert same_picture(tab.canvas(), title), "Power didn't bring the title back by frame 60"
        assert tab.loaded_once() and not tab.errors, (tab.navigations, tab.errors)


@DELTA
def test_on_nes15s_silent_title_the_page_is_below_the_floor_and_its_music_above(site, browser):
    """purpose: J1h at the page's output, with its sound running: nes15's title makes no sound, and
    the 1.5 s of output before Enter measure below -60 dBFS with DC removed (whatever DC the mix or the
    page leaves) though at least 1.4 s of it was rendered; the 5 s after Enter starts the game measure
    above it.
    origin: design"""
    title = nes15_title()
    with AudioTab(browser, site) as tab:
        tab.load(str(nes15()))
        assert tab.wait_for(is_title(title), 5.0)[0] is not None, "no title within 5 s of loading"
        tab.wait(2.0)
        tab.press("Enter")
        tab.wait(5.3)
        cap = tab.capture()
        heard(cap, "on the title")
        press = cap.mark("keydown:Enter")
        ctx = cap.contexts()[-1]
        assert press and ctx < len(press[2]), "the sound didn't start with the game's load (no context at the key press)"
        at = press[2][ctx]
        quiet = cap.samples(ctx, at - 1.5, at)
        assert len(quiet) >= 1.4 * cap.rate(ctx), f"only {len(quiet)} samples rendered in the 1.5 s on the title"
        assert dbfs(quiet) < FLOOR_DBFS, f"the silent title measures {dbfs(quiet):.1f} dBFS"
        music = cap.samples(ctx, at, at + 5.0)
        assert dbfs(music) > FLOOR_DBFS, f"the 5 s after Enter measure {dbfs(music):.1f} dBFS"


# ---------------------------------------------------------------- J1k, J1j

@DELTA
@pytest.mark.parametrize("rate", [44100, 48000])
def test_a_440_hz_pulse_reads_440_hz_at_the_output_at_44_1_and_48_khz(site, browser, rate, tmp_path):
    """purpose: J1k: with the page's AudioContext at 44.1 or 48 kHz (a device at that rate), the tone
    probe's 440.4 Hz pulse (period 253) measures 440.4 Hz within 1 % where it leaves the page (the
    resampling the axes allow is 0.5 %), over 3 s after 1.5 s of play; at most 1 % of the 128-sample
    quanta in those 3 s drop out (peak-to-peak under a quarter of the median: an underrun's silence; a
    rate mismatch starves the output by about 8 %; exactly 0 underruns is J3's number, I5's); and
    #sound-off is hidden while it sounds.
    origin: design"""
    with AudioTab(browser, site, rate=rate) as tab:
        tab.load(write(tmp_path, "tone.nes", tone_probe()))
        tab.wait(4.5)
        cap = tab.capture()
        heard(cap, "the tone")
        x, got_rate = last_seconds(cap, 3.0)
        assert got_rate == rate, f"the context ran at {got_rate} Hz, not {rate}"
        want = pulse_hz(A440)
        got = pitch(x, got_rate)
        assert abs(got - want) <= want * 0.01, f"at {rate} Hz the tone reads {got:.2f} Hz, want {want:.2f} +- 1 %"
        ctx = cap.contexts()[-1]
        end = max(q[1] for q in cap.quanta if q[0] == ctx)
        p2p = [peak_to_peak(q[3]) for q in cap.quanta if q[0] == ctx and end - 3.0 <= q[1]]
        typical = sorted(p2p)[len(p2p) // 2]
        gaps = sum(1 for v in p2p if v < typical / 4)
        assert typical > 0 and gaps <= len(p2p) // 100, f"{gaps} of {len(p2p)} quanta dropped out"
        assert not tab.sound_off_shown(), "#sound-off shows while the sound plays"


def dc_probe():
    """Nothing but the DMC's DAC set to 64 ($4011): the mix sits at a constant level (DC) and nothing
    sounds."""
    return apu_probe([(0, 0x4011, 64)])


@DELTA
def test_the_pages_output_carries_no_dc(site, browser, tmp_path):
    """purpose: J1h at the speakers: the mix's DC (the core's stream is the mixer's level, 0.35 here
    with the DAC at 64, and a stopped triangle holds a level too) is removed before the output, as the
    console's own output is AC-coupled (NESdev "APU Mixer": its high-pass filters), so a held level, an
    underrun's gap or a restart can't click: with dc_probe running, the output's mean over 2 s, after
    1.5 s, is within 0.002 of zero, and the output was rendered (at least 1.9 s of it).
    origin: design"""
    with AudioTab(browser, site) as tab:
        tab.load(write(tmp_path, "dc.nes", dc_probe()))
        tab.wait(3.6)
        cap = tab.capture()
        heard(cap, "the DC probe")
        x, rate = last_seconds(cap, 2.0)
        assert len(x) >= 1.9 * rate, f"only {len(x)} samples rendered in 2 s"
        mean = sum(x) / len(x)
        assert abs(mean) <= 0.002, f"the output sits at {mean:.4f}: the mix's DC reaches the speakers"


@DELTA
def test_after_a_load_with_no_click_sound_starts_by_the_first_key_press(site, browser, tmp_path):
    """purpose: J1j: under the browser's autoplay policy (simulated), a ROM loaded through the file
    input with no click plays silent, with the #sound-off indicator visible, for its first second;
    within 0.5 s of the first key press (Up, which the tone probe ignores) the indicator is hidden and
    the tone is at the output (above the -60 dBFS floor).
    origin: design"""
    with AudioTab(browser, site, policy=True) as tab:
        tab.load(write(tmp_path, "tone.nes", tone_probe()))
        tab.wait(1.0)
        assert tab.sound_off_shown(), "no visible #sound-off before the first gesture"
        early = tab.capture()
        loud = [q for q in early.quanta if dbfs(q[3]) > FLOOR_DBFS]
        assert not loud, f"{len(loud)} quanta of sound before any gesture: the policy wasn't met"
        tab.press("ArrowUp", hold_ms=50)
        tab.wait(0.6)
        cap = tab.capture()
        key = cap.mark("keydown:ArrowUp")
        assert key, "the key press wasn't seen by the page"
        first = min((q[2] for q in cap.quanta if dbfs(q[3]) > FLOOR_DBFS), default=None)
        assert first is not None and first - key[1] <= 500, \
            f"the first sound came {None if first is None else round(first - key[1])} ms after the key press"
        assert not tab.sound_off_shown(), "#sound-off still shows 0.6 s after the key press"


@DELTA
def test_opened_from_a_file_url_the_page_says_to_serve_it(browser):
    """purpose: J1j: opened straight from disk (file://.../src/web/index.html), where its module
    scripts and worklet can't load, the page shows a visible #message that names the command that
    serves it, `node src/web/serve.ts` (a loose word pin: "serve.ts").
    origin: design"""
    context = browser.new_context()
    try:
        page = context.new_page()
        page.goto((ROOT / "src" / "web" / "index.html").as_uri(), wait_until="load", timeout=20000)
        page.wait_for_timeout(1500)
        msg = page.locator("#message")
        shown = msg.count() == 1 and msg.is_visible()
        text = msg.inner_text() if msg.count() == 1 else ""
        assert shown and "serve.ts" in text, f"#message visible: {shown}, text {text!r}"
    finally:
        context.close()


# ---------------------------------------------------------------- J1n, J2c, J2g

@DELTA
@pytest.mark.parametrize("button", ["reset", "power"])
def test_no_sound_from_before_a_restart_is_heard_after_it(site, browser, button, tmp_path):
    """purpose: J1n: with restart_probe's tone playing, a click on #reset or #power restarts it, and it
    is silent for its first second after any restart; so from 30 ms (audio clock) after the click to
    0.85 s, the output is below the -60 dBFS floor: no sample made before the restart is heard after
    it (a buffer of old samples would carry the tone on); and the tone is back 1.3-1.9 s after.
    origin: design"""
    with AudioTab(browser, site) as tab:
        tab.load(write(tmp_path, "restart.nes", restart_probe()))
        tab.wait(2.5)
        tab.click(button)
        tab.wait(2.1)
        cap = tab.capture()
        heard(cap, "the tone")
        click = cap.mark(f"click:{button}")
        ctx = cap.contexts()[-1]
        assert click and ctx < len(click[2]), f"no sound was playing when {button} was clicked"
        playing = cap.samples(ctx, click[2][ctx] - 0.5, click[2][ctx])
        assert dbfs(playing) > -40, f"the tone wasn't playing before the click ({dbfs(playing):.1f} dBFS)"
        for c, w in windows_after(cap, click, SLACK, 0.85):
            assert dbfs(w) < FLOOR_DBFS, \
                f"context {c}: {dbfs(w):.1f} dBFS from 30 ms to 0.85 s after {button}: the old tone carried on"
        back = cap.samples(ctx, click[2][ctx] + 1.3, click[2][ctx] + 1.9)
        assert dbfs(back) > -40, f"the tone didn't come back after {button} ({dbfs(back):.1f} dBFS)"


@DELTA
def test_no_sound_of_the_previous_game_is_heard_after_a_load(site, browser, tmp_path):
    """purpose: J2c and J2g (I4's share): while game A holds a long triangle note (440.4 Hz), game B
    (restart_probe, silent for its first second) is loaded through the same file input; from 30 ms
    after the load to 0.85 s, in every AudioContext the page has, the output is below the -60 dBFS
    floor: no sample of A after the switch, and A's note doesn't carry into B; B's tone is heard
    1.3-1.9 s after the load.
    origin: design"""
    with AudioTab(browser, site) as tab:
        tab.load(write(tmp_path, "a.nes", held_note_probe()))
        tab.wait(2.0)
        tab.load(write(tmp_path, "b.nes", restart_probe()))
        tab.wait(2.1)
        cap = tab.capture()
        heard(cap, "game A")
        loads = [m for m in cap.marks if m[0] == "change:rom-file"]
        assert len(loads) == 2, f"{len(loads)} loads seen"
        switch = loads[1]
        ctx = cap.contexts()[0]
        assert ctx < len(switch[2]), "no sound was playing when B was loaded"
        note = cap.samples(ctx, switch[2][ctx] - 0.5, switch[2][ctx])
        hz = pitch(note, cap.rate(ctx))
        assert abs(hz - triangle_hz(126)) <= triangle_hz(126) * 0.01, \
            f"A's note wasn't playing before the switch ({hz:.1f} Hz, {dbfs(note):.1f} dBFS)"
        for c, w in windows_after(cap, switch, SLACK, 0.85):
            assert dbfs(w) < FLOOR_DBFS, f"context {c}: {dbfs(w):.1f} dBFS after the switch: A carried into B"
        last = cap.contexts()[-1]
        start = switch[2][last] if last < len(switch[2]) else min(q[1] for q in cap.quanta if q[0] == last)
        back = cap.samples(last, start + 1.3, start + 1.9)
        assert dbfs(back) > -40, f"B's tone isn't heard after the switch ({dbfs(back):.1f} dBFS)"


# ---------------------------------------------------------------- every channel reaches the speakers

def segments(cap, threshold=-50.0, gap=0.05, least=0.2):
    """The runs of sound in the latest context: quanta above `threshold` dBFS (DC removed), joined
    across gaps up to `gap` seconds, kept when at least `least` seconds long. [(t0, t1), ...]."""
    ctx = cap.contexts()[-1]
    loud = [q[1] for q in cap.quanta if q[0] == ctx and dbfs(q[3]) > threshold]
    runs = []
    for t in loud:
        if runs and t - runs[-1][1] <= gap:
            runs[-1][1] = t
        else:
            runs.append([t, t])
    return [(a, b) for a, b in runs if b - a >= least], ctx


@DELTA
def test_every_channel_is_heard_at_the_output(site, browser, tmp_path):
    """purpose: the five channels reach the speakers (I4's oracle: "audio captured non-silent in the
    browser"; J4 step 3, the DMC heard): channels_probe's five half-second sounds come out of the page
    as five separate runs of sound 0.35-0.65 s long, in order, at their pitches within 1 %: pulse 1
    440.4 Hz, pulse 2 661.9 Hz, the triangle 220.2 Hz, the noise (no steady pitch: its crossings'
    spacing varies by more than 20 %), and the DMC sample 1035.7 Hz.
    origin: design"""
    with AudioTab(browser, site) as tab:
        tab.load(write(tmp_path, "channels.nes", channels_probe()))
        tab.wait(6.0)
        cap = tab.capture()
        heard(cap, "the channels")
        runs, ctx = segments(cap)
        assert len(runs) == 5, f"{len(runs)} runs of sound: {[(round(a, 2), round(b, 2)) for a, b in runs]}"
        for (name, hz), (a, b) in zip(CHANNEL_SEGMENTS, runs):
            assert 0.35 <= b - a <= 0.65, f"{name} sounded for {b - a:.2f} s"
            w = cap.samples(ctx, a + 0.05, b - 0.05)
            if hz is None:
                c, _ = crossings(w)
                d = [y - x for x, y in zip(c, c[1:])]
                mean = sum(d) / len(d) if d else 0
                spread = (sum((v - mean) ** 2 for v in d) / len(d)) ** 0.5 / mean if mean else 0
                assert spread > 0.2, f"the noise has a steady pitch (spacing varies {spread:.2f})"
            else:
                got = pitch(w, cap.rate(ctx))
                assert abs(got - hz) <= hz * 0.01, f"{name}: {got:.1f} Hz, want {hz:.1f} +- 1 %"
