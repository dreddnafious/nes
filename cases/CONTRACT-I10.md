# The surface I10's cases drive (additive to CONTRACT.md and CONTRACT-I2.md to CONTRACT-I9.md)

Everything in the earlier contracts stays as it is: the CLI, the core API, the frozen drivers, the page (its
elements, keyboard, sound, game menu and `window.nesTest` hooks), the server and its vendored games. I10 adds
**the smoke run** (J5): one npm script that serves the checkout it is run in, drives the page end to end in
headless Chromium through its real entry points on every mapper the core supports, and writes a report a
person reads. It adds one export to the core (**`MAPPERS`**, the registry the smoke run's list comes from).
The cases run the command the way J5's consumer does: in a checkout (a temporary `git worktree` of the
commit, or a copy of the working tree on top of one), with the one npm command, then reading the report and
the exit code. Their broken builds are made by patching that copy, never by a flag that ships (Decided).
The broken builds' rows are in `cases/i10_cases.yaml`.

## The core's registry: `MAPPERS`
```ts
export const MAPPERS: readonly number[]      // from src/core/index.ts
```
- **What it is:** the iNES mapper numbers the core supports, ascending, each once: exactly the numbers for
  which `new NES(bytes)` accepts an iNES 1.0 file (32 KiB PRG-ROM, 8 KiB CHR-ROM, no battery) and makes a
  console. Every other number is refused with a `RomError` naming it (CONTRACT.md, unchanged). Today:
  `[0, 1, 2, 3, 4, 7]`.
- **The page gets the core through `src/core/index.ts`** (CONTRACT.md's Consumer 2), as it does now: a
  broken build that replaces `NES` there is the one the page runs.

## The command: `npm run smoke`
```
npm run smoke [-- --mapper <n> [--mapper <n> ...]]
```
Run from the root of a checkout (its working directory is the checkout). It:
1. reads the mapper list from the checkout's own `MAPPERS` (never a list of its own);
2. serves the checkout's own build (the page, `src/`, `roms/games/`, exactly as `node src/web/serve.ts`
   serves them, read from this checkout's files as they stand when the run starts: no build made earlier,
   no server already running) on a free local port on `127.0.0.1`, and stops it before it exits;
3. drives the page in headless Chromium (Playwright's) through its real entry points (below), for every
   **subject** on every mapper in the list;
4. writes the report (below), and exits **0** when the run passed, **non-zero** otherwise.

- **`--mapper <n>`** (any number of times): only those mappers' checks run (for a quicker run while
  working on one board). The report still lists the whole registry in `mappers` and says which were asked
  for in `only`. A number the registry doesn't have has no subject and no check, so a run asked only for
  such numbers has **zero checks**, and fails.
- **How long:** the whole run (no `--mapper`) finishes, report written, within **5 minutes** on this
  machine (Jason, at approval).
- **What it needs:** Node 24; the checkout's `.venv` (`requirements-dev.txt`) and `node_modules` (as
  `npm ci` leaves them), whichever the builder uses; Playwright's Chromium installed. Nothing from the
  network, and nothing of `roms/cache/` (a fresh checkout has neither): the subjects are the vendored games
  and the run's own probes. The cases give a checkout's `.venv` and `node_modules` as directories of
  links to this repository's (so they stay ignored by git).
- **Where its code lives:** anywhere but `src/`. I1's invariant (`test_i1_invariants.py`) forbids starting
  a process from `src/`, and the smoke run starts the server and Chromium. Its port is its own to choose:
  `node src/web/serve.ts --port 0` stays refused (`tests/test_serve.py`); a port taken between choosing
  and binding is chosen again.
- **What it writes:** only under `out/smoke/` (gitignored). It never writes a tracked file: whatever it
  compares against (title references and the like) is committed and read, never made or remade by a run.
  A checkout that was clean before a run is clean after it (`git status --porcelain` empty).
- **Its output while it runs:** one line on stdout per check as it finishes (flushed at once), naming the
  mapper, the subject, the check and its result; the last line names the path of `report.md`.

