# ROMs: sources and terms

**Rule:** no commercial ROM, ever. A ROM is committed to this repository only when its licence, or its
author's stated terms, allow redistribution. Anything else is **fetched by hash** at test time from its
public source into `roms/cache/`, which is gitignored and never committed.

## Test ROMs (oracles): fetched by hash, not committed
- **Source:** `christopherpow/nes-test-roms` at commit `95d8f621ae55cee0d09b91519a8989ae0e64753b`. This is
  a community collection of the NES test ROMs posted publicly by their authors (mostly on the NESdev forums).
  The pinned list, with SHA-256 for each file, is in `cases/oracles.json`.
- **Terms:** the collection has no licence file. The ROMs' own readmes (for example blargg's
  `instr_test-v5/readme.txt`) state no licence. They are published for emulator authors to test against,
  but nothing grants redistribution, so this repo **doesn't redistribute them**: it fetches them.

| Suite | Author | Checks |
|---|---|---|
| `nestest.nes` + `nestest.log` | Kevin Horton (kevtris) | The CPU: a golden trace of 8,991 instructions with registers and cycles. |
| `instr_test-v5` (16 singles) | Shay Green (blargg) | Every opcode's effects, official and unofficial. |
| `instr_timing` (2 singles) | Shay Green (blargg) | Instruction and branch cycle counts. |
| `instr_misc` (4 singles) | Shay Green (blargg) | Address wrapping, and dummy reads. |
| `cpu_reset` (2) | Shay Green (blargg) | Register and RAM state after the Reset button. |

Later increments add their suites here, with the same treatment.

## Homebrew games
Each one is researched before use: its licence, its author's terms, and its mapper. Each gets
a row here, saying whether it's committed (a licence that allows redistribution) or fetched by hash (use
allowed, redistribution not stated). A game whose terms are unclear isn't used.

| File | Game, author | Mapper | Licence | In git? |
|---|---|---|---|---|
| `nes15-1.0.0/nes15-NTSC.nes` | *nes15* 1.0.0 (the Fifteen Puzzle), Mathew Brenaman, 2011 | 0 (NROM-128, vertical mirroring) | BSD 2-clause ("Redistribution and use in source and binary forms ... are permitted") | **no: fetched by hash** (`cases/i3_oracles.json`, sha256 `01b0507abdee44e6b78edcbe95f1de951c9d7ec39e7fcc5306f5e06b732ef92c`) |
| `nes15-1.0.0/gfx/title.nam`, `bgd.pal`, `spr.pal` | the title screen's name table and the two palettes, from the game's published source | | the same BSD licence | no: fetched by hash; the I3 cases build the title's expected picture from them |

- **nes15's source:** the copy in `christopherpow/nes-test-roms` at commit `95d8f62…` (the directory
  `nes15-1.0.0/`, which carries the author's own `LICENSE`, `README` and source). The README states "This
  program is free software. It is distributed under the terms of a simple BSD-style license (OSI-approved)"
  and "This product is not sponsored or endorsed by Nintendo." Its music is J.S. Bach's BWV 558 (public
  domain).
- **Why it isn't committed, though BSD allows it:** its README says the "Ghost" font "seems to actually be
  the font used in the Commodore 64 version of 'Ghosts and Goblins'", a commercial game. The author's licence
  may not cover a typeface taken from another's game, so under this repo's rule (no IP risk) the game is
  used but not redistributed: fetched by hash from its public source, like the test ROMs.

## Controller test ROMs (oracles): fetched by hash, not committed
| Suite | Author | Checks |
|---|---|---|
| `read_joy3/test_buttons` | Shay Green (blargg) | Asks for A, B, Select, Start, Up, Down, Left, Right in turn; fails if any other bit is read. |

Same source and terms as the test ROMs above (no licence granting redistribution): fetched by SHA-256 from
`cases/i3_oracles.json` into `roms/cache/`.
