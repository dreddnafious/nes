# Brief: a playable NES emulator, built by an iterative climb

Written 2026-10-29 from the design session with Jason (and the agentFNG session's two briefings).
This is the input to `/climb`; the board it writes is the run's record.

## What this run is for
- **What's being tested is the scaffold, not the model.** The scaffold is the iterative fork of climb in
  `.agentfng/skills/climb`. The question: can the model plus this scaffold stay true for hours, unattended,
  against an outside oracle that is beyond doubt?
- **The model gets latitude; the scaffold supplies executive function over a long run.** "The skill is a
  scaffold, not a shackle." Size, scope, order and number of increments are the model's call.
- **The artifact should be worth having:** a playable emulator.
- **Winning means rigour and owning the result.** In Jason's words: "All I can do is ask that you don't
  prevaricate or confabulate … We're trying to do some science here … winning means rigor and owning the
  result." Report what happened, including failures, plainly. A red is reported as red, a guess as a guess,
  and a plateau with its real cause.

## Intent
A person opens a web page, loads a legal homebrew NES ROM, and plays it with the keyboard, with sound. The
game starts, can be played through to its end, and can be restarted without reloading the page or doing
anything unusual. "It's the faculties required by the homebrew games": whatever CPU, PPU (graphics), APU
(audio), controller and mapper (cartridge hardware) features the chosen homebrew games need are in scope.
**Audio is part of the goal, not a stretch.**

Features in scope:
- **CPU (6502/2A03):** every official opcode, plus the unofficial ones that test ROMs and games use, with
  cycle timing and interrupts (NMI, IRQ, BRK).
- **PPU:** background, sprites, scrolling, sprite-0 hit, sprite overflow, vblank and NMI timing, palettes,
  and mirroring.
- **APU:** both pulse channels, triangle, noise and DMC, the frame counter and its IRQ, and audio output in
  the browser.
- **Input:** a standard controller mapped to the keyboard.
- **Mappers:** as many as the chosen homebrew games need (NROM first, then the common ones: UxROM, CNROM,
  MMC1, MMC3, …).
- **The front end:** load a ROM from a file, play, reset or restart, and run at full speed (60 fps).

**The gradient: how many homebrew games run.** Jason: "the width of the Homebrew games that run are a useful
gradient." The test-ROM score measures accuracy; the number of vendored homebrew games that boot, play, end
and restart measures reach. Both are reported at each checkpoint.

## Journey J1: play a homebrew game in the browser
Jason's words, quoted whole (dictated):
> "yeah we open a page load of ROM play with the keyboard. I don't have a gamepad and hear the sound. What
> right means is that you can actually play a game meaning the game starts. You can play it. It ends. You can
> restart it without having to reload the browser or do anything unusual. It includes audio. It's the
> faculties required by the Homebrew games."

- **Consumer:** a person at a desktop browser, with a keyboard and no gamepad.
- **What doesn't work now:** nothing exists.
- **Steps:**
  1. Open the page.
  2. Load a ROM file.
  3. The game boots to its title screen.
  4. Start it and play with the keyboard.
  5. Hear its music and effects.
  6. Play to the game's end (game over or a win).
  7. Restart it in the page, with no reload.
- *Proposed:* "It holds when" conditions, per vendored homebrew game, checked in headless Chromium through
  Playwright:
  - the title screen renders, so the canvas isn't blank or frozen;
  - scripted key presses change what's on screen;
  - the audio output is not silent;
  - a scripted session reaches the game's end state;
  - restarting brings back the title screen with no page reload;
  - frame pacing holds at 60 fps.
- **Owed after merge:** Jason plays one himself (a field case, since an unattended climb holds none).

## Stories: many, varied, and some of them narrative
Jason: "you want a large variety of user stories that you'll satisfy over the cycles and obviously some of
those will be performance related. Also, try to have a few compound user stories, not test-likes but
narrative-likes."
- **The corpus should hold a wide spread of stories across the increments, not just a list of test-ROM
  checks:**
  - correctness (the test ROMs);
  - reach (homebrew games);
  - the front end (loading, reset, keyboard);
  - audio;
  - **performance** (frame pacing, audio continuity, startup time, long sessions).
- **A few compound, narrative journeys besides J1:** each is a whole session told start to finish, whose steps
  compose, so a case can't satisfy it piecemeal. The drafts below are the model's, marked *(drafted)*, for
  Jason to confirm or rewrite at approval.

### J2 (drafted): an evening with the collection
I open the page and load a game. I play it until I lose, then load a *different* game (on a different
mapper) from the same page, with no reload. I play that for a while. Then I go back to the first game and
restart it. Through all of it:
- the picture and sound are right for whichever game is loaded;
- nothing from the previous game leaks into the next (palette, audio channels, mapper state, save RAM);
- the page never needs a refresh.

### J3 (drafted): it stays smooth for a long sitting
I play one game for a long session (*proposed:* 30 minutes of emulated time in the check). Then I switch to
another tab and come back. Through all of it:
- the frame rate holds at 60 fps;
- the audio doesn't crackle, drift out of sync or build up lag;
- memory doesn't grow without bound.

When I come back from the other tab, the game picks up where it was: it doesn't race to catch up, and the
sound doesn't come back delayed.

### J4 (drafted): a game that pushes the hardware
I load a homebrew game known to lean on precise timing: a status bar split with sprite-0 hit or mapper IRQs,
mid-frame scrolling, sample (DMC) audio. It looks and sounds as its author intended:
- no jittering split line;
- no torn scroll;
- no missing drums.

When it doesn't, the cause traces to a test ROM that also fails, so the accuracy score and the reach score
agree.

## Decided
- **Language: TypeScript, chosen on its merits** (Rust was offered and declined).
  - **Speed:** the NES's workload (about 1.8M CPU cycles and 5.4M PPU dots a second) is well inside what
    V8's JIT does with typed arrays. JavaScript NES emulators have run at full speed in browsers for over a
    decade, so playing at 60 fps leaves plenty of headroom.
  - **Toolchain:** one language and one toolchain from the core to the page. Reviewers read a single
    language.
  - **The cost:** the hot loop has to be written with care: typed arrays, and no per-cycle allocation or
    closures.
  - **Revisit if** the per-dot PPU can't reach 60 fps in headless Chromium after profiling.
  - **The design:** the core is pure TypeScript with no browser code. It runs headless under Node for the
    tests, and in a browser page (canvas, WebAudio, keyboard) for play. The presentation layer is browser
    whatever the backend.
- **"Accurate" = passes the test ROMs.** Frame-identical to a reference emulator would mean running another
  emulator, which the honesty rule forbids.
- **The cases are pytest** (climb requires it). They drive the build through a small Node CLI (run a ROM for N
  frames or until a result, write a CPU trace, read memory, write a PNG screenshot, write audio samples), and
  through Playwright for the browser journey.
  - Use the project's own venv and interpreter explicitly. In this harness, bare `pytest` may resolve to
    another project's shim.
- **Verification level: 3, a smoke run on a local deploy.** The page is served locally on a free port and
  driven in headless Chromium (Python Playwright and its Chromium are installed). There is no level above
  this to unlock: no real hardware.
- **Mode:**
  - the design session is attended;
  - Jason approves the journeys, the roadmap and increment 1's corpus together;
  - everything after that is unattended.
- **Repo:** the model creates it with `gh`, **public**, under `dreddnafious`, after an initial commit on
  `main`. Each increment ships its own PR, stacked on the last.
- **Stopping:** there is no time budget. Jason: "continue until satisfied that you're done or I stop you or
  despite your best effort, you can't work on anything else because everything that you would be normally
  working on is stuck." A stuck increment is parked, not the end (see the settled fork below).
- **Mid-run forks:** the model decides them and writes `(run)` lines under `## Decided`. It never edits the
  Intent, the journey or the roadmap; it proposes changes instead.

## Legal: ROMs and IP
Jason: "we only want to do legal games … We absolutely don't want to accidentally break any trademarks or IP
licenses."
- **No commercial ROMs, ever.** Test ROMs and homebrew games are vendored only after checking their licence
  or the author's stated terms. The licence and its source go in `roms/LICENSES.md`, next to each file.
- A homebrew game whose terms don't plainly allow redistribution is not vendored. Neither is one that trades
  on someone else's trademark (for example, a clone of a trademarked game).
  - If a game's terms allow use but not redistribution, the tests may fetch it at test time from its
    official source instead, and that fetch is recorded.
- **Candidates to research** (from memory, **unverified**; licences must be checked first):
  - Damian Yerrick's (PinoBatch) open-source games and tools, e.g. *Concentration Room*, *Thwaite*,
    *robotfindskitten*, and the *240p Test Suite*;
  - Shiru's freely released games (e.g. *Alter Ego*, *Lawn Mower*);
  - NovaSquirrel's *Nova the Squirrel*;
  - sgadrat's *Super Tilt Bro.*

  Research may add or drop any of them. Prefer a spread across mappers, since that spread is the gradient.

## Honesty and outside use
- **Don't copy existing emulator source, don't call another emulator, don't vendor one in.** Knowing how the
  NES and emulators work is fine; copying source text isn't.
- **Record every web search, fetch and GitHub use** (beyond our own repo's routine pushes and PRs) under
  `## Outside use` on the board, a line each: when, the tool, the query or URL, and why. Nothing is blocked.
  - An audit afterwards (a similarity scan of the code plus a comparison of tool calls against the log)
    informs Jason's conclusions; it gates nothing.
  - No outside use happened during the design session itself (local tooling checks only).

## References (to verify and vendor in increment 1, licences first)
- **`nestest.nes` + `nestest.log`** (kevtris): a golden CPU trace, line by line, with registers and cycle
  counts. It covers the official and unofficial opcodes. This is the strongest oracle.
- **blargg's test ROMs:**
  - CPU: `instr_test-v5`, `instr_timing`, `cpu_interrupts_v2`, `instr_misc`, `cpu_dummy_reads`;
  - PPU: `ppu_vbl_nmi`, `sprite_hit_tests`, `sprite_overflow_tests`, `ppu_open_bus`;
  - APU: `apu_test`, `blargg_apu_2005`, `dmc_tests`.

  Most report their result at `$6000` (status byte, signature `DE B0 61`, and a text message) as well as on
  screen.
- **Mapper tests:** e.g. `mmc3_test_2`, plus others found in research.
- **NESdev wiki** (nesdev.org/wiki): the spec reference for the CPU, PPU, APU, mappers and timing.
- **Prior art for comparison only (don't read its source):** nightowl-nes, an autonomous Claude Code loop.
  Over 8 days and 68 PRs it passed the full nestest (8,991 lines), had 216 of 256 opcodes, and tested 66 of
  263 test ROMs.

## Draft roadmap
The model owns the roadmap and may reshape it at the brief. Each increment is one stacked PR, small enough for
two fresh reviewers plus Copilot to genuinely review.
1. **Rig + CPU.** Scaffold the repo, the vendored oracles with licences, and the Node CLI and score. The CPU
   passes `nestest.log` line for line, then blargg's CPU suites.
2. **PPU.** Rendering and timing, checked by the blargg PPU suites. Checkpoints start posting screenshots.
3. **First playable.** The browser front end (canvas, keyboard, ROM loading, reset), and an NROM homebrew game
   booting and responding.
4. **APU.** blargg's APU suites, plus audio in the browser. J1 is complete for NROM games.
5. **Mappers, widening the gradient.** One mapper family per increment, each unlocking more vendored homebrew
   games, until the candidates all run or the remaining work is stuck.

Playable comes before broad coverage, so a run that stops early still leaves something to play.

## Run rules
- **Checkpoints:** at each increment boundary, post inline in the transcript:
  - the score (cases green / total, and homebrew games running / vendored);
  - a small score chart (a self-contained `.html` card, or a PNG);
  - once there's a PPU, a screenshot of the emulator's screen.

  Files must live inside the project.
- **Context:** use the scaffold's `clear` and refocus at increment borders, so the run stays ONE followable
  transcript. Don't spawn separate long-lived processes for the main line of work.
- **Plateau:** a fresh researcher, then the "What did you try" list and a fresh judge, as the fork defines.
  Each research fetch is logged under `## Outside use`, and the Log says whether it helped.
- **Hazards:**
  - **Never restart or deploy agentFNG while the run is live:** it kills this session.
  - Use the explicit venv interpreter for pytest.
  - Use free ports only.
- **At the end:** report against the success criteria:
  1. stayed on track unrescued;
  2. the score climbed, or plateaued for a stated reason;
  3. honesty is known;
  4. the trail is legible without the transcript;
  5. research happened at plateaus, and whether it helped.

  Report it honestly, with what failed.

## Settled fork: a stuck increment is parked, and the run goes on
- **Jason chose (b):** "if you get stuck on a mapper find something else useful to do."
- **"Useful" is bounded:** "it should apply meaningfully to the capabilities of the end artifact."
  - That means another roadmap item, or work that widens or deepens what the emulator can play.
  - It does not mean polish for its own sake, or work on the scaffold.
- **As built, an earned exit ends the run, so the fork needs a change, made before the run starts.** When an
  increment's exit is earned, it is **parked**:
  - its work opens as a draft PR, and its "What did you try" list stays on the board;
  - the run moves on to the next independent roadmap item;
  - the run ends only when no workable item remains, or the roadmap is done, or Jason stops it.
- **The counting must keep a parked increment's red cases out of "green" without hiding them.** They are
  reported as parked and listed at the merge gate.
- **What's left on the record:**
  - each parking writes a `(run)` line under `## Decided`;
  - the final report counts parked increments separately from shipped ones.
