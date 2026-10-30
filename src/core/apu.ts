// The 2A03's APU: two pulses, the triangle, noise and the DMC, the frame counter (4- and 5-step, and
// its IRQ), $4015, and the mixer, sampled into a stream at a chosen rate.
// Spec: https://www.nesdev.org/wiki/APU and its pages: APU_Frame_Counter, APU_Length_Counter,
// APU_Envelope, APU_Sweep, APU_Pulse, APU_Triangle, APU_Noise, APU_DMC, APU_Mixer.

const LENGTHS = [
  10, 254, 20, 2, 40, 4, 80, 6, 160, 8, 60, 10, 14, 12, 26, 14,
  12, 16, 24, 18, 48, 20, 96, 22, 192, 24, 72, 26, 16, 28, 32, 30,
];

/** The pulse's four duty sequences, one bit per sequencer step. */
const DUTY = [0b01000000, 0b01100000, 0b01111000, 0b10011111];

/** The triangle's 32-step sequence. */
const TRIANGLE = [
  15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0,
  0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
];

/** NTSC periods in CPU cycles: the noise timer's, and the DMC's per output bit. */
const NOISE_PERIODS = [4, 8, 16, 32, 64, 96, 128, 160, 202, 254, 380, 508, 762, 1016, 2034, 4068];
const DMC_RATES = [428, 380, 340, 320, 286, 254, 226, 214, 190, 160, 142, 128, 106, 84, 72, 54];

// Frame-counter steps, in CPU cycles since the counter was last reset.
const STEP1 = 7457;
const STEP2 = 14913;
const STEP3 = 22371;
const STEP4_IRQ = 29828;
const STEP4 = 29829;
const PERIOD4 = 29830;
const STEP5 = 37281;
const PERIOD5 = 37282;

/** The NTSC CPU clock: the stream's samples are counted against it. */
export const CPU_HZ = 39375000 / 22;

/** The APU's IRQ sources, as bits of the console's IRQ line. */
export const IRQ_FRAME = 0x01;
export const IRQ_DMC = 0x02;

export interface ApuHost {
  irq: number;
  /** CPU cycles since power-on: the frame counter's write delay depends on their parity. */
  readonly cycles: number;
}

/** A length counter, with its halt flag and the write timing the 2005 tests pin: a halt change or a
 * reload written in the cycle a half-frame clocks the counters takes effect after that clock (and the
 * reload is lost if the counter was clocked from non-zero). */
class Length {
  count = 0;
  halt = false;
  private newHalt = false;
  private reload = -1;
  enabled = false;

  setHalt(on: boolean): void {
    this.newHalt = on;
  }

  load(index: number): void {
    if (this.enabled) this.reload = LENGTHS[index];
  }

  clock(): boolean {
    if (!this.halt && this.count > 0) {
      this.count--;
      return true;
    }
    return false;
  }

  /** The end of the APU's cycle: pending writes land, unless a clock from non-zero just happened. */
  settle(clocked: boolean): void {
    if (this.reload >= 0) {
      if (!clocked) this.count = this.reload;
      this.reload = -1;
    }
    this.halt = this.newHalt;
  }

  disable(): void {
    this.enabled = false;
    this.count = 0;
    this.reload = -1;
  }
}

class Envelope {
  start = false;
  loop = false;
  constant = false;
  volume = 0;
  private divider = 0;
  private decay = 0;

  write(v: number): void {
    this.loop = (v & 0x20) !== 0;
    this.constant = (v & 0x10) !== 0;
    this.volume = v & 15;
  }

  clock(): void {
    if (this.start) {
      this.start = false;
      this.decay = 15;
      this.divider = this.volume;
    } else if (this.divider === 0) {
      this.divider = this.volume;
      if (this.decay > 0) this.decay--;
      else if (this.loop) this.decay = 15;
    } else {
      this.divider--;
    }
  }

  get output(): number {
    return this.constant ? this.volume : this.decay;
  }
}

