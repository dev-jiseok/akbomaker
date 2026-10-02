// Rasterize the actual rendered score SVGs for engraving QA, including when the
// embedded webview compositor is suspended and native screenshots are blank.
import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

const [page, name = 'score', crop] = process.argv.slice(2);
if (!page || !/^[\w-]+$/.test(name)) throw new Error('Usage: node scripts/capture-score.mjs <page ID> <name>');
const expression = `(async () => {
  await document.fonts.ready;
  const pages = Array.from(document.querySelectorAll('.engraving svg'));
  if (!pages.length) throw new Error('No rendered score');
  const canvas = document.createElement('canvas');
  canvas.width = 1200;
  canvas.height = Math.ceil(pages.reduce((total, svg) => total + svg.viewBox.baseVal.height * 1200 / svg.viewBox.baseVal.width, 0));
  if (${crop === '--top'}) canvas.height = Math.min(canvas.height, 450);
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = 'white'; ctx.fillRect(0, 0, canvas.width, canvas.height);
  let y = 0;
  for (const svg of pages) {
    const copy = svg.cloneNode(true);
    copy.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    copy.setAttribute('width', '1200');
    const height = svg.viewBox.baseVal.height * 1200 / svg.viewBox.baseVal.width;
    copy.setAttribute('height', String(height));
    const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(copy)], {type:'image/svg+xml'}));
    const img = new Image(); img.src = url; await img.decode();
    ctx.drawImage(img, 0, y, 1200, height); y += height;
    URL.revokeObjectURL(url);
  }
  return canvas.toDataURL('image/png').split(',')[1];
})()`;
const output = execFileSync('orca', ['eval', '--page', page, '--expression', expression, '--json'], { encoding: 'utf8', maxBuffer: 20 * 1024 * 1024 });
const response = JSON.parse(output.slice(output.indexOf('{')));
if (!response.ok || !response.result?.result) throw new Error(JSON.stringify(response.error || response));
const directory = resolve('.data/screenshots');
mkdirSync(directory, { recursive: true });
const path = resolve(directory, `${name}.png`);
writeFileSync(path, Buffer.from(response.result.result, 'base64'));
console.log(path);
