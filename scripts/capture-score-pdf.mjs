// Local print QA. The PDF is an ignored intermediate, not a public artifact.
import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

const [page, name = 'score-print'] = process.argv.slice(2);
if (!page || !/^[\w-]+$/.test(name)) throw new Error('Usage: node scripts/capture-score-pdf.mjs <page ID> <name>');
const output = execFileSync('orca', ['pdf', '--page', page, '--json'], { encoding: 'utf8', maxBuffer: 30 * 1024 * 1024 });
const response = JSON.parse(output.slice(output.indexOf('{')));
if (!response.ok || !response.result?.data) throw new Error(JSON.stringify(response.error || response));
const directory = resolve('.data/tmp/pdfs');
mkdirSync(directory, { recursive: true });
const path = resolve(directory, `${name}.pdf`);
writeFileSync(path, Buffer.from(response.result.data, 'base64'));
console.log(path);
