# The surface I2's cases drive (additive to CONTRACT.md)

Everything in `CONTRACT.md` stays as it is: the `trace` and `run` commands, `new NES(bytes)`, `runFrame()`,
`peek()`, `reset()`, `RomError`, and the frozen `cases/drive.ts`. I2 adds one core method, one CLI command
and one frozen driver, `cases/drive_ppu.ts`.

## Consumer 1: the CLI, `node src/cli/nes.ts`
```
node src/cli/nes.ts screenshot <rom.nes> [--frames <n>] --out <file.png>
```
- **What it does:** powers on with the ROM, runs `--frames` frames (default 60, as `run`), and writes the
  last frame to `--out` as a PNG.
- **The PNG:**
  - 256 × 240: every one of the 240 lines, none cropped (Decided, Q5);
  - 8-bit, non-interlaced, RGB (colour type 2) or RGBA (colour type 6), any filter types;
  - each pixel is the core's frame value (below) mapped through **one** standard 2C02 palette, so the same
    value always gives the same RGB.
- **Errors:** with no `--out`, the command exits non-zero with a message naming `--out` on stderr, never a
  stack trace (as the I1 commands refuse bad options).

## Consumer 2: the core API, `src/core/index.ts`
```ts
export class NES {
  // ... everything in CONTRACT.md, plus:
  frameBuffer(): Uint16Array    // the frame the last runFrame() drew
}
```
- **Its shape:** 256 × 240 = 61,440 values, row-major, the top line first, the left pixel first.
- **Each value:**
  - bits 0–5 are the 2C02 colour index the pixel was output with (0–63), greyscale already applied (with
    `$2001` bit 0 set, the index is ANDed with `$30`);
  - bits 6–8 are the emphasis bits `$2001` bits 5–7, in effect when the pixel was output (bit 6 = `$2001`
    bit 5, and so on);
  - bits 9–15 are 0.

  Tests compare frames by these values, never by RGB, so the palette can't fail a test (Decided, Q5). For
  the browser (I3), the values index a 512-entry RGB table.
- **When it's valid:** after `runFrame()` returns. `runFrame()` stops at the wrap to scanline 0, dot 0
  (Decided, run), so lines 0–239 of the returned frame were all drawn inside that call. The array may be
  the core's own buffer, reused frame to frame; callers copy what they keep.
- **Rendering off:** a pixel output while rendering is disabled is the backdrop (`$3F00`). The cases don't
  pin the "background palette hack" (the colour at `v` when `v` points into `$3F00`–`$3FFF`).
- **Purity:** the core stays free of Node built-ins as well as DOM ones, so it runs in the browser. The PNG
  encoder (zlib) belongs in `src/cli`.

## The frozen driver `cases/drive_ppu.ts`
It runs frames and hands back what the core drew; every judgement is made by the cases. Usage:
`node cases/drive_ppu.ts '<json>'`. It prints one JSON line. The frame comes back as base64 of the 61,440
values, little-endian 16-bit.
- `{"mode": "frames", "rom": p, "frames": N, "hashFrom": k?, "frame": false?, "peek": [[addr, n], ...]?}`:
  - runs N frames;
  - returns the last frame (unless `frame` is false), the SHA-256 of each frame from frame k on, and the
    bytes `peek` reads afterwards.
- `{"mode": "screen", "rom": p, "maxFrames": N, "until": {"addr": a, "seq": [v1, ...]}, "settle": s}`:
  - runs frames until the byte at `a` (read with `peek` once per frame) has shown v1, then v2, …, in
    order;
  - then runs s more frames and returns the last one;
  - `done` is false if the budget runs out first.
- `{"mode": "bench", "rom": p, "frames": N}`: returns the wall time of N frames, and the last frame.
- **Errors:** a core without `frameBuffer()`, or one returning the wrong type or length, gives
  `ok: false, stage: "frame"`; load errors are reported as in `drive.ts`.

## What the cases build themselves (from the spec, in `cases/i2_support.py`)
- **Small NROM programs ("scenes"):** they put a known picture up the way a game does:
  - `$2006`/`$2007` uploads with rendering off;
  - OAM by `$4014` DMA;
  - `$2000`/`$2005`, then `$2001` in vblank;
  - one runs a sprite-0-hit status-bar loop.
- **The expected frame for each scene** comes from the NESdev wiki's PPU pages
  (`reference_frame`). No emulator's output is used anywhere.

## Mappers
- `ppu_read_buffer` is the one I2 oracle that isn't NROM. It's mapper 3 (CNROM): 32 KiB of CHR-ROM in four
  8 KiB banks, chosen by writes to `$8000`–`$FFFF`. The ROM tests that bank switching itself (its tests
  70–72).
- Every other I2 oracle and scene is NROM.
