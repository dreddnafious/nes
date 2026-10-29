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
  const mirroring = (f6 & 0x08) ? MIRROR_FOUR_SCREEN : (f6 & 0x01) ? MIRROR_VERTICAL : MIRROR_HORIZONTAL;
  return { mapper, submapper, nes2, prg, chr, chrIsRam, prgRamSize, battery: (f6 & 0x02) !== 0, mirroring, trainer };
}

/** What the console sees of a cartridge: CPU $4020-$FFFF, PPU $0000-$1FFF, and the mirroring. */
export interface Mapper {
  readonly info: RomInfo;
  mirroring: number;
  /** A CPU read at $4020-$FFFF; -1 where the cart drives nothing (open bus). */
  cpuRead(addr: number): number;
  /** The same read with no side effects. */
  cpuPeek(addr: number): number;
  cpuWrite(addr: number, value: number): void;
  chrRead(addr: number): number;
  chrWrite(addr: number, value: number): void;
}

/** Mapper 0: 16 or 32 KiB PRG at $8000 (16 KiB mirrored), 8 KiB CHR, and PRG-RAM at $6000. */
export class NROM implements Mapper {
  readonly info: RomInfo;
  mirroring: number;
  private prg: Uint8Array;
  private chr: Uint8Array;
  private prgRam: Uint8Array;

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

/** Mapper 3: NROM's PRG, with 8 KiB CHR banks chosen by any write to $8000-$FFFF. The board has
 * bus conflicts: the value latched is the one written ANDed with the ROM byte at that address. */
export class CNROM extends NROM {
  private chrBank = 0;

  override cpuWrite(addr: number, value: number): void {
    if (addr >= 0x8000) this.chrBank = value & this.cpuPeek(addr);
    else super.cpuWrite(addr, value);
  }

  override chrRead(addr: number): number {
    return super.chrRead(((this.chrBank << 13) | (addr & 0x1fff)));
  }

  override chrWrite(addr: number, value: number): void {
    super.chrWrite(((this.chrBank << 13) | (addr & 0x1fff)), value);
  }
}

export function createMapper(info: RomInfo): Mapper {
  switch (info.mapper) {
    case 0: return new NROM(info);
    case 3: return new CNROM(info);
    default: throw new RomError(`mapper ${info.mapper} is not supported`);
  }
}
