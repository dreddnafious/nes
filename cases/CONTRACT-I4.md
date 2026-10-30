# The surface I4's cases drive (additive to CONTRACT.md, CONTRACT-I2.md and CONTRACT-I3.md)

Everything in the earlier contracts stays as it is: the CLI's `trace`, `run` and `screenshot`, the core
API (`new NES(bytes)`, `runFrame()`, `peek()`, `reset()`, `frameBuffer()`, `RomError`), the frozen drivers
`cases/drive.ts` and `cases/drive_ppu.ts`, the page and its `window.nesTest` hooks. I4 adds the core's
sound (two methods, and one existing method pinned), a CLI command that writes it, a frozen driver
`cases/drive_apu.ts`, and the page's sound. The cases measure sound where its consumers meet it: the core's
sample stream headless, a WAV file from the CLI, and what the page sends to its AudioContext's
destination in headless Chromium.

## Consumer 1: the core API, `src/core/index.ts`
```ts
export class NES {
  // ... everything in the earlier contracts, plus:
  setSampleRate(hz: number): void               // the stream's rate, 8,000-192,000 Hz; 48,000 until set
  audioSamples(): Float32Array                  // the samples made since the last call (or power-on)
  setButtons(port: number, buttons: number): void   // in the build since I3; pinned here
}
```
- **The stream:** mono, oldest sample first. Sample *k* stands for the emulated time from *k* / rate to
  (*k* + 1) / rate seconds of the CPU's clock, **1,789,772.7272 Hz** (NESdev "Cycle reference chart"),
  counted from power-on (or from the call, after a `setSampleRate` change).
  - So N frames make N × (their CPU cycles) × rate / 1,789,772.7272 samples: within 0.02 % over 600
    frames. (A stream made at 1/60 s a frame, 60.00 fps, is 0.16 % long.)
  - Each `runFrame()`'s samples can be taken after it returns: one call after each frame gives that
    frame's share, within 16 samples (a short filter delay is fine; holding samples back and releasing
    them in bursts isn't).
  - How often `audioSamples()` is called doesn't change the stream: the samples taken every frame, or
    every 7 frames, concatenate to the same values.
- **Each sample** is the APU mixer's output level (NESdev "APU Mixer"):
  `pulse_out + tnd_out`, with `pulse_out = 95.88 / (8128 / (pulse1 + pulse2) + 100)` and
  `tnd_out = 159.79 / (1 / (triangle / 8227 + noise / 12241 + dmc / 22638) + 100)`.
  - Either the formula or the wiki's standard lookup-table form (within 4 % of it) is fine. The linear
    approximation isn't.
  - The level is band-limited to the rate however the build likes: averaging the CPU cycles a sample
    covers is enough.
  - It is **not** high-passed and has no gain applied: a constant mixer level gives that level, DC
    included (0.0 to about 1.0).
  - A stopped triangle holds the step its sequencer stopped on (NESdev "APU Triangle"), so its level
    stays in the mix.
- **The channels** are the NESdev APU pages' (Pulse, Sweep, Envelope, Length Counter, Triangle, Noise,
  DMC, Frame Counter), NTSC. The cases check pitches, duties, levels, the shift register, the DMC rate
  table, the note-ending units and DMC DMA's stolen cycles against those pages.
- **Determinism:** the same ROM with the same buttons gives the same samples, bit for bit.
- **`setButtons(port, buttons)`:** port 0 is controller 1. Its bits are A `0x01`, B `0x02`, Select `0x04`,
  Start `0x08`, Up `0x10`, Down `0x20`, Left `0x40`, Right `0x80` (the read order). They are held until
  the next call.
- **Purity:** the core makes samples and nothing else. No file under `src/core` names a Web Audio type or
  touches `window.`, `document.` or `navigator.` (Decided: WebAudio is the front end's).

## Consumer 2: the CLI, `node src/cli/nes.ts`
```
node src/cli/nes.ts audio <rom.nes> [--frames <n>] [--rate <hz>] --out <file.wav>
```
- **What it does:** powers on with the ROM, sets the rate (default 48,000), runs `--frames` frames
  (default 60, as `run`), and writes the core's samples for those frames to `--out`.
- **The file:** a RIFF WAVE file, mono, at the rate, in one of two forms:
  - 32-bit IEEE float (format 3), holding the samples as they are;
  - or 16-bit PCM (format 1), holding round(sample × 32,767), clamped.

  Either way it holds exactly the core's samples: the same count, and the same values (to the format's
  precision).
- **Errors:** each exits non-zero with a message and no stack trace, as the other commands refuse bad
  options:
  - no `--out`: the message names `--out`;
  - a `--rate` that isn't a number from 8,000 to 192,000 (0, say): the message names `--rate`.

## The frozen driver `cases/drive_apu.ts`
It runs frames and hands back the samples the core made; every judgement is made by the cases. Usage:
`node cases/drive_apu.ts '<json>'`. It prints one JSON line.
- `{"mode": "audio", "rom": p, "frames": N, "rate": R?, "take": k?, "buttons": [[f, bits], ...]?, "reset": [f, ...]?, "peekEach": [[addr, n], ...]?}`:
  - calls `setSampleRate(R)` (when R is given) before the first frame;
  - before frame f (1-based), sets controller 1 to `bits` and presses `reset()` for each f listed;
  - calls `audioSamples()` after every k-th frame (default 1) and after the last;
  - returns the samples (base64 of little-endian float32), each call's length, and (with `peekEach`)
    the bytes `peek` reads after every frame.
