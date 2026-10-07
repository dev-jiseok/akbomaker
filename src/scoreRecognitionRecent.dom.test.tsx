// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ScoreRecognition, { recentRecognitions, recognitionRecentKey, recognitionSessionKey, type RecognitionJob } from './components/ScoreRecognition';
import { request } from './api';

vi.mock('./api', () => ({ request: vi.fn() }));
vi.mock('./components/TabScoreReview', () => ({ default: ({ jobId, onDirty, onActivity }: { jobId: string; onDirty: (dirty: boolean) => void; onActivity: (busy: boolean) => void }) => <section aria-label="검수 초안"><span>{jobId} 저장 초안</span><button onClick={() => onDirty(true)}>초안 수정</button><button onClick={() => onDirty(false)}>초안 저장</button><button onClick={() => onActivity(true)}>요청 시작</button><button onClick={() => onActivity(false)}>요청 끝</button></section> }));
const api = vi.mocked(request);
const first = 'a'.repeat(32), second = 'b'.repeat(32);
const capability = { available: true, tab_review_available: true, engine: 'Audiveris', issues: [], limits: { upload_mb: 25, max_pages: 4 }, tablature_supported: false };
function job(id: string): RecognitionJob {
  return { id, status: 'ready', progress: 100, message: '검토 필요', warnings: [], notation: 'tab',
    source_url: `/api/score-omr/${id}/files/source.pdf`, preview_urls: [], results: [],
    source_coordinates: { download_url: `/api/score-omr/${id}/files/source-coordinates.json`, sha256: 'c'.repeat(64), pages: [1], tab_staffs: 1, digit_candidates: 2, matched_digits: 2, rhythm_known: false, editable_musicxml: false } };
}
function record(id = first, title = '베이스 연습') { return { id, title, status: 'ready', notation: 'tab', opened_at: 1_791_357_600_000 }; }
function seed(values = [record()]) { window.localStorage.setItem(recognitionRecentKey, JSON.stringify(values)); }
let container: HTMLDivElement, root: Root;
const onDirty = vi.fn(), onReset = vi.fn(), onActivity = vi.fn(), onImported = vi.fn();
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  // Node's experimental localStorage can shadow jsdom's accessor; keep a
  // distinct browser-local store so closing the session does not erase it.
  const data = new Map<string, string>();
  vi.stubGlobal('localStorage', { getItem: (key: string) => data.get(key) ?? null, setItem: (key: string, value: string) => data.set(key, String(value)), removeItem: (key: string) => data.delete(key), clear: () => data.clear(), key: (index: number) => [...data.keys()][index] ?? null, get length() { return data.size; } });
  sessionStorage.clear(); window.localStorage.clear(); api.mockReset(); onDirty.mockClear(); onReset.mockClear(); onActivity.mockClear(); onImported.mockClear();
  api.mockImplementation(async path => {
    if (path === '/api/score-omr/status') return capability;
    if (path === '/api/score-omr') return job(first);
    if (path === `/api/score-omr/${first}`) return job(first);
    if (path === `/api/score-omr/${second}`) return job(second);
    throw new Error(`Unexpected request: ${path}`);
  });
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); sessionStorage.clear(); window.localStorage.clear(); vi.unstubAllGlobals(); });
async function mount(disabled = false) { await act(async () => root.render(<ScoreRecognition disabled={disabled} onPrepared={vi.fn(async () => undefined)} onReset={onReset} onActivity={onActivity} onImported={onImported} onDirty={onDirty} />)); }
function button(label: string) { const value = [...container.querySelectorAll('button')].find(item => (item.getAttribute('aria-label') ?? item.textContent?.trim()) === label); if (!value) throw new Error(`Missing button: ${label}`); return value; }
async function click(label: string) { await act(async () => button(label).click()); }
const jobCalls = () => api.mock.calls.filter(([path]) => /^\/api\/score-omr\/[a-f0-9]{32}$/.test(path));