class Pulse {
  readonly length = new Length();
  readonly env = new Envelope();
  duty = 0;
  step = 0;
  period = 0;
  timer = 0;
  sweepOn = false;
  sweepPeriod = 0;
  negate = false;
  shift = 0;
  sweepReload = false;
  private sweepDivider = 0;
  /** Pulse 1 negates by ones' complement, pulse 2 by two's. */
  private readonly onesComplement: boolean;

  constructor(first: boolean) {
    this.onesComplement = first;
  }

  target(): number {
    const change = this.period >> this.shift;
    return this.negate ? this.period - change - (this.onesComplement ? 1 : 0) : this.period + change;
  }

  /** The sweep unit mutes the channel whether or not it is enabled. */
  muted(): boolean {
    return this.period < 8 || this.target() > 0x7ff;
  }

  clockSweep(): void {
    if (this.sweepDivider === 0 && this.sweepOn && this.shift > 0 && !this.muted()) {
      const t = this.target();
      if (t >= 0) this.period = t;
    }
    if (this.sweepDivider === 0 || this.sweepReload) {
      this.sweepDivider = this.sweepPeriod;
      this.sweepReload = false;
    } else {
      this.sweepDivider--;
    }
  }

  output(): number {
    if (this.length.count === 0 || this.muted() || ((DUTY[this.duty] >> (7 - this.step)) & 1) === 0) return 0;
    return this.env.output;
  }
}

export class APU {
  private readonly pulse1 = new Pulse(true);
  private readonly pulse2 = new Pulse(false);
  private readonly lengths: Length[];

  // The triangle.
  private readonly triLength = new Length();
  private triPeriod = 0;
  private triTimer = 0;
  private triStep = 0;
  private triControl = false;
  private linearReloadValue = 0;
  private linear = 0;
  private linearReload = false;

  // The noise.
  private readonly noiseLength = new Length();
  private readonly noiseEnv = new Envelope();
  private noisePeriod = NOISE_PERIODS[0];
  private noiseTimer = 0;
  private noiseShort = false;
  private lfsr = 1;

  // The DMC.
  private dmcIrqOn = false;
  private dmcLoop = false;
  private dmcPeriod = DMC_RATES[0];
  private dmcTimer = 0;
  private dmcLevel = 0;
  private dmcStart = 0xc000;
  private dmcLengthReg = 1;
  private dmcAddress = 0xc000;
  /** Sample bytes still to fetch. */
  private dmcRemaining = 0;
  private dmcBuffer = 0;
  private dmcBufferFull = false;
  private dmcShift = 0;
  private dmcBits = 8;
  private dmcSilent = true;
  /** The DMC wants its next byte: the CPU is halted at its next read to fetch it (DMC DMA). */
  dmcDue = false;
  /** Cycles until a fetch asked for by a $4015 write is due (it doesn't happen at once). */
  private dmcStartDelay = 0;

  // The frame counter.
  private fiveStep = false;
  private irqInhibit = false;
  private frameIrq = false;
  private frameCycle = 0;
  /** A $4017 write takes effect 3 or 4 cycles later: the countdown, and the value written. */
  private resetDelay = 0;
  private pendingMode = 0;
  /** The last value written to $4017: Reset writes it again. */
  private last4017 = 0;
  /** A length counter's halt flag or reload was written: they land at the end of the next cycle. */
  private lengthWrites = false;
  /** CPU cycles alternate: the pulse, noise and DMC timers run on every other one (APU cycles). */
  private apuCycle = false;

  // The mixer and the stream.
  private level = 0;
  private dirty = true;
  private rate = 48000;
  private phase = 0;
  private sum = 0;
  private summed = 0;
  private buf = new Float32Array(4096);
  private count = 0;

  private host: ApuHost;

  constructor(host: ApuHost) {
    this.host = host;
    this.lengths = [this.pulse1.length, this.pulse2.length, this.triLength, this.noiseLength];
  }

