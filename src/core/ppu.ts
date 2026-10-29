// The 2C02 PPU: its registers, its memory, and its dot timing (341 dots x 262 scanlines, vblank and
// NMI, the odd-frame skipped dot). Rendering itself comes with the PPU increment; this is what the
// CPU sees of the PPU: $2000-$2007, the vblank flag and the NMI.
// Spec: https://www.nesdev.org/wiki/PPU_registers, PPU_rendering, PPU_frame_timing.
import { type Mapper, MIRROR_HORIZONTAL, MIRROR_VERTICAL, MIRROR_SINGLE_LOW, MIRROR_SINGLE_HIGH } from './cartridge.ts';

export const DOTS_PER_LINE = 341;
export const LINES_PER_FRAME = 262;
const VBLANK_LINE = 241;
const PRERENDER_LINE = 261;

/** What the PPU drives on the CPU side: the NMI line. */
export interface PpuHost {
  nmi(): void;
}

export class PPU {
  scanline = 0;
  dot = 0;
  /** Frames completed since power-on. */
  frame = 0;
  /** Set when the PPU wraps to the next frame; the console clears it. */
  frameDone = false;

  ctrl = 0;
  mask = 0;
  status = 0;
  oamAddr = 0;
  readonly oam = new Uint8Array(256);
  readonly palette = new Uint8Array(32);
  /** Nametable RAM: 2 KiB in the console, 4 KiB with a four-screen cart. */
  readonly vram = new Uint8Array(0x1000);

  // Loopy's scroll registers: current and temporary VRAM address, fine X, and the write toggle.
  v = 0;
  t = 0;
  x = 0;
  w = 0;
  private readBuffer = 0;
  /** The PPU's I/O latch: what reads of write-only registers return (open bus). */
  latch = 0;
  private oddFrame = false;
  private nmiOut = false;

  private mapper: Mapper;
  private host: PpuHost;

  constructor(mapper: Mapper, host: PpuHost) {
    this.mapper = mapper;
    this.host = host;
  }

  /** The PPU's part of the Reset button: $2000, $2001, the write toggle and the read buffer clear. */
  reset(): void {
    this.ctrl = 0;
    this.mask = 0;
    this.w = 0;
    this.readBuffer = 0;
    this.oddFrame = false;
    this.updateNmi();
  }

  private get rendering(): boolean {
    return (this.mask & 0x18) !== 0;
  }

  private updateNmi(): void {
    const out = (this.status & 0x80) !== 0 && (this.ctrl & 0x80) !== 0;
    if (out && !this.nmiOut) this.host.nmi();
    this.nmiOut = out;
  }

  /** Advance one dot: handle the dot's events, then move on. */
  step(): void {
    if (this.dot === 1) {
      if (this.scanline === VBLANK_LINE) {
        this.status |= 0x80;
        this.updateNmi();
      } else if (this.scanline === PRERENDER_LINE) {
        this.status &= 0x1f;  // vblank, sprite-0 hit and overflow clear
        this.updateNmi();
      }
    }
    this.dot++;
    // On odd frames with rendering on, the pre-render line is one dot short.
    const lineEnd = this.scanline === PRERENDER_LINE && this.oddFrame && this.rendering ? DOTS_PER_LINE - 1 : DOTS_PER_LINE;
    if (this.dot >= lineEnd) {
      this.dot = 0;
      this.scanline++;
      if (this.scanline === LINES_PER_FRAME) {
        this.scanline = 0;
        this.frame++;
        this.oddFrame = !this.oddFrame;
        this.frameDone = true;
      }
    }
  }

  // ---- PPU memory ($0000-$3FFF) ----

  private ntIndex(addr: number): number {
    const table = (addr >> 10) & 3;
    const off = addr & 0x3ff;
    switch (this.mapper.mirroring) {
      case MIRROR_HORIZONTAL: return ((table >> 1) << 10) | off;
      case MIRROR_VERTICAL: return ((table & 1) << 10) | off;
      case MIRROR_SINGLE_LOW: return off;
      case MIRROR_SINGLE_HIGH: return 0x400 | off;
      default: return (table << 10) | off;
    }
  }

  private paletteIndex(addr: number): number {
    const i = addr & 0x1f;
    return (i & 0x13) === 0x10 ? i & 0x0f : i;  // $3F10/$14/$18/$1C mirror $3F00/$04/$08/$0C
  }

  memRead(addr: number): number {
    addr &= 0x3fff;
    if (addr < 0x2000) return this.mapper.chrRead(addr);
    if (addr < 0x3f00) return this.vram[this.ntIndex(addr)];
    return this.palette[this.paletteIndex(addr)];
  }

  memWrite(addr: number, value: number): void {
    addr &= 0x3fff;
    if (addr < 0x2000) this.mapper.chrWrite(addr, value);
    else if (addr < 0x3f00) this.vram[this.ntIndex(addr)] = value;
    else this.palette[this.paletteIndex(addr)] = value & 0x3f;
  }

  // ---- CPU-facing registers ($2000-$2007, mirrored to $3FFF) ----

  readRegister(reg: number): number {
    switch (reg) {
      case 2: {
        const v = (this.status & 0xe0) | (this.latch & 0x1f);
        this.status &= 0x7f;
        this.w = 0;
        this.updateNmi();
        this.latch = v;
        return v;
      }
      case 4: {
        let v = this.oam[this.oamAddr];
        if ((this.oamAddr & 3) === 2) v &= 0xe3;  // attribute bytes have no bits 2-4
        this.latch = v;
        return v;
      }
      case 7: {
        const addr = this.v & 0x3fff;
        let v: number;
        if (addr >= 0x3f00) {
          // Palette reads come straight through (upper bits from the latch); the buffer gets the
          // nametable byte "underneath".
          v = (this.palette[this.paletteIndex(addr)] & 0x3f) | (this.latch & 0xc0);
          this.readBuffer = this.vram[this.ntIndex(addr - 0x1000)];
        } else {
          v = this.readBuffer;
          this.readBuffer = this.memRead(addr);
        }
        this.v = (this.v + (this.ctrl & 0x04 ? 32 : 1)) & 0x7fff;
        this.latch = v;
        return v;
      }
      default:
        return this.latch;
    }
  }

  writeRegister(reg: number, value: number): void {
    this.latch = value;
    switch (reg) {
      case 0:
        this.ctrl = value;
        this.t = (this.t & 0xf3ff) | ((value & 3) << 10);
        this.updateNmi();
        break;
      case 1:
        this.mask = value;
        break;
      case 3:
        this.oamAddr = value;
        break;
      case 4:
        this.oam[this.oamAddr] = value;
        this.oamAddr = (this.oamAddr + 1) & 0xff;
        break;
      case 5:
        if (this.w === 0) {
          this.t = (this.t & 0x7fe0) | (value >> 3);
          this.x = value & 7;
        } else {
          this.t = (this.t & 0x0c1f) | ((value & 7) << 12) | ((value & 0xf8) << 2);
        }
        this.w ^= 1;
        break;
      case 6:
        if (this.w === 0) {
          this.t = (this.t & 0x00ff) | ((value & 0x3f) << 8);
        } else {
          this.t = (this.t & 0x7f00) | value;
          this.v = this.t;
        }
        this.w ^= 1;
        break;
      case 7:
        this.memWrite(this.v, value);
        this.v = (this.v + (this.ctrl & 0x04 ? 32 : 1)) & 0x7fff;
        break;
    }
  }
}
