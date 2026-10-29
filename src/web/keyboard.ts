// The keyboard map for controller 1 (Decided at approval; cases/CONTRACT-I3.md). Keys are matched by
// KeyboardEvent.code, the physical key, so Shift held doesn't turn Z or X into something else.
import {
  BUTTON_A, BUTTON_B, BUTTON_DOWN, BUTTON_LEFT, BUTTON_RIGHT, BUTTON_SELECT, BUTTON_START, BUTTON_UP,
} from '../core/controller.ts';

export const KEY_MAP: Readonly<Record<string, number>> = {
  KeyX: BUTTON_A,
  KeyZ: BUTTON_B,
  ShiftRight: BUTTON_SELECT,     // Right Shift only: ShiftLeft is no button
  Enter: BUTTON_START,
  ArrowUp: BUTTON_UP,
  ArrowDown: BUTTON_DOWN,
  ArrowLeft: BUTTON_LEFT,
  ArrowRight: BUTTON_RIGHT,
};

const OPPOSITES: ReadonlyArray<[number, number]> = [[BUTTON_UP, BUTTON_DOWN], [BUTTON_LEFT, BUTTON_RIGHT]];

/** The mapped keys held now, and the order they went down in, so of two opposite directions the
 * newest press wins, and letting it go brings back the older one if it's still held. */
export class Keys {
  private held = new Map<string, number>();
  private order = 0;

  /** A keydown; true when the key is mapped (the caller then prevents its default). Key repeat
   * doesn't make a press newer. */
  down(code: string): boolean {
    if (!(code in KEY_MAP)) return false;
    if (!this.held.has(code)) this.held.set(code, ++this.order);
    return true;
  }

  up(code: string): boolean {
    if (!(code in KEY_MAP)) return false;
    this.held.delete(code);
    return true;
  }

  /** Every button released (on window blur, and on a load). */
  clear(): void {
    this.held.clear();
  }

  /** The controller's buttons, one bit each, never both of an opposite pair. */
  buttons(): number {
    let mask = 0;
    const newest = new Map<number, number>();
    for (const [code, at] of this.held) {
      const bit = KEY_MAP[code];
      mask |= bit;
      newest.set(bit, Math.max(newest.get(bit) ?? 0, at));
    }
    for (const [a, b] of OPPOSITES) {
      if ((mask & a) && (mask & b)) mask &= ~((newest.get(a)! > newest.get(b)!) ? b : a);
    }
    return mask;
  }
}
