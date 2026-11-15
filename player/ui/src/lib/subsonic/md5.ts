/* MD5, because Subsonic's token authentication is `t = md5(password + salt)` and no WebCrypto
 * digest can produce it.
 *
 * This is not a security primitive in the sense that matters here: the digest authenticates a
 * password to a server that already knows it, and the alternative — sending `p=` in clear — is
 * strictly worse over the same connection. It is a protocol requirement, so it lives in its
 * own small file with known-answer tests rather than being hidden inside the auth code.
 */

/* Per-round left-rotate amounts. Round r uses the four entries r*4 .. r*4+3. */
const SHIFTS = [7, 12, 17, 22, 5, 9, 14, 20, 4, 11, 16, 23, 6, 10, 15, 21];

/* floor(abs(sin(i + 1)) * 2^32), the RFC 1321 constant table, computed once. */
const K = (() => {
  const table = new Int32Array(64);
  for (let i = 0; i < 64; i++) table[i] = Math.floor(Math.abs(Math.sin(i + 1)) * 0x100000000) | 0;
  return table;
})();

const HEX = "0123456789abcdef";

/** The lowercase hex MD5 digest of the UTF-8 bytes of `input`. */
export function md5Hex(input: string): string {
  const message = new TextEncoder().encode(input);
  const bitLength = message.length * 8;

  /* A 0x80 byte, zero padding, then the bit length as a little-endian 64-bit integer. */
  const padded = new Uint8Array((((message.length + 8) >>> 6) + 1) * 64);
  padded.set(message);
  padded[message.length] = 0x80;

  const view = new DataView(padded.buffer);
  view.setUint32(padded.length - 8, bitLength >>> 0, true);
  view.setUint32(padded.length - 4, Math.floor(bitLength / 0x100000000) >>> 0, true);

  let a0 = 0x67452301;
  let b0 = 0xefcdab89;
  let c0 = 0x98badcfe;
  let d0 = 0x10325476;

  const words = new Int32Array(16);

  for (let offset = 0; offset < padded.length; offset += 64) {
    for (let i = 0; i < 16; i++) words[i] = view.getInt32(offset + i * 4, true);

    let a = a0;
    let b = b0;
    let c = c0;
    let d = d0;

    for (let i = 0; i < 64; i++) {
      let f: number;
      let g: number;

      if (i < 16) {
        f = (b & c) | (~b & d);
        g = i;
      } else if (i < 32) {
        f = (d & b) | (~d & c);
        g = (5 * i + 1) & 15;
      } else if (i < 48) {
        f = b ^ c ^ d;
        g = (3 * i + 5) & 15;
      } else {
        f = c ^ (b | ~d);
        g = (7 * i) & 15;
      }

      const summed = (a + f + K[i]! + words[g]!) | 0;
      const shift = SHIFTS[(((i >> 4) << 2) + (i & 3)) % 16]!;
      const rotated = (summed << shift) | (summed >>> (32 - shift));

      a = d;
      d = c;
      c = b;
      b = (b + rotated) | 0;
    }

    a0 = (a0 + a) | 0;
    b0 = (b0 + b) | 0;
    c0 = (c0 + c) | 0;
    d0 = (d0 + d) | 0;
  }

  let out = "";
  for (const word of [a0, b0, c0, d0]) {
    for (let i = 0; i < 4; i++) {
      const byte = (word >>> (i * 8)) & 0xff;
      out += HEX[(byte >>> 4) & 0xf]! + HEX[byte & 0xf]!;
    }
  }
  return out;
}