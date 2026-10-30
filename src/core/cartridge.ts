// The cartridge: the iNES / NES 2.0 file format, and the mappers behind it.
// Spec: https://www.nesdev.org/wiki/INES and https://www.nesdev.org/wiki/NES_2.0

/** Thrown for a file the emulator can't or won't run; the message says why. */
export class RomError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'RomError';
  }
}

// Nametable mirroring, as the PPU maps $2000-$2FFF onto the console's 2 KiB (or the cart's 4 KiB).
export const MIRROR_HORIZONTAL = 0;
export const MIRROR_VERTICAL = 1;
export const MIRROR_SINGLE_LOW = 2;
export const MIRROR_SINGLE_HIGH = 3;
export const MIRROR_FOUR_SCREEN = 4;

export interface RomInfo {
  mapper: number;
  submapper: number;
  nes2: boolean;
  prg: Uint8Array;
  /** CHR-ROM, or a zeroed CHR-RAM when the file has none. */
  chr: Uint8Array;
  chrIsRam: boolean;
  prgRamSize: number;
  battery: boolean;
  mirroring: number;
  trainer: boolean;
  /** The TV system: 0 NTSC, 1 PAL, 2 either, 3 Dendy (NES 2.0 byte 12; iNES 1.0 byte 9 bit 0). */
  region: number;
}

const HEADER = 16;
const TRAINER = 512;

/** NES 2.0 sizes: a plain count of units, or (MSB nibble $F) the exponent-multiplier form. */
function nes2Size(lsb: number, msbNibble: number, unit: number): number {
  if (msbNibble === 0xf) return 2 ** (lsb >> 2) * ((lsb & 3) * 2 + 1);
  return ((msbNibble << 8) | lsb) * unit;
}

/** A NES 2.0 RAM size byte's nibble: 0 means none, else 64 << n bytes. */
function shiftSize(n: number): number {
  return n === 0 ? 0 : 64 << n;
}

export function parseRom(data: Uint8Array): RomInfo {
  if (data.length < 4 || data[0] !== 0x4e || data[1] !== 0x45 || data[2] !== 0x53 || data[3] !== 0x1a) {
    throw new RomError('not an iNES or NES 2.0 file: it has no NES header');
  }
  if (data.length < HEADER) {
    throw new RomError(`truncated file: ${data.length} bytes, shorter than the ${HEADER}-byte NES header`);
  }
  const f6 = data[6], f7 = data[7];
  const nes2 = (f7 & 0x0c) === 0x08;
  // An archaic iNES file (junk such as "DiskDude!" in bytes 7-15) carries a garbage upper mapper
  // nibble in byte 7: when bytes 12-15 aren't zero, only byte 6's nibble is trusted.
  const archaic = !nes2 && (data[12] | data[13] | data[14] | data[15]) !== 0;
  let mapper = f6 >> 4;
  let submapper = 0;
  if (nes2) {
    mapper |= (f7 & 0xf0) | ((data[8] & 0x0f) << 8);
    submapper = data[8] >> 4;
  } else if (!archaic) {
    mapper |= f7 & 0xf0;
  }

  const prgSize = nes2 ? nes2Size(data[4], data[9] & 0x0f, 0x4000) : data[4] * 0x4000;
  const chrSize = nes2 ? nes2Size(data[5], data[9] >> 4, 0x2000) : data[5] * 0x2000;
  const trainer = (f6 & 0x04) !== 0;
  const start = HEADER + (trainer ? TRAINER : 0);
  const need = start + prgSize + chrSize;
  if (data.length < need) {
    throw new RomError(`truncated file: its header promises ${prgSize} bytes of PRG ROM and ${chrSize} bytes of ` +
      `CHR ROM (${need} bytes in all, with the header${trainer ? ' and trainer' : ''}), but the file has only ` +
      `${data.length} bytes`);
  }
  if (prgSize === 0) throw new RomError('the header says the file has no PRG ROM');

  const prg = data.slice(start, start + prgSize);
  const chrIsRam = chrSize === 0;
  let chr: Uint8Array;
  if (chrIsRam) {
    const ramSize = nes2 ? shiftSize(data[11] & 0x0f) + shiftSize(data[11] >> 4) : 0;
    chr = new Uint8Array(ramSize || 0x2000);
  } else {
    chr = data.slice(start + prgSize, start + prgSize + chrSize);
  }
  const prgRamSize = nes2 ? shiftSize(data[10] & 0x0f) + shiftSize(data[10] >> 4) : 0x2000;
  const region = nes2 ? data[12] & 3 : !archaic && (data[9] & 1) ? 1 : 0;
  const mirroring = (f6 & 0x08) ? MIRROR_FOUR_SCREEN : (f6 & 0x01) ? MIRROR_VERTICAL : MIRROR_HORIZONTAL;
  return { mapper, submapper, nes2, prg, chr, chrIsRam, prgRamSize, battery: (f6 & 0x02) !== 0, mirroring, trainer,
    region };
}

