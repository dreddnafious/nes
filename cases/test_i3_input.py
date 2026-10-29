"""I3: the keyboard and the controller ports (J1 step 4, J1e, J1f, J1g, J1m). Keys are pressed through
Playwright's keyboard on the served page; what the game reads is seen on the canvas.

- pad_probe (built in the case from NESdev "Standard controller") draws each read of $4016/$4017 as a
  lit or dark cell, so a case sees the bits a game would get.
- blargg's read_joy3/test_buttons (fetched by hash) asks for each button in turn and prints Passed or
  Failed; the case reads its screen with the ROM's own font.
- nes15 shows the keys' effect on a real game: Start leaves its title, the arrows move its cursor, and its
  puzzle is seeded from the frame Start is pressed on (its title.s), so a replay is frame-exact or it shows.

The frame-exact cases pause the page and step it (window.nesTest, CONTRACT-I3.md); the keys still go
through the page's real keyboard handling."""
import pytest

from i2_support import font_from_chr, read_screen, text_lines
from i3_support import (BUTTONS, KEY, Tab, browser, brightness, digest, fresh_run, held, i3_oracle, nes15,
                        nes15_title, pad_probe, pad_read, same_picture, site, write)

DELTA = pytest.mark.xfail(strict=True, reason="delta")
LAG = 4                    # frames from a key to pad_probe's picture of it (read after one NMI, drawn at the next)
NOTHING = [0] * 8
OFFICIAL_TAIL = [1] * 16   # reads 9-24 of an official controller (NESdev "Standard controller")


def pad_tab(browser, site, tmp_path):
    tab = Tab(browser, site)
    tab.pause()
    tab.load(write(tmp_path, "pad.nes", pad_probe()))
    tab.step(LAG)
    return tab


def after(tab, n=LAG) -> dict:
    tab.step(n)
    return pad_read(tab.canvas())


def test_each_mapped_key_reads_as_its_own_button_and_nothing_else(site, browser, tmp_path):
    """purpose: the key map Decided at approval (X = A, Z = B, Right Shift = Select, Enter = Start,
    arrows = the pad), read through $4016 in the standard order A, B, Select, Start, Up, Down, Left,
    Right: with nothing held the 8 bits are 0; holding each key alone (its keydown sent twice, as key
    repeat does) sets exactly its own bit; releasing it clears it.
    origin: design"""
    with pad_tab(browser, site, tmp_path) as tab:
        got = after(tab)
        assert got["port1"][:8] == NOTHING, f"nothing held reads {got['port1'][:8]}"
        for b in BUTTONS:
            tab.down(KEY[b])
            tab.down(KEY[b])
            got = after(tab)["port1"][:8]
            assert got == held([b]), f"{KEY[b]} held reads {got}, not {b} alone ({held([b])})"
            tab.up(KEY[b])
            got = after(tab)["port1"][:8]
            assert got == NOTHING, f"{KEY[b]} released still reads {got}"
        assert not tab.errors, tab.errors


def test_the_port_reads_as_an_official_controller_and_port_2_is_left_alone(site, browser, tmp_path):
    """purpose: "controller 1 (and 2) through $4016/$4017 with the strobe" (Intent), per NESdev
    "Standard controller": after the 8th read, reads 9-24 of $4016 return 1, whether or not a button is
    held; and six keys held at once leave controller 2's 8 bits at 0 (the keyboard drives controller 1
    only).
    origin: design"""
    with pad_tab(browser, site, tmp_path) as tab:
        got = after(tab)
        assert got["port1"][8:] == OFFICIAL_TAIL, f"reads 9-24 with nothing held: {got['port1'][8:]}"
        six = ["A", "B", "Select", "Start", "Up", "Right"]     # no opposite pair
        for b in six:
            tab.down(KEY[b])
        got = after(tab)
        assert got["port1"][:8] == held(six), got["port1"][:8]
        assert got["port1"][8:] == OFFICIAL_TAIL, f"reads 9-24 with buttons held: {got['port1'][8:]}"
        assert got["port2"] == NOTHING, f"controller 2 reads {got['port2']} with keys held"


def test_with_the_strobe_held_high_every_read_is_button_a(site, browser, tmp_path):
    """purpose: the strobe (Intent; NESdev "Standard controller": while $4016 bit 0 is 1 the controller
    keeps reloading, and every read returns A): three reads with the strobe high read 1, 1, 1 while X
    (A) is held, and 0, 0, 0 while only Z (B) and the arrows are held.
    origin: design"""
    with pad_tab(browser, site, tmp_path) as tab:
        tab.down(KEY["A"])
        got = after(tab)
        assert got["strobe"] == [1, 1, 1], f"strobe-high reads with A held: {got['strobe']}"
        tab.up(KEY["A"])
        for b in ("B", "Up", "Right"):
            tab.down(KEY[b])
        got = after(tab)
        assert got["strobe"] == [0, 0, 0], f"strobe-high reads with B, Up, Right held: {got['strobe']}"