- `{"mode": "bench", "rom": p, "frames": N, "rate": R?}`: the wall time of N frames, with
  `audioSamples()` after each, and the sample count.
- **Errors:** a core without `setSampleRate()`, `audioSamples()` or `setButtons()`, or one whose
  `audioSamples()` isn't a `Float32Array`, gives `ok: false, stage: "audio"`. Load errors are reported
  as in `drive.ts`.

## Consumer 3: the page's sound
The page plays the running game's sound through Web Audio (an AudioWorklet or a buffered source, the
build's choice), paced as the roadmap says.
- **Its AudioContext:**
  - The page makes it with `new AudioContext(...)`, looked up on `window` when the page calls it (the
    cases install a wrapper before the page's scripts run).
  - Its sound is whatever it connects to that context's `destination` with `AudioNode.connect`. Not a
    `<audio>` element, and not a `MediaStreamAudioDestinationNode`. It may not replace
    `AudioNode.prototype.connect`.
  - It may make the context at load or at the first gesture, and it may keep one context or make
    more. J2e's "exactly one AudioWorkletNode" is I9's.
- **Any rate:** the page works at whatever `sampleRate` its context has. The cases force 44,100 and
  48,000 Hz, as devices at those rates would. Pitch at the output is the stream's within 1 %; the axes
  allow resampling of about 0.5 % to hold the buffer.
- **When sound starts:**
  - On a load, the page starts its sound if the browser lets it. Headless Chromium does; so does Chrome
    after the click that opened the file picker. Where it may, the game is heard from its first frames.
  - Where the autoplay policy holds it back, the sound starts no later than the first key press (any
    key) or click: within 0.5 s of it.
- **`#sound-off`:** an element that is visible while the page's sound can't play (no context yet, or a
  context whose `state` isn't `"running"`), and hidden while it plays. Its words are the builder's.
- **No DC at the output:** the console's own output is AC-coupled (NESdev "APU Mixer" describes its
  high-pass filters). So while the mix holds a constant level, the output's mean over 2 s is within
  0.002 of zero. A held level, an underrun's gap or a restart then can't click.
- **Restarts and loads** (Reset, Power, or a game loaded through `#rom-file`, J1n, J2c, J2g): from 30 ms
  of audio-clock time after the click or the file's `change` event, nothing made before it is output,
  in any context the page has. That includes the memory of an output filter: the old game's level
  mustn't decay into the new one.
- **Opened from `file://`:** the page shows `#message`, visible, naming the command that serves it,
  `node src/web/serve.ts` (a loose word pin: "serve.ts"). The rest of the page can't load from disk.
- **While `nesTest.pause()` holds the page,** what the sound does is the build's choice; the cases don't
  measure it then. Hidden tabs, underruns and latency are I5's (J3).

## What the cases install in the page (`cases/i4_support.py`, `TAP_JS`)
These run in an init script, before the page's own scripts:
- **A tap:** `window.AudioContext` (and `webkitAudioContext`) becomes a subclass of the real one. It
  wraps `AudioNode.prototype.connect` so that whatever is connected to a destination is also connected
  to a recorder AudioWorkletNode. The recorder is loaded from a `blob:` URL and reaches the destination
  through a zero gain, so it hears exactly the sum the destination does. It records every 128-sample
  render quantum: the context's `currentTime`, and the first channel's samples.
  So the page mustn't forbid `blob:` scripts (with a Content-Security-Policy, say): that would blind the
  tap.
- **Marks:** listeners in the capture phase record each click (by element id), each `change` (by id)
  and each trusted key press, with every context's `currentTime` at that moment.
- **Optionally, a forced rate:** the subclass passes `sampleRate: R` to the real constructor.
- **Optionally, the autoplay policy, simulated.** Headless Chromium doesn't enforce it, and reports
  `navigator.userActivation` as always active. The simulation follows Chrome's documented policy:
  - "Activation" is a trusted `keydown` (not Escape), `mousedown`, `pointerdown`, `pointerup` or
    `touchend`, as HTML defines its activation-triggering events.
  - A context made before any activation starts suspended, and its `state` reads `"suspended"`.
  - Before an activation, `resume()` does nothing and its promise never settles. After one,
    `resume()` works.

## What the cases bring themselves
- **I4's test ROMs** (`cases/i4_oracles.json`, fetched by hash): `apu_test`, `blargg_apu_2005.07.30`,
  `cpu_interrupts_v2`, `apu_reset`, `dmc_dma_during_read4`, `sprdma_and_dmc_dma`
  (`cases/i4_apu_roms.yaml` says how each reports). Two suites report on screen only, and their
  screens are read with the font each ROM loads:
  - `blargg_apu_2005` shows its result code as `$01` for a pass;
  - `dmc_dma_during_read4`'s older shell prints `Passed`, or a CRC.
- **`apu_probe`** (`cases/i4_support.py`, NROM, from the spec): plays a script of APU register writes,
  each at a stated frame. Frames are counted in NMIs, from the first one after it has waited two
  vblanks; it restarts the script on every reset. Between NMIs it counts idle-loop iterations (8 cycles
  each) and stores the count of the frame before at `$0300`/`$0301`, so DMC DMA's stolen cycles show.
  It can carry DMC sample data at `$E000`-`$FFF9`.
- **nes15** (I3's, fetched by hash). From its source:
  - its title is silent;
  - any button starts a puzzle and its background music;
  - Start pauses (a sound effect, then every channel muted) and resumes;
  - Select toggles its auto-solver;
  - a solved puzzle shows "SOLVED!" on its message line (row 26, columns 6-15, tile number = ASCII
    code), read with the ROM's own CHR.
