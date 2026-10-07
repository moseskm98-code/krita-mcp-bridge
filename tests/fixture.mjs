import { writeFile } from 'node:fs/promises';
import sharp from 'sharp';

// Synthetic image only; no personal photographs or external downloads.
export async function createFixture(path) {
  const width = 1200, height = 1600;
  const pixels = Buffer.alloc(width * height * 4);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const i = (y * width + x) * 4;
    pixels[i] = 50 + Math.floor(x * 100 / width);
    pixels[i + 1] = 75 + Math.floor(y * 90 / height);
    pixels[i + 2] = 155 + Math.floor(x * 60 / width);
    pixels[i + 3] = 255;
  }
  const png = await sharp(pixels, { raw: { width, height, channels: 4 } }).png().toBuffer();
  await writeFile(path, png, { flag: 'wx' });
}