def test_left_shift_and_unmapped_keys_press_no_button(site, browser, tmp_path):
    """purpose: "Select is Right Shift only" (Decided, axes), and only the mapped keys are buttons:
    holding Left Shift, A, C, Space, Tab or Escape reads no button at all.
    origin: design"""
    with pad_tab(browser, site, tmp_path) as tab:
        for code in ("ShiftLeft", "KeyA", "KeyC", "Space", "Tab", "Escape"):
            tab.down(code)
            got = after(tab)["port1"][:8]
            assert got == NOTHING, f"{code} held reads {got}"
            tab.up(code)


def test_shift_held_does_not_change_what_z_and_x_are(site, browser, tmp_path):
    """purpose: J1v: keys are physical keys, so holding Right Shift (Select) while pressing Z, X and Enter
    reads Select + B + A + Start: a shifted letter is still B or A.
    origin: design"""
    with pad_tab(browser, site, tmp_path) as tab:
        for b in ("Select", "B", "A", "Start"):
            tab.down(KEY[b])
        got = after(tab)["port1"][:8]
        assert got == held(["Select", "B", "A", "Start"]), \
            f"Right Shift + Z + X + Enter reads {got}, not Select + B + A + Start"


def test_the_newest_of_two_opposite_directions_wins(site, browser, tmp_path):
    """purpose: "when opposite directions are held at once, the newest press wins" (Decided, axes): hold
    Left, then Right: Right alone; let Right go with Left still held: Left again. The same for Up then
    Down. A game never reads both of a pair at once.
    origin: design"""
    with pad_tab(browser, site, tmp_path) as tab:
        for old, new in (("Left", "Right"), ("Up", "Down"), ("Right", "Left"), ("Down", "Up")):
            tab.down(KEY[old])
            assert after(tab)["port1"][:8] == held([old]), f"{old} alone"
            tab.down(KEY[new])
            got = after(tab)["port1"][:8]
            assert got == held([new]), f"{old} then {new} reads {got}, not {new} alone"
            tab.up(KEY[new])
            got = after(tab)["port1"][:8]
            assert got == held([old]), f"{new} released with {old} still held reads {got}, not {old}"
            tab.up(KEY[old])
            assert after(tab)["port1"][:8] == NOTHING


def test_blur_releases_every_button(site, browser, tmp_path):
    """purpose: J1g: on window blur every button is released: with Right and X held, a blur makes the
    next reads show nothing held; coming back (focus) brings no phantom press, and a fresh press of
    Right reads Right again.
    origin: design"""
    with pad_tab(browser, site, tmp_path) as tab:
        tab.down(KEY["Right"])
        tab.down(KEY["A"])
        assert after(tab)["port1"][:8] == held(["Right", "A"])
        tab.blur()
        got = after(tab)["port1"][:8]
        assert got == NOTHING, f"after blur the game still reads {got}"
        tab.focus()
        assert after(tab)["port1"][:8] == NOTHING, "focus brought a held button back"
        tab.up(KEY["Right"])
        tab.up(KEY["A"])
        tab.down(KEY["Right"])
        assert after(tab)["port1"][:8] == held(["Right"]), "Right pressed again after the blur doesn't read"


def test_blargg_test_buttons_passes_with_the_keyboard(site, browser):
    """purpose: a controller test ROM, as the roadmap asks: blargg's read_joy3/test_buttons (CNROM) asks
    for A, B, Select, Start, Up, Down, Left, Right in turn and fails on any other bit; pressing X, Z,
    Right Shift, Enter and the arrows in that order makes it print "Passed" on the canvas (read with
    the ROM's own ASCII font, bright pixels lit), and never "Failed".
    origin: design"""
    path = i3_oracle("read_joy3/test_buttons")
    data = path.read_bytes()
    prg = data[4] * 0x4000
    font = font_from_chr(data[16 + prg:16 + prg + 0x2000])
    with Tab(browser, site) as tab:
        tab.pause()
        tab.load(str(path))
        tab.step(90)
        for b in BUTTONS:
            tab.down(KEY[b])
            tab.step(8)
            tab.up(KEY[b])
            tab.step(12)
        tab.step(60)
        screen = text_lines(read_screen(tab.canvas(), font, lambda p: brightness(p) > 384))
        words = [ln.strip() for ln in screen]
        assert "Passed" in words and "Failed" not in words, f"the screen reads {screen!r}"


