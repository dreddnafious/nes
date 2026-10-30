"""I9: switching games in one page (J2, "nothing from the previous game leaks into the next"), through both
ways in: the browser's file chooser on #rom-file, and the game menu (CONTRACT-I9.md). The page is served on a
free port and driven in headless Chromium; the cartridges are probes built in the case from the spec
(i9_support: dump_probe, mess_probe, note_probe, script_probe; I3's pad_probe and boot_probe), given to the
menu by answering one of its entries' requests with the probe's bytes (Playwright routing). What a fresh page
shows and plays is the reference: B after A must be B on a fresh page."""
import math

import pytest

from i3_support import (BUTTONS, KEY, boot_probe, boot_read, browser, held, nes15, nes15_title, pad_probe,   # noqa: F401
                        pad_read, same_picture, site)
from i4_support import FLOOR_DBFS, dbfs
from i5_support import NTSC_FPS, fit, tone_probe
from i9_support import (DUMP, CollectionTab, best_match, dump_part, dump_probe, dump_read, envelope, fresh_frame,
                        goertzel, load_time, late_probe, menu_rules, mess_probe, NOTE_HZ, note_probe, params, script_probe, write)

DELTA = pytest.mark.xfail(strict=True, reason="delta")
SLACK = 0.030             # CONTRACT-I4: 30 ms of audio-clock time for a load to reach the output
DUMP_FRAME = 30           # the dump probe has shown its dump well before this frame (it takes about 6)


def ask(tab: CollectionTab, way: str, slot: int, path, loads_before=None):
    """Ask for a load, and for the menu wait until it has powered its game on."""
    tab.load_by(way, slot, path)
    if way == "menu":
        assert tab.wait_loads((loads_before or 0) + 1), "the menu entry didn't load its game within 5 s"


# ---------------------------------------------------------------- J2z, J2aa, J2d, J2h, J2i, J2s: B as on a fresh page

@pytest.mark.parametrize("way,a,b,before", params("state", DELTA, "way", "a", "b", "before"))
def test_b_loaded_after_a_powers_on_as_on_a_fresh_page(site, browser, tmp_path, way, a, b, before):
    """purpose: J2's "mapper, PRG-RAM, palette and APU state are reset on each load", whatever A left (J2z,
    J2d, J2h, J2i, J2s): with A playing in real time (the MMC3 mess probe: its IRQ line asserted and never
    acknowledged, every RAM written, horizontal arrangement, every APU channel sounding, the controller
    strobe high; or the dump probe itself after a Reset, J2aa: a load is a power-on, not a Reset), B is
    loaded through the file chooser or the menu: B's dump probe then finds a fresh console (its canvas, the
    dump of CPU RAM, $4015, the controller's shift register, the palette, nametables and their arrangement,
    CHR, OAM, PRG-RAM and any stray IRQ, is index for index a fresh page's at frame 30, no IRQ taken), and
    from 30 ms after the load to 1 s nothing of A is heard (below the -60 dBFS floor, in every context).
    origin: design"""
    roms = {"mess": mess_probe(), "dump0": dump_probe(0), "dump7": dump_probe(7)}
    path_a, path_b = write(tmp_path, "a.nes", roms[a]), write(tmp_path, "b.nes", roms[b])
    want = fresh_frame(browser, site, path_b, DUMP_FRAME)
    rules = menu_rules({0: roms[b]}) if way == "menu" else {}
    with CollectionTab(browser, site, rules=rules, tap=True) as tab:
        tab.pick(path_a)
        tab.wait(1.5)
        if before == "reset":
            tab.click("reset")
            tab.wait(1.0)
        ask(tab, way, 0, path_b, loads_before=tab.loads() if way == "menu" else None)
        tab.wait(1.2)
        frames, rgb = tab.frame_and_canvas()
        cap = tab.capture()
        assert frames >= DUMP_FRAME, f"B ran only {frames} frames in 1.2 s"
        got, fresh = dump_read(rgb), dump_read(want)
        diff = [k for k in DUMP if dump_part(got, k) != dump_part(fresh, k)]
        assert not diff, f"B after A differs from a fresh B in: {diff} " \
                         f"({[(k, dump_part(got, k)[:8], dump_part(fresh, k)[:8]) for k in diff[:3]]})"
        assert rgb == want, "B's canvas after A isn't a fresh page's, index for index"
        assert dump_part(got, "irqs") == [0], f"B took {dump_part(got, 'irqs')[0]} IRQs it never asked for"
        switch = tab.marks()[-1]
        assert cap.quanta, f"nothing reached the destination ({cap.errors})"
        for ctx in cap.contexts():
            t = load_time(cap, switch, ctx)
            w = cap.samples(ctx, t + SLACK, t + 1.0)
            assert dbfs(w) < FLOOR_DBFS, f"context {ctx}: {dbfs(w):.1f} dBFS after the switch: A is still heard"
        assert tab.loaded_once() and not tab.errors, (tab.navigations, tab.errors)