it('lists durable jobs after a tab is closed and explicitly opens the saved TAB review without starting recognition', async () => {
  seed(); await mount();
  expect(container.textContent).toContain('최근 PDF·이미지 검수 작업 (1)');
  expect(container.textContent).toContain('악보 내용은 저장하지 않습니다');
  expect(jobCalls()).toHaveLength(0);
  await click('베이스 연습 다시 열기');
  expect(container.querySelector('[aria-label="검수 초안"]')?.textContent).toContain(first + ' 저장 초안');
  expect(sessionStorage.getItem(recognitionSessionKey)).toBe(first);
  expect(jobCalls()).toHaveLength(1);
  expect(api.mock.calls.some(([path, options]) => path === '/api/score-omr' && options?.method === 'POST')).toBe(false);
  expect(onImported).not.toHaveBeenCalled();
  await act(async () => root.render(null)); sessionStorage.clear(); await mount();
  expect(container.textContent).toContain('베이스 연습');
  expect(container.querySelector('[aria-label="검수 초안"]')).toBeNull();
  await click('베이스 연습 다시 열기');
  expect(container.querySelector('[aria-label="검수 초안"]')).not.toBeNull();
});

it('retains multiple originals when opening another PDF and when clearing the current selection', async () => {
  seed([record(), record(second, '기타 연습')]); await mount();
  await click('베이스 연습 다시 열기'); await click('기타 연습 다시 열기');
  expect(container.querySelector('[aria-label="검수 초안"]')?.textContent).toContain(second);
  expect(recentRecognitions()).toHaveLength(2);
  await click('새 인식 · 이전 결과 선택 해제');
  expect(sessionStorage.getItem(recognitionSessionKey)).toBeNull();
  expect(recentRecognitions()).toHaveLength(2);
  await click('베이스 연습 다시 열기');
  expect(container.querySelector('[aria-label="검수 초안"]')?.textContent).toContain(first);
});

it('blocks recent navigation and removal while unsaved or busy, keeping the parent dirty guard informed', async () => {
  seed([record(), record(second, '기타 연습')]); await mount(); await click('베이스 연습 다시 열기');
  await click('초안 수정');
  expect(onDirty).toHaveBeenLastCalledWith(true);
  expect(button('기타 연습 다시 열기').disabled).toBe(true);
  expect(button('베이스 연습 최근 목록에서 제거').disabled).toBe(true);
  await click('기타 연습 다시 열기'); expect(jobCalls()).toHaveLength(1);
  await click('초안 저장'); expect(button('기타 연습 다시 열기').disabled).toBe(false);
  await click('요청 시작'); expect(button('기타 연습 다시 열기').disabled).toBe(true);
  await click('요청 끝'); await click('기타 연습 다시 열기');
  expect(jobCalls()).toHaveLength(2); expect(onDirty).toHaveBeenLastCalledWith(false);
});

it('respects the outer disabled guard for opening and forgetting records', async () => {
  seed(); await mount(true);
  expect(button('베이스 연습 다시 열기').disabled).toBe(true);
  expect(button('베이스 연습 최근 목록에서 제거').disabled).toBe(true);
  await click('베이스 연습 다시 열기'); expect(jobCalls()).toHaveLength(0);
});

it('deduplicates reopen clicks and keeps controls locked while the restore request is pending', async () => {
  seed(); let finish!: (value: RecognitionJob) => void;
  const pending = new Promise<RecognitionJob>(resolve => { finish = resolve; });
  api.mockImplementation(path => path === '/api/score-omr/status' ? Promise.resolve(capability) : pending);
  await mount();
  await act(async () => { button('베이스 연습 다시 열기').click(); button('베이스 연습 다시 열기').click(); });
  expect(jobCalls()).toHaveLength(1); expect(button('베이스 연습 다시 열기').disabled).toBe(true);
  expect(button('베이스 연습 최근 목록에서 제거').disabled).toBe(true);
  await act(async () => finish(job(first)));
  expect(button('베이스 연습 다시 열기').disabled).toBe(false);
  expect(container.querySelector('[aria-label="검수 초안"]')).not.toBeNull();
});

it('removes an active recent entry and its automatic session pointer without deleting its loaded original', async () => {
  seed(); await mount(); await click('베이스 연습 다시 열기'); await click('베이스 연습 최근 목록에서 제거');
  expect(recentRecognitions()).toHaveLength(0); expect(sessionStorage.getItem(recognitionSessionKey)).toBeNull();
  expect(container.querySelector('[aria-label="검수 초안"]')?.textContent).toContain(first);
  expect(api.mock.calls.some(([, options]) => options?.method === 'DELETE')).toBe(false);
});

