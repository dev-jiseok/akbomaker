// Save Orca's base64 screenshot as a local QA artifact without printing pixels.
import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

const [page, name = 'preview'] = process.argv.slice(2);
if (!page || !/^[\w-]+$/.test(name)) throw new Error('Usage: node scripts/capture-preview.mjs <Orca page ID> <name>');
const output = execFileSync('orca', ['screenshot', '--page', page, '--json'], { encoding: 'utf8', maxBuffer: 20 * 1024 * 1024 });
const response = JSON.parse(output.slice(output.indexOf('{')));
if (!response.ok || !response.result?.data) throw new Error(JSON.stringify(response.error || response));
const directory = resolve('.data/screenshots');
mkdirSync(directory, { recursive: true });
const path = resolve(directory, `${name}.png`);
writeFileSync(path, Buffer.from(response.result.data, 'base64'));
console.log(path);