# ---------------------------------------------------------------- J2y, J2j: a key held across a switch

@pytest.mark.parametrize("way", params("ways", DELTA, "way"))
def test_a_key_held_across_a_switch_is_released_until_pressed_again(site, browser, tmp_path, way):
    """purpose: J2y (the file dialog or the menu takes the click, so a key held before the switch never gets
    its keyup): with pad_probe A showing Left held (ArrowLeft down, never released), loading pad_probe B
    through the file chooser or the menu leaves every button released in B (port 1 reads nothing held) for
    as long as the key isn't pressed again; pressing Right then reads Right only. The page loaded once.
    origin: design"""
    pad = pad_probe()
    path = write(tmp_path, "pad.nes", pad)
    rules = menu_rules({0: pad}) if way == "menu" else {}
    with CollectionTab(browser, site, rules=rules) as tab:
        tab.pick(path)
        tab.wait(0.5)
        tab.down(KEY["Left"])
        tab.wait(0.4)
        assert pad_read(tab.canvas())["port1"][:8] == held({"Left"}), "A didn't read Left held"
        ask(tab, way, 0, path, loads_before=tab.loads() if way == "menu" else None)
        tab.wait(0.6)
        got = pad_read(tab.canvas())["port1"][:8]
        assert got == held(set()), f"after the switch B reads {dict(zip(BUTTONS, got))}: the held key leaked"
        tab.up(KEY["Left"])
        tab.down(KEY["Right"])
        tab.wait(0.4)
        assert pad_read(tab.canvas())["port1"][:8] == held({"Right"}), "B didn't read Right when it was pressed"
        tab.up(KEY["Right"])
        assert tab.loaded_once() and not tab.errors, (tab.navigations, tab.errors)


# ---------------------------------------------------------------- J2u, J2f: the same game twice

def test_the_same_file_picked_twice_boots_it_again(site, browser):
    """purpose: J2u and J2f through the file chooser: after each pick #rom-file's value is empty (so a real
    browser fires `change` when the same file is picked again); with nes15 started (Enter, off its title),
    picking nes15 again brings its title back within 5 s and its frame count starts again from 0 (under
    60 frames 0.5 s after the pick, where it was over 150), and the page loaded once.
    origin: design"""
    title = nes15_title()
    with CollectionTab(browser, site) as tab:
        tab.pick(nes15())
        assert tab.page.evaluate("document.getElementById('rom-file').value") == "", "the input kept its value"
        assert tab.wait_for(lambda rgb: same_picture(rgb, title), 5.0)[0] is not None, "no title"
        tab.press("Enter")
        tab.wait(3.0)
        assert not same_picture(tab.canvas(), title), "Enter didn't leave the title"
        before = tab.frames()
        tab.pick(nes15())
        tab.wait(0.5)
        assert before > 150 and tab.frames() < 60, f"frames went from {before} to {tab.frames()}: no power-on"
        assert tab.page.evaluate("document.getElementById('rom-file').value") == "", "the input kept its value"
        assert tab.wait_for(lambda rgb: same_picture(rgb, title), 5.0)[0] is not None, "the second pick showed no title"
        assert tab.loaded_once() and not tab.errors, (tab.navigations, tab.errors)