## The subjects: a game or probe on every mapper
- **Every vendored game** (each `.nes` in the checkout's `roms/games/`) whose mapper is in the run is a
  subject: loaded through the file input from that file, and through its game-menu entry.
- **A probe** is the run's own cartridge, built by the run from the spec (NESdev) on a mapper with no
  vendored game, written under the run's directory (never committed). Every mapper in the list with no
  vendored game has at least one probe. A probe shows each of controller 1's buttons on its screen.
- A mapper with **no subject** at all (a number added to the registry with no game or probe) is
  **not covered**: its `covered` check fails.

## The checks
Each subject gets one row of each subject check; each mapper gets one `covered` row (its `subject` null).
| check | what it judges | runs only if |
|---|---|---|
| `covered` | the mapper has at least one subject | (always) |
| `file-input` | the subject loads through `#rom-file`: a fresh console at frame 0 (`nesTest.frames`, `nesTest.loads` up by one), `#message` hidden or empty | the mapper is covered |
| `game-menu` | its `#game-menu` entry (a click) loads it as the file input does: `nesTest.loads` up by one, frames from 0, and the canvas at frame N the same as the file input's at frame N. **N/A** for a subject that isn't a vendored game (a probe), and only for one | `file-input` passed |
| `title` | the title on the canvas: the canvas at the subject's frame N is, colour for colour (RGB), the subject's committed reference, made from no part of the build under test at run time | `file-input` passed |
| `key` | a key changes the screen: with **Start** (`Enter`) pressed from frame N, the canvas a few frames on differs from the same frames with no key, on a run that is otherwise the same (deterministic), so the key, and not the title's own animation, made the difference. A probe checks each of controller 1's eight keys (CONTRACT-I3's map) this way | `title` passed |
| `sound` | sound is not silent: in a window known to sound for the subject, the RMS at the AudioContext's destination is above a floor; the window starts after a trusted input gesture (a Playwright key press or click) under the autoplay policy (below) | `file-input` passed; and `key` too, for a subject whose window is reached by pressing a key (a game whose title is silent) |
| `reset` | Reset (a click on `#reset`), from play, brings back the title | `title` passed |
| `power` | Power (a click on `#power`), from play, is a fresh load: `nesTest.frames` back to 0, and the canvas at frame N the title reference (a battery save apart, Decided Q3) | `title` passed |
| `loaded-once` | across all of the subject's checks, the page loaded exactly once per tab it opened (no reload) | `file-input` passed |

- **Results:** each row's `result` is one of `PASSED`, `FAILED`, `NOT RUN`, `N/A`.
- **Only the root cause is `FAILED`** (Decided): a check whose "runs only if" check didn't pass is
  `NOT RUN`, never `FAILED` (or `N/A`, for a probe's `game-menu`). A check that can't finish for any other
  reason (Chromium gone, a timeout) is `FAILED` or `NOT RUN`, and every check after it that didn't run is
  `NOT RUN`. No row is ever left out: a report always has every row its subjects and mappers call for.
- **`NOT RUN` counts as a failure.** The run **passed** when at least one row is `PASSED` and none is
  `FAILED` or `NOT RUN` (`N/A` is neither); anything else **failed**: zero checks, a mapper not covered, a
  crash.

## The real entry points
The page is driven only as a person drives it, through what the earlier contracts name:
- loads through `#rom-file` (a file on disk: the vendored file, or the probe the run wrote) and by a click
  on a `#game-menu` entry; keys through Playwright's keyboard (trusted events, matched by `code`); Reset and
  Power by clicks on `#reset` and `#power`;
- the picture read from the `#screen` canvas (its pixels, as the earlier cases read it), and the sound at
  the AudioContext's `destination` (as CONTRACT-I4's tap hears it: whatever reaches the destination, never
  the samples on their way to it);
- `nesTest.pause()`, `step(n)`, `frames` and `loads` for frame-exact checks, as the corpus uses them. No
  other hook, and no call into the core, loads, presses or reads anything.
- **The autoplay policy:** headless Chromium doesn't enforce it, even with `--autoplay-policy` (tried on
  this machine's Chromium 156), so the run simulates Chrome's documented policy before the page's scripts,
  as CONTRACT-I4's cases do: a context made before the page's first activation (a trusted `keydown`,
  `mousedown`, `pointerdown`, `pointerup` or `touchend`) starts suspended and reads `"suspended"`, and its
  `resume()` does nothing until an activation; a context made after one starts as the browser makes it.

## The report: `out/smoke/<run>/`
Each run makes its own directory under the checkout's `out/smoke/`, whose name holds its start time (UTC)
and the commit's first 7 hex digits; two runs, even started together, never share one. In it:
- **`report.json`:**
  ```
  { "commit": "<40 hex>",          // git rev-parse HEAD of the checkout
    "dirty": true | false,         // git status --porcelain lists anything (ignored files aside)
    "started": "<ISO 8601, UTC>", "seconds": <wall time of the run>,
    "port": <the port the page was served on>,
    "mappers": [<the registry, MAPPERS, all of it>],
    "only": null | [<the --mapper numbers>],
    "subjects": [ { "name": "<subject>", "mapper": <n>, "menu": true | false,
                    "rom": "roms/games/<file>" | null } ],     // menu: a vendored game; rom: null for a probe
    "checks": [ { "mapper": <n>, "subject": "<subject>" | null, "check": "<check>",
                  "result": "PASSED" | "FAILED" | "NOT RUN" | "N/A", "detail": "<why, in words>",
                  "png": "<file in this directory>" | null } ],
    "counts": { "PASSED": <n>, "FAILED": <n>, "NOT RUN": <n>, "N/A": <n> },
    "failures": <FAILED + NOT RUN>,
    "result": "PASSED" | "FAILED" }
  ```
  `subject` in a row is a subject's `name` (null only on `covered` rows and on the rows of a mapper not
  covered). A `game-menu`, `title`, `key`, `reset` or `power` row that ran (`PASSED` or `FAILED`) names in
  `png` the frame it judged, saved in this directory as a 256 × 240 PNG.
- **`report.md`**, its Markdown twin, for a person: the commit, the tree `clean` or `dirty`, the result, and
  one line per row of `checks`, in the same order, naming the mapper, the subject, the check and its result.
- **The frame PNGs** the rows name.

**`out/smoke/latest`** is a symbolic link to the directory of the run that finished last. Earlier runs'
directories are kept.

## How the cases break a build on purpose (`cases/i10_support.py`)
Each broken build is a copy of the checkout with one patch, run with the one command. So these files must
stay patchable as they are now:
- **`src/core/index.ts`** is moved aside (`index.built.ts`) and replaced by a module that re-exports it with
  one change: `NES` refusing one mapper number (with `MAPPERS` unchanged), or `MAPPERS` with a number added
  that nothing covers;
- **`src/web/keyboard.ts`**: one key's line taken out of `KEY_MAP` (`Enter: BUTTON_START,` and the like);
- **`src/core/palette.ts`**: red and blue swapped in its exported `RGB` table after it is made (every index
  still distinct, every colour wrong);
- **`src/web/index.html`**: a script put first in its `<head>` (so it runs after the run's own init scripts
  and before the page's): the file input's `change` swallowed; `putImageData` a no-op (a blank canvas);
  clicks on `#reset` swallowed; `#power` doing a Reset (a Power that keeps RAM); everything connected to a
  destination going through a zero gain (silence at the output); the page's AudioContext made as the page
  loads, before any gesture, with `resume()` doing nothing (a page that never resumes its sound on a
  gesture); or the game menu's `<!-- game menu entries -->` slot taken out (an empty menu).
- Chromium is killed (SIGKILL, every Chromium process under the run) once a check has passed and Chromium
  has been up for 3 s.