/** What the console sees of a cartridge: CPU $4020-$FFFF, PPU $0000-$1FFF, and the mirroring. */
export interface Mapper {
  readonly info: RomInfo;
  mirroring: number;
  /** The PRG-RAM at $6000 (battery-backed when info.battery says so). */
  readonly prgRam: Uint8Array;
  /** A CPU read at $4020-$FFFF; -1 where the cart drives nothing (open bus). */
  cpuRead(addr: number): number;
  /** The same read with no side effects. */
  cpuPeek(addr: number): number;
  /** A CPU write at $4020-$FFFF, made on CPU cycle `cycle` (cycles since power-on). */
  cpuWrite(addr: number, value: number, cycle: number): void;
  chrRead(addr: number): number;
  chrWrite(addr: number, value: number): void;
}

/** Mapper 0: 16 or 32 KiB PRG at $8000 (16 KiB mirrored), 8 KiB CHR, and PRG-RAM at $6000. */
export class NROM implements Mapper {
  readonly info: RomInfo;
  mirroring: number;
  private prg: Uint8Array;
  private chr: Uint8Array;
  readonly prgRam: Uint8Array;

  constructor(info: RomInfo) {
    this.info = info;
    this.mirroring = info.mirroring;
    this.prg = info.prg;
    this.chr = info.chr;
    // iNES 1.0 can't say whether there's PRG-RAM, and NES 2.0 test builds often leave it at 0; blargg's
    // $6000 protocol and Family BASIC both need it, so NROM always has at least 8 KiB at $6000.
    this.prgRam = new Uint8Array(info.prgRamSize || 0x2000);
  }

  cpuRead(addr: number): number {
    return this.cpuPeek(addr);
  }

  cpuPeek(addr: number): number {
    if (addr >= 0x8000) return this.prg[(addr - 0x8000) % this.prg.length];
    if (addr >= 0x6000) return this.prgRam[(addr - 0x6000) % this.prgRam.length];
    return -1;
  }

  cpuWrite(addr: number, value: number): void {
    if (addr >= 0x6000 && addr < 0x8000) this.prgRam[(addr - 0x6000) % this.prgRam.length] = value;
  }

  chrRead(addr: number): number {
    return this.chr[addr % this.chr.length];
  }

  chrWrite(addr: number, value: number): void {
    if (this.info.chrIsRam) this.chr[addr % this.chr.length] = value;
  }
}

/** What a write to the bank register latches. NES 2.0 submapper 1 says the board has no bus conflicts
 * (the value written is latched); submapper 2 says AND (the value ANDed with the ROM byte at that
 * address). Submapper 0 and iNES 1.0 files take `fallback`, the board's own default. */
function latched(info: RomInfo, rom: number, value: number, fallback: 'and' | 'none'): number {
  const rule = info.nes2 && info.submapper === 1 ? 'none' : info.nes2 && info.submapper === 2 ? 'and' : fallback;
  return rule === 'and' ? value & rom : value;
}

/** Mapper 3 (CNROM): NROM's PRG, with 8 KiB CHR banks chosen by any write to $8000-$FFFF. Without a
 * submapper the board's AND bus conflicts are kept, as the discrete board has them. */
export class CNROM extends NROM {
  private chrBank = 0;
  private readonly chrBanks: number;

  constructor(info: RomInfo) {
    super(info);
    this.chrBanks = Math.max(1, info.chr.length >> 13);
  }

  override cpuWrite(addr: number, value: number): void {
    if (addr >= 0x8000) this.chrBank = latched(this.info, this.cpuPeek(addr), value, 'and') % this.chrBanks;
    else super.cpuWrite(addr, value);
  }

  override chrRead(addr: number): number {
    return super.chrRead(((this.chrBank << 13) | (addr & 0x1fff)));
  }

  override chrWrite(addr: number, value: number): void {
    super.chrWrite(((this.chrBank << 13) | (addr & 0x1fff)), value);
  }
}