TITLE_END = 240
START = [(120, "down", "Enter"), (126, "up", "Enter")]


def test_start_pressed_at_frame_120_leaves_the_title_and_no_press_stays_on_it(site, browser):
    """purpose: J1e, a key press changes the screen against a reproducible baseline: two runs of nes15
    with no key both show the author's title at frame 240, identical; the same run with Enter held at
    frames 120-125 shows something else at frame 240 (the game, not a blank screen).
    origin: design"""
    title = nes15_title()
    rom = str(nes15())
    a = fresh_run(browser, site, rom, [], TITLE_END)
    b = fresh_run(browser, site, rom, [], TITLE_END)
    assert a == b, "two runs with no key differ at frame 240"
    assert same_picture(a, title), "with no key, frame 240 isn't nes15's title"
    pressed = fresh_run(browser, site, rom, START, TITLE_END)
    assert not same_picture(pressed, title) and len(set(pressed)) >= 4, \
        "Enter at frame 120 left frame 240 on the title (or blank)"


PLAY_END = 420
RIGHT = [(300, "down", "ArrowRight"), (306, "up", "ArrowRight")]
DOWN = [(300, "down", "ArrowDown"), (306, "up", "ArrowDown")]


def test_the_arrow_keys_move_nes15s_cursor(site, browser):
    """purpose: J1 step 4, "play it with the keyboard": in nes15's game (Start at frame 120), the cursor
    starts at the board's top-left (its play.s); Right at frame 300, or Down at frame 300, each gives
    a different frame 420 from the run with no arrow, and from each other.
    origin: design"""
    rom = str(nes15())
    base = fresh_run(browser, site, rom, START, PLAY_END)
    right = fresh_run(browser, site, rom, START + RIGHT, PLAY_END)
    down = fresh_run(browser, site, rom, START + DOWN, PLAY_END)
    assert base != right, "Right changed nothing"
    assert base != down, "Down changed nothing"
    assert right != down, "Right and Down did the same thing"


def test_the_same_key_script_gives_the_same_screen_three_times(site, browser):
    """purpose: J1m: inputs replay frame-exact: nes15 with Enter at frame 120 and Right at 300 gives the
    same frame 420 on three fresh pages; with Enter one frame later (121, which seeds a different
    puzzle, per its title.s), frame 420 differs, so the match isn't vacuous.
    origin: design"""
    rom = str(nes15())
    runs = [digest(fresh_run(browser, site, rom, START + RIGHT, PLAY_END)) for _ in range(3)]
    assert len(set(runs)) == 1, f"three runs of one script: {runs}"
    late = [(121, "down", "Enter"), (127, "up", "Enter")]
    shifted = digest(fresh_run(browser, site, rom, late + RIGHT, PLAY_END))
    assert shifted != runs[0], "Enter one frame later gave the same screen: the press frame isn't honoured"


def test_mapped_keys_never_scroll_the_page_and_unmapped_ones_are_left_alone(site, browser):
    """purpose: J1f: with nes15 running on a page made taller and wider than the window, every mapped
    key's keydown has had preventDefault called by the time it reaches window, and pressing each
    (arrows included) leaves the page at scroll (0, 0); Tab and the letter A are not prevented.
    origin: design"""
    with Tab(browser, site) as tab:
        tab.load(str(nes15()))
        tab.page.wait_for_timeout(500)
        tab.page.evaluate("""() => {
            const s = document.createElement('div');
            s.style.cssText = 'width:6000px;height:6000px';
            document.body.appendChild(s);
            window.scrollTo(0, 0);
            if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur();
            window.__i3_prevented = {};
            window.addEventListener('keydown', e => { window.__i3_prevented[e.code] = e.defaultPrevented; });
        }""")
        mapped = [KEY[b] for b in BUTTONS]
        for code in mapped:
            tab.press(code, hold_ms=60)
        scroll = tab.page.evaluate("[window.scrollX, window.scrollY]")
        for code in ("KeyA", "Tab"):
            tab.press(code, hold_ms=30)
        prevented = tab.page.evaluate("window.__i3_prevented")
        assert scroll == [0, 0], f"the mapped keys scrolled the page to {scroll}"
        assert all(prevented.get(c) is True for c in mapped), {c: prevented.get(c) for c in mapped}
        assert prevented.get("KeyA") is False and prevented.get("Tab") is False, prevented
