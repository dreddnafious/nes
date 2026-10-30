"""I3: invariants from the Intent and Decided, each stated in words, then its predicate; and the pacing
boundary (J1o) on both sides of 60 Hz."""
import re
import socket
from urllib.parse import urlsplit

import pytest

from conftest import NODE, ROOT, run
from i3_support import KEY, NTSC_FPS, Tab, browser, nes15, nes15_title, same_picture, site

DELTA = pytest.mark.xfail(strict=True, reason="delta")

RAF_STUB = """(() => {
  // requestAnimationFrame driven at %(hz)s Hz of real time, as a %(hz)s Hz display would; after each
  // tick the page's emulated frame count is sampled with the time.
  const period = 1000 / %(hz)s;
  let queue = new Map(), nextId = 1;
  window.requestAnimationFrame = cb => { const id = nextId++; queue.set(id, cb); return id; };
  window.cancelAnimationFrame = id => { queue.delete(id); };
  const samples = window.__i3_samples = [];
  let due = performance.now() + period;
  function tick() {
    const now = performance.now();
    const run = queue; queue = new Map();
    for (const cb of run.values()) { try { cb(now); } catch (e) { setTimeout(() => { throw e; }); } }
    const t = window.nesTest;
    samples.push([performance.now(), t && typeof t.frames === 'number' ? t.frames : -1]);
    due += period;
    if (due < performance.now() - 50) due = performance.now() + period;
    setTimeout(tick, Math.max(0, due - performance.now()));
  }
  setTimeout(tick, period);
})();"""

WARMUP_S, WINDOW_S = 1.0, 6.0
TOLERANCE = 0.005          # J1o: 60.10 emulated fps +- 0.5 %


def slope(points):
    n = len(points)
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    return sum((x - mx) * (y - my) for x, y in points) / sum((x - mx) ** 2 for x, _ in points)


@pytest.mark.parametrize("hz", [30, 60, 144])
def test_the_core_runs_60_1_frames_a_second_whatever_the_display_rate(site, browser, hz):
    """purpose: J1o and the clock-reconciliation choice (axes: "pace by an accumulator against real
    time ... never a count of rAF calls"): with requestAnimationFrame stubbed to fire at 30, 60 or 144 Hz
    of real time, nes15 runs 60.0988 emulated frames a second within 0.5 % (59.80-60.40), measured over
    6 s after a 1 s warm-up, as the slope of the page's frame count against time. 144 Hz is the
    high-refresh display (one frame per rAF would be 2.4x too fast); 30 Hz the other side (one frame
    per rAF would be half speed).
    origin: design"""
    with Tab(browser, site, init_script=RAF_STUB % {"hz": hz}) as tab:
        tab.hooks()
        tab.load(str(nes15()))
        tab.page.wait_for_timeout(int(WARMUP_S * 1000))
        t0 = tab.page.evaluate("performance.now()")
        tab.page.wait_for_timeout(int(WINDOW_S * 1000))
        samples = tab.page.evaluate("window.__i3_samples")
        pts = [(t / 1000, f) for t, f in samples if t >= t0 and f >= 0]
        assert len(pts) >= 2, f"no frame counts sampled ({len(samples)} ticks)"
        span = pts[-1][0] - pts[0][0]
        ticks = (len(pts) - 1) / span
        assert abs(ticks - hz) / hz < 0.05, f"the rAF stub ticked at {ticks:.1f} Hz, not {hz}: the measurement is void"
        fps = slope(pts)
        assert abs(fps - NTSC_FPS) / NTSC_FPS <= TOLERANCE, \
            f"{fps:.3f} emulated fps at {hz} Hz rAF (want {NTSC_FPS:.3f} +- 0.5 %)"
        assert not tab.errors, tab.errors


def own_origin(url: str, origin: str) -> bool:
    return url.startswith(("data:", "blob:")) or urlsplit(url)[:2] == urlsplit(origin)[:2]


def test_the_page_only_talks_to_its_own_server(site, browser):
    """purpose: "a page served locally" (Intent) with no runtime dependencies (Decided): through a page
    load, a game load, its title and a few seconds of keys, every request the page makes goes to the
    local server's own origin (or is a data:/blob: URL): no CDN, no web font, nothing fetched from
    outside.
    origin: design"""
    title = nes15_title()
    with Tab(browser, site) as tab:
        tab.load(str(nes15()))
        assert tab.wait_for(lambda rgb: same_picture(rgb, title), 5.0)[0] is not None, "no title"
        for b in ("Start", "Right", "Down", "A"):
            tab.press(KEY[b])
        tab.page.wait_for_timeout(500)
        outside = [u for u in tab.requests if not own_origin(u, site.url)]
        assert tab.requests and not outside, f"requests outside {site.url}: {outside[:5]}"


def test_keys_and_buttons_before_any_rom_do_nothing_and_raise_nothing(site, browser):
    """purpose: J1w: on a fresh page with no ROM, every mapped key pressed, Reset and Power clicked,
    and a blur, raise no page error and run no frame (frames stays 0); a ROM loaded afterwards still
    reaches its title within 5 s.
    origin: design"""
    title = nes15_title()
    with Tab(browser, site) as tab:
        tab.hooks()
        for code in KEY.values():
            tab.press(code, hold_ms=30)
        tab.click("reset")
        tab.click("power")
        tab.blur()
        tab.focus()
        tab.page.wait_for_timeout(300)
        assert tab.frames() == 0, f"{tab.frames()} frames ran with no ROM"
        assert not tab.errors, tab.errors
        tab.load(str(nes15()))
        assert tab.wait_for(lambda rgb: same_picture(rgb, title), 5.0)[0] is not None, \
            "no title after the keys and clicks"
        assert not tab.errors, tab.errors


def test_the_server_refuses_a_port_in_use_with_a_message_not_a_stack_trace(tmp_path):
    """purpose: the server is a command Jason runs (J1 step 1): started on a port another program
    already listens on, `node src/web/serve.ts --port N` exits non-zero within 5 s with a message that
    names the port, and no stack trace (as the CLI refuses bad input; a loose word pin on the port number).
    origin: design"""
    serve = ROOT / "src" / "web" / "serve.ts"
    assert serve.is_file(), "src/web/serve.ts doesn't exist (CONTRACT-I3)"
    with socket.socket() as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen()
        port = holder.getsockname()[1]
        r = run([NODE, str(serve), "--port", str(port)], timeout=5)
    out = (r.stderr or "") + (r.stdout or "")
    assert not r.timed_out, f"the server was still running after 5 s on a port in use: {r!r}"
    assert r.returncode not in (0, None), repr(r)
    assert str(port) in out, f"the message doesn't name port {port}: {out[-500:]!r}"
    assert not re.search(r"^\s+at \S", out, re.M), f"a stack trace: {out[-800:]!r}"


def test_the_core_does_not_depend_on_the_front_end_or_the_cli():
    """purpose: "The core in src/core is pure TypeScript ... The front end in src/web is the
    presentation layer" (Decided): the dependency runs one way: no file under src/core imports anything
    from src/web or src/cli.
    origin: design"""
    core = ROOT / "src" / "core"
    banned = re.compile(r"""(from|import)\s*\(?\s*['"][^'"]*\b(web|cli)/""")
    hits = [f"{p.relative_to(ROOT)}:{i}" for p in core.rglob("*.ts")
            for i, line in enumerate(p.read_text().splitlines(), 1) if banned.search(line)]
    assert (core / "index.ts").is_file() and not hits, hits
