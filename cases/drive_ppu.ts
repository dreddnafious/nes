// I2's frozen driver for the core API's picture (see CONTRACT-I2.md).
// Usage: node cases/drive_ppu.ts '<json request>'   -> one JSON line on stdout.
// It only runs frames and hands back what the core drew; every judgement is made by the cases.
//
// Requests (all take "rom", a path, and optionally "peek": [[addr, count], ...] read after the run):
//   {"mode": "frames", "frames": N, "hashFrom": k?, "frame": false?}
//       run N frames; return the last frame (unless "frame" is false), and the SHA-256 of every
//       frame from frame k on.
//   {"mode": "screen", "maxFrames": N, "until": {"addr": a, "seq": [v1, v2, ...]}, "settle": s}
//       run frames until the byte at a (read once per frame) has shown v1, then v2, ... in that order,
//       then s more frames; return the last frame. done is false if the budget runs out first.
//   {"mode": "bench", "frames": N}
//       time N frames; return the wall time and the last frame.
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';

const W = 256, H = 240;
const out = (o: unknown) => { process.stdout.write(JSON.stringify(o) + '\n'); };

class FrameError extends Error {}

/** The core's last frame as little-endian 16-bit bytes, after checking its shape. */
function grab(nes: any): Buffer {
  if (typeof nes.frameBuffer !== 'function') throw new FrameError('the core has no frameBuffer() method');
  const fb = nes.frameBuffer();
  if (!(fb instanceof Uint16Array)) throw new FrameError(`frameBuffer() returned ${Object.prototype.toString.call(fb)}, not a Uint16Array`);
  if (fb.length !== W * H) throw new FrameError(`frameBuffer() has ${fb.length} values, not ${W * H}`);
  const b = Buffer.alloc(W * H * 2);
  for (let i = 0; i < W * H; i++) b.writeUInt16LE(fb[i], i * 2);
  return b;
}

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
  const peeks = () => {
    const o: Record<string, number[]> = {};
    for (const [addr, n] of req.peek ?? []) {
      const v: number[] = [];
      for (let i = 0; i < n; i++) v.push(nes.peek((addr + i) & 0xffff) & 0xff);
      o[addr.toString(16).toUpperCase().padStart(4, '0')] = v;
    }
    return o;
  };
  try {
    if (req.mode === 'frames') {
      const n: number = req.frames ?? 1;
      const hashFrom: number | undefined = req.hashFrom;
      const hashes: string[] = [];
      for (let f = 1; f <= n; f++) {
        nes.runFrame();
        if (hashFrom !== undefined && f >= hashFrom) {
          hashes.push(createHash('sha256').update(grab(nes)).digest('hex'));
        }
      }
      const frame = req.frame === false ? null : grab(nes).toString('base64');
      out({ ok: true, frames: n, frame, hashes, peek: peeks() });
    } else if (req.mode === 'screen') {
      const maxFrames: number = req.maxFrames ?? 1200;
      const settle: number = req.settle ?? 2;
      const { addr, seq } = req.until;
      let frames = 0, matched = 0;
      while (frames < maxFrames && matched < seq.length) {
        nes.runFrame();
        frames++;
        if ((nes.peek(addr) & 0xff) === seq[matched]) matched++;
      }
      const done = matched === seq.length;
      if (done) for (let i = 0; i < settle; i++) { nes.runFrame(); frames++; }
      out({ ok: true, done, frames, frame: grab(nes).toString('base64'), peek: peeks() });
    } else if (req.mode === 'bench') {
      const n: number = req.frames ?? 1800;
      const t0 = performance.now();
      for (let f = 0; f < n; f++) nes.runFrame();
      const ms = performance.now() - t0;
      out({ ok: true, frames: n, ms, frame: grab(nes).toString('base64') });
    } else {
      out({ ok: false, stage: 'request', error: `unknown mode ${req.mode}` });
    }
  } catch (e: any) {
    out({ ok: false, stage: e instanceof FrameError ? 'frame' : 'run', error: String(e?.stack ?? e) });
  }
}

main();
