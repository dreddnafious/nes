---
climb: A playable NES emulator in the browser, accurate to the test ROMs, built increment by increment
phase: climb
mode: unattended
corpus: cases
test_command: .venv/bin/python -m pytest cases -p no:cacheprovider -q
source: src
plateau_k: 3
held_out_command: cd /home/jason/dev/AI_Experiments/nes && /home/jason/dev/AI_Experiments/nes/.venv/bin/python -m pytest /home/jason/.cache/agentfng/climb/nes/held_out -q -p no:cacheprovider --rootdir /home/jason/dev/AI_Experiments/nes
held_out:
  count: 8
  passed: 0
  burned: 0
readiness_open: 0
phases:
- name: brief
  start: '2026-10-29T11:04:41-05:00'
  end: '2026-10-29T11:11:47-05:00'
  log: 10
- name: corpus
  start: '2026-10-29T11:11:47-05:00'
  end: '2026-10-29T11:19:11-05:00'
  log: 22
- name: readiness
  start: '2026-10-29T11:19:11-05:00'
  end: '2026-10-29T11:21:15-05:00'
  log: 25
- name: approve
  start: '2026-10-29T11:21:15-05:00'
  end: '2026-10-29T11:23:33-05:00'
  log: 26
- name: climb
  start: '2026-10-29T11:23:33-05:00'
  log: 27
fingerprint: b990abe1d6665a36337102bf089c4790ec9865998828fb8729ab28383742423a
deltas: 42
setup_fingerprint: 4fdd25c93a3689fd08b9a9481a97dad9b10488b3b5a65f44f5f455e100e163e2
corpus_commit: b18516006ec43a5f6180cf79ccecc9c7e9ce645d
approved_counts:
  weakened: 0
  field: 0
  visits: 4
  parked: 0
intent_fingerprint: b09cb145e9a055c905bdfa51b7e1a402ac59e10043925caf937b9f830af19b86
stories_fingerprint: 707b5d80dcceec3769e3c332bed370a4b995b1d8852ea6f212bf32244d3496aa
seed_pins:
  J1: 2
  J2: 1
  J3: 1
  J4: 1
runs_from: 0
---
# Climb: nes

## Intent
**Done means:** a person opens a web page, loads a legal homebrew NES ROM from a file, and plays it with
the keyboard, with sound. Jason defines "right" as: "you can actually play a game meaning the game starts. You
can play it. It ends. You can restart it without having to reload the browser or do anything unusual. It
includes audio. It's the faculties required by the Homebrew games." Audio is part of the goal, not a stretch.
Today none of this exists.

**What this run is for.** The climb scaffold is under test (the iterative fork, in
`.agentfng/skills/climb`), not the model. The NES has an oracle beyond doubt, so a failure belongs to the
technique. In Jason's words: "winning means rigor and owning the result". Reds are reported as reds, and
plateaus with their real causes.

**Two measures,** reported at every checkpoint:
- **Accuracy:** test-ROM cases passing.
- **Reach:** how many vendored legal homebrew games boot, play, end and restart. Jason: "the width of the
  Homebrew games that run are a useful gradient".

**Features in scope** (whatever the homebrew games need):
- **CPU (Ricoh 2A03 / 6502 core):**
  - all 151 official opcodes and the unofficial opcodes that test ROMs and games use;
  - every addressing mode, the flags, and decimal mode ignored, as on the 2A03;
  - cycle-exact timing, including page-cross and branch penalties;
  - interrupts (NMI, IRQ and BRK, including hijacking) and reset;
  - OAM DMA and DMC DMA stalls;
  - open bus where test ROMs check it.
- **PPU (2C02):**
  - registers `$2000`–`$2007`, with the write latch, the read buffer and open bus;
  - background tiles and attributes, and sprites (8×8 and 8×16) with priority;
  - sprite-0 hit and sprite overflow (including the hardware bug);
  - fine and coarse scrolling, including mid-frame changes;
  - palettes and greyscale/emphasis bits, and nametable mirroring (horizontal, vertical, single-screen, four-screen);
  - vblank and NMI timing to the dot, and the odd-frame skipped dot;
  - a 256×240 frame out.
- **APU:**
  - two pulse channels (sweep, envelope, length counter), triangle (linear counter), noise and DMC (sample
    playback, with its IRQ and DMA);
  - the frame counter in 4-step and 5-step modes, and the frame IRQ;
  - mixing to an audio sample stream.
- **Input:** controller 1 (and 2) through `$4016`/`$4017` with the strobe, mapped to the keyboard.
- **Cartridges:** the iNES and NES 2.0 header; PRG and CHR in ROM and RAM; PRG-RAM; and the mappers the chosen
  homebrew games need: NROM (0) first, then the common ones (UxROM 2, CNROM 3, AxROM 7, MMC1 1, MMC3 4 with
  its scanline IRQ, and others the collection calls for).
- **Browser front end:**
  - a page served locally, with ROM loading from a file and a canvas at 60 fps;
  - WebAudio output without crackle or drift;
  - keyboard controls, and reset/restart without reloading;
  - switching to another ROM in the same page, with no state leaking between games;
  - pausing cleanly when the tab is hidden.
- **Performance:**
  - full speed, holding 60 fps, in a browser on this machine;
  - long sessions without memory growth or audio lag;
  - headless test-ROM runs fast enough that the corpus runs in minutes.

## Decided
- **Language: TypeScript** on Node 24, which runs `.ts` directly.
  - **The core** in `src/core` is pure TypeScript with no DOM. It runs headless under Node for the tests, and in
    the browser for play.
  - **The front end** in `src/web` is the presentation layer: canvas, WebAudio, keyboard. It plays in a browser
    whatever the backend.
  - **Rust was offered and declined:** there's no speed need (about 1.8M CPU cycles and 5.4M PPU dots a second
    is well inside V8's typed-array JIT), and one language is easier to review.
  - Revisit if the per-dot PPU can't hold 60 fps in headless Chromium after profiling.
- **"Accurate" = passes the test ROMs.** Frame-identical output to a reference emulator would mean running
  another emulator, which is forbidden.
- **How the cases drive the build:**
  - The cases are pytest in `cases/`.
  - They drive the build as its consumers meet it: a Node CLI (`src/cli`, run as `node src/cli/nes.ts …`) for
    headless runs (run N frames or until a result, CPU trace, memory read, PNG screenshot, audio samples, input
    scripts), and Playwright with headless Chromium against the page served on a free local port.
  - **The interpreter is pinned:** `.venv/bin/python` (made with `uv`), since bare `pytest` in this harness
    resolves to another project's shim.
- **Verification: 3 (a smoke run on a local deploy); next: none.** The page is served for real on a free local
  port, driven in headless Chromium through its real entry points (file input, keyboard, canvas, AudioContext).
  There is no real NES hardware to reach level 1 or 2.
- **Stopping** (Jason): "continue until satisfied that you're done or I stop you or despite your best effort, you
  can't work on anything else because everything that you would be normally working on is stuck." There is no
  time budget.
- **A stuck increment is PARKED, and the run goes on** (Jason chose option B; built into the fork by agentFNG):
  - Jason: "if you get stuck on a mapper find something else useful to do. 'useful' isn't unbound, it should
    apply meaningfully to the capabilities of the end artifact."
  - A parked increment opens as a draft PR on the stack; its "What did you try" list stays on the board.
- **Stories** (Jason): "a large variety of user stories that you'll satisfy over the cycles and obviously some of
  those will be performance related … a few compound user stories, not test-likes but narrative-likes."
