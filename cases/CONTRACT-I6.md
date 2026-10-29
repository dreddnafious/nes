# The surface I6's cases drive (additive to CONTRACT.md, CONTRACT-I2.md, CONTRACT-I3.md and CONTRACT-I4.md)

I6 adds **no new method, command or page element**. Everything in the earlier contracts stays as it is: the
CLI's `trace`, `run`, `screenshot` and `audio`; the core API (`new NES(bytes)`, `runFrame()`, `peek()`,
`reset()`, `frameBuffer()`, `setButtons()`, `setSampleRate()`, `audioSamples()`, `RomError`); the frozen
drivers; the page and its `window.nesTest` hooks. What changes is which files those surfaces accept: iNES
mappers **2 (UxROM), 3 (CNROM) and 7 (AxROM)** load and run, on every one of them. One new frozen driver,
`cases/drive_i6.ts`, strings the existing calls together.

## The boards (NESdev wiki: "UxROM", "CNROM", "AxROM", "Mirroring", "INES", "NES 2.0 submappers")
- **UxROM (2):** CPU `$8000`-`$BFFF` is the 16 KiB PRG bank last written to `$8000`-`$FFFF`; `$C000`-`$FFFF`
  is fixed to the last bank. PRG sizes: 64, 128, 256 KiB (iNES 1.0 or NES 2.0), and up to 4 MiB (256
  banks, NES 2.0 only: "To make use of all 8-bits for a 4 MB PRG ROM, an NES 2.0 header must be used").
  CHR: 8 KiB, unbanked; CHR-RAM when the header gives no CHR-ROM, CHR-ROM (read-only) when it does.
  Mirroring fixed by header byte 6 bit 0.
- **CNROM (3):** PRG fixed (16 KiB mirrored at `$8000` and `$C000`, or 32 KiB); PPU `$0000`-`$1FFF` is the
  8 KiB CHR-ROM bank last written to `$8000`-`$FFFF`. CHR sizes: 16 and 32 KiB. Mirroring fixed by header.
- **AxROM (7):** CPU `$8000`-`$FFFF` is the 32 KiB bank in register bits 0-2; bit 4 selects the 1 KiB VRAM
  page all four nametables show (single-screen), whatever header byte 6 bit 0 says. PRG sizes 64, 128,
  256 KiB. CHR: 8 KiB, as for UxROM.
- **The register** answers a write anywhere in `$8000`-`$FFFF`, and only there. A bank number at or past
  the bank count wraps modulo the count (the cases use power-of-two counts only).
- **Bus conflicts, by NES 2.0 submapper:** submapper 1, none (the written value is latched); submapper 2,
  AND (the written value ANDed with the ROM byte at the address). Mapper 7 with submapper 0 or an iNES 1.0
  header: none (the wiki: "emulators shouldn't emulate bus conflicts"). **Not pinned:** mappers 2 and 3
  with submapper 0 or iNES 1.0, where the wiki says "Bus conflict behavior unknown": the cases write
  there only values equal to the ROM byte they land on, as games do.
- **Not pinned (unknown on hardware):** any bank register and AxROM's page at power-on, and after Reset;
  CHR-RAM and nametable RAM at power-on. The cases only ask that power-on be the same every time
  (Decided: runs are deterministic) and that the window then shows one bank's bytes.
- **Refused, as before:** any other mapper, with a `RomError` naming its whole number (18, 35, 39, 180,
  185, 258, 259 and 263 are tried: numbers whose low nibble or low byte is 2, 3 or 7, and two close
  relatives on other wiring).
- **Old headers:** a "DiskDude!" file is read by byte 6's mapper nibble alone (as I1 pins for NROM), so an
  old UxROM dump runs as mapper 2.

## The frozen driver `cases/drive_i6.ts`
Usage: `node cases/drive_i6.ts '{"rom": p, "steps": [...]}'`; it prints one JSON line,
`{"ok": true, "out": [...]}` with one entry per `until`, `peek` or `frame` step. Steps:
`["frames", n]`, `["until", addr, value, max]` (frames until `peek(addr) == value`; the frames taken, or
-1), `["peek", addr, n]`, `["frame"]` (base64 of the 61,440 little-endian 16-bit values, as
`drive_ppu.ts`), `["buttons", bits]` (`setButtons(0, bits)`), `["reset"]`, `["power"]` (a new `NES` from
the same bytes). Load errors are reported as in `drive.ts`; a bad `frameBuffer()` as `stage: "frame"`.

## What the cases bring themselves (`cases/i6_support.py`)
- **Probe cartridges** for 2, 3 and 7, built in the case: every PRG (and CNROM CHR) bank filled with seeded
  random bytes; a small program in the top 4 KiB (the fixed bank, or repeated in every AxROM bank), with
  an identity table at `$F300`-`$F3FF` (byte n is n) for conflict-free writes. It runs a script of CPU
  writes and reads and PPU reads, writes and copies (and sprite 0, by `$4014` DMA from `$0600`), logs each
  read at `$0300`-`$05FF`, sets `$0200` to `$AA` when done (the log's end pointer at `$0201`/`$0202`), then
  turns rendering on. Optionally it then runs a status-bar split every frame: one bank write in the NMI,
  another once sprite 0 has hit. The expected log and picture come from `Board`, the wiki's boards in
  miniature, and I2's spec renderer.
- **A mid-frame bank or page write** (CNROM's CHR bank, AxROM's page) lands in the hblank after the
  sprite-0 line's last background fetch, and must show from the next line on, however the renderer
  schedules its drawing (the Decided (run) item on lazy drawing names mapper writes as its risk).
- **Holy Mapperel** v0.02 (Damian Yerrick, zlib; fetched by hash: the release archive, then a pinned LZMA2
  slice per ROM), for mappers 2, 3 and 7: its result screen, read with its own font.
- **Cheril the Goddess** (The Mojon Twins, CNROM) and the **240p Test Suite** (PinoBatch, UNROM), fetched by
  hash: what they show and how they answer the pad, from their own source.