  /** The Reset button: the channels are silenced (as a $4015 write of 0), and $4017 is written again
   * with its last value, as at power-on, so the frame counter restarts in that mode. */
  reset(): void {
    this.writeRegister(0x4015, 0);
    this.setFrameIrq(false);
    this.irqInhibit = (this.last4017 & 0x40) !== 0;
    this.fiveStep = (this.last4017 & 0x80) !== 0;
    this.resetDelay = 0;
    this.frameCycle = 0;
    this.triStep = 0;
    this.dmcLevel &= 1;
    this.dirty = true;
  }

  // ---- the stream ----

  /** Samples per second of emulated time from now on (8,000 to 192,000). */
  setSampleRate(hz: number): void {
    if (!(hz >= 8000 && hz <= 192000)) throw new RangeError(`sample rate ${hz}: not 8000-192000 Hz`);
    this.rate = hz;
    this.phase = 0;
    this.sum = 0;
    this.summed = 0;
  }

  /** The samples made since the last call. */
  takeSamples(): Float32Array {
    const out = this.buf.slice(0, this.count);
    this.count = 0;
    return out;
  }

  private emit(v: number): void {
    if (this.count === this.buf.length) {
      if (this.buf.length < 1 << 22) {
        const bigger = new Float32Array(this.buf.length * 2);
        bigger.set(this.buf);
        this.buf = bigger;
      } else {
        // Nobody is taking the samples: keep the newest half.
        this.buf.copyWithin(0, this.buf.length >> 1);
        this.count = this.buf.length >> 1;
      }
    }
    this.buf[this.count++] = v;
  }

  /** NESdev "APU Mixer", the formula. */
  private mix(): number {
    const p = this.pulse1.output() + this.pulse2.output();
    const pulse = p === 0 ? 0 : 95.88 / (8128 / p + 100);
    const t = TRIANGLE[this.triStep];
    const n = this.noiseLength.count === 0 || (this.lfsr & 1) ? 0 : this.noiseEnv.output;
    const s = t / 8227 + n / 12241 + this.dmcLevel / 22638;
    return pulse + (s === 0 ? 0 : 159.79 / (1 / s + 100));
  }

  // ---- the frame counter ----

  private setFrameIrq(on: boolean): void {
    this.frameIrq = on;
    if (on) this.host.irq |= IRQ_FRAME;
    else this.host.irq &= ~IRQ_FRAME;
  }

  private quarterFrame(): void {
    this.pulse1.env.clock();
    this.pulse2.env.clock();
    this.noiseEnv.clock();
    if (this.linearReload) this.linear = this.linearReloadValue;
    else if (this.linear > 0) this.linear--;
    if (!this.triControl) this.linearReload = false;
    this.dirty = true;
  }

  /** The half-frame clock: the length counters and the sweeps. Returns which counters it clocked. */
  private halfFrame(): number {
    let clocked = 0;
    for (let i = 0; i < 4; i++) if (this.lengths[i].clock()) clocked |= 1 << i;
    this.pulse1.clockSweep();
    this.pulse2.clockSweep();
    this.dirty = true;
    return clocked;
  }

