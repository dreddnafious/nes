"""I9: J2, "an evening with the collection", walked whole as one narrative in one page load, through the
page's real entry points only: the game menu and the file chooser, the keyboard, the canvas, and the sound
at the AudioContext's destination (CONTRACT-I9.md). Game A is Chase (mapper 2, UxROM), game B is File Fixers
(mapper 4, MMC3), both vendored; A is chosen from the menu, B from the menu, and A again from the file
chooser (J2ag). The page runs in real time; each picture is read with its frame count in one task and
compared with a fresh page's picture at that same emulated frame (J2t)."""
import math

import pytest

from i3_support import browser, site   # noqa: F401 (fixtures)
from i4_support import FLOOR_DBFS, dbfs
from i9_support import (GAMES, J1_GAMES, START_KEY, CollectionTab, best_match, digest, envelope, fresh_frame,
                        load_time)

DELTA = pytest.mark.xfail(strict=True, reason="delta")
CHASE = next(g for g in J1_GAMES if g["id"] == "chase")
FIXERS = next(g for g in J1_GAMES if g["id"] == "file-fixers")
A, B = GAMES / CHASE["file"], GAMES / FIXERS["file"]


def chase_title(browser, site) -> set:
    """Chase's title once it has settled (frames 300-363 of a fresh page: the logo down, PRESS START
    blinking), as digests."""
    with CollectionTab(browser, site) as tab:
        tab.pause()
        tab.pick(A)
        tab.step(CHASE["title_at"])
        seen = set()
        for _ in range(CHASE["cycle"]):
            seen.add(digest(tab.canvas()))
            tab.step(1)
        return seen


def sound_from(tab: CollectionTab, k=-1, seconds=3.4):
    """The 20 ms envelope of what reached the destination from the k-th load's mark on, and its samples from
    30 ms after the mark (CONTRACT-I4's slack)."""
    cap = tab.capture()
    assert cap.quanta, f"nothing reached the destination ({cap.errors})"
    ctx = cap.contexts()[-1]
    t = load_time(cap, tab.marks()[k], ctx)
    x = cap.samples(ctx, t, t + seconds)
    return envelope(x, cap.rate(ctx)), x[int(0.030 * cap.rate(ctx)):], cap, ctx, t


def onset(env: list, level=0.005):
    return next((i for i in range(2, len(env)) if env[i] > level), None)


def matches_fresh(e_after: list, e_fresh: list):
    """B's envelope after the switch against a fresh page's, on B's own timeline (each from the bin where it
    is first heard, the two lined up within 200 ms of that): (correlation, level ratio, the fresh window)
    over 0.4-1.9 s after B is first heard."""
    on, on0 = onset(e_after), onset(e_fresh)
    assert on is not None and on <= 50, f"B wasn't heard within 1 s of its load (onset bin {on})"
    assert on0 is not None, "a fresh page's B was never heard"
    mine = e_after[on + 20:on + 95]
    corr, shift = max((best_match(mine, e_fresh[s + 20:s + 95], 0)[0], s)
                      for s in range(max(0, on0 - 10), on0 + 11))
    ref = e_fresh[shift + 20:shift + 95]
    return corr, sum(mine) / max(sum(ref), 1e-9), ref


def wait_title(tab: CollectionTab, seen: set, seconds: float) -> bool:
    for _ in range(int(seconds / 0.1)):
        if digest(tab.canvas()) in seen:
            return True
        tab.wait(0.1)
    return False


