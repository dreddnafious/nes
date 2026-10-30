// The standard controller behind $4016/$4017: an 8-bit shift register, reloaded from the buttons
// while the strobe ($4016 bit 0) is high.
// Spec: https://www.nesdev.org/wiki/Standard_controller

// Button bits, in the order the register shifts them out.
export const BUTTON_A = 0x01;
export const BUTTON_B = 0x02;
export const BUTTON_SELECT = 0x04;
export const BUTTON_START = 0x08;
export const BUTTON_UP = 0x10;
export const BUTTON_DOWN = 0x20;
export const BUTTON_LEFT = 0x40;
export const BUTTON_RIGHT = 0x80;

export class Controller {
  /** The buttons held now, one bit each (BUTTON_*). */
  buttons = 0;
  private strobe = false;
  private shift = 0;

  /** A write to $4016: bit 0 is the strobe, shared by both ports. */
  writeStrobe(value: number): void {
    this.strobe = (value & 1) !== 0;
    if (this.strobe) this.shift = this.buttons;
  }

  /** A read of the port: the next button in bit 0. While the strobe is high every read is A; after
   * the 8th read an official controller returns 1. */
  read(): number {
    if (this.strobe) return this.buttons & 1;
    const bit = this.shift & 1;
    this.shift = (this.shift >> 1) | 0x80;
    return bit;
  }

  /** What the next read would return, with no side effects. */
  peek(): number {
    return this.strobe ? this.buttons & 1 : this.shift & 1;
  }
}
