# The surface I3's cases drive (additive to CONTRACT.md and CONTRACT-I2.md)

Everything in `CONTRACT.md` and `CONTRACT-I2.md` stays as it is (the CLI, the core API, and the frozen
drivers `cases/drive.ts` and `cases/drive_ppu.ts`). I3 adds the page, served locally, and the controller
ports behind it. The cases drive the page the way a person meets it: served on a free local port, opened
in headless Chromium (Playwright), a ROM given to its file input, keys pressed on its keyboard, its
buttons clicked, and its canvas read.

## The server: `node src/web/serve.ts --port <N>`
- **What it does:** serves the page at `http://127.0.0.1:<N>/` (`GET /` answers 200 with the page's HTML),
  plus whatever files the page loads, all from this repository. The core the page runs is `src/core` itself
  (the browser can't run TypeScript, so the server may strip the types as it serves; Node 24's
  `module.stripTypeScriptTypes` does that with no dependency).
- **Where:** it listens on `127.0.0.1` only, on the port given. The cases pick a free port and wait (up to
  15 s) until `GET /` answers.
- **How long:** it runs until it's stopped (SIGTERM or SIGINT). The cases also bound it in time.
- **A port already in use:** it exits non-zero within 5 s with a one-line message on stderr that names the
  port, never a stack trace (as the CLI refuses bad options).
- **No outside requests:** everything the page loads comes from the server's own origin (no CDN, no web font,
  no analytics). Runtime dependencies stay none (`package.json`).

## The page's elements
| id | element | what it does |
|---|---|---|
| `rom-file` | `<input type="file">` | Picking a file loads it: the game is powered on (a fresh console: RAM, OAM, palette `$00`, as Decided) and runs. A file the core refuses leaves the running game running (axes) and shows why in `message`. An empty pick (a cancel) changes nothing. |
| `screen` | `<canvas>` | The picture. Its backing store is **256 × 240** (every line, Decided Q5; CSS may scale it). Each pixel is the core's frame value through the **same** 512-entry palette the CLI's PNG uses (`CONTRACT-I2.md`, "one standard 2C02 palette"). Its pixels must be readable at any time by `drawImage` into another canvas (a 2D context, or WebGL with `preserveDrawingBuffer: true`). |
| `reset` | `<button>` | The console's Reset button (soft reset, `NES.reset()`): RAM and battery PRG-RAM are kept. |
| `power` | `<button>` | A power cycle of the same game: a fresh console, as at load, except that battery-backed PRG-RAM survives (Decided Q3). |
| `message` | any element | Shows why a file was refused (the core's `RomError` message). It's visible, with non-empty text, after a refused file. A PAL-flagged file (NES 2.0 byte 12 = 1) runs at NTSC timing with a notice here (Decided Q4). After a plain NTSC game loads, it's hidden or empty. |

Clicking a button or the file input must not leave it able to take the game's keys: Enter pressed after
clicking Reset is the game's Start, not a second click (J1f).

## The keyboard (Decided at approval)
Keys are matched by `KeyboardEvent.code` (the physical key), so Shift held doesn't change a letter.

| key (`code`) | controller 1 button | bit (read order) |
|---|---|---|
| `KeyX` | A | 1st |
| `KeyZ` | B | 2nd |
| `ShiftRight` | Select (Right Shift **only**; `ShiftLeft` is nothing) | 3rd |
| `Enter` | Start | 4th |
| `ArrowUp` | Up | 5th |
| `ArrowDown` | Down | 6th |
| `ArrowLeft` | Left | 7th |
| `ArrowRight` | Right | 8th |

- **Held** from `keydown` to `keyup` (repeated `keydown`s don't change it).
- **Opposite directions:** the newest press wins. Holding Left and then pressing Right reads Right only;
  releasing Right while Left is still held reads Left again (the same for Up and Down).
- **Mapped keys call `preventDefault()`**, so the page never scrolls and no focused control acts on them.
  Unmapped keys (Tab, letters other than Z and X) are left alone.
- **On window `blur`, every button is released**; a key held through the blur reads as released until it's
  pressed again.
- Keys pressed before any ROM is loaded do nothing, and raise no error.
- **Controller 2** is a standard controller with nothing pressed: the keyboard drives controller 1 only.

## The controller ports (`$4016`/`$4017`, NESdev "Standard controller")
- Writing `$4016` bit 0 = 1 then 0 latches both controllers. Reads of `$4016` (controller 1) and `$4017`
  (controller 2) then return the buttons in bit 0, in the order above.
- After the 8th read, every further read returns 1 in bit 0 (an official controller).
- While bit 0 of `$4016` is held at 1, every read returns the current state of A.

## Test hooks: `window.nesTest`
Three members, for frame-exact cases. They exist from the page's load event on.
- `nesTest.frames` (number, read-only): the frames emulated (calls to `runFrame()`) since the running game
  was powered on, by a load or by Power. It's 0 before any game is loaded, and 0 just after a load or
  Power. Reset doesn't change it.
- `nesTest.pause()`: from the moment it returns, the page's own loop emulates no frame, for the rest of
  the page's life (the cases never resume). It may be called before any game is loaded. Paused, a load or
  Power powers the game on and leaves it at frame 0, and Reset resets it; none of them runs a frame.
  Keyboard events still update the buttons while paused.
- `nesTest.step(n)`: paused only. Emulates `n` frames at once with the buttons as they are now, draws the
  last one to the canvas, and returns (or resolves to) the new `frames`.

The cases read the canvas with `drawImage` onto a 256 × 240 2D canvas and `getImageData`. They count page
loads by Playwright's main-frame navigations, and by a marker they set on `window` after the load.

## What the cases bring themselves
- **nes15** (BSD 2-clause, fetched by hash from `cases/i3_oracles.json`, not committed: `roms/LICENSES.md` says why): an NROM-128 homebrew game. Its title is a static name
  table; the cases build the expected title picture from the author's own `title.nam`, `bgd.pal` and
  `spr.pal` and the ROM's CHR, through I2's spec renderer (`i2_support.reference_frame`), and compare it to
  the canvas by palette index (each index one colour, distinct indices distinct colours). Its title
  waits for any button; the puzzle is seeded from the frame the button is pressed on (its `title.s`).
- **`read_joy3/test_buttons`** (blargg, CNROM, fetched by hash): asks for each button in turn and prints
  "Passed" or "Failed"; the cases read the canvas with the ROM's own font.
- **Probe ROMs built in the case** (`cases/i3_support.py`, NROM, from the spec):
  - `pad_probe`: each frame it strobes `$4016`, reads it 24 times and `$4017` 8 times, then reads `$4016`
    3 times with the strobe held high, and draws each read's bit 0 as a lit or dark 8 × 8 cell (rows 10,
    11 and 12);
  - `boot_probe` (battery flag set): on every reset it checks whether all 2 KiB of RAM is `$00`, adds one
    to a counter in RAM (`$0300`) and one to a counter in battery PRG-RAM (`$6000`, behind a signature),
    and draws the two counters as runs of lit cells (rows 10 and 12) and the "RAM was clean" flag as
    one cell (row 14).
