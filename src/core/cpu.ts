// The Ricoh 2A03's 6502 core: every opcode, official and unofficial, bus access by bus access.
// Each read or write is one CPU cycle, so the timing (page-cross and branch penalties, dummy reads
// and writes) falls out of doing exactly the accesses the chip does. Decimal mode doesn't exist on
// the 2A03: D is a flag the CPU keeps, and ADC/SBC ignore it.
// Spec: https://www.nesdev.org/wiki/CPU and its pages on unofficial opcodes and interrupts.
import { MODE, ACC, IMM, ZP, ZPX, ZPY, ABS, ABX, ABY, IZX, IZY } from './opcodes.ts';

export const FLAG_C = 0x01;
export const FLAG_Z = 0x02;
export const FLAG_I = 0x04;
export const FLAG_D = 0x08;
export const FLAG_B = 0x10;
export const FLAG_U = 0x20;
export const FLAG_V = 0x40;
export const FLAG_N = 0x80;

/** What the CPU is wired to. `cycle` advances the rest of the console by one CPU cycle. */
export interface CpuBus {
  cycle(): void;
  read(addr: number): number;
  write(addr: number, value: number): void;
  /** Non-zero while any device holds the IRQ line low. */
  irq: number;
}

const NMI_VECTOR = 0xfffa;
const RESET_VECTOR = 0xfffc;
const IRQ_VECTOR = 0xfffe;

export class CPU {
  a = 0;
  x = 0;
  y = 0;
  s = 0;
  /** The status register as PHP would push it, minus B: bit 5 always set, bit 4 always clear. */
  p = FLAG_U | FLAG_I;
  pc = 0;
  /** CPU cycles since power-on. */
  cycles = 0;
  /** Set by a KIL opcode: the CPU stops fetching until reset. */
  jammed = false;
  /** Set by the NMI line's falling edge; cleared when the NMI is taken. */
  nmiLatch = false;
  /** The interrupt to take before the next instruction (polled at the end of the one before). */
  interruptDue = false;
  /** The page an OAM DMA ($4014 write) is waiting to copy, or -1. */
  dmaPage = -1;

  private bus: CpuBus;
  // Interrupt polling: the state seen at the end of the last cycle, and of the one before it.
  private pending = false;
  private prevPending = false;

  constructor(bus: CpuBus) {
    this.bus = bus;
  }

  // ---- cycles ----

  private poll(): void {
    this.prevPending = this.pending;
    this.pending = this.nmiLatch || (this.bus.irq !== 0 && (this.p & FLAG_I) === 0);
  }

  private rd(addr: number): number {
    this.cycles++;
    this.bus.cycle();
    const v = this.bus.read(addr);
    this.poll();
    return v;
  }

  private wr(addr: number, value: number): void {
    this.cycles++;
    this.bus.cycle();
    this.bus.write(addr, value);
    this.poll();
  }

  private push(v: number): void {
    this.wr(0x100 | this.s, v);
    this.s = (this.s - 1) & 0xff;
  }

  private pull(): number {
    this.s = (this.s + 1) & 0xff;
    return this.rd(0x100 | this.s);
  }

  private fetch(): number {
    const v = this.rd(this.pc);
    this.pc = (this.pc + 1) & 0xffff;
    return v;
  }

  // ---- reset and interrupts ----

  /** The reset sequence, at power-on and on the Reset button: A, X, Y kept; S drops by 3 with no
   * writes; I set; PC from $FFFC. Seven cycles. */
  reset(): void {
    this.jammed = false;
    this.interruptDue = false;
    this.nmiLatch = false;
    this.dmaPage = -1;
    this.rd(this.pc);
    this.rd(this.pc);
    for (let i = 0; i < 3; i++) {
      this.rd(0x100 | this.s);
      this.s = (this.s - 1) & 0xff;
    }
    this.p |= FLAG_I;
    const lo = this.rd(RESET_VECTOR);
    this.pc = lo | (this.rd(RESET_VECTOR + 1) << 8);
    this.pending = this.prevPending = false;
  }