it('preserves missing records until explicit local-only removal and never sends server deletion', async () => {
  seed(); api.mockImplementation(async path => { if (path === '/api/score-omr/status') return capability; throw Object.assign(new Error('찾을 수 없음'), { status: 404 }); });
  await mount(); await click('베이스 연습 다시 열기');
  expect(container.textContent).toContain('서버에서 이 인식 작업을 찾을 수 없어요');
  expect(recentRecognitions()).toHaveLength(1);
  await click('찾을 수 없는 작업의 최근 기록 제거');
  expect(recentRecognitions()).toHaveLength(0); expect(sessionStorage.getItem(recognitionSessionKey)).toBeNull();
  expect(container.textContent).toContain('서버의 원본 파일과 검수 초안은 삭제하지 않았습니다');
  expect(api.mock.calls.some(([, options]) => options?.method === 'DELETE')).toBe(false);
});

it('keeps transient failures retryable instead of labeling or removing them as deleted', async () => {
  seed(); let attempts = 0;
  api.mockImplementation(async path => { if (path === '/api/score-omr/status') return capability; if (++attempts === 1) throw new Error('연결 문제'); return job(first); });
  await mount(); await click('베이스 연습 다시 열기');
  expect(container.textContent).toContain('이전 인식 작업을 불러오지 못했어요');
  expect(container.textContent).not.toContain('찾을 수 없는 작업의 최근 기록 제거');
  expect(recentRecognitions()).toHaveLength(1);
  await click('이전 인식 다시 불러오기');
  expect(container.querySelector('[aria-label="검수 초안"]')).not.toBeNull();
});

it('preserves the existing session restore and migrates it into durable recent metadata', async () => {
  sessionStorage.setItem(recognitionSessionKey, first); await mount();
  expect(container.textContent).toContain('인식 초안 준비됨');
  expect(recentRecognitions()[0]).toMatchObject({ id: first, status: 'ready', notation: 'tab' });
  expect(container.querySelector('[aria-label="검수 초안"]')).toBeNull();
  expect(onImported).not.toHaveBeenCalled();
});

it('records a newly uploaded filename as a bounded title, but not file contents or source URLs', async () => {
  await mount(); const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
  Object.defineProperty(input, 'files', { value: [new File(['sensitive PDF contents'], '직접 만든 악보.pdf', { type: 'application/pdf' })] });
  await act(async () => input.dispatchEvent(new Event('change', { bubbles: true })));
  await click('악보 인식 시작');
  expect(recentRecognitions()[0]?.title).toBe('직접 만든 악보');
  const data = JSON.parse(window.localStorage.getItem(recognitionRecentKey)!);
  expect(Object.keys(data[0]).sort()).toEqual(['id', 'notation', 'opened_at', 'status', 'title']);
  expect(JSON.stringify(data)).not.toContain('sensitive PDF contents'); expect(JSON.stringify(data)).not.toContain('/api/');
});

it('validates, deduplicates and bounds browser metadata without automatically requesting arbitrary IDs', async () => {
  const values = Array.from({ length: 15 }, (_, index) => ({ ...record(index.toString(16).padStart(32, '0'), `악보 ${index}`), opened_at: 1000 + index, extra: 'not retained' }));
  window.localStorage.setItem(recognitionRecentKey, JSON.stringify([{ ...record('../unsafe'), opened_at: 2000 }, { ...record(), title: 'x'.repeat(161) }, { ...record(), opened_at: 'invalid' }, ...values, values[0]]));
  await mount(); expect(recentRecognitions()).toHaveLength(12);
  expect(recentRecognitions()[0].title).toBe('악보 14'); expect(recentRecognitions()[0]).not.toHaveProperty('extra');
  expect(jobCalls()).toHaveLength(0);
});

it('handles malformed or unavailable browser storage without breaking recognition', async () => {
  window.localStorage.setItem(recognitionRecentKey, '{invalid'); expect(recentRecognitions()).toEqual([]);
  const get = vi.spyOn(window.localStorage, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
  const set = vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
  await mount(); expect(container.textContent).toContain('악보 인식 시작');
  expect(recentRecognitions()).toEqual([]); get.mockRestore(); set.mockRestore();
});

it('refreshes recent records from other tabs without replacing the active review', async () => {
  seed(); await mount(); await click('베이스 연습 다시 열기');
  seed([record(), record(second, '다른 탭의 기타')]);
  await act(async () => window.dispatchEvent(new StorageEvent('storage', { key: recognitionRecentKey })));
  expect(container.textContent).toContain('다른 탭의 기타');
  expect(container.querySelector('[aria-label="검수 초안"]')?.textContent).toContain(first);
});
