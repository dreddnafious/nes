# The surface I9's cases drive (additive to CONTRACT.md and CONTRACT-I2.md to CONTRACT-I8.md)

Everything in the earlier contracts stays as it is: the CLI's `trace`, `run`, `screenshot` and `audio`; the
core API (`new NES(bytes)`, `runFrame()`, `peek()`, `reset()`, `frameBuffer()`, `setButtons()`,
`setSampleRate()`, `audioSamples()`, `RomError`); the frozen drivers (`cases/drive.ts`, `drive_ppu.ts`,
`drive_apu.ts`, `drive_i6.ts`, `drive_i5.ts`, which I9's cases reuse); the page, its elements, its keyboard,
its sound and its `window.nesTest` hooks; I5's pacing and hidden tab. I9 adds the **game menu** beside the
file picker, the server's **vendored games**, one hook (**`nesTest.loads`**), the rule for **loads that
overlap**, what a load must leave behind (**nothing**), and the **collection's games** with what each must
do (J1 per game). It adds **no mapper**: research found no game on UNROM 512 (30), GTROM (111) or any other
unsupported board whose terms could be checked (`roms/LICENSES.md`), so mappers 0, 1, 2, 3, 4 and 7 stay the
set, and every other number is still refused by its number (I3's mapper-30 refusal cases stand). The
cases drive it as a person at a desktop browser meets it: the page served on a free local port and opened
in headless Chromium, games chosen from the menu with a click or picked through the browser's file
chooser, keys pressed, buttons clicked, the canvas read, and the sound heard at the AudioContext's
destination. The parametrized cases' rows are in `cases/i9_cases.yaml`.

## The vendored games: `roms/games/`
- **What they are:** every `.nes` file in `roms/games/` is a homebrew game whose licence allows it to be
  redistributed, committed to the repository after its terms were checked and recorded in
  `roms/LICENSES.md` (one row each, with its SHA-256 and mapper). Files whose terms don't state
  redistribution are never put there: they are fetched by hash into `roms/cache/` (`cases/i9_oracles.json`).
- **Served:** `GET /roms/games/<file>` answers 200 with exactly that file's bytes, for each of them. The
  content type is the builder's.
- **Nothing else under `roms/` is served:** no file of `roms/cache/` (those aren't the repository's to
  hand out, and a fresh checkout doesn't have them), and no path that leaves `roms/games/` (`..`, or its
  percent-encoded forms). Those answer 404 (or any status but 200). Everything the page loads still comes
  from its own origin (CONTRACT-I3).

## The game menu: `#game-menu`
| id | element | what it does |
|---|---|---|
| `game-menu` | any element, visible from the page's load on, beside `#rom-file` | Holds one **entry** per vendored game, no more and no fewer. |

- **An entry** is an element inside `#game-menu` with a `data-rom` attribute: the URL of its ROM (absolute
  or relative to the page), on the page's own origin, whose path is `/roms/games/<file>`. It is visible,
  and its text names the game (the builder's words; no two entries' texts the same). Which element it is
  (a button, a link that doesn't navigate, a list item) and how the menu is laid out are the builder's.
- **Choosing an entry is a click on it.** The page then fetches its ROM from that URL and loads it exactly
  as a file picked through `#rom-file` is loaded (CONTRACT-I3): the same checks, and on success the same
  power-on: a fresh console at frame 0, no battery save, every button released, nothing of the old game's
  sound heard from 30 ms after the click (CONTRACT-I4), `#message` hidden or empty (or the PAL notice).
  The bytes it loads are the bytes the server answered. The page may fetch an entry's ROM when it is
  chosen or before; the cases give their own answers (Playwright routing) from before the page loads.
- **A failed entry:** when the fetch fails (any status but 200, or the connection fails) or the core
  refuses the bytes, `#message` is visible with a reason in it (for a refusal, the core's `RomError`
  message, which names the mapper: a loose word pin, as I3's), and the game that was running runs on,
  untouched: its frame count climbs, its picture stays, its sound plays. On a page with no game yet,
  nothing starts (`nesTest.frames` stays 0). This is I3's refusal, met through the menu.
- **The same entry chosen again** loads its game again: a power-on, as at the first choice (and as the
  same file picked twice through `#rom-file`, whose value is cleared after each pick so the browser fires
  `change` again: CONTRACT-I3's input, kept).
- **Focus after a choice:** neither the entry nor the menu keeps the keyboard. Every mapped key pressed
  after a load (through the menu or the file chooser) is the game's only: no entry is chosen again,
  nothing is loaded, no file dialog opens (I3's rule for buttons and the file input, extended).
- **Paused** (`nesTest.pause()`), a choice loads and powers the game on at frame 0 and runs no frame, as
  a pick does.

## Loads that overlap: the last one asked for wins
A load is **asked for** by a click on a menu entry or by a `change` of `#rom-file` with a file in it.
Reading a file or fetching a ROM takes time, so a second load can be asked for while the first is still
in flight. Then:
- the game that runs once every answer is in is the one asked for **last**;
- a load asked for earlier that finishes later is **dropped**: it never starts, it shows no message (even
  if it failed), and it doesn't count in `nesTest.loads`;
- Reset and Power aren't loads: pressed while a load is in flight, they restart the running game, and the
  load, when it finishes, starts as asked.

## A new hook: `nesTest.loads`
`nesTest.loads` (number, read-only): how many games a load has powered on since the page loaded. It is 0
before any load, goes up by exactly 1 when a load (through `#rom-file` or `#game-menu`) starts its game,
and doesn't change for Reset, Power, a refused or failed load, or a dropped one. It is there from the
page's load event on, beside `frames`, `pause()` and `step(n)`, which keep their meanings.

## What a load leaves behind: nothing (J2)
A load is a power-on of a fresh console with the new cartridge in it, whatever ran before. Game B loaded
after game A, however A left things (its mapper's registers and IRQ line, CPU RAM, PRG-RAM and its battery
save, CHR-RAM, the nametables and their arrangement, OAM, the palette, the APU's channels and frame
counter, the controller's strobe and shift register, the keys held, the sound queued in the page's buffer
or remembered by its filters), runs exactly as B does on a fresh page:
- **its picture:** the canvas at B's emulated frame N is, index for index, the canvas a fresh page shows
  at B's frame N (with the same keys);
- **its sound:** at the destination, from 30 ms after the load is asked for, nothing of A is heard (not
  even a tail: measured at A's own pitch), and B's first 2 s have the envelope and pitches a fresh page's
  have, indexed from B's emulated sample 0. Not bit for bit: the page resamples to hold its buffer
  (J2o's settled conflict), so the cases compare the 20 ms RMS envelope (correlation) and the level;
- **its keys:** every button is released at a load, as on `blur` (J2j's open item, read the conservative
  way), so a key held through the switch reads as released in B until it is pressed again;
- **its save:** a battery save is kept across Reset and Power and cleared by any load, the same game's
  included (Decided Q3, with J2f: "A → A: the second load behaves exactly as a fresh page");
- **the page itself:** however many loads, restarts and hide/show cycles, one loop runs (I5) and the page
  makes one AudioWorkletNode of its own for its sound, and the listeners it has on `window` and `document`
  don't grow; the page is never reloaded.

## The collection's games (J1 per game, the Reach gradient)
Each is played on a paused page stepped frame by frame, through its way in (the menu for the vendored
ones, the file chooser for those fetched by hash), as its own source says (`cases/i9_cases.yaml`, `games`):
- **it boots to its title** by a stated frame (its picture up, and held but for its own blink or colour
  cycle);
- **a key plays it:** Start, through the keyboard, takes it off its title;
- **it ends:** with nobody playing well (and Start pressed once where its game-over screen waits for it),
  it is back at its own title within its budget; a game that can't end on its own isn't counted in Reach;
- **Reset and Power restart it:** Reset brings its title back; after Power its frame at the stated frame is
  a fresh page's, pixel for pixel.

| game | way in | mapper | ends |
|---|---|---|---|
| *Chase* (Shiru, public domain; 512 KiB UxROM build), `roms/games/chase.nes` | menu | 2 (iNES 1.0, 512 KiB PRG: its bank writes need no bus conflicts, as the build already has them on iNES 1.0 UxROM) | game over within 80 s idle; Start returns to the title |
| *Thwaite* 0.04 (Damian Yerrick, GPL-3+), `roms/games/thwaite.nes` | menu | 0 | game over within 3 min idle; back to the title by itself |
| *File Fixers* 0.1.0 (Wendel Scardua, MIT), `roms/games/file-fixers.nes` | menu | 4 (battery) | doesn't end on its own (an RPG): not in Reach |
| *Falling* v1.1 (Jesse Williams, MIT code), fetched by hash | file | 0 | game over within 90 s idle; Start returns to the title |
| *Linear Collision* (Bryan McClain, GPL-3.0), fetched by hash | file | 0 | its 5:00 clock runs out; back to the title by itself within 21,000 frames |

The J2 narrative plays *Chase* (A, mapper 2) and *File Fixers* (B, mapper 4, whose title music sounds from its
first second) in one page: A from the menu, B from the menu, A again from the file chooser.

## What the cases install and bring (`cases/i9_support.py`)
- **Routing:** answers for chosen ROM paths, installed before the page loads: a status (404, 500) with an
  empty body, other bytes (a probe built in the case, a non-NES file, a truncated one, an unsupported
  mapper), a failed connection, or any of these held back until the case releases it (to make loads
  overlap). Every other request goes to the server as usual.
- **Counters,** in an init script before the page's scripts: the AudioWorkletNodes made, by processor
  name (the tap's own `i4-tap`/`i5-tap` left out), and the listeners added to `window` and `document`, less
  those removed. Plus I4's tap and I5's pacing stub and hidden-tab simulation where a case needs them.
- **The file chooser:** a pick is a click on `#rom-file` and the file set in the chooser the browser
  opens (Playwright's `filechooser`), so the input has had the focus a person's click gives it.
- **`dump_probe`** (NROM with battery PRG-RAM, or AxROM with CHR-RAM): at power-on, before it writes
  anything, it reads CPU RAM, `$4015`, eight `$4016` reads with no strobe, the palette, the nametables,
  CHR, OAM, PRG-RAM, the nametable arrangement, and whether an IRQ comes with its own sources off, and
  shows every byte as a tile, so the canvas is the dump.
- **`mess_probe`** (MMC3): leaves its IRQ asserted and unacknowledged, PRG-RAM, CPU RAM, CHR-RAM, the
  nametables, OAM and the palette written, horizontal arrangement, every APU channel sounding, and the
  controller strobe high.
- **`note_probe` and `script_probe`** (I4's `apu_probe`): a held triangle note at 139.1 Hz; and a pulse
  sound from frame 0 that changes pitch and level every third of a second for 2 s.
- I3's `pad_probe` and `boot_probe`, I5's `tone_probe`, and nes15, through the menu by routing as well as
  through the file chooser.
