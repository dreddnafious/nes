// The console: the CPU's bus, with 2 KiB of RAM, the PPU and APU registers, and the cartridge.
// Spec: https://www.nesdev.org/wiki/CPU_memory_map, Open_bus_behavior.
import { APU } from './apu.ts';
import { createMapper, parseRom, type Mapper } from './cartridge.ts';
import { Controller } from './controller.ts';
import { CPU, type CpuBus } from './cpu.ts';
import { PPU } from './ppu.ts';

export class NES implements CpuBus {
  readonly cpu: CPU;
  readonly ppu: PPU;
  readonly apu: APU;
  readonly mapper: Mapper;
  /** CPU RAM, $0000-$07FF, mirrored to $1FFF. Power-on fill is all $00, so runs are deterministic. */
  readonly ram = new Uint8Array(0x800);
  /** Controller 1 ($4016) and controller 2 ($4017). */
  readonly controllers: [Controller, Controller] = [new Controller(), new Controller()];
  irq = 0;
  /** The last value on the CPU's data bus: what a read of nothing returns. */
  private openBus = 0;

  /** Power on with a ROM file; throws RomError for a file it can't or won't run. */
  constructor(rom: Uint8Array) {
    this.mapper = createMapper(parseRom(rom));
    this.cpu = new CPU(this);
    this.ppu = new PPU(this.mapper);
    this.apu = new APU(this);
    this.cpu.reset();
  }

  get cycles(): number {
    return this.cpu.cycles;
  }

  /** The console's Reset button (a soft reset): RAM and the cartridge keep their contents. */
  reset(): void {
    this.ppu.reset();
    this.apu.reset();
    this.cpu.reset();
  }

  /** Emulate until the PPU finishes the frame it's in (one NTSC frame from power-on onwards). */
  runFrame(): void {
    const ppu = this.ppu;
    while (!ppu.frameDone) this.cpu.step();
    ppu.frameDone = false;
  }

  /** The frame the last runFrame() drew: 256x240 values, row-major from the top line. Bits 0-5 are
   * the colour index (greyscale applied), bits 6-8 the emphasis bits. The buffer is reused. */
  frameBuffer(): Uint16Array {
    return this.ppu.pixels;
  }

  /** The buttons held on a controller port (0 or 1), one bit each (controller.ts's BUTTON_*). */
  setButtons(port: number, buttons: number): void {
    this.controllers[port].buttons = buttons & 0xff;
  }

  /** The cartridge's battery-backed PRG-RAM, or null when it has none. The array is the cartridge's
   * own, so a power cycle can carry a save across by copying it into the new console's. */
  batteryRam(): Uint8Array | null {
    return this.mapper.info.battery ? this.mapper.prgRam : null;
  }

  /** The TV system the file's header names: 0 NTSC, 1 PAL, 2 either, 3 Dendy. Only NTSC is
   * emulated (Decided, Q4). */
  get region(): number {
    return this.mapper.info.region;
  }

  /** Run one instruction (or the interrupt or DMA due before it). */
  step(): void {
    this.cpu.step();
  }

  // ---- CpuBus ----

  get nmi(): boolean {
    return this.ppu.nmiOutput;
  }

  /** The first two of the cycle's three PPU dots, and the APU's cycle. */
  cycleStart(): void {
    const ppu = this.ppu;
    ppu.step();
    ppu.step();
    this.apu.tick();
  }

  /** The cycle's third PPU dot. */
  cycleEnd(): void {
    this.ppu.step();
  }

  read(addr: number): number {
    let v: number;
    if (addr < 0x2000) {
      v = this.ram[addr & 0x7ff];
    } else if (addr < 0x4000) {
      v = this.ppu.readRegister(addr & 7);
    } else if (addr < 0x4020) {
      if (addr === 0x4015) {
        // An internal read: bit 5 is open bus, and the external data bus keeps its value.
        return this.apu.readStatus() | (this.openBus & 0x20);
      }
      // The controllers drive bit 0; bits 5-7 are open bus.
      if (addr === 0x4016 || addr === 0x4017) v = (this.openBus & 0xe0) | this.controllers[addr - 0x4016].read();
      else v = this.openBus;
    } else {
      v = this.mapper.cpuRead(addr);
      if (v < 0) v = this.openBus;
    }
    this.openBus = v;
    return v;
  }

  write(addr: number, value: number): void {
    this.openBus = value;
    if (addr < 0x2000) {
      this.ram[addr & 0x7ff] = value;
    } else if (addr < 0x4000) {
      this.ppu.writeRegister(addr & 7, value);
    } else if (addr < 0x4020) {
      if (addr === 0x4014) {
        this.cpu.dmaPage = value;
      } else if (addr === 0x4016) {
        this.controllers[0].writeStrobe(value);
        this.controllers[1].writeStrobe(value);
      } else if (addr <= 0x4017) {
        this.apu.writeRegister(addr, value);
      }
    } else {
      this.mapper.cpuWrite(addr, value);
    }
  }

  /** A CPU-bus read with no side effects. */
  peek(addr: number): number {
    addr &= 0xffff;
    if (addr < 0x2000) return this.ram[addr & 0x7ff];
    if (addr < 0x4000) return this.ppu.latch;
    if (addr === 0x4015) return this.apu.peekStatus() | (this.openBus & 0x20);
    if (addr < 0x4020) return this.openBus;
    const v = this.mapper.cpuPeek(addr);
    return v < 0 ? this.openBus : v;
  }
}