- **Legal** (Jason): "we only want to do legal games … We absolutely don't want to accidentally break any
  trademarks or IP licenses."
  - **No commercial ROMs, ever.**
  - A test ROM or homebrew game is vendored only after its licence or its author's stated terms are checked to
    allow redistribution, recorded in `roms/LICENSES.md`.
  - **No clone trading on another's trademark.** Terms that allow use but not redistribution mean no vendoring.
- **Honesty:**
  - Don't copy existing emulator source, don't call another emulator, don't vendor one in. Knowing how the NES
    and emulators work is fine.
  - Every web search, fetch and GitHub use (beyond this repo's routine pushes and PRs) goes under
    `## Outside use`.
  - An after-the-fact audit (a similarity scan plus a comparison of tool calls against the log) informs Jason's
    conclusions; it gates nothing.
- **Checkpoints:** at each increment's end, inline in the transcript:
  - the score: green / total, and games running / vendored;
  - a small score chart (`.html` or PNG);
  - once there's a PPU, a screenshot of the emulator's screen.
- **Hazards:** never restart or deploy agentFNG while the run is live. Use free ports only.
- **Field access:** none during the run. The climb is unattended, so there are no field cases. Jason playing a
  homebrew game himself is owed after merge (`## Field after merge`).
- **Unlock:** none; verification stays at 3. Real hardware is out of reach.
- **Data** (from the conversation; accepted at approval): the data is ROM files.
  - **Test ROMs:** 16–64 KB, mostly NROM or MMC1.
  - **Homebrew games:** 16 KB – 512 KB+, across several mappers.
  - **The odd entities the loader must meet:**
    - a non-NES file;
    - a truncated file;
    - an NES 2.0 header;
    - a header with junk in bytes 7–15 (old "DiskDude!" style);
    - a 512-byte trainer;
    - CHR-RAM (no CHR-ROM);
    - battery PRG-RAM;
    - an unsupported mapper, which must fail clearly rather than crash.

    These are hand-made from the spec, in the corpus.
  - **Volume:** the corpus runs dozens of test ROMs, each for up to tens of millions of CPU cycles, so speed
    matters.
- **Oracle ROMs are fetched by hash, never committed.** The test ROMs (`christopherpow/nes-test-roms`, pinned
  at commit `95d8f62`) carry no licence that grants redistribution. `cases/oracles.json` pins each file's URL
  and SHA-256, and `cases/conftest.py` fetches each one on first use into `roms/cache/` (gitignored). A hash
  mismatch is an error, never a silent swap. `roms/LICENSES.md` records the terms. The same rule will apply
  to homebrew games whose terms allow use but don't state redistribution.
- **The corpus's surface is `cases/CONTRACT.md`:**
  - the CLI `node src/cli/nes.ts trace …`, whose first 16 columns follow `nestest.log`;
  - the core API: `new NES(bytes)`, `runFrame()`, `peek(addr)`, `reset()`, and `RomError`;
  - the frozen driver `cases/drive.ts`, which reads blargg's `$6000` protocol itself, so the build never
    grades its own test results.
  - The source must run under Node's native type stripping (no enum, no namespace, no constructor parameter
    properties).
- **I1's corpus is I1's scope only.** Its 44 cases (42 red, plus 2 invariants already green: no calling out to
  another program, no unlicensed ROM in git) cover the CPU, the cartridge loader and the I1 invariants.
  The journeys' own multi-step cases (J1–J4, at verification 3 through Playwright) are written by each later
  increment's fresh author, when the roadmap reaches the pieces they drive (I3 for J1's first walk, I4 for
  J1's audio, I5 for J3, I8/I9 for J4 and J2). Writing them now would hold I1 red for work it doesn't do.
- **Existing tests the change will break:** none. The repo has no tests before this corpus.
- **(axes) Power-on state:** RAM, OAM and palette RAM fill with a fixed pattern (all `$00`), so runs are
  deterministic and title frames can be compared. Random fill is a debug option only.
- **(axes) A bad file, or a cancelled picker, while a game plays:** the game keeps running, and an error is
  shown.
- **(axes) Clock reconciliation:** pace by an accumulator against real time (or the audio clock), and resample
  the audio dynamically (about ± 0.5 %) to hold the buffer fill steady. Never drop or repeat frames on a 60 Hz
  display.
- **(axes) Key map:** Select is Right Shift only. When opposite directions are held at once, the newest press
  wins.
- **(axes) Q1, the reach corpus and "ends" (approved as recommended):**
  - the reach denominator is only homebrew games whose licence allows redistribution, each listed
    with its licence and mapper.
  - For endless or score-attack games, game over counts as the end.
  - Report the per-stage counts (boot, play, end, restart); a game counts toward Reach only when it passes all
    four.
- **(axes) Q2, revision target and conflicting test ROMs (approved as recommended):**
  - target the RP2A03G CPU, the RP2C02G PPU and MMC3B/C.
  - Tests that can't pass on that revision (e.g. `mmc3_test` `6-MMC3_alt`) stay red in the raw accuracy count,
    labelled expected-fail with the reason, never dropped from the denominator.
- **(axes) Q3, saves (approved as recommended):**
  - battery PRG-RAM survives a soft reset and a power cycle of the same game within the session.
  - It is cleared on a ROM switch.
  - Nothing is saved to disk in this run.
- **(axes) Q4, PAL (approved as recommended):**
  - NTSC only. PAL-flagged ROMs run at NTSC timing behind a notice, and are kept out of Reach.
- **(axes) Q5, the look (approved as recommended):**
  - one standard 2C02 palette, with all 240 lines shown.
  - Test comparisons are made on palette indices, so the palette choice can't fail a test.
- **Repo:**
  - `github.com/dreddnafious/nes`, **public**, created by the model with `gh` after the initial commit on `main`
    (`4ec9a05`).
  - **Branch** `climb/nes`, base `main`. Each increment ships its own PR, stacked on the last; the stack is
    merged once, at the end.

