// The page's sound (see cases/CONTRACT-I4.md): one AudioContext, made when the first game loads, with
// the worklet (worklet.ts) connected to its destination. The game's samples are sent to the worklet a
// frame at a time; a Reset, Power or a load flushes everything sent before it.
// Browsers may hold a context suspended until the person presses a key or clicks (the autoplay
// policy): the indicator says so, and the next key press or click resumes it.

const WORKLET = '/src/web/worklet.ts';

export class Sound {
  private ctx: AudioContext | null = null;
  private node: AudioWorkletNode | null = null;
  private readonly indicator: HTMLElement;

  constructor(indicator: HTMLElement) {
    this.indicator = indicator;
    for (const type of ['keydown', 'mousedown', 'pointerdown', 'touchend']) {
      window.addEventListener(type, () => this.resume(), true);
    }
    this.show();
  }

  /** The context's sample rate: the rate the core's stream must be made at. */
  get rate(): number {
    return this.ctx ? this.ctx.sampleRate : 48000;
  }

  /** Make the context (once) and start it if the browser lets it. */
  start(): void {
    if (!this.ctx) {
      let ctx: AudioContext;
      try {
        ctx = new AudioContext({ latencyHint: 'interactive' });
      } catch {
        return;                       // no Web Audio: the game plays silent
      }
      this.ctx = ctx;
      ctx.addEventListener('statechange', () => this.show());
      ctx.audioWorklet.addModule(WORKLET).then(() => {
        const node = new AudioWorkletNode(ctx, 'nes-output', { numberOfInputs: 0, outputChannelCount: [1] });
        node.connect(ctx.destination);
        this.node = node;
        this.show();
      }, () => { /* the worklet can't load: the game plays silent */ });
    }
    this.resume();
  }

  private resume(): void {
    const ctx = this.ctx;
    if (ctx && ctx.state !== 'running' && ctx.state !== 'closed') {
      ctx.resume().then(() => this.show(), () => this.show());
    }
    this.show();
  }

  /** Is the sound playing? */
  get playing(): boolean {
    return this.ctx !== null && this.ctx.state === 'running';
  }

  /** Show the indicator while the sound can't play. */
  show(): void {
    this.indicator.hidden = this.playing && this.node !== null;
  }

  /** The samples of one or more frames. Dropped while the sound can't play, so nothing old is heard
   * when it starts. */
  play(samples: Float32Array): void {
    if (!this.node || !this.playing || samples.length === 0) return;
    this.node.port.postMessage({ type: 'samples', samples }, [samples.buffer]);
  }

  /** Forget every sample sent so far. */
  flush(): void {
    this.node?.port.postMessage({ type: 'flush' });
  }
}
