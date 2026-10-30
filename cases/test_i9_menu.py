"""I9: the built-in game menu beside the file picker (J1's step-2 fork, settled at the first climb's approval:
"a file picker plus a built-in menu of the vendored legal games"), the server's vendored games, nesTest.loads,
loads that overlap, a failed entry, focus after a load, and a load while the tab is hidden (CONTRACT-I9.md).
Driven in headless Chromium against `node src/web/serve.ts` on a free port; a menu entry's answer is the
server's, or the case's own (Playwright routing, installed before the page loads)."""
import hashlib
import re
import urllib.error
import urllib.request

import pytest

from i3_support import (BUTTONS, KEY, browser, held, nes15, nes15_title, pad_probe, pad_read,  # noqa: F401
                        same_picture, site)
from i5_support import tone_probe
from i9_support import (GAMES, GAMES_URL, LICENSES, CollectionTab, Rule, dump_probe, fresh_frame, mapper_of, params,
                        slot_name, vendored, write)

DELTA = pytest.mark.xfail(strict=True, reason="delta")
TITLE_SECONDS = 5.0


def get(site, path: str):
    """(status, body) of a GET on the page's server."""
    try:
        with urllib.request.urlopen(site.url.rstrip("/") + path, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception as e:          # the server didn't answer
        return None, repr(e).encode()


def is_title(title):
    return lambda rgb: same_picture(rgb, title)


# ---------------------------------------------------------------- the menu and the vendored games

def test_the_menu_beside_the_file_picker_offers_exactly_the_vendored_games(site, browser):
    """purpose: the menu exists and offers the collection: on a fresh page, #game-menu holds one visible
    entry for each file in roms/games/, no more and no fewer (its data-rom resolves to /roms/games/<file> on
    the page's own origin), each named (non-empty text, no two alike); the server answers each entry's URL
    with exactly that file's bytes; nesTest.loads is 0 and nothing runs until an entry is chosen.
    origin: design"""
    games = vendored()
    assert games, "no game is vendored in roms/games/"
    with CollectionTab(browser, site) as tab:
        ents = tab.entries()
        assert ents is not None, "no #game-menu on the page"
        want = sorted(site.url.rstrip("/") + GAMES_URL + g.name for g in games)
        assert sorted(e["rom"] for e in ents) == want, f"the menu offers {[e['rom'] for e in ents]}, not {want}"
        assert all(e["visible"] for e in ents), "an entry isn't visible"
        texts = [e["text"] for e in ents]
        assert all(texts) and len(set(texts)) == len(texts), f"the entries' names: {texts}"
        assert tab.loads() == 0 and tab.frames() == 0
        for g in games:
            status, body = get(site, GAMES_URL + g.name)
            assert status == 200 and body == g.read_bytes(), f"GET {GAMES_URL}{g.name}: {status}, {len(body)} bytes"
        assert not tab.errors, tab.errors


BAD_PATHS = ["/roms/cache/nes15-1.0.0/nes15-NTSC.nes", "/roms/LICENSES.md", "/roms/games/../cache/nes15-1.0.0/nes15-NTSC.nes",
             "/roms/games/%2e%2e/cache/nes15-1.0.0/nes15-NTSC.nes", "/roms/games/%2E%2E%2Fcache%2Fnes15-1.0.0%2Fnes15-NTSC.nes",
             "/roms/games/../../package.json", "/roms/games/nope.nes", "/roms/cache/"]


@pytest.mark.parametrize("path", BAD_PATHS)
def test_the_server_never_hands_out_a_rom_that_isnt_vendored(site, path):
    """purpose: the legal rule at the server ("files whose terms allow use but don't state redistribution
    are fetched by hash into roms/cache/, never committed"): whatever the path (a cached oracle, the licence
    file, a `..` or its percent-encoded forms out of roms/games/, a game that isn't there), the page's
    server answers it with something other than 200, and never with a cached file's bytes.
    origin: design"""
    nes15()                                       # the cached file exists, so a leak would show
    status, body = get(site, path)
    assert status is not None, f"the server didn't answer: {body!r}"
    assert status != 200, f"GET {path} answered 200 ({len(body)} bytes)"
    assert body != nes15().read_bytes()


def test_every_vendored_game_is_recorded_in_the_licences_with_its_hash_and_mapper():
    """purpose: the legal rule for the collection ("a game is vendored only after its licence or author's
    terms are checked and recorded in roms/LICENSES.md"): for every file in roms/games/, roms/LICENSES.md has
    a row naming the file, its SHA-256 and its mapper number (the one its header gives), marked as in git;
    and nothing in roms/games/ is anything but an iNES file.
    origin: design"""
    games = vendored()
    assert games, "no game is vendored in roms/games/"
    text = LICENSES.read_text()
    rows = [ln for ln in text.splitlines() if ln.startswith("|")]
    others = [p.name for p in (vendored()[0].parent.iterdir()) if p.suffix != ".nes" and not p.name.startswith(".")]
    assert not others, f"roms/games/ holds files that aren't games: {others}"
    for g in games:
        data = g.read_bytes()
        assert data[:4] == b"NES\x1a", f"{g.name} isn't an iNES file"
        sha = hashlib.sha256(data).hexdigest()
        row = [r for r in rows if f"games/{g.name}" in r]
        assert len(row) == 1, f"roms/LICENSES.md has {len(row)} rows for games/{g.name}"
        assert sha in row[0], f"the row for {g.name} doesn't give its SHA-256 {sha}"
        assert re.search(rf"\b{mapper_of(data)}\b", row[0]), f"the row for {g.name} doesn't name mapper {mapper_of(data)}"
        assert re.search(r"\byes\b", row[0], re.I), f"the row for {g.name} doesn't say it is in git"


@pytest.mark.parametrize("name", params("entries", DELTA, "file"))
def test_each_entry_loads_its_game_as_the_file_picker_does(site, browser, name):
    """purpose: choosing an entry is a load (CONTRACT-I9): on a paused page, a click on the entry powers its
    game on (nesTest.loads 1, frames 0, #message empty) and its frame 120 is, index for index, the frame 120
    of the same file picked through #rom-file on a fresh page. No page error, and the page loaded once.
    origin: design"""
    g = GAMES / name
    assert g in vendored(), f"{name} isn't vendored in roms/games/"
    want = fresh_frame(browser, site, str(g), 120)
    with CollectionTab(browser, site) as tab:
        tab.pause()
        tab.choose(g.name)
        assert tab.wait_loads(1), "the entry didn't load its game within 5 s"
        assert tab.frames() == 0 and not tab.message(), f"frames {tab.frames()}, message {tab.message()!r}"
        tab.step(120)
        assert tab.canvas() == want, f"{g.name} through the menu isn't the file picker's frame 120"
        assert tab.loaded_once() and not tab.errors, (tab.navigations, tab.errors)


# ---------------------------------------------------------------- J2w, J2af: a failed entry

def mapper3000() -> bytes:
    """nes15 with an NES 2.0 header for mapper 3000 (unassigned), as I1 builds it."""
    rom = bytearray(nes15().read_bytes())
    rom[6] = (rom[6] & 0x0F) | 0x80
    rom[7] = (rom[7] & 0x0F) | 0xB8
    rom[8] = 0x0B
    return bytes(rom)


FAILURES = {
    "404": lambda: Rule(status=404),
    "500": lambda: Rule(status=500),
    "connection-failed": lambda: Rule(abort=True),
    "not-nes": lambda: Rule(body=b"This is a text file, not an NES ROM.\n" * 40),
    "truncated": lambda: Rule(body=nes15().read_bytes()[:16 + 0x2000]),
    "mapper-3000": lambda: Rule(body=mapper3000()),
}


@pytest.mark.parametrize("kind", params("failures", DELTA, "id"))
def test_a_failed_entry_says_why_and_the_running_game_runs_on(site, browser, kind):
    """purpose: J2w and J2af through the menu (I3's pinned refusal: B is validated before A is touched): with
    nes15 on its title (picked through the file chooser), choosing an entry whose answer is a 404, a 500, a
    failed connection, a non-NES file, a truncated file or a mapper-3000 file shows #message (visible, with a
    reason; for the mapper, naming 3000), and nes15 runs on: its frame count climbs by 45+ over 1.5 s and
    never restarts, its title stays on the canvas, nesTest.loads stays 1. No page error.
    origin: design"""
    title = nes15_title()
    with CollectionTab(browser, site, rules={GAMES_URL + slot_name(0): FAILURES[kind]()}) as tab:
        tab.pick(nes15())
        assert tab.wait_for(is_title(title), TITLE_SECONDS)[0] is not None, "nes15 never showed its title"
        before = tab.frames()
        tab.choose(slot_name(0))
        tab.wait(1.5)
        after = tab.frames()
        assert after >= before + 45, f"frames went from {before} to {after}: the running game stopped or restarted"
        assert same_picture(tab.canvas(), title), "nes15's title is gone"
        msg = tab.message()
        assert msg, "no visible #message saying why the entry failed"
        if kind == "mapper-3000":
            assert re.search(r"\b3000\b", msg), f"#message is {msg!r}: it doesn't name mapper 3000"
        assert tab.loads() == 1, f"nesTest.loads is {tab.loads()} after a failed entry"
        assert not tab.errors, tab.errors


@pytest.mark.parametrize("kind", params("fresh_failures", DELTA, "id"))
def test_a_failed_entry_on_a_fresh_page_starts_nothing(site, browser, kind):
    """purpose: J2w on a fresh page ("a blank or hung canvas"): a failed entry runs no frame (frames 0 and
    the canvas unchanged after 1 s, nesTest.loads 0) and shows #message with a reason; a working entry
    chosen next then starts (loads 1).
    origin: design"""
    with CollectionTab(browser, site, rules={GAMES_URL + slot_name(0): FAILURES[kind]()}) as tab:
        before = tab.canvas()
        tab.choose(slot_name(0))
        tab.wait(1.0)
        assert tab.frames() == 0 and tab.loads() == 0, f"frames {tab.frames()}, loads {tab.loads()}"
        assert tab.canvas() == before, "the canvas changed for a failed entry"
        assert tab.message(), "no visible #message for the failed entry"
        tab.pick(nes15())
        assert tab.wait_loads(1), "a good game didn't start after the failed entry"
        assert not tab.errors, tab.errors


def test_an_evening_with_a_refused_file_and_a_failed_entry_between_two_games(site, browser, tmp_path):
    """purpose: J2af (and the first climb's J2l) walked whole: A (the dump probe, from the menu) → a refused
    file (mapper 3000) → A runs on, its picture kept → a failed entry (404) → A still runs → B (pad_probe,
    through the file chooser) starts clean: #message hidden or empty, nesTest.loads 2, and B's frame on the
    canvas is a fresh page's (nothing pressed: port 1 reads nothing). The page loaded once.
    origin: design"""
    dump = dump_probe(0)
    want_a = fresh_frame(browser, site, write(tmp_path, "dump.nes", dump), 30)
    rules = {GAMES_URL + slot_name(0): Rule(body=dump), GAMES_URL + slot_name(1): Rule(status=404)}
    with CollectionTab(browser, site, rules=rules) as tab:
        tab.choose(slot_name(0))
        assert tab.wait_loads(1), "A didn't load from the menu"
        tab.wait(1.0)
        assert tab.canvas() == want_a, "A's picture isn't a fresh page's"
        f0 = tab.frames()
        tab.pick(write(tmp_path, "bad.nes", mapper3000()))
        tab.wait(1.0)
        assert "3000" in tab.message() and tab.frames() >= f0 + 30 and tab.canvas() == want_a, "A didn't run on"
        f1 = tab.frames()
        tab.choose(slot_name(1))
        tab.wait(1.0)
        assert tab.message() and tab.frames() >= f1 + 30 and tab.canvas() == want_a, "A didn't run on the failed entry"
        tab.pick(write(tmp_path, "pad.nes", pad_probe()))
        tab.wait(0.5)
        assert not tab.message(), f"#message still shows {tab.message()!r} after a good load"
        assert tab.loads() == 2, f"nesTest.loads is {tab.loads()}"
        assert pad_read(tab.canvas())["port1"][:8] == held(set()), "B didn't start clean"
        assert tab.loaded_once() and not tab.errors, (tab.navigations, tab.errors)


# ---------------------------------------------------------------- J2v: loads that overlap

@pytest.mark.parametrize("order", params("orders", DELTA, "id"))
def test_the_last_load_asked_for_wins(site, browser, tmp_path, order):
    """purpose: J2v (and the first climb's J2b, racing loads): with nes15 running, a load whose answer is
    held back (the first entry, answering pad_probe) and then another asked for (a file through the chooser,
    or the second entry answering the dump probe): the second runs (nesTest.loads + 1); when the held answer
    is let go, it is dropped: 1.5 s later the second game still runs (its frames climbing, never back to 0,
    its picture), loads hasn't moved and #message is empty. "file-then-menu": the file is picked and the
    entry clicked at once: the entry's game is the one running. "menu-then-power": Power, pressed while the
    held load is in flight, restarts nes15, and the held load, let go, then starts (loads + 1, pad_probe).
    origin: design"""
    dump = dump_probe(0)
    want_dump = fresh_frame(browser, site, write(tmp_path, "dump.nes", dump), 30)
    title = nes15_title()
    rules = {GAMES_URL + slot_name(0): Rule(body=pad_probe(), hold=True), GAMES_URL + slot_name(1): Rule(body=dump)}
    with CollectionTab(browser, site, rules=rules) as tab:
        tab.pick(nes15())
        assert tab.wait_for(is_title(title), TITLE_SECONDS)[0] is not None, "nes15 never showed its title"
        n0 = tab.loads()
        if order == "file-then-menu":
            tab.release(GAMES_URL + slot_name(0))
            tab.pick(write(tmp_path, "pad.nes", pad_probe()))
            tab.choose(slot_name(1))
            assert tab.wait_loads(n0 + 1)
            tab.wait(1.5)
            assert tab.canvas() == want_dump, "the entry asked for last isn't the game running"
            return
        tab.choose(slot_name(0))
        tab.wait(0.3)
        if order == "menu-then-power":
            tab.click("power")
            tab.wait(0.5)
            assert tab.frames() < 45 and tab.loads() == n0, "Power didn't restart nes15 while the load was in flight"
            tab.release(GAMES_URL + slot_name(0))
            assert tab.wait_loads(n0 + 1), "the held load didn't start once let go"
            tab.wait(0.5)
            assert pad_read(tab.canvas())["port1"][:8] == held(set()), "pad_probe isn't running"
            return
        if order == "menu-then-file":
            tab.pick(write(tmp_path, "dump.nes", dump))
        else:
            tab.choose(slot_name(1))
        assert tab.wait_loads(n0 + 1), "the load asked for second didn't start"
        tab.wait(1.0)
        assert tab.canvas() == want_dump, "the second game isn't running"
        f = tab.frames()
        tab.release(GAMES_URL + slot_name(0))
        tab.wait(1.5)
        assert tab.loads() == n0 + 1, f"the dropped load started (loads {tab.loads()})"
        assert tab.frames() >= f + 45, f"frames went from {f} to {tab.frames()}: the second game was replaced"
        assert tab.canvas() == want_dump, "the second game's picture is gone"
        assert not tab.message(), f"#message shows {tab.message()!r} for a dropped load"
        assert not tab.errors, tab.errors


# ---------------------------------------------------------------- J2x: focus after a load

MAPPED = [KEY[b] for b in BUTTONS]


@pytest.mark.parametrize("way", params("ways", DELTA, "way"))
def test_after_a_load_every_game_key_is_the_games_only(site, browser, tmp_path, way):
    """purpose: J2x: after a load through the file chooser (the input clicked, as a person does) or the menu
    (an entry clicked), pressing and holding each mapped key in turn (X, Z, Right Shift, Enter and the
    arrows) reaches the game (pad_probe reads exactly that button) and nothing else: no file dialog opens,
    no load happens (the frame count never drops back), and the page doesn't reload.
    origin: design"""
    pad = pad_probe()
    path = write(tmp_path, "pad.nes", pad)
    rules = {GAMES_URL + slot_name(0): Rule(body=pad)} if way == "menu" else {}
    with CollectionTab(browser, site, rules=rules) as tab:
        if way == "file":
            tab.pick(path)
        else:
            tab.choose(slot_name(0))
            assert tab.wait_loads(1), "the entry didn't load"
        tab.wait(0.5)
        choosers = tab.choosers
        last = tab.frames()
        for name, code in zip(BUTTONS, MAPPED):
            tab.down(code)
            tab.wait(0.25)
            got = pad_read(tab.canvas())["port1"][:8]
            tab.up(code)
            tab.wait(0.15)
            assert got == held({name}), f"{code} held: the game read {dict(zip(BUTTONS, got))}"
            now = tab.frames()
            assert now > last, f"after {code} the frame count went from {last} to {now}: a load happened"
            last = now
        assert tab.choosers == choosers, "a key opened the file dialog"
        if way == "menu":
            assert tab.loads() == 1, f"a key loaded a game again (loads {tab.loads()})"
        assert tab.loaded_once() and not tab.errors, (tab.navigations, tab.errors)


# ---------------------------------------------------------------- the hidden tab (I5) meets the menu

def test_a_menu_load_while_the_tab_is_hidden_waits_until_it_shows(site, browser):
    """purpose: J3ae through the menu (I5: "a game loaded while hidden waits"): with the tab hidden, a menu
    entry (answering I5's tone probe) is chosen; the game is loaded (nesTest.loads 1) at frame 0 and stays
    there for 2 s with no sound at the destination; once the tab shows it runs (90+ frames in 2 s) and is
    heard.
    origin: design"""
    rules = {GAMES_URL + slot_name(0): Rule(body=tone_probe())}
    with CollectionTab(browser, site, rules=rules, tap=True, hz=60) as tab:
        tab.hide()
        tab.choose(slot_name(0))
        assert tab.wait_loads(1), "the entry didn't load while hidden"
        tab.wait(2.0)
        assert tab.frames() == 0, f"{tab.frames()} frames ran while the tab was hidden"
        cap = tab.capture()
        loud = [q for q in cap.quanta if q[3] and max(abs(v) for v in q[3]) > 0.001]
        assert not loud, f"{len(loud)} quanta of sound reached the destination while hidden"
        tab.show()
        tab.wait(2.0)
        assert tab.frames() >= 90, f"only {tab.frames()} frames in 2 s after the tab showed"
        tab.clear_tap()
        tab.wait(0.6)
        cap = tab.capture()
        ctx = cap.contexts()[-1] if cap.quanta else None
        assert ctx is not None and max(abs(v) for v in cap.samples(ctx)) > 0.005, "the game wasn't heard once shown"
        assert not tab.errors, tab.errors