def test_the_same_menu_entry_chosen_twice_boots_it_again(site, browser, tmp_path):
    """purpose: J2u and J2f through the menu: choosing the entry that is running loads it again: with
    boot_probe (routed through the first entry) at frame 10 after a Reset (RAM counter 2, RAM not clean),
    choosing the same entry again powers it on (nesTest.loads + 1, frames 0) with a fresh console (RAM
    clean, both counters at 1: a load is a power-on, and the battery save is cleared, J2f), and its frame 10
    is a fresh page's.
    origin: design"""
    boot = boot_probe()
    path = write(tmp_path, "boot.nes", boot)
    want = fresh_frame(browser, site, path, 10)
    with CollectionTab(browser, site, rules=menu_rules({0: boot})) as tab:
        tab.pause()
        ask(tab, "menu", 0, None, loads_before=0)
        tab.step(10)
        tab.click("reset")
        tab.step(10)
        assert boot_read(tab.canvas()) == {"ram": 2, "battery": 2, "clean": False}, boot_read(tab.canvas())
        ask(tab, "menu", 0, None, loads_before=1)
        assert tab.loads() == 2 and tab.frames() == 0, f"loads {tab.loads()}, frames {tab.frames()}"
        tab.step(10)
        rgb = tab.canvas()
        assert boot_read(rgb) == {"ram": 1, "battery": 1, "clean": True}, f"the second choice: {boot_read(rgb)}"
        assert rgb == want, "the second choice's frame 10 isn't a fresh page's"
        assert tab.loaded_once() and not tab.errors, (tab.navigations, tab.errors)


# ---------------------------------------------------------------- Q3 across a switch, through the menu

def test_a_battery_save_survives_power_but_not_a_load_through_the_menu(site, browser, tmp_path):
    """purpose: Decided Q3 through the menu (A → B → A starts A without its save): boot_probe (battery
    PRG-RAM) chosen from the menu boots with battery counter 1; Power keeps the save (counter 2, RAM clean);
    choosing another entry (the dump probe) and then boot_probe's entry again starts it with no save
    (counter 1), as on a fresh page.
    origin: design"""
    boot = boot_probe()
    with CollectionTab(browser, site, rules=menu_rules({0: boot, 1: dump_probe(0)})) as tab:
        tab.pause()
        ask(tab, "menu", 0, None, loads_before=0)
        tab.step(10)
        assert boot_read(tab.canvas()) == {"ram": 1, "battery": 1, "clean": True}, boot_read(tab.canvas())
        tab.click("power")
        tab.step(10)
        assert boot_read(tab.canvas()) == {"ram": 1, "battery": 2, "clean": True}, f"after Power: {boot_read(tab.canvas())}"
        ask(tab, "menu", 1, None, loads_before=1)
        tab.step(10)
        ask(tab, "menu", 0, None, loads_before=2)
        tab.step(10)
        assert boot_read(tab.canvas()) == {"ram": 1, "battery": 1, "clean": True}, \
            f"A → B → A kept A's save: {boot_read(tab.canvas())}"
        assert not tab.errors, tab.errors


# ---------------------------------------------------------------- J2ab, J2ac, J2g: no sample of A after the switch

