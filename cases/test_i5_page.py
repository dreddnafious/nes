"""I5: the page over time (J3, "a long sitting stays smooth"). The page is served on a free port and driven
in headless Chromium with I5's pacing stub, hidden-tab simulation and tap installed (CONTRACT-I5.md)."""
import pytest

from i3_support import browser, nes15, site   # noqa: F401 (fixtures)
from i5_support import NTSC_FPS, X, I5Tab, fit, increments, tone_probe, write

DELTA = pytest.mark.xfail(strict=True, reason="delta")

WARMUP_S = 1.5


def start(tab: I5Tab, rom: str, settle=WARMUP_S):
    tab.hooks()
    tab.load(rom)
    tab.wait(settle)


@pytest.mark.parametrize("hz", [60, 59.94])
def test_at_a_60_hz_display_every_refresh_shows_exactly_one_new_frame(site, browser, hz, tmp_path):
    """purpose: J1o's "no doubled or skipped frame at 60 Hz" and the frame pacing open item, on its proposed
    answer (lock to a display within 1 % of 60 Hz): with requestAnimationFrame at 60 or 59.94 Hz of real
    time, every refresh over 12 s emulates exactly one new frame (none emulates 0 or 2). The sound holds
    across it: no underrun reaches the destination in that window.
    origin: design"""
    with I5Tab(browser, site, hz=hz) as tab:
        start(tab, write(tmp_path, "tone.nes", tone_probe()))
        t0 = tab.now()
        tab.wait(12)
        ticks = tab.ticks()
        steps = increments(ticks, t0, tab.now())
        assert len(steps) >= 12 * hz * 0.9, f"only {len(steps)} refreshes in 12 s: the stub didn't run"
        odd = {n: steps.count(n) for n in set(steps) if n != 1}
        assert not odd, f"at {hz} Hz, {sum(odd.values())} of {len(steps)} refreshes didn't show exactly one new frame: {odd}"
        q = tab.quanta()
        load = q.marks_of("change:rom-file")[0][2]
        bad = q.underruns(load + 1.0, 1e9)
        assert not bad, f"{len(bad)} underruns at the destination, first at {bad[0] - load:.2f} s after the load"
        assert not tab.errors, tab.errors


