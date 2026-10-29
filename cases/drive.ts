// The frozen driver the cases use to run the core API (see CONTRACT.md).
// Usage: node cases/drive.ts '<json request>'   → one JSON line on stdout.
// It never trusts the build to grade itself: the $6000 protocol is read here.
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';

const out = (o: unknown) => { process.stdout.write(JSON.stringify(o) + '\n'); };

async function main() {
  const req = JSON.parse(process.argv[2] ?? '{}');
  let core: any;
  try {
    core = await import('../src/core/index.ts');
  } catch (e: any) {
    out({ ok: false, stage: 'import', error: String(e?.message ?? e) });
    return;
  }
  const rom = new Uint8Array(readFileSync(req.rom));
  let nes: any;
  try {
    nes = new core.NES(rom);
  } catch (e: any) {
    const isRomError = typeof core.RomError === 'function' && e instanceof core.RomError;
    out({ ok: false, stage: 'load', romError: isRomError, error: String(e?.message ?? e) });
    return;
  }
  const peek = (a: number) => nes.peek(a) & 0xff;
  const hash = (from: number, to: number) => {
    const h = createHash('sha256');
    const b = new Uint8Array(to - from + 1);
    for (let a = from; a <= to; a++) b[a - from] = peek(a);
    h.update(b);
    return h.digest('hex');
  };
  try {
    if (req.mode === 'load') {
      out({ ok: true, stage: 'load' });
    } else if (req.mode === 'power') {
      // State straight after power-on, before any frame runs.
      const ram: number[] = [];
      for (let a = 0; a < 0x800; a++) ram.push(peek(a));
      out({ ok: true, ram });
    } else if (req.mode === 'blargg') {
      const maxFrames: number = req.maxFrames ?? 3600;
      let frames = 0, resets = 0, resetAt = -1;
      const text = () => {
        let s = '';
        for (let a = 0x6004; a < 0x6004 + 2048; a++) {
          const c = peek(a);
          if (c === 0) break;
          s += String.fromCharCode(c);
        }
        return s;
      };
      const signed = () => peek(0x6001) === 0xde && peek(0x6002) === 0xb0 && peek(0x6003) === 0x61;
      while (frames < maxFrames) {
        nes.runFrame();
        frames++;
        if (!signed()) continue;
        const st = peek(0x6000);
        if (st === 0x80) continue;
        if (st === 0x81) {
          if (resetAt < 0) resetAt = frames + 6;
          else if (frames >= resetAt) { nes.reset(); resets++; resetAt = -1; }
          continue;
        }
        out({ ok: true, done: true, signature: true, status: st, text: text(), frames, resets,
              ram: hash(0x0000, 0x07ff), prgRam: hash(0x6000, 0x7fff) });
        return;
      }
      out({ ok: true, done: false, signature: signed(), status: peek(0x6000), text: text(), frames, resets });
    } else if (req.mode === 'bench') {
      const frames: number = req.frames ?? 1800;
      const t0 = performance.now();
      for (let i = 0; i < frames; i++) nes.runFrame();
      out({ ok: true, frames, ms: performance.now() - t0 });
    } else {
      out({ ok: false, stage: 'request', error: `unknown mode ${req.mode}` });
    }
  } catch (e: any) {
    out({ ok: false, stage: 'run', error: String(e?.stack ?? e) });
  }
}

main();
