// The 6502's 256 opcodes: mnemonic, addressing mode and whether it's official.
// Unofficial mnemonics follow nestest.log's (NOP, LAX, SAX, SBC, DCP, ISB, SLO, RLA, SRE, RRA) and
// NESdev's for the rest (ANC, ALR, ARR, XAA, AXS, AHX, TAS, SHY, SHX, LAS, KIL).

export const IMP = 0;   // implied
export const ACC = 1;   // accumulator
export const IMM = 2;   // #$nn
export const ZP = 3;    // $nn
export const ZPX = 4;   // $nn,X
export const ZPY = 5;   // $nn,Y
export const ABS = 6;   // $nnnn
export const ABX = 7;   // $nnnn,X
export const ABY = 8;   // $nnnn,Y
export const IND = 9;   // ($nnnn)
export const IZX = 10;  // ($nn,X)
export const IZY = 11;  // ($nn),Y
export const REL = 12;  // branch offset

/** Instruction length in bytes, by mode. */
export const MODE_LENGTH = [1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 2, 2, 2];

const M: Record<string, number> = {
  imp: IMP, acc: ACC, imm: IMM, zp: ZP, zpx: ZPX, zpy: ZPY, abs: ABS, abx: ABX, aby: ABY,
  ind: IND, izx: IZX, izy: IZY, rel: REL,
};

// One row per high nibble; a leading '*' marks an unofficial opcode.
const ROWS = [
  'BRK imp|ORA izx|*KIL imp|*SLO izx|*NOP zp|ORA zp|ASL zp|*SLO zp|PHP imp|ORA imm|ASL acc|*ANC imm|*NOP abs|ORA abs|ASL abs|*SLO abs',
  'BPL rel|ORA izy|*KIL imp|*SLO izy|*NOP zpx|ORA zpx|ASL zpx|*SLO zpx|CLC imp|ORA aby|*NOP imp|*SLO aby|*NOP abx|ORA abx|ASL abx|*SLO abx',
  'JSR abs|AND izx|*KIL imp|*RLA izx|BIT zp|AND zp|ROL zp|*RLA zp|PLP imp|AND imm|ROL acc|*ANC imm|BIT abs|AND abs|ROL abs|*RLA abs',
  'BMI rel|AND izy|*KIL imp|*RLA izy|*NOP zpx|AND zpx|ROL zpx|*RLA zpx|SEC imp|AND aby|*NOP imp|*RLA aby|*NOP abx|AND abx|ROL abx|*RLA abx',
  'RTI imp|EOR izx|*KIL imp|*SRE izx|*NOP zp|EOR zp|LSR zp|*SRE zp|PHA imp|EOR imm|LSR acc|*ALR imm|JMP abs|EOR abs|LSR abs|*SRE abs',
  'BVC rel|EOR izy|*KIL imp|*SRE izy|*NOP zpx|EOR zpx|LSR zpx|*SRE zpx|CLI imp|EOR aby|*NOP imp|*SRE aby|*NOP abx|EOR abx|LSR abx|*SRE abx',
  'RTS imp|ADC izx|*KIL imp|*RRA izx|*NOP zp|ADC zp|ROR zp|*RRA zp|PLA imp|ADC imm|ROR acc|*ARR imm|JMP ind|ADC abs|ROR abs|*RRA abs',
  'BVS rel|ADC izy|*KIL imp|*RRA izy|*NOP zpx|ADC zpx|ROR zpx|*RRA zpx|SEI imp|ADC aby|*NOP imp|*RRA aby|*NOP abx|ADC abx|ROR abx|*RRA abx',
  '*NOP imm|STA izx|*NOP imm|*SAX izx|STY zp|STA zp|STX zp|*SAX zp|DEY imp|*NOP imm|TXA imp|*XAA imm|STY abs|STA abs|STX abs|*SAX abs',
  'BCC rel|STA izy|*KIL imp|*AHX izy|STY zpx|STA zpx|STX zpy|*SAX zpy|TYA imp|STA aby|TXS imp|*TAS aby|*SHY abx|STA abx|*SHX aby|*AHX aby',
  'LDY imm|LDA izx|LDX imm|*LAX izx|LDY zp|LDA zp|LDX zp|*LAX zp|TAY imp|LDA imm|TAX imp|*LAX imm|LDY abs|LDA abs|LDX abs|*LAX abs',
  'BCS rel|LDA izy|*KIL imp|*LAX izy|LDY zpx|LDA zpx|LDX zpy|*LAX zpy|CLV imp|LDA aby|TSX imp|*LAS aby|LDY abx|LDA abx|LDX aby|*LAX aby',
  'CPY imm|CMP izx|*NOP imm|*DCP izx|CPY zp|CMP zp|DEC zp|*DCP zp|INY imp|CMP imm|DEX imp|*AXS imm|CPY abs|CMP abs|DEC abs|*DCP abs',
  'BNE rel|CMP izy|*KIL imp|*DCP izy|*NOP zpx|CMP zpx|DEC zpx|*DCP zpx|CLD imp|CMP aby|*NOP imp|*DCP aby|*NOP abx|CMP abx|DEC abx|*DCP abx',
  'CPX imm|SBC izx|*NOP imm|*ISB izx|CPX zp|SBC zp|INC zp|*ISB zp|INX imp|SBC imm|NOP imp|*SBC imm|CPX abs|SBC abs|INC abs|*ISB abs',
  'BEQ rel|SBC izy|*KIL imp|*ISB izy|*NOP zpx|SBC zpx|INC zpx|*ISB zpx|SED imp|SBC aby|*NOP imp|*ISB aby|*NOP abx|SBC abx|INC abx|*ISB abx',
];

export const NAME: string[] = [];
export const MODE = new Uint8Array(256);
export const OFFICIAL: boolean[] = [];

ROWS.forEach((row, hi) => {
  row.split('|').forEach((cell, lo) => {
    const op = (hi << 4) | lo;
    const unofficial = cell.startsWith('*');
    const [name, mode] = (unofficial ? cell.slice(1) : cell).split(' ');
    NAME[op] = name;
    MODE[op] = M[mode];
    OFFICIAL[op] = !unofficial;
  });
});
