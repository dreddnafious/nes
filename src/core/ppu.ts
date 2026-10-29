// The 2C02 PPU: its registers and memory, its dot timing (341 dots x 262 scanlines, vblank and NMI,
// the odd-frame skipped dot), and the picture: background fetches through Loopy's v/t/x scroll
// registers, sprite evaluation (with the overflow flag's hardware bug) and sprite fetches, sprite-0
// hit, and a 256x240 frame of colour indices with the greyscale and emphasis bits.
// Spec: https://www.nesdev.org/wiki/PPU_registers, PPU_rendering, PPU_scrolling, PPU_sprite_evaluation,
// PPU_sprite_priority, PPU_frame_timing, PPU_palettes, Open_bus_behavior#PPU_open_bus.
import { type Mapper, MIRROR_HORIZONTAL, MIRROR_VERTICAL, MIRROR_SINGLE_LOW, MIRROR_SINGLE_HIGH } from './cartridge.ts';

export const DOTS_PER_LINE = 341;
export const LINES_PER_FRAME = 262;
export const WIDTH = 256;
export const HEIGHT = 240;
const VBLANK_LINE = 241;
const PRERENDER_LINE = 261;
/** A bit of the open-bus latch not refreshed with a 1 for about 600 ms reads 0 (36 frames). */
const DECAY_FRAMES = 36;

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
  /** The picture: 256x240 values, bits 0-5 the colour index, bits 6-8 the emphasis bits. */
  readonly pixels = new Uint16Array(WIDTH * HEIGHT);

  // Loopy's scroll registers: current and temporary VRAM address, fine X, and the write toggle.
  v = 0;
  t = 0;
  x = 0;
  w = 0;
  private readBuffer = 0;
  /** The PPU's I/O latch: what reads of write-only registers return (open bus). */
  latch = 0;
  /** The frame each latch bit was last refreshed with a 1. */
  private readonly latchStamp = new Int32Array(8);
  private oddFrame = false;
  /** Whether this odd frame's pre-render line drops its last dot: decided at dot 338. */
  private skipDot = false;
  /** The NMI output: low (true) while the vblank flag and the NMI enable are both set. */
  nmiOutput = false;
  /** A $2002 read on the clock before the flag sets keeps it from setting this frame. */
  private suppressVblank = false;

  // The background: each fetched tile decoded to 8 pixels (palette << 2 | pattern, 0 = transparent),
  // at its position on the line (tiles 0 and 1 come from the line before, dots 321-336).
  private readonly bgLine = new Uint8Array(34 * 8);
  // Sprites, decoded for a whole line at the fetch (dots 257-320 of the line before): pattern bits 0-1,
  // palette bits 2-3, bit 4 behind the background, bit 5 sprite 0.
  private spLine = new Uint8Array(WIDTH);
  private spNext = new Uint8Array(WIDTH);
  /** Whether this line's sprite fetches filled spNext; a line without them leaves the next empty. */
  private spFetched = false;
  private spLineEmpty = true;
  /** OAM indices of the sprites in range for the next line (secondary OAM), and how many. */
  private readonly secondary = new Uint8Array(8);
  private secondaryCount = 0;
  private sprite0InRange = false;
  /** The dot on this line at which evaluation finds a ninth sprite and sets the overflow flag. */
  private overflowDot = -1;
  /** Pixels of the current line already drawn (they are drawn lazily, before anything can change them). */
  private drawn = 0;

  private mapper: Mapper;

  constructor(mapper: Mapper) {
    this.mapper = mapper;
  }

  /** The PPU's part of the Reset button: $2000, $2001, the write toggle and the read buffer clear. */
  reset(): void {
    this.catchUp();
    this.ctrl = 0;
    this.mask = 0;
    this.w = 0;
    this.readBuffer = 0;
    this.oddFrame = false;
    this.updateNmi();
  }

  private updateNmi(): void {
    this.nmiOutput = (this.status & 0x80) !== 0 && (this.ctrl & 0x80) !== 0;
  }

  /** Advance one dot: handle the dot's events, then move on. */
  step(): void {
    const line = this.scanline;
    const dot = this.dot;
    if (line < HEIGHT || line === PRERENDER_LINE) {
      if (line === PRERENDER_LINE && dot === 1) {
        this.status &= 0x1f;  // vblank, sprite-0 hit and overflow clear
        this.updateNmi();
      }
      if ((this.mask & 0x18) !== 0) this.render(line, dot);
      if (dot === 257 && line < HEIGHT) this.finishLine();
      else if (dot === 338 && line === PRERENDER_LINE) this.skipDot = this.oddFrame && (this.mask & 0x18) !== 0;
    } else if (line === VBLANK_LINE && dot === 1) {
      if (!this.suppressVblank) {
        this.status |= 0x80;
        this.updateNmi();
      }
      this.suppressVblank = false;
    }

    this.dot = dot + 1;
    // On odd frames with rendering on (as it was at dot 338), the pre-render line is one dot short.
    if (this.dot >= DOTS_PER_LINE || (this.skipDot && this.dot === DOTS_PER_LINE - 1)) this.nextLine();
  }

  private nextLine(): void {
    this.dot = 0;
    this.drawn = 0;
    this.overflowDot = -1;
    if (this.spFetched) {
      const tmp = this.spLine;
      this.spLine = this.spNext;
      this.spNext = tmp;
      this.spFetched = false;
      this.spLineEmpty = false;
    } else if (!this.spLineEmpty) {
      this.spLine.fill(0);
      this.spLineEmpty = true;
    }
    if (++this.scanline === LINES_PER_FRAME) {
      this.scanline = 0;
      this.skipDot = false;
      this.frame++;
      this.oddFrame = !this.oddFrame;
      this.frameDone = true;
    }
  }

  /** A rendering line's work for one dot (rendering is on): fetches, scroll updates, sprites. */
  private render(line: number, dot: number): void {
    if (dot >= 1 && dot <= 256) {
      if ((dot & 7) === 0) {
        this.fetchTile(dot + 8);
        this.incHori();
        if (dot === 256) this.incVert();
      } else if (dot === 65 && line < HEIGHT) {
        this.evaluateSprites(line);
      }
      if (dot === this.overflowDot && line < HEIGHT) this.status |= 0x20;
    } else if (dot === 257) {
      this.v = (this.v & ~0x041f) | (this.t & 0x041f);
      this.oamAddr = 0;
      if (line < HEIGHT) this.fetchSprites(line);
    } else if (dot >= 280 && dot <= 304) {
      if (line === PRERENDER_LINE) this.v = (this.v & ~0x7be0) | (this.t & 0x7be0);
    } else if (dot === 328 || dot === 336) {
      this.fetchTile(dot - 328);
      this.incHori();
    }
  }

  private incHori(): void {
    if ((this.v & 0x1f) === 31) this.v = (this.v & ~0x1f) ^ 0x0400;
    else this.v++;
  }

  private incVert(): void {
    let v = this.v;
    if ((v & 0x7000) !== 0x7000) {
      v += 0x1000;
    } else {
      v &= ~0x7000;
      let y = (v >> 5) & 31;
      if (y === 29) {
        y = 0;
        v ^= 0x0800;
      } else if (y === 31) {
        y = 0;
      } else {
        y++;
      }
      v = (v & ~0x03e0) | (y << 5);
    }
    this.v = v;
  }

  /** Fetch the tile at v (nametable byte, attribute, both pattern planes) and decode it at pos. */
  private fetchTile(pos: number): void {
    const v = this.v;
    const tile = this.vram[this.ntIndex(0x2000 | (v & 0x0fff))];
    const attr = this.vram[this.ntIndex(0x23c0 | (v & 0x0c00) | ((v >> 4) & 0x38) | ((v >> 2) & 7))];
    const pal = ((attr >> (((v >> 4) & 4) | (v & 2))) & 3) << 2;
    const addr = ((this.ctrl & 0x10) << 8) | (tile << 4) | ((v >> 12) & 7);
    const lo = this.mapper.chrRead(addr);
    const hi = this.mapper.chrRead(addr | 8);
    const out = this.bgLine;
    for (let i = 0; i < 8; i++) {
      const b = 7 - i;
      const p = ((lo >> b) & 1) | (((hi >> b) & 1) << 1);
      out[pos + i] = p === 0 ? 0 : pal | p;
    }
  }

  /** Sprite evaluation for the next line, done at once at dot 65; the ninth sprite's overflow flag is
   * scheduled for the dot the hardware's search would reach it, two dots per OAM read, eight per
   * sprite copied. Past eight sprites the search steps through OAM diagonally (the hardware bug: m
   * advances along with n when a byte isn't in range). */
  private evaluateSprites(line: number): void {
    const oam = this.oam;
    const h = this.ctrl & 0x20 ? 16 : 8;
    let count = 0;
    let n = 0;
    let dot = 65;
    this.sprite0InRange = false;
    this.overflowDot = -1;
    for (; n < 64 && count < 8; n++) {
      const row = line - oam[n << 2];
      if (row >= 0 && row < h) {
        if (n === 0) this.sprite0InRange = true;
        this.secondary[count++] = n;
        dot += 8;
      } else {
        dot += 2;
      }
    }
    this.secondaryCount = count;
    if (count === 8) {
      let m = 0;
      for (; n < 64; n++) {
        const row = line - oam[(n << 2) | m];
        if (row >= 0 && row < h) {
          this.overflowDot = dot;
          break;
        }
        m = (m + 1) & 3;
        dot += 2;
      }
    }
  }

  /** The sprite fetches (dots 257-320): decode the sprites found for the next line into its buffer;
   * where sprites overlap, the lowest OAM index with an opaque pixel is the one kept. */
  private fetchSprites(line: number): void {
    const out = this.spNext;
    out.fill(0);
    this.spFetched = true;
    const count = this.secondaryCount;
    const oam = this.oam;
    const tall = (this.ctrl & 0x20) !== 0;
    const h = tall ? 16 : 8;
    for (let i = 0; i < count; i++) {
      const n = this.secondary[i] << 2;
      const tile = oam[n + 1];
      const attr = oam[n + 2];
      const sx = oam[n + 3];
      let row = (line - oam[n]) & (h - 1);
      if (attr & 0x80) row = h - 1 - row;
      const addr = tall
        ? ((tile & 1) << 12) | (((tile & 0xfe) + (row >> 3)) << 4) | (row & 7)
        : ((this.ctrl & 0x08) << 9) | (tile << 4) | row;
      const lo = this.mapper.chrRead(addr);
      const hi = this.mapper.chrRead(addr | 8);
      const flags = ((attr & 3) << 2) | (attr & 0x20 ? 0x10 : 0) | (i === 0 && this.sprite0InRange ? 0x20 : 0);
      for (let c = 0; c < 8; c++) {
        const x = sx + c;
        if (x > 255) break;
        const b = attr & 0x40 ? c : 7 - c;
        const p = ((lo >> b) & 1) | (((hi >> b) & 1) << 1);
        if (p !== 0 && (out[x] & 3) === 0) out[x] = flags | p;
      }
    }
    this.secondaryCount = 0;
  }

  /** Draw the pixels of the current line whose dots have passed. Called before anything that could
   * change how they look (a register write) or see their effect (a $2002 read), and at dot 257. */
  catchUp(): void {
    if (this.scanline >= HEIGHT) return;
    const end = this.dot - 1 > WIDTH ? WIDTH : this.dot - 1;
    if (end > this.drawn) {
      this.draw(this.drawn, end);
      this.drawn = end;
    }
  }

  private finishLine(): void {
    if (this.drawn < WIDTH) {
      this.draw(this.drawn, WIDTH);
      this.drawn = WIDTH;
    }
  }

  /** Output pixels from..to-1 of the current line, with the registers as they are now. */
  private draw(from: number, to: number): void {
    const mask = this.mask;
    const pal = this.palette;
    const out = this.pixels;
    const base = this.scanline * WIDTH;
    const grey = mask & 1 ? 0x30 : 0x3f;
    const emphasis = (mask & 0xe0) << 1;
    if ((mask & 0x18) === 0) {
      // Rendering off: the backdrop, or the palette entry v points at (the "background palette hack").
      const v = this.v & 0x3fff;
      const c = ((v >= 0x3f00 ? pal[this.paletteIndex(v)] : pal[0]) & grey) | emphasis;
      out.fill(c, base + from, base + to);
      return;
    }
    const bg = this.bgLine;
    const sp = this.spLine;
    const fineX = this.x;
    const showBg = (mask & 0x08) !== 0;
    const showSp = (mask & 0x10) !== 0;
    for (let x = from; x < to; x++) {
      const b = showBg && (x >= 8 || (mask & 0x02)) ? bg[x + fineX] : 0;
      const s = showSp && (x >= 8 || (mask & 0x04)) ? sp[x] : 0;
      let c: number;
      if ((s & 3) !== 0) {
        if ((s & 0x20) !== 0 && b !== 0 && x !== 255) this.status |= 0x40;
        c = b !== 0 && (s & 0x10) !== 0 ? pal[b] : pal[0x10 | (s & 0x0f)];
      } else {
        c = b !== 0 ? pal[b] : pal[0];
      }
      out[base + x] = (c & grey) | emphasis;
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

  // ---- the open-bus latch ----

  /** The latch as it reads now, its bits that weren't refreshed with a 1 lately decayed to 0. */
  private decayed(): number {
    let v = this.latch;
    if (v !== 0) {
      for (let i = 0; i < 8; i++) {
        if ((v >> i) & 1 && this.frame - this.latchStamp[i] >= DECAY_FRAMES) v &= ~(1 << i);
      }
      this.latch = v;
    }
    return v;
  }

  /** Drive the bits in `bits` with `value`: they read back as it, and a 1 refreshes them. */
  private refresh(value: number, bits: number): void {
    this.latch = (this.decayed() & ~bits) | (value & bits);
    for (let i = 0; i < 8; i++) {
      if ((bits >> i) & (value >> i) & 1) this.latchStamp[i] = this.frame;
    }
  }

  // ---- CPU-facing registers ($2000-$2007, mirrored to $3FFF) ----

  readRegister(reg: number): number {
    switch (reg) {
      case 2: {
        this.catchUp();
        // A read on the clock before the flag sets sees it clear, and the flag doesn't set.
        if (this.scanline === VBLANK_LINE && this.dot === 1) this.suppressVblank = true;
        const v = (this.status & 0xe0) | (this.decayed() & 0x1f);
        this.status &= 0x7f;
        this.w = 0;
        this.updateNmi();
        this.refresh(v, 0xe0);
        return v;
      }
      case 4: {
        let v = this.oam[this.oamAddr];
        if ((this.oamAddr & 3) === 2) v &= 0xe3;  // attribute bytes have no bits 2-4
        this.refresh(v, 0xff);
        return v;
      }
      case 7: {
        const addr = this.v & 0x3fff;
        let v: number;
        if (addr >= 0x3f00) {
          // Palette reads come straight through (bits 6-7 are open bus); the buffer gets the
          // nametable byte "underneath".
          v = (this.palette[this.paletteIndex(addr)] & 0x3f) | (this.decayed() & 0xc0);
          this.readBuffer = this.vram[this.ntIndex(addr - 0x1000)];
          this.refresh(v, 0x3f);
        } else {
          v = this.readBuffer;
          this.readBuffer = this.memRead(addr);
          this.refresh(v, 0xff);
        }
        this.v = (this.v + (this.ctrl & 0x04 ? 32 : 1)) & 0x7fff;
        return v;
      }
      default:
        return this.decayed();
    }
  }

  writeRegister(reg: number, value: number): void {
    this.catchUp();
    this.refresh(value, 0xff);
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