@pytest.mark.parametrize("way", params("ways", DELTA, "way"))
def test_no_sample_of_a_reaches_the_output_after_a_switch(site, browser, tmp_path, way):
    """purpose: J2ab/J2ac (measured at the destination, not the producer) and J2g (mid-note): A holds a
    triangle note at 139.1 Hz; B (late_probe), loaded through the file chooser or the menu while it sounds,
    is silent for 0.3 s and then holds a pulse tone 740 Hz above it. At the destination, in every context the
    page has, each 60 ms block (stepped 30 ms, every sample weighed alike, so a short tail at the start
    counts in full) from 30 ms after the load to 0.33 s, and from 0.55 s (past B's onset) to 2 s, holds A's
    pitch at least 30 dB below where A had it before the switch: no queued tail, no filter memory; and B is
    heard (above -40 dBFS).
    origin: design"""
    a, b = write(tmp_path, "a.nes", note_probe()), late_probe()
    path_b = write(tmp_path, "b.nes", b)
    rules = menu_rules({0: b}) if way == "menu" else {}
    with CollectionTab(browser, site, rules=rules, tap=True) as tab:
        tab.pick(a)
        tab.wait(2.0)
        ask(tab, way, 0, path_b, loads_before=tab.loads() if way == "menu" else None)
        tab.wait(2.3)
        cap = tab.capture()
        assert cap.quanta, f"nothing reached the destination ({cap.errors})"
        switch = tab.marks()[-1]
        first = cap.contexts()[0]
        t = load_time(cap, switch, first)
        note = goertzel(cap.samples(first, t - 0.6, t - 0.05), cap.rate(first), NOTE_HZ, hann=False)
        assert note > 0.01, f"A's note wasn't sounding before the switch (amplitude {note:.4f})"
        for ctx in cap.contexts():
            t = load_time(cap, switch, ctx)
            for k in [t + SLACK + 0.03 * j for j in range(10)] + [t + 0.55 + 0.03 * j for j in range(48)]:
                level = goertzel(cap.samples(ctx, k, k + 0.06), cap.rate(ctx), NOTE_HZ, hann=False)
                assert level < note * 10 ** (-30 / 20), \
                    f"context {ctx}: A's note at {20 * math.log10(max(level, 1e-12) / note):.0f} dB, " \
                    f"{(k - t) * 1000:.0f} ms after the switch"
        last = cap.contexts()[-1]
        t = load_time(cap, switch, last)
        assert dbfs(cap.samples(last, t + 0.6, t + 1.0)) > -40, "B isn't heard after the switch"
        assert not tab.errors, tab.errors


# ---------------------------------------------------------------- J2ad, J2ae, J2c: B's first 2 s as on a fresh page

def from_the_load(tab: CollectionTab, seconds=2.8):
    """The 20 ms envelope of the last load's sound at the destination, from its mark on (a context made by
    the load starts at its first quantum), in the context that plays."""
    cap = tab.capture()
    assert cap.quanta, f"nothing reached the destination ({cap.errors})"
    ctx = cap.contexts()[-1]
    t = load_time(cap, tab.marks()[-1], ctx)
    return envelope(cap.samples(ctx, t, t + seconds), cap.rate(ctx))


def onset(env: list, start=2, level=0.01):
    return next((i for i in range(start, len(env)) if env[i] > level), None)


