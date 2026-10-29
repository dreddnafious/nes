// One standard 2C02 palette: the RGB for each of the 64 colour indices, and a 512-entry table that
// also covers the three emphasis bits (frame value = index | emphasis << 6, as NES.frameBuffer gives).
// Tests compare frames by index, never by RGB, so this table can't fail one (Decided, Q5).
// Spec: https://www.nesdev.org/wiki/PPU_palettes (NTSC: $2001 bit 5 emphasises red, 6 green, 7 blue).

const NTSC = [
  0x7c7c7c, 0x0000fc, 0x0000bc, 0x4428bc, 0x940084, 0xa80020, 0xa81000, 0x881400,
  0x503000, 0x007800, 0x006800, 0x005800, 0x004058, 0x000000, 0x000000, 0x000000,
  0xbcbcbc, 0x0078f8, 0x0058f8, 0x6844fc, 0xd800cc, 0xe40058, 0xf83800, 0xe45c10,
  0xac7c00, 0x00b800, 0x00a800, 0x00a844, 0x008888, 0x000000, 0x000000, 0x000000,
  0xf8f8f8, 0x3cbcfc, 0x6888fc, 0x9878f8, 0xf878f8, 0xf85898, 0xf87858, 0xfca044,
  0xf8b800, 0xb8f818, 0x58d854, 0x58f898, 0x00e8d8, 0x787878, 0x000000, 0x000000,
  0xfcfcfc, 0xa4e4fc, 0xb8b8f8, 0xd8b8f8, 0xf8b8f8, 0xf8a4c0, 0xf0d0b0, 0xfce0a8,
  0xf8d878, 0xd8f878, 0xb8f8b8, 0xb8f8d8, 0x00fcfc, 0xf8d8f8, 0x000000, 0x000000,
];

/** Emphasis darkens the channels it doesn't name (to about 3/4). */
const ATTENUATE = 0.75;

/** RGB as 0xRRGGBB for each frame value 0-511. */
export const RGB: Uint32Array = (() => {
  const table = new Uint32Array(512);
  for (let e = 0; e < 8; e++) {
    for (let i = 0; i < 64; i++) {
      let r = NTSC[i] >> 16, g = (NTSC[i] >> 8) & 0xff, b = NTSC[i] & 0xff;
      if (e !== 0) {
        if (!(e & 1)) r = Math.round(r * ATTENUATE);
        if (!(e & 2)) g = Math.round(g * ATTENUATE);
        if (!(e & 4)) b = Math.round(b * ATTENUATE);
      }
      table[(e << 6) | i] = (r << 16) | (g << 8) | b;
    }
  }
  return table;
})();
