# The surface I5's cases drive (additive to CONTRACT.md and CONTRACT-I2.md to CONTRACT-I8.md)

I5 adds **no new method, command, element or hook**. The page, its `window.nesTest` hooks, its sound and
the core API stay as the earlier contracts say. What I5 pins is how the page behaves over time: its pacing
against the display, its sound buffer over a long sitting, a hidden tab, and its memory. It also pins the
core over 30 minutes of emulated play. The cases measure these where a person meets them: what the
canvas and `nesTest.frames` show per display refresh, what reaches the AudioContext's destination, and
the page's heap.

## The page's loop
- **Driven by `requestAnimationFrame`.** Each callback's timestamp (and `performance.now()`) is the clock
  it paces by. It never paces by `setTimeout` or `setInterval`.
- **At a 60 Hz display** (refreshes measured within 1 % of 60 Hz, 59.4-60.6 Hz: 60.00 and 59.94 are the
  common ones), **every refresh emulates exactly one new frame.** No refresh doubles a frame or skips one,
  so the game runs at the display's rate, at most 1 % off NTSC's 60.0988. The sound follows the game: it
  plays the samples the frames made, at the AudioContext's rate, holding its buffer by resampling. That
  is the open item settled at approval (`## Decided`, frame pacing at a 60 Hz display).
- **At any other display rate** (30, 75, 120, 144 Hz...), the loop paces by an accumulator against real
  time, as I3 pins: 60.0988 frames a second within 0.5 %, and never a burst. The frame count stays within
  1.5 frames of a straight line at that rate.
- **One loop.** However many games are loaded and however often the tab is hidden and shown, one loop
  runs: each refresh runs exactly one `requestAnimationFrame` callback of the page's.

## A hidden tab (simulated in the cases; the real one is owed after merge)
Headless Chromium never hides a tab: another tab in front, or a minimized window, leaves
`document.visibilityState` at `"visible"` and `requestAnimationFrame` running. So the cases install a
simulation before the page's scripts, following what Chrome documents for a hidden tab:
- `document.visibilityState` reads `"hidden"` and `document.hidden` reads `true`, and `visibilitychange`
  fires on `document` at each change;
- `requestAnimationFrame` callbacks stop while hidden (they stay queued, and run at the first refresh
  after the tab shows);
- `setTimeout`/`setInterval` callbacks wait at least 1 s while hidden (background timer throttling);
- the AudioContext keeps running: its clock and the page's worklet go on.

The simulation can also move the clock (`performance.now()` and the rAF timestamps) forward while hidden,
as a tab hidden for an hour would see. The page may use `visibilitychange` and `document.visibilityState`
as a real page would. What it must do:
- **While hidden, no frame is emulated.**
- **On showing, at most 2 frames of catch-up**, however long it was hidden: the frame count at the first
  refresh after showing is at most 2 above its count when hidden.
- **The sound is back within 200 ms of showing:** the game's sound reaches the destination again within
  200 ms of audio-clock time after the `visibilitychange` that shows the tab.
- **A game loaded while hidden waits:** it is loaded and powered on (frame 0) at once, but emulates no
  frame and makes no sound until the tab shows.

## The sound over a long sitting
Measured at the AudioContext's destination by I5's tap (`cases/i5_support.py`), on `tone_probe`: a game
that plays an unbroken tone and adds a second, louder one while A is held.
- **No underrun:** from 1 s after a load and 1 s after the tab shows, the destination never gets a run of
  8 or more samples of exact silence (`0.0`) while the game's tone plays. An emptied buffer is what writes
  them; the tone, high-passed, never holds at exactly 0.
- **Latency:** a key press is heard within 100 ms of audio-clock time. That is measured from the key's
  `keydown` to the first render quantum where the second tone sounds, and covers everything the page
  controls: the next frame, the buffer and the worklet. Across 2 minutes it grows by no more than 10 ms.
  The device's own output latency is beyond headless Chromium and is owed after merge.
- **Steady memory:** after a garbage collection (CDP `HeapProfiler.collectGarbage`), the page's JS heap
  (CDP `Runtime.getHeapUsage`) at the end of a sitting is within 10 % of its heap at the end of the first
  minute. After 20 loads, it is within 10 % of the heap after the first.

## The core over 30 minutes: `cases/drive_i5.ts`
`node --expose-gc cases/drive_i5.ts '<json>'` runs N frames of the core and hands back the SHA-256 of the
frames asked for, `heapUsed` after a forced GC at the frames asked for, peeks after the last frame, and the
wall time. 108,000 frames is 30 minutes of NTSC play: 3.2 × 10⁹ CPU cycles, past 2³¹, and 9.6 × 10⁹ PPU
dots, past 2³² and 2³³. Whatever counters the core keeps must go past those without changing a frame's
picture, its interrupts or its timing.
- **The core's speed:** at least 5× real time on every game and demo the corpus vendors or fetches, as
  I1-I8 pin it for each board.

## What the cases bring themselves (`cases/i5_support.py`)
- **`tone_probe`** (NROM, built from the spec): pulse 1 plays a 440 Hz tone at volume 4 from power-on.
  Each NMI reads controller 1, and pulse 2 plays 880 Hz at volume 15 while A is held, volume 0 otherwise.
  Rendering stays off.
- **The pacing stub:** `requestAnimationFrame` driven at a stated rate of real time, as I3's `RAF_STUB`
  does. After each refresh it records the time, `nesTest.frames` and how many rAF callbacks it ran into a
  preallocated buffer, and it
  carries the hidden-tab simulation.
- **I5's tap:** I4's tap (`TAP_JS`: the AudioContext subclass and the recorder on the destination),
  reduced in the page to each render quantum's audio-clock time, its RMS and its longest run of exact
  zeros. These are stored in preallocated buffers, so the tap doesn't grow the heap it measures.
- **I8's `IrqSplit` probe** (`split_probe`), whose expected frame I2's spec renderer gives, run for 30
  minutes.