@pytest.mark.parametrize("way", params("ways", DELTA, "way"))
def test_b_s_first_two_seconds_at_the_output_match_a_fresh_page_s(site, browser, tmp_path, way):
    """purpose: J2's "the new game's first 2 s of audio match a fresh page's" (J2c), on B's own timeline
    (J2ad: indexed from its emulated sample 0, the resampler and filters restarted), with sound in the
    window compared (J2ae). B is script_probe (a pulse sound from frame 0 that changes pitch and level each
    third of a second); loaded after A's held note, through the file chooser or the menu:
      - B is heard from its first sample: it sounds within 200 ms of the load, and its first third of a
        second (20 frames at one level) is heard whole (15+ of its 16.7 bins of 20 ms);
      - from 0.4 s to 1.9 s of B's time its 20 ms RMS envelope correlates at least 0.95 with a fresh page's
        B on the same timeline, its level within 1.5 dB;
      - and the fresh window has sound and changes (its loudest bin at least 2.5x its quietest sounding one).
    origin: design"""
    a, b = write(tmp_path, "a.nes", note_probe()), script_probe()
    path_b = write(tmp_path, "b.nes", b)
    rules = menu_rules({0: b}) if way == "menu" else {}
    with CollectionTab(browser, site, rules=rules, tap=True) as fresh:
        ask(fresh, way, 0, path_b, loads_before=0 if way == "menu" else None)
        fresh.wait(3.0)
        e0 = from_the_load(fresh)
    with CollectionTab(browser, site, rules=rules, tap=True) as tab:
        tab.pick(a)
        tab.wait(2.0)
        ask(tab, way, 0, path_b, loads_before=tab.loads() if way == "menu" else None)
        tab.wait(3.0)
        e1 = from_the_load(tab)
        assert not tab.errors, tab.errors
    on = onset(e1)
    assert on is not None and on <= 10, f"B wasn't heard within 200 ms of the load (onset bin {on})"
    first = e1[on + 1]
    whole = next((k for k in range(on + 1, len(e1)) if abs(e1[k] - first) > 0.2 * first), len(e1)) - on
    assert whole >= 15, f"B's first third of a second was heard for {whole * 20} ms: its first samples were lost"
    mine = e1[on + 20:on + 95]
    corr, shift = max((best_match(mine, e0[s + 20:s + 95], 0)[0], s) for s in range(-15, 26))
    ref = e0[shift + 20:shift + 95]
    sounding = [v for v in ref if v > 0.002]
    assert len(sounding) >= 40, f"the fresh page's B has sound in only {len(sounding)} of {len(ref)} bins (J2ae)"
    assert max(sounding) >= 2.5 * min(sounding), "the fresh window doesn't change: it would test nothing"
    assert corr >= 0.95, f"B after A correlates {corr:.3f} at best with a fresh B's envelope"
    level = sum(mine) / max(sum(ref), 1e-9)
    assert abs(20 * math.log10(max(level, 1e-9))) <= 1.5, f"B after A is {level:.2f}x a fresh B's level"


# ---------------------------------------------------------------- J2e: one loop, one worklet, no growing listeners

@pytest.mark.parametrize("way", params("ways", DELTA, "way"))
def test_one_loop_one_worklet_and_no_new_listeners_after_many_switches(site, browser, tmp_path, way):
    """purpose: J2e (accumulating loops and listeners): after A → B → A and ten more switches (through the
    file chooser, or alternating it with the menu) and five rapid Reset/Power clicks, at 144 Hz every
    refresh runs exactly one requestAnimationFrame callback, the game runs 60.0988 frames a second within
    0.5 %, the page has made exactly one AudioWorkletNode of its own, and its window/document listeners are
    as many as after the first load. The page loaded once.
    origin: design"""
    roms = [str(nes15()), write(tmp_path, "tone.nes", tone_probe()), write(tmp_path, "pad.nes", pad_probe())]
    rules = menu_rules({0: tone_probe()}) if way == "menu" else {}
    with CollectionTab(browser, site, rules=rules, hz=144) as tab:
        tab.pick(roms[0])
        tab.wait(0.5)
        base = tab.listeners()
        for k in range(1, 13):
            if way == "menu" and k % 2:
                ask(tab, "menu", 0, None, loads_before=tab.loads())
            else:
                tab.pick(roms[k % 3])
            tab.wait(0.3)
        for button in ("reset", "power") * 2 + ("reset",):
            tab.click(button)
            tab.wait(0.1)
        tab.wait(1.0)
        t0 = tab.now()
        tab.wait(5)
        ticks = [t for t in tab.ticks() if t[0] >= t0]
        assert len(ticks) >= 5 * 144 * 0.9, f"only {len(ticks)} refreshes in 5 s"
        cbs = sorted({n for _, _, n in ticks})
        assert cbs == [1], f"refreshes ran {cbs} rAF callbacks: not one loop"
        slope, _ = fit([(t / 1000, f) for t, f, _ in ticks if f >= 0])
        assert abs(slope - NTSC_FPS) / NTSC_FPS <= 0.005, f"{slope:.2f} frames a second after the switches"
        made = tab.worklets()
        assert sum(made.values()) == 1, f"the page made AudioWorkletNodes {made}: not one"
        assert tab.listeners() == base, f"window/document listeners went from {base} to {tab.listeners()}"
        assert tab.loaded_once() and not tab.errors, (tab.navigations, tab.errors)
