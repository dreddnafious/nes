// The page (see cases/CONTRACT-I3.md, CONTRACT-I4.md and CONTRACT-I9.md): a ROM from the file input or
// the game menu, the canvas, the sound, the keyboard on controller 1, the Reset and Power buttons, and a
// loop paced against real time. Everything here is presentation; the console itself is src/core.
import { NES, RomError } from '../core/index.ts';
import { RGB } from '../core/palette.ts';
import { Keys } from './keyboard.ts';
import { Sound } from './sound.ts';

/** NTSC: 1,789,772.7 CPU cycles a second, 29,780.5 a frame (NESdev "Cycle reference chart"). */
const FRAME_MS = 1000 / (1789772.7272 / 29780.5);
/** After a stall (a hidden tab, a debugger), the loop drops what it owes past this many frames
 * rather than racing to catch up. */
const MAX_BEHIND = 4;
const REGIONS = ['NTSC', 'PAL', 'NTSC/PAL', 'Dendy'];

const input = document.getElementById('rom-file') as HTMLInputElement;
const gameMenu = document.getElementById('game-menu') as HTMLElement;
const canvas = document.getElementById('screen') as HTMLCanvasElement;
const message = document.getElementById('message') as HTMLElement;
const resetButton = document.getElementById('reset') as HTMLButtonElement;
const powerButton = document.getElementById('power') as HTMLButtonElement;
const soundOff = document.getElementById('sound-off') as HTMLElement;

const ctx = canvas.getContext('2d', { alpha: false })!;
const image = ctx.createImageData(256, 240);
const pixels = new Uint32Array(image.data.buffer);
/** The palette as the canvas's bytes (R, G, B, A in memory: little-endian ABGR words). */
const ABGR = RGB.map(c => (0xff000000 | ((c & 0xff) << 16) | (c & 0xff00) | (c >>> 16)) >>> 0);

const keys = new Keys();
const sound = new Sound(soundOff);
let nes: NES | null = null;
let rom: Uint8Array | null = null;
let frames = 0;
let loads = 0;
/** The latest load asked for: a load that finishes after a later one was asked for is dropped. */
let asked = 0;
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
  sound.play(nes!.audioSamples());
  frames++;
}

/** Start a console on these bytes: a fresh power-on. The battery save carries over only on Power.
 * The old console's sound, sent or not, is never heard after this. */
function start(next: NES, bytes: Uint8Array): void {
  sound.start();
  next.setSampleRate(sound.rate);
  sound.flush();
  nes = next;
  rom = bytes;
  frames = 0;
  behind = 0;
  last = performance.now();
}

/** Load these bytes for the ask-th load asked for, unless a later one has been asked for since. */
function load(bytes: Uint8Array, name: string, ask: number): void {
  if (ask !== asked) return;
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
  loads++;
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
  const ask = ++asked;
  const bytes = readNow(file);
  if (bytes) load(bytes, file.name, ask);
  else {
    file.arrayBuffer().then(b => load(new Uint8Array(b), file.name, ask),
      e => { if (ask === asked) show(`${file.name}: ${String(e)}`); });
  }
  input.value = '';                  // so picking the same file again is a change
});

/** The menu's ROMs, fetched as the page loads so a choice can start its game at once; a fetch that
 * fails is tried again at the next choice. */
const fetched = new Map<string, Uint8Array>();
const fetching = new Map<string, Promise<Uint8Array>>();

function fetchRom(url: string): Promise<Uint8Array> {
  let pending = fetching.get(url);
  if (!pending) {
    pending = fetch(url, { cache: 'no-store' }).then(async r => {
      if (!r.ok) throw new Error(`the server answered ${r.status}${r.statusText ? ` ${r.statusText}` : ''}`);
      return new Uint8Array(await r.arrayBuffer());
    });
    fetching.set(url, pending);
    pending.then(bytes => fetched.set(url, bytes), () => fetching.delete(url));
  }
  return pending;
}

function entries(): HTMLElement[] {
  return Array.from(gameMenu.querySelectorAll<HTMLElement>('[data-rom]'));
}

for (const entry of entries()) void fetchRom(new URL(entry.dataset.rom!, location.href).href).catch(() => {});

gameMenu.addEventListener('click', e => {
  const entry = (e.target as Element).closest<HTMLElement>('[data-rom]');
  if (!entry || !gameMenu.contains(entry)) return;
  entry.blur();                      // the keyboard stays the game's
  const url = new URL(entry.dataset.rom!, location.href).href;
  const name = (entry.textContent ?? '').trim() || url;
  const ask = ++asked;
  const bytes = fetched.get(url);
  if (bytes) {
    load(bytes, name, ask);
    return;
  }
  fetchRom(url).then(b => load(b, name, ask),
    err => { if (ask === asked) show(`${name}: can't be fetched (${err instanceof Error ? err.message : String(err)})`); });
});

resetButton.addEventListener('click', () => {
  resetButton.blur();
  if (!nes) return;
  nes.audioSamples();                // made before the reset: never heard
  nes.reset();
  sound.flush();
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

/** A display within 1 % of 60 Hz (60.00 and 59.94 are the common ones) is locked to: each refresh
 * runs exactly one frame, so none is doubled or skipped, and the game runs at most 1 % off NTSC's
 * 60.0988. The worklet's resampling holds the sound's buffer across the difference. */
const LOCK_MIN_HZ = 59.4;
const LOCK_MAX_HZ = 60.6;
/** The refresh rate is measured over the last INTERVALS refresh intervals; a gap of more than
 * GAP_MS (a hidden tab, a stall) isn't an interval. */
const INTERVALS = 31;
const MIN_INTERVALS = 15;
const GAP_MS = 100;
const intervals = new Float64Array(INTERVALS).fill(Infinity);   // unmeasured slots sort last
const sorted = new Float64Array(INTERVALS);
let counted = 0;
let nextInterval = 0;
let lastStamp = -1;

function measure(now: number): void {
  const d = lastStamp < 0 ? 0 : now - lastStamp;
  lastStamp = now;
  if (d <= 0 || d > GAP_MS) return;
  intervals[nextInterval] = d;
  nextInterval = (nextInterval + 1) % INTERVALS;
  if (counted < INTERVALS) counted++;
}

/** Is the display measured within 1 % of 60 Hz? */
function locked(): boolean {
  if (counted < MIN_INTERVALS) return false;
  sorted.set(intervals);
  sorted.sort();
  // The mean, not the median: timers and timestamps land on whole milliseconds (16 and 17 for a
  // 16.68 ms refresh), which only the mean averages out. A late refresh (over 1.5x the median)
  // is left out of it.
  const cut = 1.5 * sorted[counted >> 1];
  let sum = 0;
  let n = 0;
  for (let i = 0; i < counted && sorted[i] <= cut; i++, n++) sum += sorted[i];
  const hz = 1000 * n / sum;
  return hz >= LOCK_MIN_HZ && hz <= LOCK_MAX_HZ;
}

/** At a 60 Hz display, one frame a refresh. Otherwise paced by an accumulator against real time,
 * never by counting rAF calls, so the game runs at 60.1 frames a second whatever the display's
 * rate. Either way a stall or a hidden tab is never raced through: at most one frame on return. */
function tick(now: number): void {
  if (paused) return;
  requestAnimationFrame(tick);
  measure(now);
  if (!nes) {
    last = now;
    return;
  }
  if (locked()) {
    behind = 0;
    last = now;
    runFrame();
    draw();
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
  get loads(): number {
    return loads;
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
