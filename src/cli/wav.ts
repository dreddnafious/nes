// A WAV encoder for the CLI's sound: RIFF WAVE, mono, 32-bit IEEE float (format 3), so the file holds
// the core's samples exactly.
// Spec: the RIFF WAVE format (a "fmt " chunk, then a "data" chunk).

export function encodeWav(rate: number, chunks: Float32Array[]): Uint8Array {
  const count = chunks.reduce((n, c) => n + c.length, 0);
  const out = Buffer.alloc(44 + count * 4);
  out.write('RIFF', 0, 'latin1');
  out.writeUInt32LE(36 + count * 4, 4);
  out.write('WAVE', 8, 'latin1');
  out.write('fmt ', 12, 'latin1');
  out.writeUInt32LE(16, 16);
  out.writeUInt16LE(3, 20);           // IEEE float
  out.writeUInt16LE(1, 22);           // mono
  out.writeUInt32LE(rate, 24);
  out.writeUInt32LE(rate * 4, 28);    // bytes a second
  out.writeUInt16LE(4, 32);           // bytes a frame
  out.writeUInt16LE(32, 34);          // bits a sample
  out.write('data', 36, 'latin1');
  out.writeUInt32LE(count * 4, 40);
  let at = 44;
  for (const c of chunks) {
    for (let i = 0; i < c.length; i++, at += 4) out.writeFloatLE(c[i], at);
  }
  return out;
}
