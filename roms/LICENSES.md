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
Not chosen yet. Each one is researched before use: its licence, its author's terms, and its mapper. Each gets
a row here, saying whether it's committed (a licence that allows redistribution) or fetched by hash (use
allowed, redistribution not stated). A game whose terms are unclear isn't used.
