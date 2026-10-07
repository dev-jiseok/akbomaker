// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ScoreImport, { readSourceAttribution } from './components/ScoreImport';
import Workspace from './components/Workspace';
import { recognitionSessionKey, recognitionUrl, validRecognitionPages, type RecognitionJob } from './components/ScoreRecognition';
import { request } from './api';
import type { Job, ScoreDocument } from './types';

vi.mock('./api', () => ({ request: vi.fn() }));
vi.mock('./components/ScoreViewer', () => ({ default: ({ xml }: { xml: string }) => <div className="engraving" data-xml={xml}><svg aria-label="테스트 악보" /></div>, EmptyScore: () => <div>빈 악보</div> }));

let container: HTMLDivElement;
let root: Root;
const api = vi.mocked(request);
const capability = { available: true, engine: 'Audiveris', issues: [], limits: { upload_mb: 25, max_pages: 4 }, tablature_supported: false };
const listing = { title: '원본 악보', parts: [{ id: 'P1', name: 'Part 1', measures: 4 }, { id: 'P2', name: 'Part 2', measures: 8 }] };
const jobId = 'a'.repeat(32);
const baseJob: RecognitionJob = { id: jobId, status: 'queued', progress: 0, message: '인식 대기', warnings: ['가사 검토 필요'], source_url: `/api/score-omr/${jobId}/files/source.pdf`, preview_urls: [], results: [] };
const readyJob: RecognitionJob = { ...baseJob, status: 'ready', progress: 100, message: '완료', preview_urls: [`/api/score-omr/${jobId}/files/page-1.png`], results: [
  { id: 'R1', filename: 'first.mxl', download_url: `/api/score-omr/${jobId}/files/first.mxl`, sha256: 'a'.repeat(64) },
  { id: 'R2', filename: 'second.musicxml', download_url: `/api/score-omr/${jobId}/files/second.musicxml`, sha256: 'b'.repeat(64) },
] };
const score = { version: 1, instrument: 'drums', title: '현재 악보', bpm: 120, ticks: 16, revision: 'revision-1', edited: false, notes: [], annotations: [], layout: { preset: 'practice', measures_per_line: 4, show_numbers: true } } as ScoreDocument;

