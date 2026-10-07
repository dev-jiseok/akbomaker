// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ScoreRecognition, { recognitionSessionKey, type RecognitionJob } from './components/ScoreRecognition';
import { request } from './api';

vi.mock('./api', () => ({ request: vi.fn() }));
const api = vi.mocked(request);
const id = 'a'.repeat(32);
const url = `/api/score-omr/${id}/files/source-coordinates.json`;
const capability = { available: true, engine: 'Audiveris', issues: [], limits: { upload_mb: 25, max_pages: 4 }, tablature_supported: false };
const ready: RecognitionJob = {
  id, status: 'ready', progress: 100, message: '검토 필요', warnings: [],
  source_url: `/api/score-omr/${id}/files/source.pdf`, preview_urls: [],
  results: [{ id: 'R1', filename: 'score.musicxml', download_url: `/api/score-omr/${id}/files/score.musicxml`, sha256: 'c'.repeat(64) }],
  source_coordinates: { download_url: url, sha256: 'b'.repeat(64), pages: [1, 3], tab_staffs: 3, digit_candidates: 20, matched_digits: 18, rhythm_known: false, editable_musicxml: false },
};
let container: HTMLDivElement;
let root: Root;
const onPrepared = vi.fn(async () => undefined);

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  sessionStorage.clear(); window.localStorage?.clear(); sessionStorage.setItem(recognitionSessionKey, id);
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
  api.mockReset(); onPrepared.mockClear();
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); sessionStorage.clear(); window.localStorage?.clear(); });

async function mount(job: RecognitionJob) {
  api.mockImplementation(async path => {
    if (path === '/api/score-omr/status') return capability;
    if (path === `/api/score-omr/${id}`) return job;
    throw new Error(`Unexpected request: ${path}`);
  });
  await act(async () => root.render(<ScoreRecognition disabled={false} onPrepared={onPrepared} onReset={vi.fn()} onActivity={vi.fn()} />));
}

it('shows source-coordinate review and its limitations without importing it as a score', async () => {
  await mount(ready);
  expect(container.textContent).toContain('PDF 원본의 TAB 숫자·줄 확인 자료');
  expect(container.textContent).toContain('TAB 보표 3개');
  expect(container.textContent).toContain('숫자 후보 20개');
  expect(container.textContent).toContain('18개의 줄 위치');
  expect(container.textContent).toContain('정확도 점수가 아니며');
  expect(container.textContent).toContain('음표 길이가 없어 완성된 악보로 가져오지 않습니다');
  const download = [...container.querySelectorAll('a')].find(link => link.textContent === '원본 숫자·좌표 JSON 다운로드');
  expect(download?.getAttribute('href')).toBe(new URL(url, window.location.origin).href);
  expect(download?.hasAttribute('download')).toBe(true);
  expect(container.querySelectorAll('select[aria-label="가져올 인식 결과"] option')).toHaveLength(1);
  expect(onPrepared).not.toHaveBeenCalled();
  expect(api.mock.calls).toHaveLength(2);
});

it.each(['error', 'cancelled'] as const)('retains completed coordinate evidence when OMR is %s', async status => {
  await mount({ ...ready, status, results: [], error: status === 'error' ? '엔진 오류' : undefined });
  expect(container.textContent).toContain('원본 숫자·좌표 JSON 다운로드');
  expect(container.querySelector('select[aria-label="가져올 인식 결과"]')).toBeNull();
  expect(onPrepared).not.toHaveBeenCalled();
});

it.each([
  'https://other.invalid/api/score-omr/' + id + '/files/source-coordinates.json',
  '/api/score-omr/' + 'b'.repeat(32) + '/files/source-coordinates.json',
  'javascript:alert(1)',
])('does not expose a coordinate download outside the current same-origin job: %s', async download_url => {
  await mount({ ...ready, source_coordinates: { ...ready.source_coordinates!, download_url } });
  expect(container.textContent).not.toContain('원본 숫자·좌표 JSON 다운로드');
  expect(container.textContent).not.toContain('PDF 원본의 TAB 숫자·줄 확인 자료');
});

it('keeps older jobs without coordinate evidence usable', async () => {
  await mount({ ...ready, source_coordinates: undefined });
  expect(container.textContent).not.toContain('원본 숫자·좌표 JSON 다운로드');
  expect(container.textContent).toContain('가져오기용 MusicXML');
  expect(container.querySelectorAll('select[aria-label="가져올 인식 결과"] option')).toHaveLength(1);
});
