// The headless command line (see cases/CONTRACT.md):
//   node src/cli/nes.ts trace <rom.nes> [--pc <hex>] [--lines <n>] [--mem <hexaddr>:<n>]...
//   node src/cli/nes.ts run   <rom.nes> [--frames <n>] [--mem <hexaddr>:<n>]...
import { readFileSync } from 'node:fs';
import { NES, RomError } from '../core/index.ts';
import { traceLine } from '../core/disasm.ts';

const USAGE = `usage:
  node src/cli/nes.ts trace <rom.nes> [--pc <hex>] [--lines <n>] [--mem <hexaddr>:<n>]...
  node src/cli/nes.ts run   <rom.nes> [--frames <n>] [--mem <hexaddr>:<n>]...`;

interface Args {
  command: string;
  rom: string;
  pc?: number;
  lines: number;
  frames: number;
  mem: Array<[number, number]>;
}

function fail(message: string, code = 2): never {
  process.stderr.write(`${message}\n`);
  process.exit(code);
}

function number(flag: string, text: string, radix: number): number {
  const digits = radix === 16 ? /^(?:\$|0x)?([0-9a-f]+)$/i : /^([0-9]+)$/;
  const m = digits.exec(text);
  if (!m) fail(`${flag}: not a ${radix === 16 ? 'hex ' : ''}number: ${text}`);
  return parseInt(m[1], radix);
}

function parseArgs(argv: string[]): Args {
  const [command, rom, ...rest] = argv;
  if (!command || !rom || !['trace', 'run'].includes(command)) fail(USAGE);
  const args: Args = { command, rom, lines: 100, frames: 60, mem: [] };
  for (let i = 0; i < rest.length; i++) {
    const flag = rest[i];
    const value = rest[++i];
    if (value === undefined) fail(`${flag} needs a value\n${USAGE}`);
    switch (flag) {
      case '--pc': args.pc = number(flag, value, 16) & 0xffff; break;
      case '--lines': args.lines = number(flag, value, 10); break;
      case '--frames': args.frames = number(flag, value, 10); break;
      case '--mem': {
        const [addr, n] = value.split(':');
        args.mem.push([number(flag, addr, 16) & 0xffff, number(flag, n ?? '1', 10)]);
        break;
      }
      default: fail(`unknown option ${flag}\n${USAGE}`);
    }
  }
  return args;
}

function main(): void {
  // A reader that stops early (`| head`, quitting `less`) isn't an error.
  process.stdout.on('error', (e: NodeJS.ErrnoException) => {
    if (e.code === 'EPIPE') process.exit(0);
    throw e;
  });
  const args = parseArgs(process.argv.slice(2));
  let data: Uint8Array;
  try {
    data = new Uint8Array(readFileSync(args.rom));
  } catch (e) {
    fail(`${args.rom}: can't read the file: ${(e as NodeJS.ErrnoException).code ?? String(e)}`);
  }
  let nes: NES;
  try {
    nes = new NES(data);
  } catch (e) {
    if (e instanceof RomError) fail(`${args.rom}: ${e.message}`);
    throw e;
  }
  const out: string[] = [];
  if (args.command === 'trace') {
    if (args.pc !== undefined) {
      // nestest's automation mode: start at the given PC with the state reset leaves (P=24 SP=FD).
      nes.cpu.pc = args.pc;
      nes.cpu.p = 0x24;
    }
    for (let n = 0; n < args.lines; n++) {
      while (nes.cpu.interruptDue || nes.cpu.dmaPage >= 0) nes.step();
      out.push(traceLine(nes));
      nes.step();
    }
  } else {
    for (let f = 0; f < args.frames; f++) nes.runFrame();
  }
  for (const [addr, n] of args.mem) {
    const bytes: string[] = [];
    for (let i = 0; i < n; i++) bytes.push(nes.peek((addr + i) & 0xffff).toString(16).toUpperCase().padStart(2, '0'));
    out.push(`MEM ${addr.toString(16).toUpperCase().padStart(4, '0')}: ${bytes.join(' ')}`);
  }
  process.stdout.write(out.join('\n') + '\n');
}

main();