function deferred<T>() { let resolve!: (value: T) => void; let reject!: (error: Error) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
function button(text: string) { const found = [...container.querySelectorAll('button')].find(item => item.textContent?.trim() === text); if (!found) throw new Error(`Missing button: ${text}`); return found; }
async function click(text: string) { await act(async () => button(text).click()); }
async function choose(label: string, file: File) {
  const input = container.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)!;
  Object.defineProperty(input, 'files', { value: [file], configurable: true });
  await act(async () => input.dispatchEvent(new Event('change', { bubbles: true })));
}
async function select(label: string, value: string) {
  const input = container.querySelector<HTMLSelectElement>(`select[aria-label="${label}"]`)!;
  await act(async () => { input.value = value; input.dispatchEvent(new Event('change', { bubbles: true })); });
}
async function mount(props: Partial<React.ComponentProps<typeof ScoreImport>> = {}) { await act(async () => root.render(<ScoreImport disabled={false} {...props} />)); }
async function prepareOmr() {
  await click('PDF·이미지 · 악보 인식');
  await choose('PDF 이미지 악보 파일 선택', new File(['%PDF-1.7'], 'source.pdf', { type: 'application/pdf' }));
  await click('악보 인식 시작');
  await act(async () => { await vi.advanceTimersByTimeAsync(1200); });
}

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  sessionStorage.clear();
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
  vi.useFakeTimers();
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob(['<score-partwise/>'], { type: 'application/xml' }) }));
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn().mockReturnValue('blob:local-preview') });
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
  api.mockReset();
  api.mockImplementation(async (path) => {
    if (path === '/api/score-omr/status') return capability;
    if (path === '/api/score-omr') return baseJob;
    if (path === `/api/score-omr/${jobId}`) return readyJob;
    if (path === `/api/score-omr/${jobId}/cancel`) return { ...baseJob, status: 'cancelled' };
    if (path === '/api/score-import/inspect') return listing;
    if (path === '/api/score-import') return { id: 'project', source_type: 'musicxml' };
    if (path === '/api/source-scores') return { id: 'project', source_type: 'musicxml', score_preserved: { instrument: 'bass', part_id: 'P1', revision: 'r1' } };
    if (path === '/api/score-import/preserve-preview') return { xml: '<score-partwise><repeat/></score-partwise>', warnings: ['원본 연주기호 유지'], mode: 'layout-only' };
    if (path.endsWith('/import-preview')) return { document: score, warnings: [] };
    throw new Error(`Unexpected path: ${path}`);
  });
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('score import recognition and preservation', () => {
  it('keeps normal MusicXML import explicit and free of OMR fields', async () => {
    const onImported = vi.fn(); await mount({ onImported });
    await choose('MusicXML 악보 파일 선택', new File(['<xml/>'], 'source.musicxml'));
    expect(onImported).not.toHaveBeenCalled();
    expect(api).toHaveBeenCalledTimes(1);
    await select('가져올 파트', 'P2'); await select('가져올 악기', 'guitar');
    await click('격자 편집 프로젝트 열기');
    const body = api.mock.calls.find(([path]) => path === '/api/score-import')![1]!.body as FormData;
    expect(body.get('part_id')).toBe('P2'); expect(body.get('instrument')).toBe('guitar'); expect(body.has('omr_id')).toBe(false);
    expect(onImported).toHaveBeenCalledWith({ id: 'project', source_type: 'musicxml' });
  });

  it('does not inspect or import completed recognition until selected; requires review and provenance', async () => {
    const onImported = vi.fn(); await mount({ onImported }); await prepareOmr();
    expect(container.textContent).toContain('인식 초안 준비됨'); expect(container.textContent).toContain('결과 2개');
    expect(container.querySelector('img')?.src).toContain('/page-1.png');
    expect(api.mock.calls.some(([path]) => path === '/api/score-import/inspect')).toBe(false);
    expect(onImported).not.toHaveBeenCalled();
    await select('가져올 인식 결과', 'R2'); await click('선택한 결과의 파트 확인');
    expect(vi.mocked(fetch).mock.calls[0][0]).toContain('/second.musicxml');
    const check = container.querySelector<HTMLInputElement>('.recognition-review input')!;
    expect(check.checked).toBe(false); expect(button('원본 유지 프로젝트 열기').disabled).toBe(true);
    await act(async () => check.click());
    expect(button('원본 유지 프로젝트 열기').disabled).toBe(false);
    await click('원본 유지 프로젝트 열기');
    const body = api.mock.calls.find(([path]) => path === '/api/source-scores')![1]!.body as FormData;
    expect(body.get('omr_id')).toBe(jobId); expect(body.get('omr_result_id')).toBe('R2');
    expect(onImported).toHaveBeenCalledOnce();
  });

  it('resets review on part changes and removes prepared input on result changes', async () => {
    await mount(); await prepareOmr(); await click('선택한 결과의 파트 확인');
    await act(async () => container.querySelector<HTMLInputElement>('.recognition-review input')!.click());
    await select('가져올 파트', 'P2');
    expect(container.querySelector<HTMLInputElement>('.recognition-review input')!.checked).toBe(false);
    await select('가져올 인식 결과', 'R2');
    expect(container.querySelector('.import-part-selection')).toBeNull();
  });

  it('retains MusicXML access when the recognition engine is unavailable', async () => {
    api.mockResolvedValueOnce({ ...capability, available: false, issues: ['Audiveris 설정 필요'] });
    await mount(); await click('PDF·이미지 · 악보 인식');
    expect(container.textContent).toContain('Audiveris 설정 필요');
    await choose('PDF 이미지 악보 파일 선택', new File(['pdf'], 'source.pdf'));
    expect(button('악보 인식 시작').disabled).toBe(true);
    await click('MusicXML · 원본 악보 파일');
    expect(container.querySelector('input[aria-label="MusicXML 악보 파일 선택"]')).not.toBeNull();
  });

  it('deduplicates rapid recognition starts and aborts an upload on unmount', async () => {
    const upload = deferred<RecognitionJob>();
    api.mockImplementation(async path => path === '/api/score-omr/status' ? capability : upload.promise);
    await mount(); await click('PDF·이미지 · 악보 인식');
    await choose('PDF 이미지 악보 파일 선택', new File(['pdf'], 'source.pdf'));
    await act(async () => { button('악보 인식 시작').click(); button('악보 인식 시작').click(); });
    expect(api.mock.calls.filter(([path]) => path === '/api/score-omr')).toHaveLength(1);
    const signal = api.mock.calls.find(([path]) => path === '/api/score-omr')![1]!.signal;
    await act(async () => root.render(null)); expect(signal?.aborted).toBe(true);
    await act(async () => upload.resolve(readyJob));
    expect(container.textContent).toBe('');
  });

  it('ignores an in-flight ready response after cancellation', async () => {
    const status = deferred<RecognitionJob>(); const original = api.getMockImplementation()!;
    api.mockImplementation((path, options) => path === `/api/score-omr/${jobId}` ? status.promise : original(path, options));
    await mount(); await prepareOmr();
    expect(button('MusicXML · 원본 악보 파일').disabled).toBe(true);
    await click('인식 취소'); await act(async () => status.resolve(readyJob));
    expect(container.textContent).toContain('인식을 취소했어요');
    expect(container.querySelector('select[aria-label="가져올 인식 결과"]')).toBeNull();
    expect(button('MusicXML · 원본 악보 파일').disabled).toBe(false);
  });

  it('recovers polling after a failed cancellation and transient status failure', async () => {
    let polls = 0; const original = api.getMockImplementation()!;
    api.mockImplementation(async (path, options) => {
      if (path.endsWith('/cancel')) throw new Error('취소 요청 실패');
      if (path === `/api/score-omr/${jobId}`) { polls++; if (polls === 1) throw new Error('연결 끊김'); return readyJob; }
      return original(path, options);
    });
    await mount(); await prepareOmr(); expect(container.textContent).toContain('자동으로 다시 확인');
    await click('인식 취소'); expect(container.textContent).toContain('취소 요청 실패');
    await act(async () => { await vi.advanceTimersByTimeAsync(1200); });
    expect(container.textContent).toContain('인식 초안 준비됨'); expect(polls).toBe(2);
  });

  it('shows engine failures and missing results without offering import', async () => {
    const original = api.getMockImplementation()!;
    api.mockImplementation((path, options) => path === `/api/score-omr/${jobId}` ? Promise.resolve({ ...readyJob, results: [] }) : original(path, options));
    await mount(); await prepareOmr();
    expect(container.textContent).toContain('가져올 수 있는 MusicXML 결과가 없어요');
    expect(container.querySelector('.import-part-selection')).toBeNull();
  });

  it('releases image previews when changing formats and validates empty/large input', async () => {
    await mount(); await click('PDF·이미지 · 악보 인식');
    await choose('PDF 이미지 악보 파일 선택', new File(['png'], 'source.png'));
    expect(URL.createObjectURL).toHaveBeenCalledOnce();
    expect(container.querySelector('img')?.alt).toBe('선택한 원본 악보 이미지');
    await choose('PDF 이미지 악보 파일 선택', new File([], 'empty.pdf'));
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:local-preview');
    expect(button('악보 인식 시작').disabled).toBe(true); expect(container.textContent).toContain('25MB 이하');
  });

  it('keeps layout-only preview separate from editable import and cleans up scoped printing', async () => {
    const onImported = vi.fn(), onDocument = vi.fn(); await mount({ onImported, onDocument });
    await choose('MusicXML 악보 파일 선택', new File(['xml'], 'score.musicxml'));
    await select('미리보기 스타일', 'large'); await select('미리보기 한 줄 마디', '2');
    await click('원본 표기 유지 · 스타일 미리보기');
    const body = api.mock.calls.find(([path]) => path.endsWith('/preserve-preview'))![1]!.body as FormData;
    expect(body.get('preset')).toBe('large'); expect(body.get('measures_per_line')).toBe('2');
    expect(container.querySelector('.engraving')?.getAttribute('data-xml')).toContain('<repeat/>');
    expect(container.textContent).toContain('음표 편집 프로젝트를 만들거나 현재 편집본을 변경하지 않아요');
    expect(onImported).not.toHaveBeenCalled(); expect(onDocument).not.toHaveBeenCalled();
    const normalPaper = document.createElement('div'); normalPaper.id = 'print-score'; document.body.appendChild(normalPaper);
    const print = vi.spyOn(window, 'print').mockImplementation(() => {
      expect(document.body.hasAttribute('data-score-preserve-print')).toBe(true);
      expect(container.querySelector('[data-score-preserve-target]')).not.toBeNull();
      expect(normalPaper.hasAttribute('data-score-preserve-target')).toBe(false);
    });
    await click('이 미리보기 인쇄 / PDF 저장'); expect(print).toHaveBeenCalledOnce();
    await act(async () => window.dispatchEvent(new Event('afterprint')));
    expect(document.body.hasAttribute('data-score-preserve-print')).toBe(false);
    expect(container.querySelector('[data-score-preserve-target]')).toBeNull(); normalPaper.remove();
    await click('PDF·이미지 · 악보 인식'); expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:local-preview');
  });

  it('prevents a late editor import from replacing a newer revision', async () => {
    const result = deferred<{ document: ScoreDocument; warnings: string[] }>(); const onDocument = vi.fn();
    const original = api.getMockImplementation()!;
    api.mockImplementation((path, options) => path.endsWith('/import-preview') ? result.promise : original(path, options));
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    await mount({ document: score, endpoint: '/api/jobs/project/scores/drums', onDocument });
    await choose('MusicXML 악보 파일 선택', new File(['xml'], 'score.musicxml')); await click('편집 초안에 가져오기');
    await mount({ document: { ...score, revision: 'revision-2' }, endpoint: '/api/jobs/project/scores/drums', onDocument });
    await act(async () => result.resolve({ document: score, warnings: [] }));
    expect(onDocument).not.toHaveBeenCalled(); expect(container.textContent).toContain('악보 버전이 변경');
  });

  it('bounds page selections and rejects external or cross-job download URLs', () => {
    expect(validRecognitionPages('1, 3-5', 4)).toBe(true);
    for (const invalid of ['0', '1-5', '4-1', '1,2,3,4,5', '1e3', '', '1-9999']) expect(validRecognitionPages(invalid, 4)).toBe(false);
    expect(recognitionUrl(`/api/score-omr/${jobId}/files/a.mxl`, jobId)).toContain('/files/a.mxl');
    for (const invalid of ['https://untrusted.example/file.mxl', '/api/jobs/id/files/a.mxl', `/api/score-omr/${jobId}/../other/file.mxl`, 'javascript:alert(1)']) expect(recognitionUrl(invalid, jobId)).toBeUndefined();
  });

  it('continues polling when cancellation is accepted but the worker is still stopping', async () => {
    let polls = 0; const original = api.getMockImplementation()!;
    api.mockImplementation((path, options) => {
      if (path.endsWith('/cancel')) return Promise.resolve({ ...baseJob, status: 'running', message: '중단 중' });
      if (path === `/api/score-omr/${jobId}`) return Promise.resolve(++polls === 1 ? { ...baseJob, status: 'running' } : { ...baseJob, status: 'cancelled' });
      return original(path, options);
    });
    await mount(); await prepareOmr(); await click('인식 취소');
    expect(container.textContent).toContain('중단 중');
    await act(async () => { await vi.advanceTimersByTimeAsync(1200); });
    expect(container.textContent).toContain('인식을 취소했어요');
    expect(button('MusicXML · 원본 악보 파일').disabled).toBe(false);
  });

  it('retains original OMR provenance links in an imported project without external previews', async () => {
    const imported: Job = { id: 'project', title: '가져온 곡', source_type: 'musicxml', status: 'completed', duration: 2, bpm: 120,
      demo: false, stage: 'completed', progress: 100, message: '', error: null, created_at: '2026-10-07', original_url: null, residual_url: null,
      stems: [{ id: 'bass', label: '베이스', status: 'ready', score_status: 'ready', waveform: [], score_url: '/score.musicxml' }],
      score_omr: { id: jobId, result_id: 'R1', engine: 'Audiveris', source_url: baseJob.source_url, result_url: readyJob.results[0].download_url,
        preview_urls: [readyJob.preview_urls[0], 'https://untrusted.example/tracking.png'], source_sha256: 'a'.repeat(64), result_sha256: 'b'.repeat(64), warnings: ['가사 검토 필요'] },
    };
    await act(async () => root.render(<Workspace job={imported} health={null} onJob={vi.fn()} onNew={vi.fn()} onError={vi.fn()} onEditorDirty={vi.fn()} />));
    expect(container.textContent).toContain('PDF·이미지 인식 초안');
    expect(container.textContent).toContain('원본 TAB 복원이 아닙니다');
    expect([...container.querySelectorAll('a')].find(a => a.textContent === '원본 PDF·이미지 열기')?.getAttribute('href')).toContain(baseJob.source_url);
    expect([...container.querySelectorAll('a')].find(a => a.textContent === '가져오기용 MusicXML')?.getAttribute('href')).toContain('/first.mxl');
    expect(container.querySelectorAll('.workspace-omr-source img')).toHaveLength(1);
    expect(container.querySelector('audio')).toBeNull();
  });

  it('preserves source creator, rights, and credits as bounded plain text in the printed panel', async () => {
    const xml = '<score-partwise><identification><creator type="composer">Original Composer</creator><creator type="lyricist">Writer</creator><rights>© 2026 Publisher &lt;img src=x onerror=alert(1)&gt;</rights></identification><credit><credit-words>원본 악보</credit-words><credit-words>Original Composer</credit-words><credit-words>https://publisher.example</credit-words></credit></score-partwise>';
    const original = api.getMockImplementation()!;
    api.mockImplementation((path, options) => path.endsWith('/preserve-preview') ? Promise.resolve({ xml, mode: 'layout-only', warnings: [] }) : original(path, options));
    await mount(); await choose('MusicXML 악보 파일 선택', new File(['xml'], 'score.musicxml'));
    await click('원본 표기 유지 · 스타일 미리보기');
    const attribution = container.querySelector('.import-preserved-paper .import-source-attribution')!;
    expect(attribution.textContent).toContain('작곡: Original Composer'); expect(attribution.textContent).toContain('작사: Writer');
    expect(attribution.textContent).toContain('© 2026 Publisher <img src=x onerror=alert(1)>');
    expect(attribution.querySelector('img')).toBeNull(); expect(attribution.querySelector('a')).toBeNull();
    expect(attribution.textContent!.match(/Original Composer/g)).toHaveLength(1);
    expect(attribution.textContent).not.toContain('원본 악보');
    const bounded = readSourceAttribution(`<score-partwise><identification><rights>${'x'.repeat(10000)}</rights></identification></score-partwise>`, 'Title');
    expect(bounded.truncated).toBe(true); expect(bounded.lines[0].length).toBeLessThan(420);
  });

  it('restores the last recognition on remount without applying it or retaining acknowledgment', async () => {
    const onImported = vi.fn(); await mount({ onImported }); await prepareOmr();
    expect(sessionStorage.getItem(recognitionSessionKey)).toBe(jobId);
    await click('선택한 결과의 파트 확인');
    await act(async () => container.querySelector<HTMLInputElement>('.recognition-review input')!.click());
    await act(async () => root.render(null)); await mount({ onImported });
    expect(container.textContent).toContain('인식 초안 준비됨'); expect(container.querySelector('.import-part-selection')).toBeNull();
    expect(onImported).not.toHaveBeenCalled();
    await click('선택한 결과의 파트 확인');
    expect(container.querySelector<HTMLInputElement>('.recognition-review input')!.checked).toBe(false);
    await click('새 인식 · 이전 결과 선택 해제');
    expect(sessionStorage.getItem(recognitionSessionKey)).toBeNull();
    expect(container.querySelector('.import-part-selection')).toBeNull();
  });

  it('allows retry or explicit forget when restoring a previous recognition fails', async () => {
    sessionStorage.setItem(recognitionSessionKey, jobId);
    let attempts = 0; const original = api.getMockImplementation()!;
    api.mockImplementation((path, options) => path === `/api/score-omr/${jobId}` && ++attempts === 1 ? Promise.reject(new Error('일시적인 연결 문제')) : original(path, options));
    await mount(); expect(container.textContent).toContain('이전 인식 작업을 불러오지 못했어요');
    expect(sessionStorage.getItem(recognitionSessionKey)).toBe(jobId);
    await click('이전 인식 다시 불러오기'); expect(container.textContent).toContain('인식 초안 준비됨');
    expect(api.mock.calls.filter(([path]) => path === '/api/score-omr')).toHaveLength(0);
  });

  it('separates raw engine downloads from compatibility-prepared results and only imports the prepared file', async () => {
    const normalized = { ...readyJob, results: [{ ...readyJob.results[0], filename: 'first.normalized.musicxml', download_url: `/api/score-omr/${jobId}/files/first.normalized.musicxml`, raw_download_url: readyJob.results[0].download_url, normalizations: ['드럼 번호 호환성 보정'] }, { ...readyJob.results[1], raw_download_url: 'https://untrusted.example/raw.mxl' }] };
    const original = api.getMockImplementation()!;
    api.mockImplementation((path, options) => path === `/api/score-omr/${jobId}` ? Promise.resolve(normalized) : original(path, options));
    await mount(); await prepareOmr();
    const link = (label: string) => [...container.querySelectorAll('a')].find(item => item.textContent === label);
    expect(link('가져오기용 MusicXML')?.href).toContain('/first.normalized.musicxml');
    expect(link('엔진 원본 MusicXML (미보정)')?.href).toContain('/first.mxl');
    expect(container.textContent).toContain('템포·음표 위치·길이를 바꾸거나 인식 오류를 고친 결과는 아니에요');
    await click('선택한 결과의 파트 확인');
    expect(vi.mocked(fetch).mock.calls[0][0]).toContain('/first.normalized.musicxml');
    await select('가져올 인식 결과', 'R2');
    expect(link('엔진 원본 MusicXML (미보정)')).toBeUndefined();
    expect(container.textContent).not.toContain('호환성 보정은 드럼 악기 번호');
  });

  it('keeps separate raw project downloads available but rejects external raw provenance URLs', async () => {
    const imported: Job = { id: 'project', title: '가져온 곡', source_type: 'musicxml', status: 'completed', duration: 2, bpm: 120,
      demo: false, stage: 'completed', progress: 100, message: '', error: null, created_at: '2026-10-07', original_url: null, residual_url: null,
      stems: [{ id: 'drums', label: '드럼', status: 'ready', score_status: 'ready', waveform: [], score_url: '/score.musicxml' }],
      score_omr: { id: jobId, result_id: 'R1', engine: 'Audiveris', source_url: baseJob.source_url, result_url: `/api/score-omr/${jobId}/files/prepared.musicxml`,
        raw_result_url: readyJob.results[0].download_url, normalizations: ['드럼 번호 호환성 보정'], preview_urls: [], source_sha256: 'a'.repeat(64), result_sha256: 'b'.repeat(64), warnings: [] },
    };
    const render = async () => act(async () => root.render(<Workspace job={imported} health={null} onJob={vi.fn()} onNew={vi.fn()} onError={vi.fn()} onEditorDirty={vi.fn()} />));
    await render();
    const link = (label: string) => [...container.querySelectorAll('a')].find(item => item.textContent === label);
    expect(link('가져오기용 MusicXML')?.href).toContain('/prepared.musicxml');
    expect(link('엔진 원본 MusicXML (미보정)')?.href).toContain('/first.mxl');
    expect(container.textContent).toContain('호환성 보정은 드럼 악기 번호의 기준만 맞춥니다');
    imported.score_omr!.raw_result_url = 'https://untrusted.example/raw.mxl'; await render();
    expect(link('엔진 원본 MusicXML (미보정)')).toBeUndefined();
  });

  it('selects drums for a new project using the server recognition notation without applying automatically', async () => {
    const onImported = vi.fn(); const original = api.getMockImplementation()!;
    api.mockImplementation((path, options) => path === `/api/score-omr/${jobId}` ? Promise.resolve({ ...readyJob, notation: 'drums' }) : original(path, options));
    await mount({ onImported }); await prepareOmr(); await click('선택한 결과의 파트 확인');
    expect(container.querySelector<HTMLSelectElement>('select[aria-label="가져올 악기"]')!.value).toBe('drums');
    expect(button('원본 유지 프로젝트 열기').disabled).toBe(true); expect(onImported).not.toHaveBeenCalled();
    await act(async () => container.querySelector<HTMLInputElement>('.recognition-review input')!.click());
    await click('원본 유지 프로젝트 열기');
    const body = api.mock.calls.find(([path]) => path === '/api/source-scores')![1]!.body as FormData;
    expect(body.get('instrument')).toBe('drums');
  });
});
