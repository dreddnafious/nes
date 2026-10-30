// I4's frozen driver for the core's sound (see CONTRACT-I4.md).
// Usage: node cases/drive_apu.ts '<json request>'   -> one JSON line on stdout.
// It only runs frames and hands back the samples the core made; every judgement is made by the cases.
//
// Requests (all take "rom", a path):
//   {"mode": "audio", "frames": N, "rate": R?, "take": k?, "buttons": [[f, bits], ...]?,
//    "reset": [f, ...]?, "peekEach": [[addr, count], ...]?}
//       setSampleRate(R) before the first frame (if R is given); before frame f (1-based) the
//       buttons on controller 1 become bits (setButtons(0, bits)) and, for f in "reset", reset() is
//       pressed; audioSamples() is called after every k-th frame (default 1) and after the last.
//       Returns the samples (base64 of little-endian float32), the length of each audioSamples()
//       call, and (if peekEach) the bytes read after every frame.
//   {"mode": "bench", "frames": N, "rate": R?}
//       times N frames with audioSamples() called after each; returns the wall time and the count.
import { readFileSync } from 'node:fs';

const out = (o: unknown) => { process.stdout.write(JSON.stringify(o) + '\n'); };

class AudioError extends Error {}

/** One audioSamples() call, after checking its shape. */
function take(nes: any): Float32Array {
  if (typeof nes.audioSamples !== 'function') throw new AudioError('the core has no audioSamples() method');
  const s = nes.audioSamples();
  if (!(s instanceof Float32Array)) {
    throw new AudioError(`audioSamples() returned ${Object.prototype.toString.call(s)}, not a Float32Array`);
  }
  return s;
}

function setRate(nes: any, rate: number | undefined): void {
  if (rate === undefined) return;
  if (typeof nes.setSampleRate !== 'function') throw new AudioError('the core has no setSampleRate() method');
  nes.setSampleRate(rate);
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
  try {
    if (req.mode === 'audio') {
      const n: number = req.frames ?? 60;
      const every: number = req.take ?? 1;
      const buttons = new Map<number, number>((req.buttons ?? []).map((b: number[]) => [b[0], b[1]]));
      const resets = new Set<number>(req.reset ?? []);
      setRate(nes, req.rate);
      const chunks: Float32Array[] = [];
      const counts: number[] = [];
      const peeks: number[][] = [];
      for (let f = 1; f <= n; f++) {
        if (buttons.has(f)) {
          if (typeof nes.setButtons !== 'function') throw new AudioError('the core has no setButtons() method');
          nes.setButtons(0, buttons.get(f));
        }
        if (resets.has(f)) nes.reset();
        nes.runFrame();
        if (f % every === 0 || f === n) {
          const s = take(nes);
          chunks.push(s.slice());
          counts.push(s.length);
        }
        if (req.peekEach) {
          const row: number[] = [];
          for (const [addr, c] of req.peekEach) for (let i = 0; i < c; i++) row.push(nes.peek((addr + i) & 0xffff) & 0xff);
          peeks.push(row);
        }
      }
      const total = counts.reduce((a, b) => a + b, 0);
      const all = new Float32Array(total);
      let at = 0;
      for (const c of chunks) { all.set(c, at); at += c.length; }
      const b64 = Buffer.from(all.buffer, all.byteOffset, all.byteLength).toString('base64');
      out({ ok: true, frames: n, rate: req.rate ?? null, counts, samples: b64, peeks: req.peekEach ? peeks : null });
    } else if (req.mode === 'bench') {
      const n: number = req.frames ?? 1800;
      setRate(nes, req.rate);
      let total = 0;
      const t0 = performance.now();
      for (let f = 0; f < n; f++) {
        nes.runFrame();
        total += take(nes).length;
      }
      out({ ok: true, frames: n, ms: performance.now() - t0, samples: total });
    } else {
      out({ ok: false, stage: 'request', error: `unknown mode ${req.mode}` });
    }
  } catch (e: any) {
    out({ ok: false, stage: e instanceof AudioError ? 'audio' : 'run', error: String(e?.stack ?? e) });
  }
}

main();
