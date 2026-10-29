// The page's audio worklet (see cases/CONTRACT-I4.md): it plays the core's sample stream, sent from
// the page's main thread, at the AudioContext's rate.
// - The console's output is AC-coupled (NESdev "APU Mixer"), and the core's stream keeps its DC: a
//   first-order high-pass filter at 37 Hz takes it out, so a held level, a gap or a restart can't click.
// - A FIFO of filtered samples absorbs the main thread's bursts (a frame at a time). Its fill is held
//   near a target by reading it up to 0.5 % faster or slower (linear interpolation), which keeps the
//   emulation's clock and the audio device's in step without dropping or repeating frames.
// - "flush" drops everything sent before it, the filter's memory included: after a Reset, Power or a
//   load, nothing of the old sound is heard.
// It runs in the AudioWorkletGlobalScope, so it imports nothing.

declare const sampleRate: number;
declare function registerProcessor(name: string, processor: unknown): void;
declare class AudioWorkletProcessor {
  readonly port: MessagePort;
}

/** The fill the FIFO is held at, in seconds: three frames' worth. */
const TARGET_S = 0.05;
/** The most the reading speed moves off 1.0. */
const MAX_ADJUST = 0.005;

class NesOutput extends AudioWorkletProcessor {
  private readonly fifo = new Float32Array(1 << 16);
  private readonly mask = (1 << 16) - 1;
  private head = 0;          // where the next sample is written
  private tail = 0;          // the integer part of the read position
  private frac = 0;          // its fraction
  private fill = 0;
  private playing = false;
  private readonly target = Math.round(sampleRate * TARGET_S);
  // The high-pass filter: y[n] = a (y[n-1] + x[n] - x[n-1]).
  private readonly a = 1 / (1 + 2 * Math.PI * 37 / sampleRate);
  private x1 = 0;
  private y1 = 0;
  private primed = false;

  constructor() {
    super();
    this.port.onmessage = (e: MessageEvent) => {
      const m = e.data as { type: string; samples?: Float32Array };
      if (m.type === 'flush') this.flush();
      else if (m.type === 'samples' && m.samples) this.push(m.samples);
    };
  }

  private flush(): void {
    this.head = this.tail = 0;
    this.frac = 0;
    this.fill = 0;
    this.playing = false;
    this.primed = false;
  }

  private push(x: Float32Array): void {
    if (!this.primed && x.length > 0) {
      // Start the filter at rest on the first sample: a constant level gives 0 from the start.
      this.x1 = x[0];
      this.y1 = 0;
      this.primed = true;
    }
    const a = this.a;
    for (let i = 0; i < x.length; i++) {
      const y = a * (this.y1 + x[i] - this.x1);
      this.x1 = x[i];
      this.y1 = y;
      this.fifo[this.head] = y;
      this.head = (this.head + 1) & this.mask;
    }
    this.fill += x.length;
    // Too far behind (a stall, a suspended context, frames stepped at once): skip to the target.
    if (this.fill > 4 * this.target) {
      const drop = this.fill - this.target;
      this.tail = (this.tail + drop) & this.mask;
      this.fill -= drop;
    }
    if (!this.playing && this.fill >= this.target) this.playing = true;
  }

  process(_inputs: Float32Array[][], outputs: Float32Array[][]): boolean {
    const out = outputs[0];
    const n = out[0].length;
    let i = 0;
    if (this.playing) {
      const step = 1 + Math.max(-MAX_ADJUST, Math.min(MAX_ADJUST, (this.fill - this.target) / this.target * MAX_ADJUST));
      for (; i < n && this.fill >= 2; i++) {
        const s0 = this.fifo[this.tail];
        const s1 = this.fifo[(this.tail + 1) & this.mask];
        out[0][i] = s0 + (s1 - s0) * this.frac;
        this.frac += step;
        const whole = Math.floor(this.frac);
        this.frac -= whole;
        this.tail = (this.tail + whole) & this.mask;
        this.fill -= whole;
      }
      // Ran dry: wait for the target again before playing.
      if (i < n) this.playing = false;
    }
    for (; i < n; i++) out[0][i] = 0;
    for (let c = 1; c < out.length; c++) out[c].set(out[0]);
    return true;
  }
}

registerProcessor('nes-output', NesOutput);
