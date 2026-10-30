# The surface I8's cases drive (additive to CONTRACT.md, CONTRACT-I2.md, CONTRACT-I3.md, CONTRACT-I4.md, CONTRACT-I6.md and CONTRACT-I7.md)

I8 adds **no new method, command, page element or driver**. Everything in the earlier contracts stays as
it is: the CLI's `trace`, `run`, `screenshot` and `audio`; the core API (`new NES(bytes)`, `runFrame()`,
`peek()`, `reset()`, `frameBuffer()`, `setButtons()`, `setSampleRate()`, `audioSamples()`, `RomError`);
the frozen drivers (`cases/drive.ts`, `cases/drive_ppu.ts`, `cases/drive_i6.ts`, which I8's cases reuse;
`drive_i6.ts`'s `["power"]` is a new console from the file, so it carries no save); the page and its
`window.nesTest` hooks. What changes is which files those surfaces accept: iNES **mapper 4 (MMC3, the
TxROM boards)** loads and runs, on every one of them, with its scanline IRQ on the CPU's /IRQ line.

## The MMC3 (NESdev wiki: "MMC3", "NES 2.0 submappers", "Mirroring"; the target is the Sharp MMC3B/C, NES 2.0 submapper 0, Decided Q2)
- **Registers**, each answering its whole range, even or odd address (`addr & $E001`): `$8000` bank
  select (bits 2-0 the register R0-R7 the next data write sets; bit 6 PRG mode; bit 7 CHR A12 inversion;
  bits 5-3 do nothing on a NES 2.0 submapper-0 file); `$8001` bank data; `$A000` nametable arrangement;
  `$A001` PRG-RAM protect; `$C000` IRQ latch; `$C001` IRQ reload; `$E000` IRQ disable and acknowledge;
  `$E001` IRQ enable. Writes below `$8000` never reach them. No bus conflicts.
- **PRG:** 8 KiB banks. Mode 0: `$8000` = R6, `$A000` = R7, `$C000` = the second-last bank, `$E000` =
  the last; mode 1: `$8000` = the second-last, `$C000` = R6. R6/R7 ignore their top two bits; numbers past
  the last bank wrap (the cases use power-of-two sizes, 32 to 512 KiB).
- **CHR:** inversion 0: R0, R1 2 KiB at `$0000`, `$0800` (their low bit ignored), R2-R5 1 KiB at `$1000`
  -`$1C00`; inversion 1: the halves swapped. CHR-ROM (32-256 KiB) is never written; CHR-RAM (8 KiB, or
  the NES 2.0 byte 11 size: 32 KiB is tried) is banked the same way. Numbers past the last bank wrap.
- **Nametables:** `$A000` bit 0: 0 = vertical mirroring (A10), 1 = horizontal (A11), whatever header
  byte 6 bit 0 says; a four-screen header (byte 6 bit 3) gives four nametables and `$A000` does nothing.
- **PRG-RAM** at `$6000`-`$7FFF`: 8 KiB, by the NES 2.0 header (byte 10, volatile or battery-backed; 0
  means none, and a written byte doesn't read back), or on an iNES 1.0 file. On a NES 2.0 file `$A001`
  works as the MMC3's: bit 7 = 0 disables it (reads aren't the stored byte, writes are lost), bit 6 = 1
  write-protects it. With `$A001` = `$80` it holds what's written on any file. The cases also run blargg's
  `mmc3_test_2`, iNES 1.0 files that use PRG-RAM at `$6000` without ever writing `$A001`: they need it
  enabled from power-on on such a file (the wiki: "many implementations of iNES Mapper 004 simply omit
  the write protection").
- **Battery:** battery-backed PRG-RAM is kept across Reset and the page's Power and cleared by loading
  another game (Decided Q3); plain PRG-RAM is kept across Reset only.
- **The IRQ counter:** clocked by a rise of PPU A12 after it has been low for a while (the wiki: "three
  falling edges of M2"; the fetch schedule's short lows inside a line don't count). On a clock: reloaded
  from the latch if it's 0 or `$C001` asked for it, else decremented; then, if it's 0 and IRQs are
  enabled, the IRQ line is asserted until `$E000`. A latch of 0 gives an IRQ at every clock. `$C000`
  doesn't touch the counter; `$C001` makes the next clock a reload and asserts nothing; `$E001` enables
  without delivering a zero passed while disabled. With the background at `$0000` and sprites at `$1000`
  (8x8, or 8x16 on odd tiles) the counter is clocked once a line during the sprite fetches, on the
  pre-render line and lines 0-239 (241 a frame), so with the NMI writing `$C000` = N, `$C001` the IRQ comes
  at the end of line N - 1 (N = 0: the pre-render line), and a scroll the handler writes shows from line
  N + 1. It is also clocked by `$2006` and by `$2007` accesses that move A12 from 0 to 1 with rendering off,
  and not at all by frames drawn with rendering off. The MMC3's IRQ and the APU's share the line.
- **Not pinned** (the wiki says unknown, or it varies by revision or board): every register, the latch,
  counter, reload flag and enable at power-on (the cases ask only that power-on be the same every time and
  that `$E000`-`$FFFF` hold the last bank); what the console's Reset does to the MMC3 (the probes
  reinitialise it); the `$C001` reload glitch (`$C001` written right after a reload by `$C001`); an A12 rise
  through `$3000`-`$3FFF`, or after a very short low; the exact dot of the clock (only the line, and
  blargg's `4-scanline_timing` to the PPU clock); the background at `$1000` with sprites at `$0000` beyond
  that ROM, and 8x16 sprites on even tiles; `$A001` on an iNES 1.0 file other than `$80`, and bank select
  bit 5 there (the MMC6 conflation); NES 2.0 submappers 1-5 (MMC6, MC-ACC, NEC, T9552); more than 8 KiB
  of PRG-RAM; PRG past 512 KiB; open-bus values; PRG-RAM and CHR-RAM contents never written.
- **Refused, as before:** any other mapper, by its whole number (20, 68, 74, 118, 119, 206, 260 and 516
  are tried, and Holy Mapperel's mapper-118 build).

## What the cases bring themselves (`cases/i8_support.py`)
- **`Mmc3`**, the wiki's MMC3 in miniature, which refuses (raises `Unknown`) anything in the "Not pinned"
  list, so no case can depend on it; and `irq_schedule`, the IRQs of a rendered frame from its counter.
- **`Mmc3Probe`**, a probe cartridge on the plan of I6's and I7's: seeded random PRG banks (and CHR-ROM);
  a small interpreter in the last 8 KiB bank (the one window known at power-on) that writes `$E000`, then
  runs a script of CPU writes and reads, PPU reads, writes, copies and fills, `$2006` writes that move A12
  by hand, IRQ queries (CLI, NOP, NOP, SEI) and vblank waits; it logs reads at `$0300`, sets `$0200` = `$AA`
  when done (so `i6_support.run_probe` reads it), then turns rendering on: still, with a sprite-0 split
  (I6's), or with an **MMC3 IRQ split** (`IrqSplit`): each NMI sets the top scroll and `$C000` = N, `$C001`,
  `$E001`; the handler sets the next band's scroll (multiples of 8), optionally a CHR bank, a second
  latch, re-enabling for every IRQ, the APU's frame IRQ beside it, OAM DMA in the main loop, or every other
  frame blanked; it counts IRQs per frame at `$0740`-`$077F` and in totals at `$0700`. The expected log and
  picture come from `Mmc3`, `irq_schedule` and I2's spec renderer.
- **blargg's `mmc3_test_2`** singles (`$6000` protocol, read by `cases/drive.ts`): 1-5 must report
  passed. `6-MMC3_alt` tests the other revision (MMC3A/NEC) and can't pass on the MMC3B/C: per Decided Q2
  it stays red in the raw accuracy count, reported as expected-fail with that reason, in the denominator.
  The corpus asks nothing a right build can't meet: it pins that ROM through one case, that it fails
  exactly at its sub-test 2 (status 2): the right failure, for the right reason.
- **Holy Mapperel** v0.02 (Damian Yerrick, zlib; its MMC3 builds and its mapper-118 build, cut from the
  release archive I6 pins): its result screen, read with its own font.
- **nesdoug's 33_MMC3** (Doug Fraker, MIT), fetched by hash: a demo with four IRQ scroll bands, PRG
  banking and PRG-RAM; what it shows, from its own source.