  /** IRQ, NMI and BRK share one sequence; an NMI arriving before the vector is chosen takes it over. */
  private interrupt(brk: boolean): void {
    if (brk) {
      this.fetch();
    } else {
      this.rd(this.pc);
      this.rd(this.pc);
    }
    this.push(this.pc >> 8);
    this.push(this.pc & 0xff);
    const nmi = this.nmiLatch;
    if (nmi) this.nmiLatch = false;
    this.push(this.p | (brk ? FLAG_B : 0));
    this.p |= FLAG_I;
    const vector = nmi ? NMI_VECTOR : IRQ_VECTOR;
    const lo = this.rd(vector);
    this.pc = lo | (this.rd(vector + 1) << 8);
    // The first instruction of the handler always runs before another interrupt is taken.
    this.pending = this.prevPending = false;
  }

  // ---- stepping ----

  /** Run one instruction, or the interrupt or DMA due before it. */
  step(): void {
    if (this.dmaPage >= 0) {
      this.oamDma();
      return;
    }
    if (this.interruptDue) {
      this.interruptDue = false;
      this.interrupt(false);
      return;
    }
    if (this.jammed) {
      this.cycles++;
      this.bus.cycle();
      return;
    }
    this.execute(this.fetch());
    this.interruptDue = this.prevPending;
  }

  /** OAM DMA: one halt cycle, one more to align to a read cycle, then 256 read/write pairs. */
  private oamDma(): void {
    const page = this.dmaPage << 8;
    this.dmaPage = -1;
    this.idle();
    if (this.cycles & 1) this.idle();
    for (let i = 0; i < 256; i++) {
      const v = this.rd(page | i);
      this.wr(0x2004, v);
    }
  }

  private idle(): void {
    this.cycles++;
    this.bus.cycle();
    this.poll();
  }

  // ---- addressing ----

  /** The effective address for modes that read; indexed modes pay the page-cross cycle only when
   * the index carries into the high byte, with the dummy read at the un-carried address. */
  private addrRead(mode: number): number {
    switch (mode) {
      case ZP: return this.fetch();
      case ZPX: { const b = this.fetch(); this.rd(b); return (b + this.x) & 0xff; }
      case ZPY: { const b = this.fetch(); this.rd(b); return (b + this.y) & 0xff; }
      case ABS: { const lo = this.fetch(); return lo | (this.fetch() << 8); }
      case ABX: return this.indexed(this.absolute(), this.x, false);
      case ABY: return this.indexed(this.absolute(), this.y, false);
      case IZX: return this.indirectX();
      case IZY: return this.indexed(this.zpPointer(), this.y, false);
    }
    throw new Error(`no read address for mode ${mode}`);
  }

  /** The effective address for stores and read-modify-writes: indexed modes always take the extra
   * cycle and make the dummy read. */
  private addrWrite(mode: number): number {
    switch (mode) {
      case ABX: return this.indexed(this.absolute(), this.x, true);
      case ABY: return this.indexed(this.absolute(), this.y, true);
      case IZY: return this.indexed(this.zpPointer(), this.y, true);
    }
    return this.addrRead(mode);
  }

  private absolute(): number {
    const lo = this.fetch();
    return lo | (this.fetch() << 8);
  }

  private zpPointer(): number {
    const p = this.fetch();
    const lo = this.rd(p);
    return lo | (this.rd((p + 1) & 0xff) << 8);
  }

  private indirectX(): number {
    let p = this.fetch();
    this.rd(p);
    p = (p + this.x) & 0xff;
    const lo = this.rd(p);
    return lo | (this.rd((p + 1) & 0xff) << 8);
  }