## References
- **NESdev wiki** (https://www.nesdev.org/wiki): the spec for the CPU, PPU, APU, mappers, the iNES/NES 2.0
  headers and timing. Read for spec facts only.
- **`nestest.nes` + `nestest.log`** (kevtris):
  - **checks:** the CPU against a golden trace, line by line (PC, the opcode bytes, A/X/Y/P/SP, the PPU dot and
    the CPU cycle count), over 8,991 lines covering every official opcode and then the unofficial ones;
  - **how it runs:** started at `$C000` (automation mode);
  - **the strongest oracle for increment 1.**
- **blargg's test ROMs:**
  - **how they report:** a status byte at `$6000` (`$80` running, `$81` needs reset, `$00`–`$7F` result, 0 =
    pass), the signature `DE B0 61` at `$6001`–`$6003`, and a zero-terminated text message from `$6004`; also on
    screen;
  - **CPU:** `instr_test-v5` (16 ROMs, every opcode's effects), `instr_timing` (cycle counts), `instr_misc`,
    `cpu_dummy_reads`, `cpu_interrupts_v2` (NMI/IRQ/BRK timing, which needs the PPU and the APU's IRQ),
    `cpu_reset`;
  - **PPU:** `ppu_vbl_nmi` (vblank/NMI timing), `sprite_hit_tests_2005`, `sprite_overflow_tests`,
    `ppu_open_bus`, `ppu_read_buffer`;
  - **APU:** `apu_test` (length, frame counter, IRQ, DMC basics), `blargg_apu_2005.07.30`, `dmc_dma_during_read4`;
  - **mappers:** `mmc3_test_2` (scanline IRQ, A12 behaviour).
- **Other suites, to research:** controller and read tests, and mapper tests for MMC1 and the discrete mappers.
- **Homebrew games** (legal, licence-checked before vendoring; candidates from memory, unverified):
  - Damian Yerrick's (PinoBatch) open-source games: *Concentration Room*, *Thwaite*, *robotfindskitten*, and
    the *240p Test Suite*;
  - Shiru's freely released games (*Alter Ego*, *Lawn Mower*, …);
  - NovaSquirrel's *Nova the Squirrel*;
  - sgadrat's *Super Tilt Bro.*

  The research picks a spread across mappers.
- **Prior art for comparison only** (don't read its source): nightowl-nes, an autonomous Claude Code issue loop.
  Over 8 days and 68 PRs it passed the full nestest (8,991 lines), had 216 of 256 opcodes, and tested 66 of 263
  test ROMs.

## Roadmap
Each increment is one PR, stacked on the last, small enough for two fresh reviewers plus Copilot to genuinely
review it. Playable comes before breadth, so a run that stops early still leaves something to play. Mapper
increments are independent of each other, so a parked one doesn't block the rest.

**I1: rig + CPU.**
- **Builds:**
  - the repo scaffold;
  - `roms/` with the oracle ROMs vendored and their licences;
  - the Node CLI (`run`, `trace`, `mem`), the cartridge loader (iNES, NROM) and the bus with RAM mirroring;
  - the 6502 CPU, cycle-exact, official and unofficial opcodes.
- **Serves:** the base for every journey.
- **Oracle:** `nestest.log` line for line, including cycles; blargg `instr_test-v5` (all 16), `instr_timing`,
  `instr_misc` and `cpu_dummy_reads` where they run without a PPU.
- **Depends on:** nothing.

**I2: PPU.**
- **Builds:** the dot-based 2C02: registers, rendering, sprites, sprite-0 and overflow, scrolling, palettes,
  mirroring, vblank/NMI timing; and the CLI's PNG screenshots.
- **Serves:** J1 steps 3–4 and J4.
- **Oracle:** blargg `ppu_vbl_nmi`, `sprite_hit_tests_2005`, `sprite_overflow_tests`, `ppu_open_bus`,
  `ppu_read_buffer`; and a test ROM's on-screen text rendering legibly in the screenshot.
- **Depends on:** I1.

**I3: first playable in the browser.**
- **Builds:** the page (a local static server, a file input, the canvas, the keyboard map, reset, a 60 fps loop
  paced by `requestAnimationFrame`) and the controller ports.
- **Serves:** J1 steps 1–4 and 7, for an NROM homebrew game.
- **Oracle:** Playwright; an NROM homebrew boots to its title, and key presses change the screen; a controller
  test ROM, if a legal one is found.
- **Depends on:** I2.

**I4: APU and audio.**
- **Builds:** all five channels, the frame counter and IRQ, DMC DMA, and the mixer; WebAudio output (an
  AudioWorklet or a buffered source) with the emulation paced against the audio clock.
- **Serves:** J1 step 5; J1 complete for NROM games.
- **Oracle:** blargg `apu_test`, `blargg_apu_2005`, `cpu_interrupts_v2`; audio captured non-silent in the
  browser.
- **Depends on:** I1 (the core), I3 (the browser audio).

**I5: long sessions and performance.**
- **Builds:** frame pacing, audio-buffer control, behaviour on tab hide and show, memory steadiness, and
  headless speed.
- **Serves:** J3.
- **Oracle:** Playwright measurements (frame times, audio underruns, heap over a long run), and the headless
  frames per second.
- **Depends on:** I3, I4.

**I6: discrete mappers (UxROM 2, CNROM 3, AxROM 7).**
- **Builds:** bank switching and single-screen mirroring.
- **Serves:** J1/J2 reach.
- **Oracle:** the mapper test ROMs found in research; vendored homebrew on these mappers boots and plays.
- **Depends on:** I2.

**I7: MMC1 (mapper 1).**
- **Builds:** the serial shift register, PRG/CHR banking modes, mirroring control and PRG-RAM.
- **Serves:** J1/J2 reach.
- **Oracle:** MMC1 test ROMs from research; homebrew on MMC1.
- **Depends on:** I2.

**I8: MMC3 (mapper 4).**
- **Builds:** banking, and the A12-clocked scanline IRQ.
- **Serves:** J4 (split screens), J1/J2 reach.
- **Oracle:** `mmc3_test_2`; homebrew on MMC3 with a status bar.
- **Depends on:** I2.

**I9: the collection session.**
- **Builds:** switching games without reload and with no state leaking, plus whatever mappers or features the
  remaining vendored homebrew needs (UNROM-512, GTROM, …, as research finds).
- **Serves:** J2, and widens the reach gradient.
- **Oracle:** the J2 narrative case in Playwright; each vendored game boots, plays, ends and restarts.
- **Depends on:** I3, I4, and the mappers its games use.

## Stories

### J1: play a homebrew game in the browser, start to end, with sound
Jason's words, quoted whole (dictated in the design session):
> "Heifer journey yeah we open a page load of ROM play with the keyboard. I don't have a gamepad and hear the
> sound. What right means is that you can actually play a game meaning the game starts. You can play it. It
> ends. You can restart it without having to reload the browser or do anything unusual. It includes audio.
> It's the faculties required by the Homebrew games."

- **Consumer:** a person at a desktop browser, with a keyboard and no gamepad.
- **Doesn't work now:** nothing exists: no emulator, no page.
- **Steps:**
  1. Open the page (served locally).
  2. Load a homebrew ROM file.
  3. The game boots to its title screen.
  4. Start it and play it with the keyboard.
  5. Hear its music and sound effects while playing.
  6. Play until the game ends (game over, or the end of the game).
  7. Restart the game from the page, with no browser reload and nothing unusual.
- **Left with:** a game that played start to end, with sound, ready to play again.
- **It holds when:** checked in headless Chromium through Playwright, per vendored homebrew game:
  - the title screen renders within **5 s** of loading : the canvas is not blank and shows the
    game's picture;
  - a scripted key press changes the screen, compared with the same run without the press;
  - the audio output during play is not silent: its RMS is above the noise floor over a **5 s** window
    ;
  - a scripted session reaches the game's end state, detected the way that game shows it;
  - restarting brings back the title screen, and the page has loaded exactly **once**;
  - over **10 s** of play, the median frame interval is **16.7 ms ± 1 ms** and **≥ 99 %** of frames take
    under **20 ms** .
- fork (step 2): settled a file picker plus a built-in menu of the vendored legal games (the person, at approval)
- fork (step 4): settled arrows + Z/X (B/A) + Enter (Start) + Right Shift (Select) (the person, at approval)
- fork (step 7): settled both, as two buttons: soft reset and power cycle (the person, at approval)

### J2: an evening with the collection
Drafted by the model from the conversation and confirmed by Jason at approval: Jason said "the width of the Homebrew games that run are a useful gradient" and
asked for "a few compound user stories, not test-likes but narrative-likes". The words below are the model's.
> I open the page and load a game. I play it until I lose. Then I load a *different* game, one on a different
> cartridge type, from the same page with no reload, and play it for a while. Then I go back to the first game
> and start it again. The picture and the sound are right for whichever game is loaded, and nothing from the
> previous game leaks into the next.

- **Consumer:** a person at a desktop browser, with a keyboard.
- **Doesn't work now:** nothing exists.
- **Steps:**
  1. Load game A (mapper X) and play it to game over.
  2. Load game B (mapper Y ≠ X) without reloading.
  3. Play B; its graphics, sound and controls work.
  4. Load A again.
  5. A boots to its title and plays as it did the first time.
- **Left with:** two games played back to back in one page.
- **It holds when:** in one Playwright session (one page load), A → B → A:
  - each game's title screenshot after the switch matches its title screenshot from a fresh page;
  - no sample of the previous game is output after the switch, and the new game's first 2 s of audio match a
    fresh page's (J2c, replacing the proposed 0.5 s silence at approval);
  - mapper, PRG-RAM, palette and APU state are reset on each load;
  - the page has loaded exactly once.

### J3: a long sitting stays smooth
Drafted by the model from the conversation and confirmed by Jason at approval: Jason said "obviously some of those will be performance related." The words
below are the model's.
> I play one game for a long time. Then I switch to another tab for a minute and come back. The whole time the
> picture is smooth and the sound is clean: no crackle, no drift, no growing delay between pressing a button and
> hearing it. When I come back from the other tab, the game simply carries on: it doesn't race to catch up.

- **Consumer:** a person at a desktop browser.
- **Doesn't work now:** nothing exists.
- **Steps:**
  1. Load a game and play.
  2. Keep playing for a long session.
  3. Switch tabs away, then back.
  4. Carry on playing.
- **Left with:** a session that felt like a console.
- **It holds when:** numbers, measured in Playwright:
  - over **30 minutes** of emulated play (fast-forwarded headless for the core, and **2 minutes** real-time in
    the browser), the frame rate holds the J1 numbers;
  - audio underruns are **0** after the first second;
  - audio latency stays under **100 ms** and doesn't grow by more than **10 ms** across the session;
  - the JS heap after the session is within **10 %** of the heap after the first minute;
  - after the tab is hidden for **≥ 10 s** and shown again, the emulator runs no more than **2 frames** of
    catch-up, and audio resumes within **200 ms**;
  - headless, the core runs at **≥ 5×** real time , so the corpus runs in minutes.

### J4: a game that pushes the hardware
Drafted by the model from the conversation and confirmed by Jason at approval: Jason's "It's the faculties required by the Homebrew games." The words below are
the model's.
> I load a homebrew game that leans on precise timing: a status bar split with sprite-0 hit or a mapper IRQ,
> scrolling that changes mid-frame, sampled drums. It looks and sounds the way its author meant: the split line
> doesn't jitter, the scroll doesn't tear, the drums play.

- **Consumer:** a person at a desktop browser.
- **Doesn't work now:** nothing exists.
- **Steps:**
  1. Load a timing-sensitive game.
  2. Play through a scrolling section with a status bar.
  3. Hear its DMC samples.
- **Left with:** a game that looks and sounds as its author meant.
- **It holds when:**
  - across **600 consecutive frames** of scripted play, the status bar's split scanline sits on the same
    row in every frame ;
  - the DMC channel's output is non-zero during the game's sampled sounds;
  - every test ROM covering those features (sprite-0 hit, MMC3 IRQ, DMC) passes, so the accuracy score and
    the reach score agree.

## Variants
*From axes (a fresh subagent, given only the Intent and the Stories): 35 BREAKS rows. Where a row's pinning case
leaned on "a reference capture from an independent emulator", that would mean running another emulator, which
is forbidden here. The pin is adapted, marked "adapted:", to evidence we may use: a test ROM, the author's
published materials, or hardware facts from the spec.*

### J1a: title renders — unknown input (of J1)
- **What the consumer would see:** for an unsupported mapper, a bad header or a non-NES file, a blank or
  garbled canvas with no reason. Garbled tiles still pass "not blank".
- **The case that pins it:**
  - loading a mapper-30-style unsupported ROM, or a `.txt`, shows a clear message ("mapper N not supported",
    "not an iNES/NES 2.0 file");
  - nothing starts, and a game already running keeps running.

### J1b: title renders — partial (truncated file) (of J1)
- **What the consumer would see:** a file shorter than its header promises boots from padded banks and draws
  junk that passes "not blank".
- **The case that pins it:** a file shorter than its header's PRG+CHR size is rejected with a message naming
  the size mismatch.

### J1c: title renders — unknown reference picture (of J1)
- **What the consumer would see:** a title drawn from the wrong bank or with the wrong mirroring passes.
- **The case that pins it:**
  - adapted: the frame at a fixed emulated frame N is compared, by palette index, against the game's title as
    its author publishes it, where that exists;
  - otherwise, against structural invariants the corpus author can state (the expected text rows legible, the
    CHR tiles in use drawn from the right bank);
  - plus the PPU test ROMs that cover the mechanism.

### J1d: title renders — later (long intro) (of J1)
- **What the consumer would see:** a correct game with a logo or intro longer than 5 s fails.
- **The case that pins it:** each game has a stated title frame number; the check is that the title is
  reached by that emulated frame. The 5 s is wall time at full speed.

### J1e: key press changes the screen — again (non-reproducible baseline) (of J1)
- **What the consumer would see:** an animated title or RNG makes any two runs differ, so dead input passes.
- **The case that pins it:**
  - inputs are injected at emulated frame numbers;
  - two no-press runs give identical frame hashes at frame N;
  - the press run differs at N.

### J1f: key press changes the screen — for real (button focus, page scroll) (of J1)
- **What the consumer would see:** after a page button (Load, Reset) is clicked it keeps focus, so Enter (Start)
  clicks it again. Arrow keys and Space scroll the page.
- **The case that pins it:**
  - click Reset, then press Enter: the game starts and nothing resets;
  - mapped keys call `preventDefault`, and the page never scrolls.

### J1g: key press changes the screen — for real (stuck keys on blur) (of J1)
- **What the consumer would see:** Alt-Tab while holding a direction loses the keyup, so the button is still
  held on return.
- **The case that pins it:** on window blur every button is released. Hold Right, blur, come back: the
  character stands still.

### J1h: audio not silent — unknown noise floor (DC offset) (of J1)
- **What the consumer would see:** the raw APU mix carries a DC offset, so RMS beats any small floor even in
  silence.
- **The case that pins it:**
  - RMS is measured after a DC-blocking high-pass;
  - the floor is a stated number (−60 dBFS);
  - a game sitting on a silent screen measures below it.

### J1i: audio not silent — empty window (of J1)
- **What the consumer would see:** a game with no sound during the chosen 5 s reads as broken audio.
- **The case that pins it:** the window is aligned to a scripted event known to make sound.
  - adapted: known from the game's own behaviour (music on its title), not from a reference emulator.

### J1j: audio not silent — for real (autoplay policy, file://) (of J1)
- **What the consumer would see:** the AudioContext stays suspended until a user gesture, so a drag-and-drop
  load plays silent. Opened from `file://`, the worklet fails and the game plays silent.
- **The case that pins it:**
  - after a load with no click, sound starts no later than the first key press, and a visible "sound off"
    indicator shows until then;
  - opening from `file://` shows a "serve it with …" message.

### J1k: audio not silent — for real (crackle, sample-rate mismatch) (of J1)
- **What the consumer would see:** RMS on the emulator's stream can't see crackle, or a 44.1 vs 48 kHz
  mismatch.
- **The case that pins it:** measured at the AudioWorklet output, a 440 Hz pulse test tone reads
  440 Hz ± 1 % on both 44.1 kHz and 48 kHz contexts.

### J1l: game reaches its end — unknown end detector (of J1)
- **What the consumer would see:** with no per-game end detector, games are quietly skipped.
- **The case that pins it:**
  - each vendored game has a checked-in end condition (a RAM byte value, or a frame property);
  - a game without one counts as red, not skipped.

### J1m: game reaches its end — for real (wall-clock input drift) (of J1)
- **What the consumer would see:** Playwright key presses land at wall-clock times, so a long script drifts
  and diverges before the end.
- **The case that pins it:** inputs replay through a frame-indexed hook; the same script reaches the same
  end-frame hash on 3 consecutive runs.

### J1n: restart returns to the title — at once (stale audio tail) (of J1)
- **What the consumer would see:** samples queued before a restart play after it, over the title.
- **The case that pins it:** no sample generated before a restart is output after it (generation-tagged
  buffer, flushed on reset).

### J1o: frame pacing — for real (high-refresh display, headless rAF) (of J1)
- **What the consumer would see:** on a 120/144 Hz display, one frame per rAF runs the game 2–2.4× too fast.
  Headless rAF isn't vsync-paced, so it says nothing about real timing.
- **The case that pins it:**
  - pacing comes from a time accumulator or the audio clock, not a count of rAF calls;
  - with rAF stubbed at 144 Hz, the core runs 60.10 emulated fps ± 0.5 %;
  - headed interval numbers on Jason's display are owed (`## Field after merge`).

### J2a: title matches after a switch — again (non-determinism) (of J2)
- **What the consumer would see:** a non-deterministic power-on state, or wall-time screenshots, make A's second
  title differ with no leak, or match by coincidence.
- **The case that pins it:** screenshots are taken at emulated frame N after power-on and compared, index-exact,
  with a fresh page at frame N.

### J2b: title matches after a switch — at once (racing loads) (of J2)
- **What the consumer would see:** picking B, then C within 50 ms; if B's read finishes last, B runs, or both
  load.
- **The case that pins it:** with B's read delayed, C runs, B never starts, and only one run loop exists.

### J2c: silence between games — empty (fast title music) (of J2)
- **What the consumer would see:** a game whose music starts within 0.5 s fails the silence rule though nothing
  leaked.
- **The case that pins it:**
  - replaces the 0.5 s rule: no sample of the previous game appears after the switch instant (generation tag);
  - the new game's first 2 s of audio matches its fresh-page audio, sample for sample.

### J2d: state reset on each load — unknown (incomplete reset list) (of J2)
- **What the consumer would see:** CPU RAM, VRAM, OAM, CHR-RAM, mirroring, IRQ lines, the controller latch,
  held keys or the audio buffer leak across loads.
- **The case that pins it:**
  - behavioural: B loaded after A matches B on a fresh page, frame for frame and sample for sample, over
    10 s;
  - each load builds a fresh core instance.

### J2e: state reset on each load — again (accumulating loops/listeners) (of J2)
- **What the consumer would see:** each load adds listeners, a rAF loop or a worklet node, so the game runs 2×
  fast, input is doubled, or A's sound continues.
- **The case that pins it:**
  - after A → B → A, then 10 more switches and 5 rapid restarts: exactly one loop and one AudioWorkletNode;
  - the listener count is unchanged;
  - the core runs at 60.1 fps.

### J3a: long-session frame rate — later (counter wraparound) (of J3)
- **What the consumer would see:** an int32 cycle counter wraps after about 20 min, and finer counters
  sooner, so timing breaks mid-session.
- **The case that pins it:**
  - with the counters started near 2³¹ and 2³², the CPU, PPU and APU timing test ROMs all pass;
  - a 2-hour emulated run keeps the frame and NMI cadence exact.

### J3b: long-session frame rate — later (headless fast-forward meaning) (of J3)
- **What the consumer would see:** fast-forwarded headless frames are about 3 ms apart, so "holds the J1
  intervals" means nothing.
- **The case that pins it:**
  - headless measures per-frame emulation cost: p99 under budget, with no upward trend across 108,000 frames;
  - only the real-time leg checks intervals.

### J3c: zero underruns — empty (refill after tab return) (of J3)
- **What the consumer would see:** the buffer restarts empty, so the first callbacks underrun and click.
- **The case that pins it:**
  - audio resumes only once the buffer is pre-filled;
  - time paused on purpose isn't counted;
  - underruns across hide/show are 0.

### J3d: zero underruns — for real (real device, main-thread stalls) (of J3)
- **What the consumer would see:** a fake audio device never exercises real timing, and GC pauses starve the
  worklet.
- **The case that pins it:**
  - a 50 ms main-thread busy-loop causes 0 underruns (the worklet buffers ahead);
  - a headed real-device count is owed (`## Field after merge`).

### J3e: audio latency — for real (latency reads 0 headless) (of J3)
- **What the consumer would see:** `outputLatency` reads 0, so "under 100 ms" passes vacuously.
- **The case that pins it:**
  - latency = the worklet buffer fill + `baseLatency` + `outputLatency`, with the buffer-fill part asserted
    headless;
  - a headed measurement is owed (`## Field after merge`).

### J3f: audio latency — later (clock drift) (of J3)
- **What the consumer would see:** a 0.01 % clock mismatch adds about 180 ms per 30 min, which a 2-minute leg
  can't see.
- **The case that pins it:**
  - the rate controller holds buffer fill within ± 1 ms;
  - the latency slope over the leg, projected to 30 min, stays at or under 10 ms.

### J3g: heap steady — for real (imprecise memory API) (of J3)
- **What the consumer would see:** without precise memory info and a forced GC, readings swing more than 10 %.
- **The case that pins it:** Chromium is launched with `--enable-precise-memory-info`, and GC is forced via
  CDP before both readings.

### J3h: heap steady — again (per-load leak) (of J3)
- **What the consumer would see:** a leak that shows only across switches (an old core kept alive by a
  closure).
- **The case that pins it:** with GC forced, the heap after 20 A/B loads is within 10 % of the heap after 2
  loads.

### J3i: tab hide/show — for real (headless visibility) (of J3)
- **What the consumer would see:** headless may not fire `visibilitychange`, so the check passes without
  running the pause path.
- **The case that pins it:**
  - visibility is toggled via CDP;
  - `document.visibilityState` is asserted to change;
  - the core's frame counter stays frozen while hidden.

### J3j: headless speed — unknown (budget undefined) (of J3)
- **What the consumer would see:** 5× real time doesn't mean "minutes", since corpus size and run length
  aren't set.
- **The case that pins it:**
  - the budget is the corpus's wall time on this machine: **≤ 5 min** with parallel workers (accepted at approval);
  - a per-ROM timeout counts as red.

### J4a: split row stable — unknown (stable but wrong) (of J4)
- **What the consumer would see:** a split one scanline off in every frame passes.
- **The case that pins it:**
  - adapted: the expected split row comes from the game's own code or data (the sprite-0 Y, or the IRQ
    latch value the game writes, read from the emulator's bus trace), checked against the hardware rule
    from the spec;
  - plus `sprite_hit_tests` and `mmc3_test` timing.

### J4b: split row stable — later (legitimate scene changes) (of J4)
- **What the consumer would see:** a script crossing a pause menu or a transition moves the split legitimately.
- **The case that pins it:** the 600 frames are a stated gameplay segment, with the expected row per frame
  derived as in J4a.

### J4c: DMC output non-zero — again (DAC holds its level) (of J4)
- **What the consumer would see:** the DAC holds its last level, so output stays non-zero though later
  samples never play.
- **The case that pins it:**
  - during each sampled sound, DMA fetches come from the game's sample address and the DMC output varies
    (AC energy above the floor);
  - no fetches happen in the silence afterwards.

### J4d: covering test ROMs pass — partial ($80/$81, screen-only ROMs) (of J4)
- **What the consumer would see:** reading early, or ignoring `$81`, records the wrong result; screen-only ROMs
  have no machine-readable result.
- **The case that pins it:**
  - the harness checks the `DE B0 61` signature, waits for a status below `$80` with a timeout that counts as
    red, and performs a requested reset after at least 100 ms;
  - screen-only ROMs are judged by their on-screen text (adapted: from the ROM's documented pass screen,
    not a reference emulator).

### J4e: accuracy score — unknown (shrinking denominator) (of J4)
- **What the consumer would see:** a ROM that hangs, crashes the harness or isn't run drops out, and the
  percentage rises.
- **The case that pins it:**
  - the denominator is a fixed vendored list;
  - a hang, timeout or crash counts as a fail.

*From the corpus (the model's fuzz of each seed; the increment named is the one whose author pins it):*

### J1p: press Reset mid-game (of J1)
The same player presses the NES Reset button in the middle of play instead of power-cycling. The game goes
back to its own reset path with RAM kept, as on the console.
- I1 pins the core part: blargg `cpu_reset/registers` and `ram_after_reset`.
- I3 pins the page's Reset button.

### J1q: load a ROM over a game that's running (of J1)
The player picks a second file while the first game is playing. Either the new game starts clean, or (for a
bad file) the old one carries on with an error shown (axes choice).
- Pinned by I3, with J2's switch cases.

### J1r: the first sound after loading with no click (of J1)
The player loads a ROM through the picker and only ever presses keys. Sound must start by the first key
press (J1j).
- Pinned by I4.

### J2f: the same game twice in a row (of J2)
A → A: the second load behaves exactly as a fresh page (frame N matches, index-exact). Reloading the same ROM
is the case most likely to reuse stale state.
- Pinned by I9.

### J2g: switch games mid-note (of J2)
B loads while A holds a long note on the triangle channel. The note must not carry into B.
- Pinned by I4/I9, with J2c's generation tag.

### J3k: the background tab for an hour (of J3)
The page is left hidden for an hour, then shown again. There's no catch-up burst, no audio backlog, and no
memory growth while it was hidden.
- Pinned by I5, with emulated hidden time via CDP.

### J3l: the whole corpus in its time budget (of J3)
The headless corpus, over the full oracle list, finishes in its budget (J3j). Every increment's added ROMs
count against it.
- I1 pins the core speed (≥ 5× real time).
- I5 pins the corpus wall time.

### J4f: a homebrew game that uses unofficial opcodes (of J4)
Some homebrew games (and their sound engines) use unofficial opcodes (LAX, SAX, DCP, …). They run exactly as
the hardware does.
- I1 pins them: nestest lines 5004–8991, and `instr_test-v5`.

### J4g: rereading the controller while DMC samples play (of J4)
A game rereads `$4016` to dodge the DMC DMA's bit-deletion glitch. With DMC samples playing, input still
arrives intact.
- Pinned by I4: blargg `dmc_dma_during_read4`, plus the input path.
## Story map
*Each step names the I1 case that pins it. Where no I1 case does, it says GAP and names the later roadmap
increment whose fresh author pins it. Those GAPs are open by design: an iterative climb cases each increment
when it reaches it (see Readiness).*
- J1: open the page (served locally) → GAP (I3: the page, served locally)
- J1: load a homebrew ROM file → test_nrom128_runs_with_its_16k_bank_mirrored, test_nes2_header_is_read_as_nes2, test_trainer_is_skipped, test_archaic_header_junk_is_ignored
- J1: the game boots to its title screen → GAP (I2: PPU rendering; I3: the canvas)
- J1: start it and play with the keyboard → test_blargg_cpu_rom_reports_passed, test_nestest_official_opcodes_match_the_golden_trace
- J1: hear the music and effects → GAP (I4: the APU and WebAudio)
- J1: play until the game ends → GAP (I9: per-game end conditions; I3 onward: the homebrew games)
- J1: restart from the page without reloading → GAP (I3: the Reset/Power buttons)
- J1: left with a game played start to end, with sound, ready to play again → GAP (I4/I9: the full J1 walk in Playwright)
- J2: load game A and play it to game over → GAP (I9)
- J2: load game B on another mapper without reloading → GAP (I9, after I6–I8 mappers)
- J2: B's graphics, sound and controls work → GAP (I9)
- J2: load A again, and it plays as it did the first time → test_runs_are_deterministic, test_power_on_ram_is_zero_filled
- J2: left with two games played back to back in one page → GAP (I9)
- J3: load a game and play → GAP (I3)
- J3: keep playing for a long session → test_headless_core_runs_at_least_five_times_real_time
- J3: switch tabs away and back → GAP (I5)
- J3: left with a session that felt like a console → GAP (I5)
- J4: load a timing-sensitive game → GAP (I8)
- J4: play a scrolling section with a status bar → GAP (I2: sprite-0 hit; I8: the MMC3 IRQ)
- J4: hear its DMC samples → GAP (I4)
- J4: left with a game that looks and sounds as its author meant → test_nestest_unofficial_opcodes_match_the_golden_trace, test_blargg_cpu_rom_reports_passed
- J1a: an unsupported mapper or non-NES file gives a clear refusal → test_not_an_nes_file_is_refused_with_a_reason, test_unsupported_mapper_is_refused_by_number
- J1a: left with the old game still running → GAP (I3: the page keeps the running game)
- J1b: a truncated file is refused, naming the size mismatch → test_truncated_file_is_refused_naming_the_mismatch
- J1c: the title compared at a fixed frame by palette index → GAP (I2/I3)
- J1d: the title reached by its stated frame → GAP (I3)
- J1e: inputs at frame numbers; the baseline is reproducible → test_runs_are_deterministic
- J1e: a press changes frame N → GAP (I3: controller input)
- J1f: Reset doesn't keep focus; mapped keys don't scroll → GAP (I3)
- J1g: buttons are released on blur → GAP (I3)
- J1h: RMS after a DC block, with a −60 dBFS floor → GAP (I4)
- J1i: the audio window aligned to a known sound → GAP (I4)
- J1j: autoplay policy and file:// → GAP (I4)
- J1k: a 440 Hz tone true at 44.1 and 48 kHz → GAP (I4)
- J1l: every game has an end condition, or it counts red → GAP (I9)
- J1m: frame-indexed input replay is reproducible → GAP (I3/I9)
- J1n: no stale audio after a restart → GAP (I4)
- J1o: pacing by accumulator; 144 Hz rAF still gives 60.1 fps → GAP (I3/I5)
- J1p: Reset mid-game keeps RAM and registers as the console does → cpu_reset/registers, cpu_reset/ram_after_reset
- J1p: the page's Reset button → GAP (I3)
- J1q: loading a ROM over a running game → GAP (I3/I9)
- J1r: the first sound with no click → GAP (I4)
- J2a: screenshots at emulated frame N, index-exact → test_runs_are_deterministic
- J2a: compared across a switch → GAP (I9)
- J2b: racing loads: the last one wins and there's one loop → GAP (I9)
- J2c: no previous-game sample after the switch → GAP (I4/I9)
- J2d: B after A matches a fresh B → GAP (I9)
- J2e: one loop and one worklet after many switches → GAP (I9)
- J2f: A → A behaves as fresh → GAP (I9)
- J2g: switching mid-note → GAP (I4/I9)
- J3a: counters near 2³¹ and 2³² still pass the timing ROMs → GAP (I5)
- J3b: per-frame cost with no upward trend over 108,000 frames → GAP (I5)
- J3c: 0 underruns across hide/show → GAP (I5)
- J3d: a 50 ms main-thread stall causes 0 underruns → GAP (I5)
- J3e: latency includes the buffer fill → GAP (I5)
- J3f: the rate controller holds the buffer fill → GAP (I5)
- J3g: heap measured precisely, after a forced GC → GAP (I5)
- J3h: the heap after 20 loads is within 10 % → GAP (I5/I9)
- J3i: visibility toggled through CDP; the core frozen while hidden → GAP (I5)
- J3j: the core is ≥ 5× real time → test_headless_core_runs_at_least_five_times_real_time
- J3j: the corpus runs within its wall-time budget → GAP (I5)
- J3k: an hour in a background tab → GAP (I5)
- J3l: the core speed bound every increment keeps → test_headless_core_runs_at_least_five_times_real_time
- J3l: the whole corpus within its budget → GAP (I5)
- J4a: the expected split row derived from the game and the spec → GAP (I2/I8)
- J4b: a stated gameplay segment → GAP (I8)
- J4c: DMC fetches and AC energy during samples → GAP (I4)
- J4d: the $6000 protocol is read by the frozen driver, with $81 → reset → test_blargg_cpu_rom_reports_passed, cpu_reset/registers
- J4e: a hang or timeout counts as a fail; the denominator is fixed → test_blargg_cpu_rom_reports_passed
- J4f: unofficial opcodes run as on hardware → test_nestest_unofficial_opcodes_match_the_golden_trace, test_nestest_unofficial_opcodes_take_the_golden_cycles, instr_test-v5/03-immediate, instr_test-v5/07-abs_xy
- J4g: DMC DMA during controller reads → GAP (I4)

## Readiness

### Open for Jason
Each item has my recommendation. "Accept all recommendations" is a complete answer.
1. **Fork J1 step 2, how a ROM is loaded:** a file picker only | a picker plus a built-in menu of the vendored
   legal games. *Recommend:* both, so the reach gradient is playable without hunting for files.
2. **Fork J1 step 4, the key map:** arrows, Z/X (B/A), Enter (Start), Right Shift (Select) | WASD, J/K, Enter,
   Space. *Recommend:* arrows, Z/X, Enter, Right Shift.
3. **Fork J1 step 7, restart:** a soft reset | a power cycle | both, as two buttons. *Recommend:* both.
4. **J2 (drafted), an evening with the collection:** confirm or rewrite. *Recommend:* confirm. It's the
   compound "narrative-like" story you asked for, and it drives the reach gradient.
5. **J3 (drafted), a long sitting stays smooth:** confirm or rewrite. *Recommend:* confirm. It carries your
   performance stories.
6. **J4 (drafted), a game that pushes the hardware:** confirm or rewrite. *Recommend:* confirm. It ties the
   accuracy score to the reach score.
7. **J1 "It holds when" (proposed):** checked per vendored game, in headless Chromium. *Recommend:* accept.
8. **J1, title within 5 s (proposed).** *Recommend:* accept, with J1d's per-game title frame.
9. **J1, audio measured over a 5 s window (proposed).** *Recommend:* accept, aligned to a known sound (J1i),
   floor −60 dBFS (J1h).
10. **J1, frame pacing:** median 16.7 ms ± 1 ms, ≥ 99 % of frames < 20 ms (proposed). *Recommend:* accept, as
    a headed field check after merge. Headless pins only the accumulator pacing (J1o).
11. **J2, 0.5 s of silence between games (proposed).** *Recommend:* replace it with J2c: no sample from the
    previous game after the switch, and the new game's audio matches a fresh page.
12. **J3's numbers (proposed):** 30 min emulated plus 2 min real time; 0 underruns; latency < 100 ms and
    growing ≤ 10 ms; heap within 10 %; ≤ 2 frames of catch-up; audio back within 200 ms. *Recommend:* accept.
13. **J3, headless ≥ 5× real time (proposed)**, and with it J3j's corpus budget of ≤ 5 min. *Recommend:*
    accept. I1 already pins the 5× bound.
14. **J4, the split row the same over 600 frames (proposed).** *Recommend:* accept, with the expected row
    derived from the game's own code and the spec (J4a).
15. **(axes) Q1, the reach corpus:** only redistributable or fetch-allowed homebrew; game over counts as the
    end; Reach counts a game only when boot, play, end and restart all pass. *Recommend:* accept.
16. **(axes) Q2, revision target:** the RP2A03G CPU, RP2C02G PPU and MMC3B/C. Tests that can't both pass stay
    red and are labelled expected-fail; they're never dropped. *Recommend:* accept.
17. **(axes) Q3, saves:** battery RAM survives reset and power cycle in a session, is cleared on a ROM switch,
    and is never written to disk. *Recommend:* accept.
18. **(axes) Q4, PAL:** NTSC only; PAL-flagged ROMs get a notice and are kept out of Reach. *Recommend:*
    accept.
19. **(axes) Q5, the look:** one standard 2C02 palette, all 240 lines, comparisons by palette index.
    *Recommend:* accept.
20. **A word-only pin, `test_not_an_nes_file_is_refused_with_a_reason`:** the message is checked for the word
    "NES" or "header". The behaviour itself (a RomError, nothing runs) is pinned. *Recommend:* accept the
    loose pin. Wording is the builder's.
21. **A word-only pin, `test_truncated_file_is_refused_naming_the_mismatch`:** the message is checked for a
    size word and a digit. *Recommend:* accept.
22. **A word-only pin, `test_unsupported_mapper_is_refused_by_number`:** checked for "mapper" and "3000".
    *Recommend:* accept. The number is the part that matters.
23. **The 56 story GAPs are owed to later increments by design.** Each journey's multi-step case is written
    by the fresh author of the increment that reaches it (the map names which). *Recommend:* approve that
    shape. Casing J1–J4 now would hold I1 red for work I1 doesn't do, and I1's author would be the one
    shaping every later increment.

### Inferred
- **Oracle ROMs are fetched by SHA-256 from a pinned commit, never committed.** They carry no redistribution
  licence, and you asked for no IP risk.
- **blargg's `$6000` protocol is read by the frozen driver (`cases/drive.ts`), not by the build.** The build
  can't grade itself.
- **The CLI trace's first 16 columns follow `nestest.log`.** Its registers, PPU and CYC fields are read by
  pattern, so a disassembly is optional.
- **The test ROMs are blargg's single-test NROM builds, not the multi-test MMC1 builds.** I1 needn't build
  MMC1 to be graded, and each ROM is its own step on the score gradient.
- **The frame budget per ROM is 3,600 frames, with a 180 s wall timeout,** and a timeout counts red.
- **The loader cases use hand-assembled programs that report through the same protocol.** "Accepted" then
  means the program actually ran from the right bytes.
- **The unsupported mapper is NES 2.0 mapper 3000:** an unassigned number, so no later mapper increment turns
  the case wrong.
- **Two invariants are green from the start:** no calling out to another program, and no unlicensed ROM in
  git. They guard every later increment.

### Invariants
- `test_the_core_is_pure_and_has_no_browser_code`: "The core in src/core is pure TypeScript with no DOM." A
  static scan for browser globals. It's evadable in principle; the after-the-fact audit backs it.
- `test_no_emulator_is_vendored_or_depended_on`: "don't vendor one in": no runtime dependencies at all, and no
  emulator, NES or 6502 package by name.
- `test_the_build_never_calls_out_to_another_program`: "don't call another emulator": no child_process,
  exec/spawn, dlopen, native addon or WebAssembly instantiation under src/.
- `test_no_rom_is_committed_without_a_licence_saying_so`: "never commercial ROMs; vendored only after its
  licence is checked": every ROM tracked by git has a LICENSES.md line marking it committed.
- `test_the_typescript_type_checks_under_strict`: the source type-checks under strict mode.
- `test_runs_are_deterministic`: the same ROM from power-on gives the same result, frames, RAM and PRG-RAM.
  This follows from the fixed power-on fill decided at axes.
- `test_headless_core_runs_at_least_five_times_real_time`: 1,800 frames in ≤ 6.0 s on this machine, a bound
  every later increment must keep.

### Not posed
- **Everything past I1 is cased by its own increment's fresh author:** the PPU, the APU, the mappers, the
  browser page, audio, the journeys' Playwright walks, and performance. That includes the story GAPs above.
- **blargg's multi-test MMC1 builds** (`all_instrs`, `official_only`, `instr_timing.nes`) are left out: they
  repeat the singles, and need MMC1 (I7).
- **Left for later increments' authors:** `cpu_dummy_reads` (CNROM; I6); `cpu_dummy_writes` and
  `cpu_exec_space` (PPU/APU; I2/I4); `cpu_interrupts_v2` (I4); and the older screen-only CPU tests
  (`branch_timing_tests`, `cpu_timing_test6`, `blargg_nes_cpu_test5`).
- **No case needs more than verification 3.** I1's cases all run headless in-process (level 5). The headed
  frame-interval and real-audio-device checks are owed under `## Field after merge`.
- **Decimal mode, as such, isn't separately pinned.** The 2A03 has no BCD, and nestest's and instr_test's ADC
  and SBC checksums would catch a build that did BCD.

### Reverse map: every I1 promise meets a consumer
- **The nestest cases (5)** → the CLI's trace, which is how a developer meets the CPU; also J4f (homebrew
  using unofficial opcodes) and J1 step 4 (games execute correctly).
- **The blargg CPU ROMs (24)** → the core API, as the page will use it (J1 step 4), and J4d/J4e (the protocol
  and the fixed denominator); `cpu_reset/*` → J1p.
- **The loader cases (8)** → J1 step 2 (loading a ROM), J1a, J1b; power-on RAM → J2a.
- **The pure core** → J1 step 1: the page runs the same core.
- **No emulator / no calling out / no unlicensed ROM** → Jason and the after-the-fact audit (the honesty and
  legal rules under `## Decided`).
- **Strict tsc** → the per-increment PR reviewers.
- **Determinism** → J2a and J1e.
- **Speed** → J3j and J3l.

None is suspect.

### Field: what you'll do by hand
None during the run (an unattended climb has no field cases). After merge, three checks are owed (`## Field
after merge`):
- you play a homebrew game start to end, with sound;
- headed frame intervals on your display;
- underruns and latency on the real audio device.

## Approved
- **2026-10-29, Jason, at the approve gate: "Approve and freeze it".** He gave no per-item answers. The
  headline said "accept all recommendations is a full answer", so each of the 23 open items takes its
  recommendation, recorded below as his approval of it, not in his words.
  - **Forks:** J1 step 2, a picker plus a built-in menu; step 4, arrows, Z/X, Enter, Right Shift; step 7,
    soft reset and power cycle, as two buttons.
  - **The drafted journeys J2, J3 and J4** are confirmed as written; the (drafted) marks are removed.
  - **The proposed numbers** in J1, J3 and J4 are accepted. J2's 0.5 s silence is replaced by J2c (no sample of
    the previous game after the switch; the new game matches a fresh page).
  - **Axes Q1–Q5** are accepted as proposed: the reach-corpus rules; RP2A03G/RP2C02G/MMC3B-C; saves kept
    within the session only; NTSC only; one palette and all 240 lines, compared by index.
  - **The three loose word pins** on the loader's error messages are accepted.
  - **The journeys' cases come increment by increment:** the 56 story GAPs are owed to the named later
    increments.
  - **Approved with them:** the 9-increment roadmap, and I1's 44-case corpus.
## Gaps
## Provisional
## Field after merge
- **J1, Jason plays a homebrew game himself** in his own browser: it starts, plays, ends and restarts, with
  sound. This is the journey's own field check; it is owed because an unattended climb holds no field cases.
- **J1o: real-display frame intervals.** The median is 16.7 ms ± 1 ms and ≥ 99 % of frames take < 20 ms, in
  a headed browser on Jason's display, including a high-refresh one if he has it. Headless rAF can't show
  this.
- **J3d / J3e: real audio device.** 0 underruns, and latency < 100 ms, on this machine's real output
  (PipeWire/Pulse), headed. Headless has no real device.
## Outside use
- 2026-10-29 · gh · `gh repo create dreddnafious/nes --public` · create this run's repo (Jason's instruction).
- 2026-10-29 · uv/PyPI · installed `pytest`, `playwright`, `pillow`, `numpy` into `.venv` · test tooling (no emulator packages).
- 2026-10-29 · git clone (GitHub) · `https://github.com/christopherpow/nes-test-roms` (depth 1, into gitignored `.oracle-src/`) · inventory of the test-ROM oracles: their readmes, iNES headers and terms; the hashes for `cases/oracles.json`. Test ROMs and their readmes only; no emulator source was opened.
- 2026-10-29 · uv/PyPI · installed `pyyaml` into `.venv` · the corpus reads its case data from YAML.
- 2026-10-29 · HTTP fetch (by the corpus, at test time) · the 26 pinned `raw.githubusercontent.com/christopherpow/nes-test-roms/95d8f62…/…` URLs in `cases/oracles.json` · the oracle ROMs, cached in `roms/cache/`; each is checked by SHA-256.
## Log
- brief: wrote the Intent (Jason's words, every feature named), Decided (language, oracle, harness,
  verification 3, parking, legal and honesty rules, repo and branch), References (the oracles to vendor in
  I1), and a 9-increment Roadmap; made the initial commit, created the public repo, branched `climb/nes`.
- ask: 4 journeys (3 drafted), 3 forks, 8 proposed; verification 3; data known. No questions asked
  (an unattended start): J1 is quoted whole from the design session; J2–J4 are drafted from what Jason asked
  for (narrative compounds, performance); field access none; unlock none.
- axes: 21 outputs, 35 breaks → variants (5 pins adapted away from a reference emulator, which the honesty
  rule forbids; 3 headed checks owed under Field after merge), 4 choices decided, 5 questions open.
- mark_corpus: entering the corpus from the brief (4 journeys, 35 variants, 5 open questions); I1's cases are
  written now, in the design session, before the context clears.
- corpus: I1's 44 cases in `cases/`:
  - 5 nestest cases (official and unofficial, registers and cycles, the result bytes);
  - 24 blargg CPU ROMs (YAML data, read through the frozen `$6000` driver);
  - 8 cartridge-loader cases on spec-built files (NROM-128 mirroring, NES 2.0, trainer, DiskDude, power-on
    RAM, not-NES, truncated, mapper 3000);
  - 7 invariants (a pure core, no emulator dependency, no calling out, no unlicensed ROM in git, strict tsc,
    determinism, ≥ 5× real time).

  Against the empty build: 42 xfailed, 2 passed, 0 broken. Against a right-shaped stub, every red failed at
  its own assertion (4 more passed against the stub: pure core, no emulator dependency, zeroed power-on RAM,
  bench). Variants added: J1p–J1r, J2f–J2g, J3k–J3l, J4f–J4g. Oracle ROMs are fetched by hash, not committed
  (`roms/LICENSES.md`).
- mark_readiness: the corpus holds 44 cases (42 red as strict deltas, 2 green invariants), none broken.
- readiness: the coverage audit shows I1's promises all cased. The story map walks 4 journeys and 44 variants in 71 steps: 15 pinned by I1 cases, and 56 GAPs owed to named later increments by design. The reverse map has no suspect promise. 23 open items for Jason: 3 forks, 3 drafted journeys, 8 proposed numbers, 5 axes questions, 3 word-only message pins, and the shape of the deferred GAPs. No field sessions.
- approve_route: person, because this is the first approval (the design session): Jason approves the journeys, the roadmap and I1 together.
- mark_approve: up for approval: the journeys, the 9-increment roadmap and I1's 44-case corpus; readiness has 23 open items, each with a recommendation.
- approved: Jason approved and froze it at the gate; all 23 open items took their recommendations (no per-item answers given). The forks are settled, J2–J4 confirmed, the proposed parts accepted, axes Q1–Q5 answered.
- set_aside: a fresh subagent wrote 8 held-out cases for I1, under ~/.cache/agentfng/climb/nes/held_out/ (sealed, unread); all 8 fail for want of the build, none broken.
