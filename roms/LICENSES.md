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
| `MK1_NES/examples/05_cheril_the_goddess_v1/dev/cart.nes` | *Cheril the Goddess* (v1), The Mojon Twins, 2015 | 3 (CNROM: 32 KiB PRG, 32 KiB CHR-ROM, horizontal) | engine LGPL-3.0; the README: the example games "are now free to produce, which means you can put them into carts and sell them, as long as you tell us first"; "the game assets ... are donationware". Its title screen says "COPYLEFT 2015 BY THE MOJON TWINS". Use allowed; redistribution of the ROM file isn't stated in so many words | **no: fetched by hash** (`cases/i6_oracles.json`, from `mojontwins/MK1_NES` at commit `ec3fbcc…`, sha256 `e03d52dd214a998997c003593d2d5921fd9679cea02a385d789f8e14d037ba16`) |
| `240pee.nes` (v0.23) | *240p Test Suite* for NES, Damian Yerrick (PinoBatch), after Artemio Urbina's 240p Test Suite | 2 (UNROM: 64 KiB PRG, 8 KiB CHR-RAM, vertical, NES 2.0) | GPL-2.0-or-later (README: "free software under the GNU General Public License, version 2 or later"; the GitHub repo's licence is GPL-2.0) | **no: fetched by hash** (`cases/i6_oracles.json`, the `v0.23` release asset of `pinobatch/240p-test-mini`, sha256 `04f01d7372f66ea2befe325b9bd655a9fcc395a31fa46d9466286bb8f9d2e62e`); Decided: oracle ROMs are fetched by hash. A test utility, not a game: it boots and plays but has no end, so it isn't counted in Reach |
| `nova-the-squirrel/v1.0.6a/nova.nes` | *Nova the Squirrel* v1.0.6a, NovaSquirrel, 2014-2019 | 1 (MMC1, SXROM-style: 256 KiB PRG, 8 KiB CHR-RAM, 32 KiB battery-backed PRG-RAM, vertical, NES 2.0) | code GPL-3.0-or-later (repo `LICENSE.txt`; README "All code is available under the GPL license version 3 or later"); assets CC BY-NC-SA 4.0 (README: "the game may not be sold without permission ... Permission is not granted to use the character and design of Nova in anything else"). Redistribution allowed (non-commercial, attributed) | **no: fetched by hash** (`cases/i7_oracles.json`, the `v1.0.6a` release asset of `NovaSquirrel/NovaTheSquirrel`, sha256 `e4780e90b9d1587489bfb797d2ca395be21371ea9262fa9f87f99324ec6960ab`); Decided: oracle ROMs are fetched by hash. The README says the game "takes inspiration from" commercial games and was made "without copyright infringement" |
| `minekart-madness/v1.0/minekart_madness_mmc1.nes` | *Minekart Madness* v1.0 (MMC1 build), Matt Hughson (design, code), Haller Zoltan (art), TUI (music, sfx), 2022; neslib/famitone by Shiru | 1 (MMC1, SKROM: 128 KiB PRG, 128 KiB CHR-ROM, 8 KiB battery PRG-RAM, vertical, NES 2.0) | MIT (the `mhughson/mbh-cart` repository's `LICENSE`, "Copyright (c) 2022 Matthew Hughson"); the release notes: "an MMC1 Mapper version of the game, which is compatible with every emulator". The art and music are credited to others on its own credits screen; the MIT grant is the repository owner's | **no: fetched by hash** (`cases/i7_oracles.json`, the `v1.0` release asset of `mhughson/mbh-cart`, sha256 `45d18d8b3662e6c08043759e550e658bcd23e48394a00f24de5e5fbe57e4900a`): the contributors' own terms for the art and music aren't stated apart from the repository's licence |

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

## APU test ROMs (I4's oracles): fetched by hash, not committed
| Suite | Author | Checks |
|---|---|---|
| `apu_test` (8 singles) | Shay Green (blargg) | Length counters and table, the frame IRQ flag and its timing, clock jitter, DMC basics and rates. |
| `blargg_apu_2005.07.30` (11) | Shay Green (blargg) | The frame counter's and length counters' exact timing; reports on screen only. |
| `cpu_interrupts_v2` (5 singles) | Shay Green (blargg) | CLI/SEI/PLP/RTI latency, NMI against BRK and IRQ, IRQ around DMA, branch delays; uses the APU's frame IRQ. |
| `apu_reset` (6) | Shay Green (blargg) | The APU's state at power and after Reset. |
| `dmc_dma_during_read4` (5) | Shay Green (blargg) | DMC DMA landing on `$2007` and `$4016` reads and writes; reports on screen only. |
| `sprdma_and_dmc_dma` (2) | Shay Green (blargg) | DMC DMA landing during sprite DMA. |

Same source and terms as the test ROMs above (no licence granting redistribution): fetched by SHA-256 from
`cases/i4_oracles.json` into `roms/cache/`. Left out, and why: `apu_mixer` (judged by ear), `dmc_tests` (no
readme or source, no machine-readable result), `pal_apu_tests` (PAL; Decided Q4 is NTSC only).

## Mapper test ROMs (I6's oracles): fetched by hash, not committed
| Suite | Author | Checks |
|---|---|---|
| Holy Mapperel v0.02 (`M2_P128K_CR8K_V`, `M3_P32K_C32K_H`, `M7_P128K_CR8K`) | Damian Yerrick (PinoBatch), 2013-2017 | Detects the mapper by how the board answers mirroring and bank writes, measures PRG and CHR by bank tags, tests every bank and CHR-RAM byte, and shows the result on screen. |

- **Source:** the release asset `holy-mapperel-bin-0.02.7z` of `github.com/pinobatch/holy-mapperel` (tag
  `v0.02`), sha256 `70f85671e21f293599baebb662faeb06a4c04e9c9ceb283d96d4197f09e4ce7a`. Each ROM is a pinned
  slice of one of the archive's raw LZMA2 blocks, checked by its own SHA-256 (`cases/i6_oracles.json`).
- **Terms:** the zlib licence (the repo's `LICENSE` and README "Legal": "Available under zlib License";
  "Permission is granted to anyone to use this software for any purpose ... and to alter it and
  redistribute it freely"). Redistribution is allowed, but under Decided ("Oracle ROMs are fetched by hash,
  never committed") the ROMs are fetched, not vendored. Its README notes it is "Not sponsored or endorsed by
  DC Comics, IREM, Nintendo, or Sunsoft" (it was renamed from "Holy Diver Batman" to avoid those marks).
- **Left out, and why:** the collection's other mapper-2/3/7 files (`other/PCM.demo.wgraphics.nes`,
  `other/oam3.nes`, `other/quantum_disco_brothers_by_wAMMA.nes`, `other/CMC80s.NES`, `other/TANESPOT.NES`)
  carry no author's terms that could be checked; the collection's `240pee/240pee.nes` is an older build of
  the same 240p Test Suite, replaced by the author's own tagged release.

## MMC1 test ROMs (I7's oracles): fetched by hash, not committed
| Suite | Author | Checks |
|---|---|---|
| Holy Mapperel v0.02, SxROM builds (`M1_P128K_C128K`, `_C128K_S8K`, `_C128K_W8K`, `_C32K`, `_C32K_S8K`, `_C32K_W8K`, `_CR8K`, `M1_P512K_CR8K_S8K`, `_S32K`) | Damian Yerrick (PinoBatch), 2013-2017 | Detects the MMC1 board from its NES 2.0 sizes and behaviour, reads back every PRG and CHR bank's tag and every PRG-RAM byte, and shows the result on screen. |
| Multi-test builds: `instr_test-v5` (`all_instrs`, `official_only`), `instr_timing`, `instr_misc`, `cpu_interrupts_v2`, `ppu_vbl_nmi`, `apu_test` | Shay Green (blargg) | The same tests as the single-test builds the corpus runs on NROM, packed one per PRG bank of an MMC1 cartridge; report through `$6000`. |

- **Source and terms:** Holy Mapperel as for I6 (the pinned `holy-mapperel-bin-0.02.7z` release asset,
  zlib licence; each ROM a pinned slice of one of its LZMA2 blocks). The blargg builds from
  `christopherpow/nes-test-roms` at commit `95d8f62…`, with the same terms as the test ROMs above (no
  licence granting redistribution). All pinned by SHA-256 in `cases/i7_oracles.json`.
- **Left out, and why:** `MMC1_A12/mmc1_a12.nes` (Bregalad; no readme or stated terms, judged by eye, and
  it checks SNROM's PRG-RAM disable, which the wiki says is "not strictly required"); `blargg_nes_cpu_test5`
  (screen-only, and its tests are covered by `instr_test-v5`); the collection's other mapper-1 files
  (`other/8bitpeoples_-_deadline_console_invitro.nes`, `other/midscanline.nes`, `other/pulsar.nes`,
  `blargg_litewall`, `scrolltest/scroll.nes`): demos whose authors' terms couldn't be checked.
- **Homebrew looked at and left out:** *Lizard* (Brad Smith; source CC BY 4.0, but its assets "may not be
  reused or redistributed" and the ROM is only on itch.io), *Raven's Gate* (MMC1 build, a commercial
  pay-what-you-want release), and PinoBatch's *Thwaite*, *Concentration Room* and *RHDE* and
  NovaSquirrel's *DABG* (their release headers say mapper 0, NROM).

## MMC3 test ROMs and homebrew (I8's oracles): fetched by hash, not committed
| Suite / file | Author | Mapper | Terms | Checks |
|---|---|---|---|---|
| `mmc3_test_2` singles (`1-clocking`, `2-details`, `3-A12_clocking`, `4-scanline_timing`, `5-MMC3`, `6-MMC3_alt`) | Shay Green (blargg) | 4 (iNES 1.0: 32 KiB PRG, 8 KiB CHR-ROM) | the collection's: no licence granting redistribution | The MMC3's IRQ counter, clocked by hand and to the PPU clock; `6-MMC3_alt` tests the other revision: red in the raw count (Decided Q2), pinned by failing at its sub-test 2. |
| Holy Mapperel v0.02, MMC3 builds (`M4_P128K_CR8K`, `M4_P128K_CR32K`, `M4_P256K_C256K`) and `M118_P128K_C64K` (for a refusal) | Damian Yerrick (PinoBatch), 2013-2017 | 4 (NES 2.0), 118 | zlib ("redistribute it freely") | Detects the board, reads back every PRG and CHR bank's tag through each window, checks the IRQ, shows the result on screen. |
| `nesdoug-33_MMC3/d03805b/hello_mmc3.nes` | Doug Fraker (nesdoug), 2019-2023; neslib by Shiru; banked_call by Christopher Parker (cppchriscpp, MIT, per its `MMC3/Credit.txt`) | 4 (iNES 1.0: 128 KiB PRG, 128 KiB CHR-ROM, 8 KiB PRG-RAM) | MIT (the `nesdoug/33_MMC3` repository's `License.txt`, "Copyright (c) 2019 Doug Fraker") | A demo, not a game: four IRQ scroll bands, PRG banking through R6, PRG-RAM, CHR bank animation. Not counted in Reach (no end). |

- **Source and terms:** `mmc3_test_2` from `christopherpow/nes-test-roms` at commit `95d8f62…`, with the
  same terms as the test ROMs above. Holy Mapperel as for I6 and I7 (the pinned `holy-mapperel-bin-0.02.7z`
  release asset; each ROM a pinned slice of its second LZMA2 block). `hello_mmc3.nes` is the build the
  author committed in his repository (`BUILD/hello_mmc3.nes` at commit `d03805b13ed0d4d696744a8f6ad578e9882fc0eb`);
  MIT allows redistribution, but under Decided ("Oracle ROMs are fetched by hash, never committed") it is
  fetched. Its graphics (`Alpha.chr`, `Gears.chr`) and music come with the repository under the same
  licence; nothing in it names another's trademark. All pinned by SHA-256 in `cases/i8_oracles.json`.
- **Left out, and why:** `mmc3_test` (the older build of the same tests: its singles are subsets of
  `mmc3_test_2`'s, and its `6-MMC6` tests the same other-revision behaviour as `6-MMC3_alt`) and
  `mmc3_irq_tests` (an older, screen-only build of the same tests on a devcart runtime); the collection's
  other mapper-4 files (`other/BladeBuster.nes`, `other/DROPOFF7.NES`, `other/high-hopes.nes`,
  `other/max-300.nes`, `other/rastesam4.nes`, `stomper/smwstomp.nes`): no author's terms that could be
  checked (and `smwstomp` names a commercial game).
- **Homebrew looked at and left out:** *NES Quest* (`cfrantz/ld43`, Ludum Dare 43; an MMC3 IRQ status bar,
  but the repository has no licence), *Annalog* (`mdsteele/annalog`, GPL-3.0 code, but sold as a cartridge
  with no direct ROM download), *Famidash* (`tfdsoft/famidash`, MIT, but a clone of a commercial game under
  its name: no clone trading on another's trademark).
