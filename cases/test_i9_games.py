"""I9: J1 per game, the Reach gradient ("how many vendored legal homebrew games boot, play, end and restart"):
each game of the collection (roms/LICENSES.md, "The collection (I9)") is played on the page through its way in,
the game menu for the vendored ones and the file chooser for the ones fetched by hash, on a paused page
stepped frame by frame (nesTest), with Start pressed through the keyboard and Reset and Power clicked. What
each game shows and how it ends comes from its own source (i9_support.J1_GAMES); the title it must come back
to is its own first title, from a fresh page."""
import pytest

from i3_support import browser, site   # noqa: F401 (fixtures)
from i9_support import START_KEY, CollectionTab, digest, game_path, params, same_layout

DELTA = pytest.mark.xfail(strict=True, reason="delta")


def press(tab: CollectionTab, frames=5):
    tab.down(START_KEY)
    tab.step(frames)
    tab.up(START_KEY)


def title_of(browser, site, g: dict):
    """The game's title on a fresh page (through the file chooser): the pictures over its cycle from
    title_at, as digests, and the picture at title_at itself."""
    with CollectionTab(browser, site) as tab:
        tab.pause()
        tab.pick(game_path(g))
        tab.step(g["title_at"])
        first = tab.canvas()
        seen = {digest(first)}
        for _ in range(g["cycle"] - 1):
            tab.step(1)
            seen.add(digest(tab.canvas()))
        assert not tab.errors, tab.errors
        return seen, first


def is_title(g, seen, first):
    if g["match"] == "layout":
        return lambda rgb: same_layout(rgb, first)
    return lambda rgb: digest(rgb) in seen


def until(tab: CollectionTab, pred, frames: int, every=10):
    """Step up to `frames` frames, `every` at a time, until pred(canvas); the frames taken, or None."""
    done = 0
    while done < frames:
        tab.step(every)
        done += every
        if pred(tab.canvas()):
            return done
    return None


@pytest.mark.parametrize("g", params("games", DELTA))
def test_j1_each_game_of_the_collection_boots_plays_ends_and_restarts(site, browser, g):
    """purpose: J1 per game (the Reach gradient), through the game's way in (the menu for a vendored game,
    the file chooser for one fetched by hash), as its own source tells it:
      1. it boots to its title: by title_at its picture is up (3+ colours) and holds (over its own cycle it
         shows at most 2 pictures, its blink; any number for a title that cycles its colours);
      2. a key plays it: Start, pressed through the keyboard, takes it off its title into play;
      3. it ends: played by nobody (and Start pressed once at the end, where its game-over screen waits for
         it), it comes back to its own title within the budget its source allows; a game that can't end on
         its own (file-fixers, an RPG) is exempt, and so isn't counted in Reach;
      4. Reset and Power restart it: in play, Reset brings its title back; Power starts a fresh console whose
         frame title_at is, pixel for pixel, a fresh page's.
    No page error, and the page loaded once.
    origin: design"""
    seen, first = title_of(browser, site, g)
    assert len(set(first)) >= 3, f"{g['id']}: frame {g['title_at']} has {len(set(first))} colours: no title"
    if g["match"] == "frames":
        assert len(seen) <= 2, f"{g['id']}: the title shows {len(seen)} pictures over {g['cycle']} frames"
    title = is_title(g, seen, first)
    with CollectionTab(browser, site) as tab:
        tab.pause()
        if "file" in g:
            tab.choose(g["file"])
            assert tab.wait_loads(1), f"{g['id']}: the menu entry didn't load"
        else:
            tab.pick(game_path(g))
        assert tab.frames() == 0 and not tab.message(), f"{g['id']}: didn't load clean ({tab.message()!r})"
        tab.step(g["title_at"])
        assert title(tab.canvas()), f"{g['id']}: no title at frame {g['title_at']}"
        at = 0
        for k in g["start"]:
            tab.step(k - at) if k > at else None
            press(tab)
            at = k + 5
        tab.step(g["play"])
        assert not title(tab.canvas()), f"{g['id']}: Start didn't take it off its title"
        if g["end"]:
            e = g["end"]
            if e["idle"]:
                tab.step(e["idle"])
            if e["press_start"]:
                press(tab)
            took = until(tab, title, e["within"], every=10 if e["within"] <= 1200 else 60)
            assert took is not None, f"{g['id']}: it didn't end and come back to its title within the budget"
            for k in g["start"]:
                press(tab)
                tab.step(60)
            tab.step(g["play"])
            assert not title(tab.canvas()), f"{g['id']}: Start didn't start it again after it ended"
        tab.click("reset")
        took = until(tab, title, g["title_at"] + g["cycle"] + 300)
        assert took is not None, f"{g['id']}: Reset didn't bring its title back"
        press(tab)
        tab.step(g["play"])
        tab.click("power")
        assert tab.frames() == 0, f"{g['id']}: Power left frames at {tab.frames()}"
        tab.step(g["title_at"])
        assert tab.canvas() == first, f"{g['id']}: after Power, frame {g['title_at']} isn't a fresh page's"
        assert tab.loaded_once() and not tab.errors, (tab.navigations, tab.errors)
