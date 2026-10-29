// I6's frozen driver for the core API (see CONTRACT-I6.md). It adds no surface to the build: it only
// strings the existing calls together (new NES, runFrame, peek, reset, frameBuffer) in the order a case asks.
// Usage: node cases/drive_i6.ts '<json request>'   -> one JSON line on stdout.
// Every judgement is made by the cases.
//
// Request: {"rom": p, "steps": [step, ...]}, each step one of
//   ["frames", n]                run n frames
//   ["until", addr, value, max]  run frames until peek(addr) == value (checked after each frame), at most
//                                max frames; outputs the frames it took, or -1 if it never did
//   ["peek", addr, n]            outputs n bytes read with peek() from addr on
//   ["frame"]                    outputs the last frame, base64 of 61,440 little-endian 16-bit values
//   ["buttons", bits]            controller 1 holds these buttons from now on (setButtons(0, bits),
//                                CONTRACT-I4: A 0x01, B 0x02, Select 0x04, Start 0x08, Up 0x10, Down 0x20,
//                                Left 0x40, Right 0x80)
//   ["reset"]                    the console's Reset button (reset())
//   ["power"]                    a power cycle: a new NES from the same file's bytes
// Output: {"ok": true, "out": [one entry per "until", "peek" or "frame" step, in order]}
import { readFileSync } from 'node:fs';

const W = 256, H = 240;
const out = (o: unknown) => { process.stdout.write(JSON.stringify(o) + '\n'); };

class FrameError extends Error {}

function grab(nes: any): string {
  if (typeof nes.frameBuffer !== 'function') throw new FrameError('the core has no frameBuffer() method');
  const fb = nes.frameBuffer();
  if (!(fb instanceof Uint16Array)) throw new FrameError(`frameBuffer() returned ${Object.prototype.toString.call(fb)}, not a Uint16Array`);
  if (fb.length !== W * H) throw new FrameError(`frameBuffer() has ${fb.length} values, not ${W * H}`);
  const b = Buffer.alloc(W * H * 2);
  for (let i = 0; i < W * H; i++) b.writeUInt16LE(fb[i], i * 2);
  return b.toString('base64');
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
  const load = () => new core.NES(rom.slice());
  let nes: any;
  try {
    nes = load();
  } catch (e: any) {
    const isRomError = typeof core.RomError === 'function' && e instanceof core.RomError;
    out({ ok: false, stage: 'load', romError: isRomError, error: String(e?.message ?? e) });
    return;
  }
  const results: unknown[] = [];
  try {
    for (const step of req.steps ?? []) {
      const [kind, a, b, c] = step;
      if (kind === 'frames') {
        for (let i = 0; i < a; i++) nes.runFrame();
      } else if (kind === 'until') {
        let n = 0, hit = -1;
        while (n < c) {
          nes.runFrame();
          n++;
          if ((nes.peek(a) & 0xff) === b) { hit = n; break; }
        }
        results.push(hit);
      } else if (kind === 'peek') {
        const v: number[] = [];
        for (let i = 0; i < b; i++) v.push(nes.peek((a + i) & 0xffff) & 0xff);
        results.push(v);
      } else if (kind === 'frame') {
        results.push(grab(nes));
      } else if (kind === 'buttons') {
        nes.setButtons(0, a);
      } else if (kind === 'reset') {
        nes.reset();
      } else if (kind === 'power') {
        nes = load();
      } else {
        out({ ok: false, stage: 'request', error: `unknown step ${JSON.stringify(step)}` });
        return;
      }
    }
    out({ ok: true, out: results });
  } catch (e: any) {
    out({ ok: false, stage: e instanceof FrameError ? 'frame' : 'run', error: String(e?.stack ?? e) });
  }
}

main();