  /** One CPU cycle. */
  tick(): void {
    let clocked = 0;
    if (this.resetDelay > 0 && --this.resetDelay === 0) {
      this.fiveStep = (this.pendingMode & 0x80) !== 0;
      this.frameCycle = 0;
      if (this.fiveStep) {
        this.quarterFrame();
        clocked = this.halfFrame();
      }
    } else {
      const c = ++this.frameCycle;
      if (c === STEP1 || c === STEP3) {
        this.quarterFrame();
      } else if (c === STEP2) {
        this.quarterFrame();
        clocked = this.halfFrame();
      } else if (this.fiveStep) {
        if (c === STEP5) {
          this.quarterFrame();
          clocked = this.halfFrame();
        } else if (c === PERIOD5) {
          this.frameCycle = 0;
        }
      } else if (c >= STEP4_IRQ) {
        if (c === STEP4) {
          this.quarterFrame();
          clocked = this.halfFrame();
        }
        if (!this.irqInhibit) this.setFrameIrq(true);
        if (c === PERIOD4) this.frameCycle = 0;
      }
    }
    if (this.lengthWrites) {
      this.lengthWrites = false;
      for (let i = 0; i < 4; i++) this.lengths[i].settle((clocked & (1 << i)) !== 0);
    }

    // The triangle's timer runs at the CPU's rate; it steps only while both its counters are non-zero.
    if (--this.triTimer < 0) {
      this.triTimer = this.triPeriod;
      if (this.triLength.count > 0 && this.linear > 0) {
        this.triStep = (this.triStep + 1) & 31;
        this.dirty = true;
      }
    }

    this.apuCycle = !this.apuCycle;
    if (this.apuCycle) {
      this.clockPulse(this.pulse1);
      this.clockPulse(this.pulse2);
      if (--this.noiseTimer < 0) {
        this.noiseTimer = (this.noisePeriod >> 1) - 1;
        const fb = (this.lfsr ^ (this.lfsr >> (this.noiseShort ? 6 : 1))) & 1;
        this.lfsr = (this.lfsr >> 1) | (fb << 14);
        if (this.noiseLength.count > 0) this.dirty = true;
      }
      if (--this.dmcTimer < 0) {
        this.dmcTimer = (this.dmcPeriod >> 1) - 1;
        this.clockDmc();
      }
    }
    if (this.dmcStartDelay > 0 && --this.dmcStartDelay === 0) this.requestDmc();

    if (this.dirty) {
      this.level = this.mix();
      this.dirty = false;
    }
    this.sum += this.level;
    this.summed++;
    this.phase += this.rate;
    if (this.phase >= CPU_HZ) {
      this.phase -= CPU_HZ;
      this.emit(this.sum / this.summed);
      this.sum = 0;
      this.summed = 0;
    }
  }

  private clockPulse(p: Pulse): void {
    if (--p.timer < 0) {
      p.timer = p.period;
      p.step = (p.step + 1) & 7;
      if (p.length.count > 0) this.dirty = true;
    }
  }

  // ---- the DMC ----

  private clockDmc(): void {
    if (!this.dmcSilent) {
      if (this.dmcShift & 1) {
        if (this.dmcLevel <= 125) {
          this.dmcLevel += 2;
          this.dirty = true;
        }
      } else if (this.dmcLevel >= 2) {
        this.dmcLevel -= 2;
        this.dirty = true;
      }
    }
    this.dmcShift >>= 1;
    if (--this.dmcBits === 0) {
      this.dmcBits = 8;
      if (this.dmcBufferFull) {
        this.dmcSilent = false;
        this.dmcShift = this.dmcBuffer;
        this.dmcBufferFull = false;
        this.requestDmc();
      } else {
        this.dmcSilent = true;
      }
    }
  }

  private requestDmc(): void {
    if (!this.dmcBufferFull && this.dmcRemaining > 0) this.dmcDue = true;
  }

  /** The address the DMC's next fetch reads. */
  get dmcFetchAddress(): number {
    return this.dmcAddress;
  }

  /** The byte a DMC DMA fetched. */
  dmcFill(value: number): void {
    this.dmcDue = false;
    if (this.dmcRemaining === 0) return;
    this.dmcBuffer = value;
    this.dmcBufferFull = true;
    this.dmcAddress = this.dmcAddress === 0xffff ? 0x8000 : this.dmcAddress + 1;
    if (--this.dmcRemaining === 0) {
      if (this.dmcLoop) {
        this.restartDmc();
      } else if (this.dmcIrqOn) {
        this.host.irq |= IRQ_DMC;
      }
    }
  }

  private restartDmc(): void {
    this.dmcAddress = this.dmcStart;
    this.dmcRemaining = this.dmcLengthReg;
  }

  // ---- the registers ----

  readStatus(): number {
    const v = this.peekStatus();
    this.setFrameIrq(false);
    return v;
  }

  peekStatus(): number {
    let v = 0;
    for (let i = 0; i < 4; i++) if (this.lengths[i].count > 0) v |= 1 << i;
    if (this.dmcRemaining > 0) v |= 0x10;
    if (this.frameIrq) v |= 0x40;
    if (this.host.irq & IRQ_DMC) v |= 0x80;
    return v;
  }