  private indexed(base: number, index: number, always: boolean): number {
    const addr = (base + index) & 0xffff;
    if (always || ((base ^ addr) & 0x100)) this.rd((base & 0xff00) | (addr & 0xff));
    return addr;
  }

  private operand(mode: number): number {
    if (mode === IMM) return this.fetch();
    return this.rd(this.addrRead(mode));
  }

  // ---- ALU ----

  private zn(v: number): number {
    this.p = (this.p & ~(FLAG_Z | FLAG_N)) | (v === 0 ? FLAG_Z : 0) | (v & FLAG_N);
    return v;
  }

  private setFlag(flag: number, on: boolean): void {
    if (on) this.p |= flag;
    else this.p &= ~flag;
  }

  private adc(v: number): void {
    const sum = this.a + v + (this.p & FLAG_C);
    this.setFlag(FLAG_C, sum > 0xff);
    this.setFlag(FLAG_V, ((~(this.a ^ v) & (this.a ^ sum)) & 0x80) !== 0);
    this.a = this.zn(sum & 0xff);
  }

  private compare(reg: number, v: number): void {
    this.setFlag(FLAG_C, reg >= v);
    this.zn((reg - v) & 0xff);
  }

  private asl(v: number): number {
    this.setFlag(FLAG_C, (v & 0x80) !== 0);
    return this.zn((v << 1) & 0xff);
  }

  private lsr(v: number): number {
    this.setFlag(FLAG_C, (v & 1) !== 0);
    return this.zn(v >> 1);
  }

  private rol(v: number): number {
    const r = ((v << 1) | (this.p & FLAG_C)) & 0xff;
    this.setFlag(FLAG_C, (v & 0x80) !== 0);
    return this.zn(r);
  }

  private ror(v: number): number {
    const r = (v >> 1) | ((this.p & FLAG_C) << 7);
    this.setFlag(FLAG_C, (v & 1) !== 0);
    return this.zn(r);
  }

  /** Read-modify-write: read, write the old value back (the dummy write), write the new one. */
  private rmw(mode: number, f: (v: number) => number): number {
    if (mode === ACC) {
      this.rd(this.pc);
      return (this.a = f(this.a));
    }
    const addr = this.addrWrite(mode);
    const v = this.rd(addr);
    this.wr(addr, v);
    const r = f(v);
    this.wr(addr, r);
    return r;
  }

  private branch(taken: boolean): void {
    const off = this.fetch();
    if (!taken) return;
    this.rd(this.pc);
    const target = (this.pc + ((off ^ 0x80) - 0x80)) & 0xffff;
    if ((target ^ this.pc) & 0xff00) this.rd((this.pc & 0xff00) | (target & 0xff));
    this.pc = target;
  }

  /** SHY, SHX, AHX, TAS: store reg & (high byte of the base + 1). When the index carries, the high
   * byte of the address is replaced by the stored value (the chip's own glitch). */
  private storeHigh(base: number, index: number, reg: number): void {
    const addr = (base + index) & 0xffff;
    this.rd((base & 0xff00) | (addr & 0xff));
    const v = reg & (((base >> 8) + 1) & 0xff);
    const target = (base ^ addr) & 0x100 ? (v << 8) | (addr & 0xff) : addr;
    this.wr(target, v);
  }

  // ---- the instruction set ----

