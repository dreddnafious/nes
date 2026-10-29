// The page (see cases/CONTRACT-I3.md): a ROM from the file input, the canvas, the keyboard on
// controller 1, the Reset and Power buttons, and a loop paced against real time. Everything here is
// presentation; the console itself is src/core.
import { NES, RomError } from '../core/index.ts';
import { RGB } from '../core/palette.ts';
import { Keys } from './keyboard.ts';

/** NTSC: 1,789,772.7 CPU cycles a second, 29,780.5 a frame (NESdev "Cycle reference chart"). */
const FRAME_MS = 1000 / (1789772.7272 / 29780.5);
/** After a stall (a hidden tab, a debugger), the loop drops what it owes past this many frames
 * rather than racing to catch up. */
const MAX_BEHIND = 4;
const REGIONS = ['NTSC', 'PAL', 'NTSC/PAL', 'Dendy'];

const input = document.getElementById('rom-file') as HTMLInputElement;
const canvas = document.getElementById('screen') as HTMLCanvasElement;
const message = document.getElementById('message') as HTMLElement;
const resetButton = document.getElementById('reset') as HTMLButtonElement;
const powerButton = document.getElementById('power') as HTMLButtonElement;

const ctx = canvas.getContext('2d', { alpha: false })!;
const image = ctx.createImageData(256, 240);
const pixels = new Uint32Array(image.data.buffer);
/** The palette as the canvas's bytes (R, G, B, A in memory: little-endian ABGR words). */
const ABGR = RGB.map(c => (0xff000000 | ((c & 0xff) << 16) | (c & 0xff00) | (c >>> 16)) >>> 0);

const keys = new Keys();
let nes: NES | null = null;
let rom: Uint8Array | null = null;
let frames = 0;
let paused = false;
let behind = 0;
let last = 0;

function show(text: string): void {
  message.textContent = text;
  message.hidden = text === '';
}

function draw(): void {
  if (!nes) return;
  const frame = nes.frameBuffer();
  for (let i = 0; i < frame.length; i++) pixels[i] = ABGR[frame[i] & 0x1ff];
  ctx.putImageData(image, 0, 0);
}

function runFrame(): void {
  nes!.setButtons(0, keys.buttons());
  nes!.runFrame();
  frames++;
}

/** Start a console on these bytes: a fresh power-on. The battery save carries over only on Power. */
function start(next: NES, bytes: Uint8Array): void {
  nes = next;
  rom = bytes;
  frames = 0;
  behind = 0;
  last = performance.now();
}

function load(bytes: Uint8Array, name: string): void {
  let next: NES;
  try {
    next = new NES(bytes);
  } catch (e) {
    // The game that's running (if any) keeps running; the reason is shown.
    show(`${name}: ${e instanceof RomError ? e.message : `can't be loaded (${String(e)})`}`);
    return;
  }
  keys.clear();
  start(next, bytes);
  show(next.region === 1 || next.region === 3
    ? `${name} is a ${REGIONS[next.region]} game: it runs here at NTSC timing, so it may play too fast.`
    : '');
}

function power(): void {
  if (!nes || !rom) return;
  const save = nes.batteryRam();
  const next = new NES(rom);
  const into = next.batteryRam();
  if (save && into) into.set(save.subarray(0, into.length));
  start(next, rom);
}

/** The file's bytes, read before the change event returns, so a load is complete (and frame 0)
 * the moment the pick is. A synchronous XHR of the file's blob: URL; null where that's refused. */
function readNow(file: File): Uint8Array | null {
  const url = URL.createObjectURL(file);
  try {
    const xhr = new XMLHttpRequest();
    xhr.open('GET', url, false);
    xhr.overrideMimeType('text/plain; charset=x-user-defined');
    xhr.send();
    const text = xhr.responseText;
    const bytes = new Uint8Array(text.length);
    for (let i = 0; i < text.length; i++) bytes[i] = text.charCodeAt(i) & 0xff;
    return bytes;
  } catch {
    return null;
  } finally {
    URL.revokeObjectURL(url);
  }
}

input.addEventListener('change', () => {
  const file = input.files?.[0];
  input.blur();
  if (!file) return;                 // a cancelled pick changes nothing
  const bytes = readNow(file);
  if (bytes) load(bytes, file.name);
  else file.arrayBuffer().then(b => load(new Uint8Array(b), file.name), e => show(`${file.name}: ${String(e)}`));
  input.value = '';                  // so picking the same file again is a change
});

resetButton.addEventListener('click', () => {
  resetButton.blur();
  nes?.reset();
});

powerButton.addEventListener('click', () => {
  powerButton.blur();
  power();
});

// Captured on window, so a mapped key is the game's before anything else sees it: no scrolling,
// and no focused control acts on it (Enter after clicking Reset is Start, not another click).
window.addEventListener('keydown', e => {
  if (keys.down(e.code)) e.preventDefault();
}, true);
window.addEventListener('keyup', e => {
  if (keys.up(e.code)) e.preventDefault();
}, true);
window.addEventListener('blur', () => keys.clear());

/** Paced by an accumulator against real time, never by counting rAF calls, so the game runs at
 * 60.1 frames a second whatever the display's rate. */
function tick(now: number): void {
  if (paused) return;
  requestAnimationFrame(tick);
  if (!nes) {
    last = now;
    return;
  }
  behind += Math.max(0, now - last);
  last = now;
  if (behind > MAX_BEHIND * FRAME_MS) behind = FRAME_MS;
  let ran = false;
  while (behind >= FRAME_MS) {
    runFrame();
    behind -= FRAME_MS;
    ran = true;
  }
  if (ran) draw();
}
requestAnimationFrame(tick);

// Test hooks for frame-exact cases (CONTRACT-I3.md). Keys still go through the handlers above.
(window as unknown as { nesTest: object }).nesTest = Object.freeze({
  get frames(): number {
    return frames;
  },
  pause(): void {
    paused = true;
  },
  step(n: number): number {
    if (!nes) return frames;
    for (let i = 0; i < n; i++) runFrame();
    draw();
    return frames;
  },
});