  writeRegister(addr: number, value: number): void {
    this.dirty = true;
    switch (addr) {
      case 0x4000: case 0x4004: {
        const p = addr === 0x4000 ? this.pulse1 : this.pulse2;
        p.duty = value >> 6;
        p.env.write(value);
        p.length.setHalt((value & 0x20) !== 0);
        this.lengthWrites = true;
        break;
      }
      case 0x4001: case 0x4005: {
        const p = addr === 0x4001 ? this.pulse1 : this.pulse2;
        p.sweepOn = (value & 0x80) !== 0;
        p.sweepPeriod = (value >> 4) & 7;
        p.negate = (value & 0x08) !== 0;
        p.shift = value & 7;
        p.sweepReload = true;
        break;
      }
      case 0x4002: case 0x4006: {
        const p = addr === 0x4002 ? this.pulse1 : this.pulse2;
        p.period = (p.period & 0x700) | value;
        break;
      }
      case 0x4003: case 0x4007: {
        const p = addr === 0x4003 ? this.pulse1 : this.pulse2;
        p.period = (p.period & 0xff) | ((value & 7) << 8);
        p.length.load(value >> 3);
        this.lengthWrites = true;
        p.step = 0;
        p.env.start = true;
        break;
      }
      case 0x4008:
        this.triControl = (value & 0x80) !== 0;
        this.triLength.setHalt(this.triControl);
        this.lengthWrites = true;
        this.linearReloadValue = value & 0x7f;
        break;
      case 0x400a:
        this.triPeriod = (this.triPeriod & 0x700) | value;
        break;
      case 0x400b:
        this.triPeriod = (this.triPeriod & 0xff) | ((value & 7) << 8);
        this.triLength.load(value >> 3);
        this.lengthWrites = true;
        this.linearReload = true;
        break;
      case 0x400c:
        this.noiseEnv.write(value);
        this.noiseLength.setHalt((value & 0x20) !== 0);
        this.lengthWrites = true;
        break;
      case 0x400e:
        this.noiseShort = (value & 0x80) !== 0;
        this.noisePeriod = NOISE_PERIODS[value & 15];
        break;
      case 0x400f:
        this.noiseLength.load(value >> 3);
        this.lengthWrites = true;
        this.noiseEnv.start = true;
        break;
      case 0x4010:
        this.dmcIrqOn = (value & 0x80) !== 0;
        this.dmcLoop = (value & 0x40) !== 0;
        this.dmcPeriod = DMC_RATES[value & 15];
        if (!this.dmcIrqOn) this.host.irq &= ~IRQ_DMC;
        break;
      case 0x4011:
        this.dmcLevel = value & 0x7f;
        break;
      case 0x4012:
        this.dmcStart = 0xc000 | (value << 6);
        break;
      case 0x4013:
        this.dmcLengthReg = (value << 4) | 1;
        break;
      case 0x4015:
        for (let i = 0; i < 4; i++) {
          if (value & (1 << i)) this.lengths[i].enabled = true;
          else this.lengths[i].disable();
        }
        this.host.irq &= ~IRQ_DMC;
        if (value & 0x10) {
          if (this.dmcRemaining === 0) {
            this.restartDmc();
            // The load DMA halts the CPU on the get cycle of the 2nd APU cycle after this one:
            // the 3rd CPU cycle after a write on a put cycle, the 4th after one on a get (odd).
            if (!this.dmcBufferFull) this.dmcStartDelay = (this.host.cycles & 1) ? 3 : 2;
          }
        } else {
          this.dmcRemaining = 0;
          this.dmcDue = false;
        }
        break;
      case 0x4017:
        this.last4017 = value;
        this.irqInhibit = (value & 0x40) !== 0;
        if (this.irqInhibit) this.setFrameIrq(false);
        this.pendingMode = value;
        // Written on an odd CPU cycle (between APU cycles) the reset lands one cycle later.
        this.resetDelay = (this.host.cycles & 1) ? 4 : 3;
        break;
    }
  }
}