/** Mapper 2 (UxROM): the 16 KiB PRG bank chosen by any write to $8000-$FFFF at $8000-$BFFF, the last
 * bank fixed at $C000-$FFFF; 8 KiB of CHR, unbanked; mirroring fixed by the header. All 8 bits of the
 * register reach the ROM (4 MiB with an NES 2.0 header); a number past the bank count wraps. */
export class UxROM extends NROM {
  private bank = 0;
  private readonly banks: number;
  private readonly rom: Uint8Array;

  constructor(info: RomInfo) {
    super(info);
    this.rom = info.prg;
    this.banks = Math.max(1, info.prg.length >> 14);
  }

  override cpuPeek(addr: number): number {
    if (addr >= 0xc000) return this.rom[((this.banks - 1) << 14) | (addr & 0x3fff)];
    if (addr >= 0x8000) return this.rom[(this.bank << 14) | (addr & 0x3fff)];
    return super.cpuPeek(addr);
  }

  override cpuWrite(addr: number, value: number): void {
    if (addr >= 0x8000) this.bank = latched(this.info, this.cpuPeek(addr), value, 'none') % this.banks;
    else super.cpuWrite(addr, value);
  }
}

/** Mapper 7 (AxROM): the 32 KiB PRG bank in register bits 0-2 at $8000-$FFFF, and bit 4 choosing the
 * 1 KiB of nametable RAM all four nametables show (single-screen). 8 KiB of CHR, unbanked. */
export class AxROM extends NROM {
  private bank = 0;
  private readonly banks: number;
  private readonly rom: Uint8Array;

  constructor(info: RomInfo) {
    super(info);
    this.rom = info.prg;
    this.banks = Math.max(1, info.prg.length >> 15);
    this.mirroring = MIRROR_SINGLE_LOW;
  }

  override cpuPeek(addr: number): number {
    if (addr >= 0x8000) return this.rom[((this.bank << 15) | (addr & 0x7fff)) % this.rom.length];
    return super.cpuPeek(addr);
  }

  override cpuWrite(addr: number, value: number): void {
    if (addr < 0x8000) {
      super.cpuWrite(addr, value);
      return;
    }
    const v = latched(this.info, this.cpuPeek(addr), value, 'none');
    this.bank = (v & 0x07) % this.banks;
    this.mirroring = v & 0x10 ? MIRROR_SINGLE_HIGH : MIRROR_SINGLE_LOW;
  }
}

/** Mapper 1 (MMC1B, the SxROM boards). Spec: https://www.nesdev.org/wiki/MMC1
 * - The serial port ($8000-$FFFF): bit 7 set clears the shift register and ORs control with $0C;
 *   otherwise bit 0 is shifted in, low bit first, and the fifth write loads the register its
 *   address's bits 14-13 pick (control, CHR bank 0, CHR bank 1, PRG bank). A write on the cycle right
 *   after another (a read-modify-write's second) is ignored, unless it's a bit-7 reset.
 * - Control: bits 1-0 mirroring (one-screen A, one-screen B, vertical, horizontal); bits 3-2 PRG mode
 *   (0/1 32 KiB, 2 first bank fixed at $8000, 3 last bank fixed at $C000); bit 4 CHR mode (8/4 KiB).
 * - PRG bank bits 3-0 the 16 KiB bank; bit 4 disables PRG-RAM (MMC1B). No bus conflicts.
 * - SUROM/SXROM (512 KiB PRG): CHR bank 0 bit 4 picks the 256 KiB half, the fixed bank included.
 *   SXROM/SOROM (32/16 KiB PRG-RAM): CHR bank 0 bits 3-2 (bit 3) pick the 8 KiB PRG-RAM bank.
 * - PRG-RAM: the NES 2.0 header's size (0: none); an iNES 1.0 file gets 8 KiB.
 * - NES 2.0 submapper 5 (SEROM, SHROM, SH1ROM): 32 KiB of PRG, not banked. */
export class MMC1 implements Mapper {
  readonly info: RomInfo;
  mirroring = MIRROR_SINGLE_LOW;
  readonly prgRam: Uint8Array;
  private readonly prg: Uint8Array;
  private readonly chr: Uint8Array;
  // Power-on: unknown on hardware; chosen as control $0C (the last bank fixed at $C000), the rest 0.
  private control = 0x0c;
  private chr0 = 0;
  private chr1 = 0;
  private prgBank = 0;
  private shift = 0;
  private shiftCount = 0;
  private lastWrite = -2;
  /** Offsets into PRG for $8000 and $C000, CHR for $0000 and $1000, PRG-RAM for $6000 (-1 disabled). */
  private prgLo = 0;
  private prgHi = 0;
  private chrLo = 0;
  private chrHi = 0;
  private ramBase = -1;