  private execute(op: number): void {
    const mode = MODE[op];
    switch (op) {
      // loads
      case 0xa9: case 0xa5: case 0xb5: case 0xad: case 0xbd: case 0xb9: case 0xa1: case 0xb1:
        this.a = this.zn(this.operand(mode)); break;
      case 0xa2: case 0xa6: case 0xb6: case 0xae: case 0xbe:
        this.x = this.zn(this.operand(mode)); break;
      case 0xa0: case 0xa4: case 0xb4: case 0xac: case 0xbc:
        this.y = this.zn(this.operand(mode)); break;
      case 0xa7: case 0xb7: case 0xaf: case 0xbf: case 0xa3: case 0xb3:  // LAX
        this.a = this.x = this.zn(this.operand(mode)); break;
      case 0xab:  // LAX #imm (LXA): A = X = (A | $FF) & imm on the RP2A03G
        this.a = this.x = this.zn((this.a | 0xff) & this.fetch()); break;

      // stores
      case 0x85: case 0x95: case 0x8d: case 0x9d: case 0x99: case 0x81: case 0x91:
        this.wr(this.addrWrite(mode), this.a); break;
      case 0x86: case 0x96: case 0x8e:
        this.wr(this.addrWrite(mode), this.x); break;
      case 0x84: case 0x94: case 0x8c:
        this.wr(this.addrWrite(mode), this.y); break;
      case 0x87: case 0x97: case 0x8f: case 0x83:  // SAX
        this.wr(this.addrWrite(mode), this.a & this.x); break;
      case 0x9c: this.storeHigh(this.absolute(), this.x, this.y); break;            // SHY abs,X
      case 0x9e: this.storeHigh(this.absolute(), this.y, this.x); break;            // SHX abs,Y
      case 0x9f: this.storeHigh(this.absolute(), this.y, this.a & this.x); break;   // AHX abs,Y
      case 0x93: this.storeHigh(this.zpPointer(), this.y, this.a & this.x); break;  // AHX (zp),Y
      case 0x9b:  // TAS abs,Y
        this.s = this.a & this.x;
        this.storeHigh(this.absolute(), this.y, this.s); break;

      // logic and arithmetic
      case 0x09: case 0x05: case 0x15: case 0x0d: case 0x1d: case 0x19: case 0x01: case 0x11:
        this.a = this.zn(this.a | this.operand(mode)); break;
      case 0x29: case 0x25: case 0x35: case 0x2d: case 0x3d: case 0x39: case 0x21: case 0x31:
        this.a = this.zn(this.a & this.operand(mode)); break;
      case 0x49: case 0x45: case 0x55: case 0x4d: case 0x5d: case 0x59: case 0x41: case 0x51:
        this.a = this.zn(this.a ^ this.operand(mode)); break;
      case 0x69: case 0x65: case 0x75: case 0x6d: case 0x7d: case 0x79: case 0x61: case 0x71:
        this.adc(this.operand(mode)); break;
      case 0xe9: case 0xeb: case 0xe5: case 0xf5: case 0xed: case 0xfd: case 0xf9: case 0xe1: case 0xf1:
        this.adc(this.operand(mode) ^ 0xff); break;
      case 0xc9: case 0xc5: case 0xd5: case 0xcd: case 0xdd: case 0xd9: case 0xc1: case 0xd1:
        this.compare(this.a, this.operand(mode)); break;
      case 0xe0: case 0xe4: case 0xec:
        this.compare(this.x, this.operand(mode)); break;
      case 0xc0: case 0xc4: case 0xcc:
        this.compare(this.y, this.operand(mode)); break;
      case 0x24: case 0x2c: {
        const v = this.operand(mode);
        this.p = (this.p & ~(FLAG_Z | FLAG_V | FLAG_N)) | ((this.a & v) === 0 ? FLAG_Z : 0) | (v & 0xc0);
        break;
      }
      case 0x0b: case 0x2b:  // ANC
        this.a = this.zn(this.a & this.fetch());
        this.setFlag(FLAG_C, (this.a & 0x80) !== 0); break;
      case 0x4b:  // ALR
        this.a = this.lsr(this.a & this.fetch()); break;
      case 0x6b: {  // ARR
        const v = this.a & this.fetch();
        this.a = this.zn((v >> 1) | ((this.p & FLAG_C) << 7));
        this.setFlag(FLAG_C, (this.a & 0x40) !== 0);
        this.setFlag(FLAG_V, (((this.a >> 6) ^ (this.a >> 5)) & 1) !== 0);
        break;
      }
      case 0xcb: {  // AXS
        const t = (this.a & this.x) - this.fetch();
        this.setFlag(FLAG_C, t >= 0);
        this.x = this.zn(t & 0xff);
        break;
      }
      case 0x8b:  // XAA: unstable on hardware; the common model, A = (A | $EE) & X & imm
        this.a = this.zn((this.a | 0xee) & this.x & this.fetch()); break;
      case 0xbb: {  // LAS
        const v = this.operand(mode) & this.s;
        this.a = this.x = this.s = this.zn(v);
        break;
      }

      // shifts, increments and their unofficial combinations
      case 0x0a: case 0x06: case 0x16: case 0x0e: case 0x1e:
        this.rmw(mode, (v) => this.asl(v)); break;
      case 0x4a: case 0x46: case 0x56: case 0x4e: case 0x5e:
        this.rmw(mode, (v) => this.lsr(v)); break;
      case 0x2a: case 0x26: case 0x36: case 0x2e: case 0x3e:
        this.rmw(mode, (v) => this.rol(v)); break;
      case 0x6a: case 0x66: case 0x76: case 0x6e: case 0x7e:
        this.rmw(mode, (v) => this.ror(v)); break;
      case 0xe6: case 0xf6: case 0xee: case 0xfe:
        this.rmw(mode, (v) => this.zn((v + 1) & 0xff)); break;
      case 0xc6: case 0xd6: case 0xce: case 0xde:
        this.rmw(mode, (v) => this.zn((v - 1) & 0xff)); break;
      case 0x07: case 0x17: case 0x0f: case 0x1f: case 0x1b: case 0x03: case 0x13:  // SLO
        this.rmw(mode, (v) => { const r = this.asl(v); this.a = this.zn(this.a | r); return r; }); break;
      case 0x27: case 0x37: case 0x2f: case 0x3f: case 0x3b: case 0x23: case 0x33:  // RLA
        this.rmw(mode, (v) => { const r = this.rol(v); this.a = this.zn(this.a & r); return r; }); break;
      case 0x47: case 0x57: case 0x4f: case 0x5f: case 0x5b: case 0x43: case 0x53:  // SRE
        this.rmw(mode, (v) => { const r = this.lsr(v); this.a = this.zn(this.a ^ r); return r; }); break;
      case 0x67: case 0x77: case 0x6f: case 0x7f: case 0x7b: case 0x63: case 0x73:  // RRA
        this.rmw(mode, (v) => { const r = this.ror(v); this.adc(r); return r; }); break;
      case 0xc7: case 0xd7: case 0xcf: case 0xdf: case 0xdb: case 0xc3: case 0xd3:  // DCP
        this.rmw(mode, (v) => { const r = (v - 1) & 0xff; this.compare(this.a, r); return r; }); break;
      case 0xe7: case 0xf7: case 0xef: case 0xff: case 0xfb: case 0xe3: case 0xf3:  // ISB
        this.rmw(mode, (v) => { const r = (v + 1) & 0xff; this.adc(r ^ 0xff); return r; }); break;

      // registers and flags (two cycles: the second is a dummy read of the next byte)
      case 0xaa: this.rd(this.pc); this.x = this.zn(this.a); break;
      case 0xa8: this.rd(this.pc); this.y = this.zn(this.a); break;
      case 0x8a: this.rd(this.pc); this.a = this.zn(this.x); break;
      case 0x98: this.rd(this.pc); this.a = this.zn(this.y); break;
      case 0xba: this.rd(this.pc); this.x = this.zn(this.s); break;
      case 0x9a: this.rd(this.pc); this.s = this.x; break;
      case 0xe8: this.rd(this.pc); this.x = this.zn((this.x + 1) & 0xff); break;
      case 0xc8: this.rd(this.pc); this.y = this.zn((this.y + 1) & 0xff); break;
      case 0xca: this.rd(this.pc); this.x = this.zn((this.x - 1) & 0xff); break;
      case 0x88: this.rd(this.pc); this.y = this.zn((this.y - 1) & 0xff); break;
      case 0x18: this.rd(this.pc); this.p &= ~FLAG_C; break;
      case 0x38: this.rd(this.pc); this.p |= FLAG_C; break;
      case 0x58: this.rd(this.pc); this.p &= ~FLAG_I; break;
      case 0x78: this.rd(this.pc); this.p |= FLAG_I; break;
      case 0xb8: this.rd(this.pc); this.p &= ~FLAG_V; break;
      case 0xd8: this.rd(this.pc); this.p &= ~FLAG_D; break;
      case 0xf8: this.rd(this.pc); this.p |= FLAG_D; break;

      // no-ops: the reading ones read their operand, page-cross cycle included
      case 0xea: case 0x1a: case 0x3a: case 0x5a: case 0x7a: case 0xda: case 0xfa:
        this.rd(this.pc); break;
      case 0x80: case 0x82: case 0x89: case 0xc2: case 0xe2:
      case 0x04: case 0x44: case 0x64: case 0x14: case 0x34: case 0x54: case 0x74: case 0xd4: case 0xf4:
      case 0x0c: case 0x1c: case 0x3c: case 0x5c: case 0x7c: case 0xdc: case 0xfc:
        this.operand(mode); break;

      // the stack
      case 0x48: this.rd(this.pc); this.push(this.a); break;
      case 0x08: this.rd(this.pc); this.push(this.p | FLAG_B); break;
      case 0x68: this.rd(this.pc); this.rd(0x100 | this.s); this.a = this.zn(this.pull()); break;
      case 0x28: this.rd(this.pc); this.rd(0x100 | this.s); this.p = (this.pull() & ~FLAG_B) | FLAG_U; break;

      // jumps, calls and returns
      case 0x4c: this.pc = this.absolute(); break;
      case 0x6c: {
        const ptr = this.absolute();
        const lo = this.rd(ptr);
        // The page-wrap bug: the high byte comes from the same page as the low byte.
        this.pc = lo | (this.rd((ptr & 0xff00) | ((ptr + 1) & 0xff)) << 8);
        break;
      }
      case 0x20: {
        const lo = this.fetch();
        this.rd(0x100 | this.s);
        this.push(this.pc >> 8);
        this.push(this.pc & 0xff);
        this.pc = lo | (this.rd(this.pc) << 8);
        break;
      }
      case 0x60: {
        this.rd(this.pc);
        this.rd(0x100 | this.s);
        const lo = this.pull();
        this.pc = lo | (this.pull() << 8);
        this.rd(this.pc);
        this.pc = (this.pc + 1) & 0xffff;
        break;
      }
      case 0x40: {
        this.rd(this.pc);
        this.rd(0x100 | this.s);
        this.p = (this.pull() & ~FLAG_B) | FLAG_U;
        const lo = this.pull();
        this.pc = lo | (this.pull() << 8);
        break;
      }
      case 0x00: this.interrupt(true); break;

      // branches
      case 0x10: this.branch((this.p & FLAG_N) === 0); break;
      case 0x30: this.branch((this.p & FLAG_N) !== 0); break;
      case 0x50: this.branch((this.p & FLAG_V) === 0); break;
      case 0x70: this.branch((this.p & FLAG_V) !== 0); break;
      case 0x90: this.branch((this.p & FLAG_C) === 0); break;
      case 0xb0: this.branch((this.p & FLAG_C) !== 0); break;
      case 0xd0: this.branch((this.p & FLAG_Z) === 0); break;
      case 0xf0: this.branch((this.p & FLAG_Z) !== 0); break;

      // KIL: the CPU locks up until reset
      default:
        this.jammed = true;
        this.pc = (this.pc - 1) & 0xffff;
        break;
    }
  }
}
