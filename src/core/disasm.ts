// A nestest.log-style trace line for the instruction at the CPU's PC (see cases/CONTRACT.md).
import type { NES } from './nes.ts';
import { NAME, MODE, OFFICIAL, MODE_LENGTH, IMP, ACC, IMM, ZP, ZPX, ZPY, ABS, ABX, ABY, IND, IZX, IZY, REL } from './opcodes.ts';

const hex2 = (v: number) => v.toString(16).toUpperCase().padStart(2, '0');
const hex4 = (v: number) => v.toString(16).toUpperCase().padStart(4, '0');

function operand(mode: number, pc: number, b1: number, b2: number): string {
  const w = b1 | (b2 << 8);
  switch (mode) {
    case IMP: return '';
    case ACC: return 'A';
    case IMM: return `#$${hex2(b1)}`;
    case ZP: return `$${hex2(b1)}`;
    case ZPX: return `$${hex2(b1)},X`;
    case ZPY: return `$${hex2(b1)},Y`;
    case ABS: return `$${hex4(w)}`;
    case ABX: return `$${hex4(w)},X`;
    case ABY: return `$${hex4(w)},Y`;
    case IND: return `($${hex4(w)})`;
    case IZX: return `($${hex2(b1)},X)`;
    case IZY: return `($${hex2(b1)}),Y`;
    case REL: return `$${hex4((pc + 2 + ((b1 ^ 0x80) - 0x80)) & 0xffff)}`;
  }
  return '';
}

/** One line: PC, the instruction bytes and the disassembly (columns 0-15 as nestest.log), then
 * the registers, the PPU scanline/dot and the CPU cycle count, all read without side effects. */
export function traceLine(nes: NES): string {
  const { cpu, ppu } = nes;
  const pc = cpu.pc;
  const op = nes.peek(pc);
  const mode = MODE[op];
  const len = MODE_LENGTH[mode];
  const bytes = [op];
  for (let i = 1; i < len; i++) bytes.push(nes.peek((pc + i) & 0xffff));
  const text = `${NAME[op]} ${operand(mode, pc, bytes[1] ?? 0, bytes[2] ?? 0)}`.trimEnd();
  const head = `${hex4(pc)}  ${bytes.map(hex2).join(' ').padEnd(9)}${OFFICIAL[op] ? ' ' : '*'}${text.padEnd(32)}`;
  const regs = `A:${hex2(cpu.a)} X:${hex2(cpu.x)} Y:${hex2(cpu.y)} P:${hex2(cpu.p)} SP:${hex2(cpu.s)}`;
  const ppuPos = `PPU:${String(ppu.scanline).padStart(3)},${String(ppu.dot).padStart(3)}`;
  return `${head}${regs} ${ppuPos} CYC:${cpu.cycles}`;
}