  constructor(info: RomInfo) {
    this.info = info;
    this.prg = info.prg;
    this.chr = info.chr;
    this.prgRam = new Uint8Array(info.nes2 ? info.prgRamSize : 0x2000);
    this.update();
  }

  private update(): void {
    const c = this.control;
    this.mirroring = [MIRROR_SINGLE_LOW, MIRROR_SINGLE_HIGH, MIRROR_VERTICAL, MIRROR_HORIZONTAL][c & 3];
    const banks = this.prg.length >> 14;
    if (this.info.nes2 && this.info.submapper === 5) {
      this.prgLo = 0;
      this.prgHi = banks > 1 ? 0x4000 : 0;
    } else {
      // 512 KiB: the outer 256 KiB half from CHR bank 0 bit 4; the 16 banks of a half wrap within it.
      const outer = banks > 16 && (this.chr0 & 0x10) ? 16 : 0;
      const per = Math.min(16, banks);
      const bank = (b: number): number => (outer + (b % per)) << 14;
      const mode = (c >> 2) & 3;
      const reg = this.prgBank & 0x0f;
      if (mode < 2) {
        this.prgLo = bank(reg & 0x0e);
        this.prgHi = bank((reg & 0x0e) | 1);
      } else if (mode === 2) {
        this.prgLo = bank(0);
        this.prgHi = bank(reg);
      } else {
        this.prgLo = bank(reg);
        this.prgHi = bank(per - 1);
      }
    }
    const chrLen = this.chr.length;
    if (c & 0x10) {
      this.chrLo = (this.chr0 << 12) % chrLen;
      this.chrHi = (this.chr1 << 12) % chrLen;
    } else {
      this.chrLo = ((this.chr0 & 0x1e) << 12) % chrLen;
      this.chrHi = (this.chrLo + 0x1000) % chrLen;
    }
    const ram = this.prgRam.length;
    if (ram === 0 || (this.prgBank & 0x10)) {
      this.ramBase = -1;
    } else {
      const ramBank = ram >= 0x8000 ? (this.chr0 >> 2) & 3 : ram >= 0x4000 ? (this.chr0 >> 3) & 1 : 0;
      this.ramBase = (ramBank << 13) % ram;
    }
  }

  cpuRead(addr: number): number {
    return this.cpuPeek(addr);
  }

  cpuPeek(addr: number): number {
    if (addr >= 0xc000) return this.prg[this.prgHi | (addr & 0x3fff)];
    if (addr >= 0x8000) return this.prg[this.prgLo | (addr & 0x3fff)];
    if (addr >= 0x6000 && this.ramBase >= 0) return this.prgRam[this.ramBase + ((addr & 0x1fff) % this.prgRam.length)];
    return -1;
  }

  cpuWrite(addr: number, value: number, cycle: number): void {
    if (addr < 0x8000) {
      if (addr >= 0x6000 && this.ramBase >= 0) this.prgRam[this.ramBase + ((addr & 0x1fff) % this.prgRam.length)] = value;
      return;
    }
    const consecutive = cycle - this.lastWrite === 1;
    this.lastWrite = cycle;
    if (value & 0x80) {
      this.shift = 0;
      this.shiftCount = 0;
      this.control |= 0x0c;
      this.update();
      return;
    }
    if (consecutive) return;
    this.shift |= (value & 1) << this.shiftCount;
    if (++this.shiftCount < 5) return;
    const v = this.shift;
    this.shift = 0;
    this.shiftCount = 0;
    switch ((addr >> 13) & 3) {
      case 0: this.control = v; break;
      case 1: this.chr0 = v; break;
      case 2: this.chr1 = v; break;
      default: this.prgBank = v;
    }
    this.update();
  }

  chrRead(addr: number): number {
    return this.chr[(addr & 0x1000 ? this.chrHi : this.chrLo) | (addr & 0x0fff)];
  }

  chrWrite(addr: number, value: number): void {
    if (this.info.chrIsRam) this.chr[(addr & 0x1000 ? this.chrHi : this.chrLo) | (addr & 0x0fff)] = value;
  }
}

export function createMapper(info: RomInfo): Mapper {
  switch (info.mapper) {
    case 0: return new NROM(info);
    case 1: return new MMC1(info);
    case 2: return new UxROM(info);
    case 3: return new CNROM(info);
    case 7: return new AxROM(info);
    default: throw new RomError(`mapper ${info.mapper} is not supported`);
  }
}
