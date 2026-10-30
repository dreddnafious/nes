// The 2A03's APU, as far as the CPU sees it: the frame counter (4- and 5-step, and its IRQ), the
// four length counters and $4015. The channels' sound comes with the APU increment.
// Spec: https://www.nesdev.org/wiki/APU_Frame_Counter, APU_Length_Counter, APU#Status_($4015).

const LENGTHS = [
  10, 254, 20, 2, 40, 4, 80, 6, 160, 8, 60, 10, 14, 12, 26, 14,
  12, 16, 24, 18, 48, 20, 96, 22, 192, 24, 72, 26, 16, 28, 32, 30,
];

// Frame-counter steps, in CPU cycles since the counter was last reset. Only the half-frame steps
// (which clock the length counters) and the IRQ are here; the quarter-frame steps at 7457 and 22371
// clock the envelopes and the linear counter, which come with the channels.
const STEP2 = 14913;
const STEP4_IRQ = 29828;
const STEP4 = 29829;
const PERIOD4 = 29830;
const STEP5 = 37281;
const PERIOD5 = 37282;

/** The APU's IRQ sources, as bits of the console's IRQ line. */
export const IRQ_FRAME = 0x01;
export const IRQ_DMC = 0x02;

export interface ApuHost {
  irq: number;
  /** CPU cycles since power-on: the frame counter's write delay depends on their parity. */
  readonly cycles: number;
}

export class APU {
  /** Length counters for pulse 1, pulse 2, triangle, noise. */
  readonly length = new Uint8Array(4);
  private halt = [false, false, false, false];
  private enabled = [false, false, false, false];

  private fiveStep = false;
  private irqInhibit = false;
  private frameIrq = false;
  private frameCycle = 0;
  /** A $4017 write takes effect 3 or 4 cycles later: the countdown, and the value written. */
  private resetDelay = 0;
  private pendingMode = 0;

  private host: ApuHost;

  constructor(host: ApuHost) {
    this.host = host;
  }

  /** The Reset button: the channels are silenced (as a $4015 write of 0) and the frame counter
   * restarts in the mode it had. */
  reset(): void {
    this.writeRegister(0x4015, 0);
    this.setFrameIrq(false);
    this.writeRegister(0x4017, (this.fiveStep ? 0x80 : 0) | (this.irqInhibit ? 0x40 : 0));
  }

  private setFrameIrq(on: boolean): void {
    this.frameIrq = on;
    if (on) this.host.irq |= IRQ_FRAME;
    else this.host.irq &= ~IRQ_FRAME;
  }

  private clockLengths(): void {
    for (let i = 0; i < 4; i++) {
      if (!this.halt[i] && this.length[i] > 0) this.length[i]--;
    }
  }

  /** One CPU cycle. */
  tick(): void {
    if (this.resetDelay > 0 && --this.resetDelay === 0) {
      this.fiveStep = (this.pendingMode & 0x80) !== 0;
      this.frameCycle = 0;
      if (this.fiveStep) this.clockLengths();
      return;
    }
    const c = ++this.frameCycle;
    if (this.fiveStep) {
      if (c === STEP2 || c === STEP5) this.clockLengths();
      if (c === PERIOD5) this.frameCycle = 0;
    } else {
      if (c === STEP2 || c === STEP4) this.clockLengths();
      if ((c === STEP4_IRQ || c === STEP4 || c === PERIOD4) && !this.irqInhibit) this.setFrameIrq(true);
      if (c === PERIOD4) this.frameCycle = 0;
    }
  }

  readStatus(): number {
    const v = this.peekStatus();
    this.setFrameIrq(false);
    return v;
  }

  peekStatus(): number {
    let v = 0;
    for (let i = 0; i < 4; i++) if (this.length[i] > 0) v |= 1 << i;
    if (this.frameIrq) v |= 0x40;
    if (this.host.irq & IRQ_DMC) v |= 0x80;
    return v;
  }

  writeRegister(addr: number, value: number): void {
    switch (addr) {
      case 0x4000: this.halt[0] = (value & 0x20) !== 0; break;
      case 0x4004: this.halt[1] = (value & 0x20) !== 0; break;
      case 0x4008: this.halt[2] = (value & 0x80) !== 0; break;
      case 0x400c: this.halt[3] = (value & 0x20) !== 0; break;
      case 0x4003: this.loadLength(0, value); break;
      case 0x4007: this.loadLength(1, value); break;
      case 0x400b: this.loadLength(2, value); break;
      case 0x400f: this.loadLength(3, value); break;
      case 0x4015:
        for (let i = 0; i < 4; i++) {
          this.enabled[i] = (value & (1 << i)) !== 0;
          if (!this.enabled[i]) this.length[i] = 0;
        }
        this.host.irq &= ~IRQ_DMC;
        break;
      case 0x4017:
        this.irqInhibit = (value & 0x40) !== 0;
        if (this.irqInhibit) this.setFrameIrq(false);
        this.pendingMode = value;
        // Written on an odd CPU cycle (between APU cycles) the reset lands one cycle later.
        this.resetDelay = (this.host.cycles & 1) ? 4 : 3;
        break;
    }
  }

  private loadLength(ch: number, value: number): void {
    if (this.enabled[ch]) this.length[ch] = LENGTHS[value >> 3];
  }
}