def test_j2_an_evening_with_the_collection(site, browser):
    """purpose: J2 walked through in one Playwright session and one page load, A → B → A:
      1. A (Chase, mapper 2) is chosen from the game menu: its title at the frame it has reached is, pixel
         for pixel, a fresh page's at that frame, and its silent title is silent at the destination (below
         the -60 dBFS floor from 30 ms to 2 s after the click), as on a fresh page; Start plays it, and with
         nobody playing it goes to game over within 45 s; Start on its game-over screen brings its title back;
      2. B (File Fixers, mapper 4) is chosen from the menu without a reload: its title is a fresh page's at
         the same frame; its first 2 s of sound (the title music, from when it is first heard, within 1 s) match a
         fresh page's on B's own timeline
         (envelope correlation >= 0.95, level within 1.5 dB; the fresh window has sound in at least 15 of
         its 75 bins, J2ae);
      3. B plays: Start takes it off its title to its desktop, its music goes on (above -40 dBFS), and the
         d-pad moves things on screen (Right held for half a second changes the picture);
      4. A is picked again, through the file chooser (J2ag): its title at its frame is a fresh page's, and
         from 30 ms to 2 s after the pick nothing is heard: none of B's music leaks into A's silent title;
      5. A plays as it did the first time: Start takes it off its title;
    and the page has loaded exactly once (nesTest.loads 3), with no page error.
    origin: design"""
    seen = chase_title(browser, site)
    with CollectionTab(browser, site, tap=True) as fresh:
        fresh.pick(B)
        fresh.wait(3.6)
        e_fresh, _, _, _, _ = sound_from(fresh)
    with CollectionTab(browser, site, tap=True) as tab:
        # 1. A from the menu, to game over.
        tab.choose(A.name)
        assert tab.wait_loads(1), "A didn't load from the menu"
        tab.wait(6.0)
        fa1, rgb_a1 = tab.frame_and_canvas()
        _, x, _, _, _ = sound_from(tab, -1, 2.0)
        assert dbfs(x) < FLOOR_DBFS, f"A's silent title is heard at {dbfs(x):.1f} dBFS"
        tab.press(START_KEY)
        tab.wait(2.0)
        assert digest(tab.canvas()) not in seen, "Start didn't take A off its title"
        tab.wait(45.0)
        tab.press(START_KEY)
        assert wait_title(tab, seen, 10.0), "A didn't reach game over within 45 s, or Start there didn't bring its title back"
        # 2. B from the menu.
        tab.clear_tap()
        tab.choose(B.name)
        assert tab.wait_loads(2), "B didn't load from the menu"
        tab.wait(3.6)
        fb, rgb_b = tab.frame_and_canvas()
        e_b, _, _, _, _ = sound_from(tab)
        corr, level, ref = matches_fresh(e_b, e_fresh)
        sounding = [v for v in ref if v > 0.002]
        assert len(sounding) >= 15 and max(sounding) >= 2 * min(sounding), "the fresh window holds no changing sound (J2ae)"
        assert corr >= 0.95 and abs(20 * math.log10(max(level, 1e-9))) <= 1.5, \
            f"B's first 2 s after the switch: correlation {corr:.3f}, level {level:.2f}x a fresh page's"
        # 3. B plays.
        title_b = digest(rgb_b)
        tab.press(START_KEY)
        tab.wait(2.0)
        desk = tab.canvas()
        assert digest(desk) != title_b, "Start didn't take B off its title"
        tab.down("ArrowRight")
        tab.wait(0.5)
        tab.up("ArrowRight")
        tab.wait(0.3)
        assert tab.canvas() != desk, "the d-pad changed nothing in B"
        cap = tab.capture()
        ctx = cap.contexts()[-1]
        end = max(q[1] for q in cap.quanta if q[0] == ctx)
        assert dbfs(cap.samples(ctx, end - 1.0, end + 1)) > -40, "B's music isn't heard in play"
        # 4. A again, through the file chooser.
        tab.clear_tap()
        tab.pick(A)
        assert tab.wait_loads(3), "A didn't load from the file chooser"
        tab.wait(6.0)
        fa2, rgb_a2 = tab.frame_and_canvas()
        _, x, _, _, _ = sound_from(tab, -1, 2.0)
        assert dbfs(x) < FLOOR_DBFS, f"{dbfs(x):.1f} dBFS after A was picked again: B leaked"
        # 5. A plays as the first time.
        tab.press(START_KEY)
        tab.wait(2.0)
        assert digest(tab.canvas()) not in seen, "Start didn't take A off its title the second time"
        assert tab.loads() == 3 and tab.loaded_once(), f"loads {tab.loads()}, navigations {tab.navigations}"
        assert not tab.errors, tab.errors
    assert rgb_a1 == fresh_frame(browser, site, str(A), fa1), f"A's title at frame {fa1} isn't a fresh page's"
    assert rgb_b == fresh_frame(browser, site, str(B), fb), f"B's title at frame {fb} isn't a fresh page's"
    assert rgb_a2 == fresh_frame(browser, site, str(A), fa2), f"A's second title at frame {fa2} isn't a fresh page's"
