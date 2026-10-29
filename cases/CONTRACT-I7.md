# The surface I7's cases drive (additive to CONTRACT.md, CONTRACT-I2.md, CONTRACT-I3.md, CONTRACT-I4.md and CONTRACT-I6.md)

I7 adds **no new method, command, page element or driver**. Everything in the earlier contracts stays as
it is: the CLI's `trace`, `run`, `screenshot` and `audio`; the core API (`new NES(bytes)`, `runFrame()`,
`peek()`, `reset()`, `frameBuffer()`, `setButtons()`, `setSampleRate()`, `audioSamples()`, `RomError`);
the frozen drivers (`cases/drive.ts`, `cases/drive_ppu.ts`, `cases/drive_i6.ts`, which I7's cases reuse);
the page and its `window.nesTest` hooks. What changes is which files those surfaces accept: iNES
**mapper 1 (MMC1, the SxROM boards)** loads and runs, on every one of them.

## The MMC1 (NESdev wiki: "MMC1", "NES 2.0", "Mirroring")
- **The serial port** ($8000-$FFFF): a write with bit 7 set clears the shift register and ORs control
  with `$0C` (PRG mode 3), leaving the CHR mode and mirroring alone. Otherwise bit 0 is shifted in, low
  bit first; the fifth write copies the five bits into the register its address's bits 14-13 pick
  (`$8000` control, `$A000` CHR bank 0, `$C000` CHR bank 1, `$E000` PRG bank) and clears the shift
  register; the first four writes' addresses don't matter. Writes below `$8000` never reach it.
- **Consecutive-cycle writes:** a write in the CPU cycle right after another write (a read-modify-write
  instruction's second write) has its bit 0 ignored; its bit-7 reset never is.
- **Control:** bits 1-0 mirroring (0 one-screen A, 1 one-screen B, 2 vertical, 3 horizontal), whatever
  the header says; bits 3-2 PRG mode (0/1: 32 KiB at `$8000`, the bank's low bit ignored; 2: the first
  bank fixed at `$8000`, `$C000` switched; 3: the last bank fixed at `$C000`, `$8000` switched); bit 4
  CHR mode (0: 8 KiB, CHR bank 0's low bit ignored and CHR bank 1 unused; 1: two 4 KiB banks).
- **Banks:** PRG bank bits 3-0 pick a 16 KiB bank; CHR bank numbers pick 4 KiB banks of CHR-ROM, or of
  8 KiB CHR-RAM ("that makes 2 banks"). A number past the ROM's last bank wraps (the cases use
  power-of-two sizes for that). No bus conflicts.
- **PRG-RAM at `$6000`-`$7FFF`:** its size is the NES 2.0 header's (byte 10: volatile and battery-backed;
  0 means none, and the program finds none); an iNES 1.0 file has at least 8 KiB (the cases ask no more of
  it). It holds what's written while `$E000` bit 4 is clear.
- **SxROM variants, by NES 2.0 sizes:** SUROM/SXROM (512 KiB PRG): CHR bank bit 4 picks the 256 KiB half,
  the "fixed" bank included. SXROM (32 KiB PRG-RAM): CHR bank bits 3-2 pick the 8 KiB PRG-RAM bank. In
  4 KiB CHR mode the cases keep both CHR registers' upper bits equal.
- **Battery:** battery-backed PRG-RAM is kept across Reset and Power within the session and cleared by
  loading another game (Decided Q3); the page's Power carries it (CONTRACT-I3).
- **Not pinned** (the wiki says unknown, or it varies by ASIC revision or board): every register and the
  shift register at power-on (the cases ask only that power-on be the same every time); what the console's
  Reset does to the MMC1 (games, and the probes, write `$80` and load every register at reset); PRG-RAM
  with `$E000` bit 4 set (MMC1A: always enabled; MMC1B: disabled), so Holy Mapperel's WRAM digit may read
  0 or 1; SNROM's CHR-bit-4 PRG-RAM disable ("not strictly required"); SOROM's 16 KiB PRG-RAM; the upper
  CHR bits disagreeing in 4 KiB mode; 32 KiB PRG files (SEROM, submapper 5); open bus with no PRG-RAM;
  PRG-RAM and CHR-RAM contents never written.
- **Refused, as before:** any other mapper, by its whole number (17, 33, 65, 105, 257 and 513 are tried).

## What the cases bring themselves (`cases/i7_support.py`)
- **`Mmc1`**, the wiki's MMC1 in miniature, which refuses (raises `Unknown`) anything in the "Not pinned"
  list, so no case can depend on it.
- **`Mmc1Probe`**, a probe cartridge on the plan of I6's: seeded random PRG banks (and CHR-ROM); a small
  interpreter in the top 4 KiB of every 16 KiB bank that writes `$80` to `$8000`, then runs a script of
  single CPU writes (a register load is five), read-modify-write instructions (INC, DEC, ROR, and the
  unofficial RRA abs,X) on ROM addresses, and CPU and PPU reads and writes; it logs reads at `$0300`, sets
  `$0200` = `$AA` when done (so `i6_support.run_probe`/`check_probe` read it), then turns rendering on,
  optionally with a sprite-0 status-bar split that loads CHR bank 0 in the NMI and again after the hit
  (the fifth write right after it). The expected log and picture come from `Mmc1` and I2's spec renderer.
- **Holy Mapperel** v0.02 (Damian Yerrick, zlib; its SxROM builds, cut from the release archive I6 pins):
  its result screen, read with its own font.
- **blargg's multi-test builds** of `instr_test-v5` (`all_instrs`, `official_only`), `instr_timing`,
  `instr_misc`, `cpu_interrupts_v2`, `ppu_vbl_nmi` and `apu_test`: MMC1 cartridges read through the
  `$6000` protocol by `cases/drive.ts`.
- **Nova the Squirrel** (NovaSquirrel, SXROM) and **Minekart Madness** (Matt Hughson, SKROM build),
  fetched by hash: what they show and how they answer the pad, from their own source.