@pytest.mark.parametrize("hz", [75, 120, 144])
def test_at_other_display_rates_frames_follow_real_time_without_bursts(site, browser, hz, tmp_path):
    """purpose: J3's "the frame rate holds" at displays far from 60 Hz (J3v, for real): with
    requestAnimationFrame at 75, 120 or 144 Hz, the game runs 60.0988 frames a second within 0.5 % over 8 s,
    and the frame count never strays more than 1.5 frames from a straight line at that rate: no burst of
    frames to catch up, no stall.
    origin: design"""
    with I5Tab(browser, site, hz=hz) as tab:
        start(tab, write(tmp_path, "tone.nes", tone_probe()))
        t0 = tab.now()
        tab.wait(8)
        pts = [(t / 1000, f) for t, f in tab.ticks() if t >= t0 and f >= 0]
        assert len(pts) >= 8 * hz * 0.9, f"only {len(pts)} refreshes in 8 s at {hz} Hz"
        slope, _ = fit(pts)
        assert abs(slope - NTSC_FPS) / NTSC_FPS <= 0.005, f"{slope:.3f} frames a second at {hz} Hz"
        base = sorted(f - NTSC_FPS * t for t, f in pts)[len(pts) // 2]
        worst = max(abs(f - NTSC_FPS * t - base) for t, f in pts)
        assert worst <= 1.5, f"at {hz} Hz the frame count strayed {worst:.2f} frames from real time"
        assert not tab.errors, tab.errors


def test_j3_a_long_sitting_stays_smooth(site, browser, tmp_path):
    """purpose: J3 walked through ("I play one game for a long time. Then I switch to another tab ... and come
    back"), at 60 Hz, on the tone probe, for 2 minutes of real time:
      1. load and play: A pressed at 5 s is heard within 100 ms (keydown to the louder tone at the
         destination, audio-clock time);
      2. keep playing: from 1 s after the load, no underrun reaches the destination, and the heap at 60 s
         (after a GC) is taken;
      3. hide the tab for 12 s at 70 s: no frame is emulated while hidden; the first refresh after it shows
         emulates at most 2 frames; the tone is back within 200 ms of the show;
      4. carry on: from 1 s after the show, no underrun; A pressed at 115 s is heard within 100 ms, and no
         more than 10 ms later than at 5 s; the heap at the end is within 10 % of the heap at 60 s;
    and the page loaded once.
    origin: design"""
    with I5Tab(browser, site, hz=60) as tab:
        start(tab, write(tmp_path, "tone.nes", tone_probe()), settle=4.5)
        tab.press(X, 300)
        tab.wait(55)
        heap1 = tab.heap()
        tab.wait(10)
        before = tab.hide()
        tab.wait(12)
        hidden_frames = tab.page.evaluate("window.nesTest.frames") - before
        n_before_show = len(tab.ticks())
        tab.show()
        tab.wait(1.0)
        after = tab.ticks()[n_before_show:]
        tab.wait(37)
        tab.press(X, 300)
        tab.wait(2)
        heap2 = tab.heap()
        q = tab.quanta()

        assert hidden_frames == 0, f"{hidden_frames} frames were emulated while the tab was hidden"
        assert after, "no refresh ran after the tab showed"
        catch_up = after[0][1] - before
        assert catch_up <= 2, f"the first refresh after showing emulated {catch_up} frames of catch-up"

        load = q.marks_of("change:rom-file")[0][2]
        hide = q.marks_of("visibility:hidden")[0][2]
        show = q.marks_of("visibility:visible")[0][2]
        bad = q.underruns(load + 1.0, hide) + q.underruns(show + 1.0, 1e9)
        assert not bad, f"{len(bad)} underruns at the destination, first at {bad[0] - load:.1f} s after the load"
        base = q.median_rms(hide - 3.0, hide)
        assert base > 0, "the tone wasn't heard before the tab was hidden"
        back = q.onset(show, base * 0.7, within=1.0)
        assert back is not None and back - show <= 0.200, \
            f"the tone came back {('%.0f ms' % ((back - show) * 1000)) if back else 'not within 1 s'} after the show"

        lat = []
        for label, _, t in q.marks_of("keydown:" + X):
            rest = q.median_rms(t - 1.0, t)
            on = q.onset(t, rest * 2.5, within=0.5)
            assert on is not None, f"A pressed at {t - load:.1f} s was never heard"
            lat.append(on - t)
        assert len(lat) == 2, f"{len(lat)} presses of A were recorded"
        assert all(x <= 0.100 for x in lat), f"key-to-sound latency {[round(x * 1000) for x in lat]} ms, over 100 ms"
        assert lat[1] - lat[0] <= 0.010, f"latency grew from {lat[0] * 1000:.0f} to {lat[1] * 1000:.0f} ms"
        assert heap2 <= heap1 * 1.10, f"the heap grew from {heap1} to {heap2} bytes over the sitting"
        assert tab.loaded_once()
        assert not tab.errors, tab.errors


def test_hidden_for_an_hour_the_game_carries_on_where_it_left_off(site, browser, tmp_path):
    """purpose: J3's "when I come back from the other tab, the game simply carries on: it doesn't race to
    catch up", at its edge (J3ad, later): the tab hidden while the clock moves on an hour (the simulation's
    clock jumps 3,600,000 ms) emulates no frame while hidden, at most 2 at the first refresh after it shows,
    then 60.1 a second again (within 1 %, over 3 s); and the tone is back within 200 ms of the show.
    origin: design"""
    with I5Tab(browser, site, hz=60) as tab:
        start(tab, write(tmp_path, "tone.nes", tone_probe()), settle=3)
        before = tab.hide()
        tab.wait(2)
        assert tab.page.evaluate("window.nesTest.frames") == before, "frames were emulated while hidden"
        n = len(tab.ticks())
        tab.show(jump_ms=3_600_000)
        tab.wait(3.5)
        after = tab.ticks()[n:]
        assert after and after[0][1] - before <= 2, f"{after[0][1] - before if after else '?'} frames of catch-up"
        pts = [(t / 1000, f) for t, f in after[6:]]
        slope, _ = fit(pts)
        assert abs(slope - NTSC_FPS) / NTSC_FPS <= 0.01, f"{slope:.2f} frames a second after an hour hidden"
        q = tab.quanta()
        show = q.marks_of("visibility:visible")[0][2]
        hide = q.marks_of("visibility:hidden")[0][2]
        base = q.median_rms(hide - 1.0, hide)
        back = q.onset(show, base * 0.7, within=1.0)
        assert back is not None and back - show <= 0.200, "the tone wasn't back within 200 ms of the show"
        assert not tab.errors, tab.errors


def test_a_game_loaded_while_the_tab_is_hidden_waits_until_it_shows(site, browser, tmp_path):
    """purpose: J3ae (at once): a load that finishes while the tab is hidden powers the game on (frame 0) but
    emulates no frame and sends no sound until the tab shows; then it runs and is heard.
    origin: design"""
    with I5Tab(browser, site, hz=60) as tab:
        tab.hooks()
        tab.hide()
        tab.load(write(tmp_path, "tone.nes", tone_probe()))
        tab.wait(3)
        assert tab.page.evaluate("window.nesTest.frames") == 0, "the game ran while the tab was hidden"
        q = tab.quanta()
        load = q.marks_of("change:rom-file")[0][2] if q.rows else None
        if load is not None:
            heard = [r for r in q.window(load, 1e9) if r[2] > 0.001]
            assert not heard, f"{len(heard)} quanta of sound reached the destination while hidden"
        tab.show()
        tab.wait(2)
        assert tab.page.evaluate("window.nesTest.frames") >= 90, "the game didn't run after the tab showed"
        q = tab.quanta()
        show = q.marks_of("visibility:visible")[0][2]
        assert q.median_rms(show + 1.0, show + 2.0) > 0.005, "the game wasn't heard after the tab showed"
        assert not tab.errors, tab.errors


def test_one_loop_runs_after_loads_and_twenty_hide_show_cycles(site, browser, tmp_path):
    """purpose: J3w (again): after three loads (nes15, the tone probe, nes15) and twenty quick hide/show
    cycles, exactly one loop runs: at 144 Hz every refresh over 6 s runs exactly one requestAnimationFrame
    callback (a second loop would be a second callback), and the game runs 60.0988 frames a second within
    0.5 %.
    origin: design"""
    with I5Tab(browser, site, hz=144) as tab:
        tab.hooks()
        tone = write(tmp_path, "tone.nes", tone_probe())
        for rom in (str(nes15()), tone, str(nes15())):
            tab.load(rom)
            tab.wait(0.5)
        for _ in range(20):
            tab.hide()
            tab.wait(0.05)
            tab.show()
            tab.wait(0.05)
        tab.wait(1.0)
        t0 = tab.now()
        tab.wait(6)
        cbs = [n for t, n in tab.callbacks() if t >= t0]
        assert len(cbs) >= 6 * 144 * 0.9, f"only {len(cbs)} refreshes in 6 s"
        assert set(cbs) == {1}, f"refreshes ran {sorted(set(cbs))} rAF callbacks: not one loop"
        pts = [(t / 1000, f) for t, f in tab.ticks() if t >= t0 and f >= 0]
        slope, _ = fit(pts)
        assert abs(slope - NTSC_FPS) / NTSC_FPS <= 0.005, f"{slope:.2f} frames a second after loads and hide/show"
        assert tab.loaded_once()
        assert not tab.errors, tab.errors


def test_twenty_loads_leave_the_heap_where_the_first_left_it(site, browser, tmp_path):
    """purpose: J3ab (again) and J3's "the JS heap ... within 10 %": switching games twenty times (nes15 and
    the tone probe in turn, 1 s each) leaves the page's heap, after a GC, within 10 % of the heap after the
    first load and its first second: no console, buffer, audio node or listener of an old game is kept.
    origin: design"""
    with I5Tab(browser, site, hz=60) as tab:
        tab.hooks()
        tone = write(tmp_path, "tone.nes", tone_probe())
        roms = [str(nes15()), tone]
        tab.load(roms[0])
        tab.wait(1)
        first = tab.heap()
        for k in range(1, 21):
            tab.load(roms[k % 2])
            tab.wait(1)
        tab.load(roms[0])
        tab.wait(1)
        last = tab.heap()
        assert last <= first * 1.10, f"the heap grew from {first} to {last} bytes over 20 loads"
        assert tab.loaded_once()
        assert not tab.errors, tab.errors
