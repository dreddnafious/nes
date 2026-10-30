// A PNG encoder for the CLI's screenshots: 8-bit RGB, no interlace, filter 0 on every row.
// Spec: https://www.w3.org/TR/png/ (signature, IHDR, IDAT, IEND; each chunk's CRC-32).
import { crc32, deflateSync } from 'node:zlib';

function chunk(type: string, body: Uint8Array): Buffer {
  const out = Buffer.alloc(12 + body.length);
  out.writeUInt32BE(body.length, 0);
  out.write(type, 4, 'latin1');
  out.set(body, 8);
  out.writeUInt32BE(crc32(out.subarray(4, 8 + body.length)), 8 + body.length);
  return out;
}

/** Encode width x height pixels, each 0xRRGGBB, row-major from the top. */
export function encodePng(width: number, height: number, rgb: ArrayLike<number>): Buffer {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;   // bit depth
  ihdr[9] = 2;   // colour type: RGB
  const stride = width * 3 + 1;
  const raw = Buffer.alloc(stride * height);
  for (let y = 0; y < height; y++) {
    let o = y * stride + 1;  // the row's filter byte stays 0 (none)
    for (let x = 0; x < width; x++) {
      const c = rgb[y * width + x];
      raw[o++] = c >> 16;
      raw[o++] = (c >> 8) & 0xff;
      raw[o++] = c & 0xff;
    }
  }
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(raw)),
    chunk('IEND', new Uint8Array(0)),
  ]);
}
