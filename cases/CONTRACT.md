# The surface the cases drive

The corpus drives the emulator the way its consumers meet it. Each new increment's author may **add** to this
surface in new files, but may never change what an approved case relies on.

## Running things
- **Node 24 runs the TypeScript directly**, using native type stripping (`node file.ts`), with no build step.
  So the source must stay within what type stripping accepts: no `enum`, no `namespace`, no constructor
  parameter properties.
  - Relative imports carry the `.ts` extension.
- **The Python side:** `.venv/bin/python -m pytest cases -p no:cacheprovider -q` (the board's `test_command`).
- **Oracle ROMs** are fetched by hash from `cases/oracles.json` into `roms/cache/`, and are never committed
  (see `roms/LICENSES.md`).

## Consumer 1: the CLI, `node src/cli/nes.ts`
```
node src/cli/nes.ts trace <rom.nes> [--pc <hex>] [--lines <n>] [--mem <hexaddr>:<n>]
```
- **What it does:** powers on with the ROM, then sets PC to `--pc` if given (the CPU's other state is as reset
  leaves it: `P=24 SP=FD`, 7 cycles spent).
- **Its output:** one line per instruction, **before** that instruction executes, for `--lines` instructions.
  - **Columns 0–15 follow `nestest.log` exactly:**
    - the PC: 4 upper-case hex digits at columns 0–3;
    - two spaces;
    - the instruction's bytes, as upper-case hex pairs separated by single spaces and padded with spaces to
      columns 6–15.
  - **After that,** anything (a disassembly is welcome), then the fields
    `A:hh X:hh Y:hh P:hh SP:hh PPU:<scanline>,<dot> CYC:<cpu cycles>`, written the way `nestest.log` writes
    them (spaces after `PPU:` and after the comma are allowed).
- **After the trace,** for each `--mem`, one line: `MEM <addr>: hh hh …` (the bytes as the CPU bus reads them).

## Consumer 2: the core API, `src/core/index.ts`
```ts
export class RomError extends Error {}          // thrown for a file the emulator can't or won't run
export class NES {
  constructor(rom: Uint8Array)                  // power-on; throws RomError (message says why)
  runFrame(): void                              // emulate until the next frame boundary (one NTSC PPU frame)
  peek(addr: number): number                    // CPU-bus read with NO side effects; must work at least for
                                                //   $0000-$07FF (+mirrors), $6000-$7FFF and $8000-$FFFF
  reset(): void                                 // the console's Reset button (a soft reset)
}
```
`cases/drive.ts` is the frozen driver that uses this API. It implements blargg's `$6000` result protocol
outside the build, so the build can't grade itself:
- it checks the signature `DE B0 61` at `$6001`;
- `$80` means running;
- `$81` means press reset after at least 100 ms (6 frames);
- `$00`–`$7F` is the result, with the text at `$6004`.

## The odd ROM files the loader must handle (built inside the cases from the iNES/NES 2.0 spec)
- **Rejected, with a `RomError` saying why:**
  - not an iNES file;
  - truncated against its header;
  - a mapper the build doesn't support (the message names the mapper number).
- **Accepted:**
  - an NES 2.0 header;
  - a 512-byte trainer (skipped);
  - an archaic header with junk in bytes 7–15 ("DiskDude!"), whose mapper's upper nibble is then ignored.
