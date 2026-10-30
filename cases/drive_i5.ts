// I5's frozen driver for long runs of the core API (see CONTRACT-I5.md).
// Usage: node --expose-gc cases/drive_i5.ts '<json request>'   -> one JSON line on stdout.
// It only runs frames and hands back what the core drew, what it read and how long it took; every
// judgement is made by the cases.
//
// {"rom": p, "frames": N, "hash": [[from, to], ...], "heapAt": [f, ...]?, "peek": [[addr, n], ...]?,
//  "samples": true?}
//   Runs N frames. After frame f (1-based) for each f in a hash range [from, to], records the SHA-256 of
//   the core's frame (little-endian 16-bit values, as drive_ppu.ts hashes it). After each frame in
//   heapAt, runs the garbage collector (node --expose-gc) and records process.memoryUsage().heapUsed.
//   With "samples", calls audioSamples() after every frame and counts them (as a page would). Returns
//   the hashes by frame, the heap readings, the peeks after the last frame, the sample count and the
//   wall time of the whole run.
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';

const W = 256, H = 240;
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
  const gc = (globalThis as any).gc as (() => void) | undefined;
  if ((req.heapAt ?? []).length && typeof gc !== 'function') {
    out({ ok: false, stage: 'gc', error: 'run with node --expose-gc' });
    return;
  }
  let nes: any;
  try {
    nes = new core.NES(new Uint8Array(readFileSync(req.rom)));
  } catch (e: any) {
    const isRomError = typeof core.RomError === 'function' && e instanceof core.RomError;
    out({ ok: false, stage: 'load', romError: isRomError, error: String(e?.message ?? e) });
    return;
  }
  const want = new Set<number>();
  for (const [a, b] of req.hash ?? []) for (let f = a; f <= b; f++) want.add(f);
  const heapAt = new Set<number>(req.heapAt ?? []);
  const hashes: Record<string, string> = {};
  const heap: Record<string, number> = {};
  const buf = Buffer.alloc(W * H * 2);
  let samples = 0;
  const t0 = performance.now();
  try {
    for (let f = 1; f <= req.frames; f++) {
      nes.runFrame();
      if (req.samples) samples += nes.audioSamples().length;
      if (want.has(f)) {
        const fb: Uint16Array = nes.frameBuffer();
        for (let i = 0; i < W * H; i++) buf.writeUInt16LE(fb[i], i * 2);
        hashes[String(f)] = createHash('sha256').update(buf).digest('hex');
      }
      if (heapAt.has(f)) {
        gc!();
        gc!();
        heap[String(f)] = process.memoryUsage().heapUsed;
      }
    }
  } catch (e: any) {
    out({ ok: false, stage: 'run', error: String(e?.stack ?? e) });
    return;
  }
  const secs = (performance.now() - t0) / 1000;
  const peek: Record<string, number[]> = {};
  for (const [addr, n] of req.peek ?? []) {
    const v: number[] = [];
    for (let i = 0; i < n; i++) v.push(nes.peek((addr + i) & 0xffff) & 0xff);
    peek[addr.toString(16).toUpperCase().padStart(4, '0')] = v;
  }
  out({ ok: true, frames: req.frames, secs, hashes, heap, peek, samples });
}

main();
