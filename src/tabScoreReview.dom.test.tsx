// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import TabScoreReview from './components/TabScoreReview';
import { request } from './api';
import { createTabDraft, tabReviewUrl, type TabReviewDraft, type TabReviewState } from './tabReview';

vi.mock('./api', () => ({ request: vi.fn() }));
const id = 'a'.repeat(32), endpoint = `/api/score-omr/${id}/tab-review`;
let state: TabReviewState, container: HTMLDivElement, root: Root;
const api = vi.mocked(request), onImported = vi.fn(), onActivity = vi.fn(), onDirty = vi.fn();
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (error: Error) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
function button(label: string) { const result = [...container.querySelectorAll<HTMLButtonElement>('button')].find(node => node.textContent?.trim() === label); if (!result) throw new Error(`Missing button ${label}`); return result; }
async function click(label: string) { await act(async () => button(label).click()); }
async function select(label: string, value: string) { const node = container.querySelector<HTMLSelectElement>(`select[aria-label="${label}"]`)!; await act(async () => { node.value = value; node.dispatchEvent(new Event('change', { bubbles: true })); }); }
async function input(label: string, value: string) { const node = container.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)!; await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(node, value); node.dispatchEvent(new Event('input', { bubbles: true })); }); }
async function check(label: string) { const node = container.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)!; await act(async () => node.click()); }
async function mount(props: Partial<React.ComponentProps<typeof TabScoreReview>> = {}) { await act(async () => root.render(<TabScoreReview jobId={id} disabled={false} onImported={onImported} onActivity={onActivity} onDirty={onDirty} {...props} />)); }
function completeDraft(): TabReviewDraft { const draft = createTabDraft(state); return { ...draft, rows: draft.rows.map((row, i) => ({ ...row, measure: 1, onset: String(i), type: 'quarter' })) }; }

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
  state = { revision: '1'.repeat(32), source_sha256: 'b'.repeat(64), coordinate_sha256: 'c'.repeat(64), source_url: `/api/score-omr/${id}/files/source.pdf`, preview_urls: [`/api/score-omr/${id}/files/preview-1.png`, `/api/score-omr/${id}/files/preview-2.png`], draft: null,
    analysis: { source_sha256: 'b'.repeat(64), pages: [1, 2].map(page => ({ page, width: 600, height: 800, staffs: [{ id: `p${page}s0`, kind: 'tab', line_count: 4, bbox: [20, 100, 580, 118], warnings: ['원본 줄 확인'], measures: [{ id: `p${page}s0m1`, index: 1, bbox: [20, 100, 580, 118], digit_ids: ['d0', 'd1'] }], digits: [{ id: 'd0', text: '5', fret: 5, string: 1, bbox: [100, 98, 104, 104], accepted: true }, { id: 'd1', text: '3', fret: 3, string: 2, bbox: [150, 104, 154, 110], accepted: true }] }] })) } };
  api.mockReset(); onImported.mockReset(); onActivity.mockReset(); onDirty.mockReset();
  api.mockImplementation(async (path, options) => {
    if (path.endsWith('/import')) return { id: 'created-source-project', score_preserved: { instrument: 'bass' } };
    if (options?.method === 'PUT') { const body = JSON.parse(options.body as string); return { ...state, revision: '2'.repeat(32), draft: body.draft }; }
    return state;
  });
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn().mockReturnValue('blob:review-backup') });
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
  vi.spyOn(window, 'confirm').mockReturnValue(false);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); });

describe('manual PDF TAB review', () => {
  it('shows the actual dotted duration in the review list', async () => {
    state.draft = completeDraft();
    state.draft.rows[0].dots = 2;
    state.draft.rows[1].onset = '2';
    state.draft.rows[1].dots = 1;
    await mount();
    expect(container.querySelector('.tab-review-candidates')?.textContent).toContain('겹점 4분음표 · 1.75박');
    expect(container.querySelector('.tab-review-candidates')?.textContent).toContain('점 4분음표 · 1.5박');
  });
  it('loads one TAB staff without fabricated rhythm and displays original evidence with highlighted coordinates', async () => {
    await mount();
    expect(api.mock.calls[0][0]).toBe(endpoint); expect(onActivity).toHaveBeenLastCalledWith(false);
    expect(container.querySelector<HTMLInputElement>('[aria-label="TAB 음표 마디"]')!.value).toBe('');
    expect(container.querySelector<HTMLInputElement>('[aria-label="TAB 음표 시작 위치"]')!.value).toBe('');
    expect(container.querySelector<HTMLSelectElement>('[aria-label="TAB 음표 길이"]')!.value).toBe('');
    expect(container.querySelector('.tab-review-source image')?.getAttribute('href')).toContain('/preview-1.png');
    expect(container.querySelectorAll('.tab-review-source rect')).toHaveLength(2);
    expect(container.textContent).toContain('좌표 경계 후보'); expect(container.textContent).toContain('입력 완료 0/2');
    expect(container.querySelector('.tab-review-submit')?.textContent).toContain('TAB 보표 1/2개 · 1페이지');
    expect(container.textContent).toContain('전체 TAB 보표 2개 중 선택한 1개만 포함');
    expect(button('확인한 TAB로 편집 프로젝트 만들기').disabled).toBe(true); expect(onImported).not.toHaveBeenCalled();
  });

  it('saves incomplete drafts with canonical fractions and source hashes, not confirmed import claims', async () => {
    await mount(); await input('TAB 음표 마디', '1'); await input('TAB 음표 시작 위치', '0.5');
    expect(onDirty).toHaveBeenLastCalledWith(true); await click('검수 초안 저장');
    const call = api.mock.calls.find(([, options]) => options?.method === 'PUT')!;
    const body = JSON.parse(call[1]!.body as string);
    expect(body.base_revision).toBe('1'.repeat(32)); expect(body.confirmed).toBeUndefined();
    expect(body.draft.rows[0].onset).toBe('1/2'); expect(body.draft.rows[0].type).toBeNull();
    expect(body.draft.source_sha256).toBe(state.source_sha256); expect(body.draft.coordinate_sha256).toBe(state.coordinate_sha256);
    expect(onDirty).toHaveBeenLastCalledWith(false); expect(onImported).not.toHaveBeenCalled();
    await click('검수 초안 저장');
    const last = api.mock.calls.filter(([, options]) => options?.method === 'PUT').at(-1)!;
    expect(JSON.parse(last[1]!.body as string).base_revision).toBe('2'.repeat(32));
  });

  it('restores persisted decisions but never restores the import confirmation checkbox', async () => {
    state.draft = completeDraft(); await mount();
    expect(container.querySelector<HTMLInputElement>('[aria-label="TAB 음표 시작 위치"]')!.value).toBe('0');
    expect(container.textContent).toContain('입력 완료 2/2');
    expect(container.querySelector<HTMLInputElement>('[aria-label="TAB 원본 대조 확인"]')!.checked).toBe(false);
    expect(button('확인한 TAB로 편집 프로젝트 만들기').disabled).toBe(true);
    await check('TAB 원본 대조 확인'); await click('확인한 TAB로 편집 프로젝트 만들기');
    const call = api.mock.calls.find(([path]) => path.endsWith('/import'))!;
    expect(JSON.parse(call[1]!.body as string)).toEqual({ base_revision: state.revision, draft: state.draft, confirmed: true });
    expect(onImported).toHaveBeenCalledOnce();
  });

  it('requires an explicit exclusion reason and retains every original candidate in the saved draft', async () => {
    state.draft = completeDraft(); await mount(); await select('TAB 숫자 처리', 'exclude'); await click('선택 항목 적용');
    expect(button('확인한 TAB로 편집 프로젝트 만들기').disabled).toBe(true); expect(container.textContent).toContain('제외 이유 필요');
    await input('TAB 숫자 제외 이유', '중복 숫자'); await click('검수 초안 저장');
    const call = api.mock.calls.find(([, options]) => options?.method === 'PUT')!;
    const draft = JSON.parse(call[1]!.body as string).draft as TabReviewDraft;
    expect(draft.rows).toHaveLength(2); expect(draft.rows[0]).toMatchObject({ id: 'p1s0:d0', decision: 'exclude', reason: '중복 숫자', onset: null, type: null, string: null, fret: null });
  });

  it('does not silently apply repeated eighth-note timing until a user explicitly selects and confirms it', async () => {
    await mount(); await check('p1s0:d0 순서 배정 선택'); await check('p1s0:d1 순서 배정 선택');
    await click('체크한 숫자에 직접 배정'); expect(container.querySelector<HTMLInputElement>('[aria-label="TAB 음표 시작 위치"]')!.value).toBe('');
    vi.mocked(window.confirm).mockReturnValue(true); await click('체크한 숫자에 직접 배정');
    expect(container.querySelector<HTMLInputElement>('[aria-label="TAB 음표 시작 위치"]')!.value).toBe('0');
    await click('검수 초안 저장'); const call = api.mock.calls.find(([, options]) => options?.method === 'PUT')!;
    expect(JSON.parse(call[1]!.body as string).draft.rows.map((row: TabReviewDraft['rows'][number]) => [row.onset, row.type])).toEqual([['0', 'eighth'], ['1/2', 'eighth']]);
    expect(container.textContent).toContain('자동 리듬 인식이 아닙니다');
  });

  it('adds a manual note without inventing its timing or losing source digit accounting', async () => {
    await mount(); await click('누락된 음표 직접 추가');
    expect(container.querySelector<HTMLInputElement>('[aria-label="TAB 음표 시작 위치"]')!.value).toBe('');
    expect(container.querySelector<HTMLInputElement>('[aria-label="TAB 음표 프렛"]')!.value).toBe('');
    await click('검수 초안 저장'); const call = api.mock.calls.find(([, options]) => options?.method === 'PUT')!;
    const rows = JSON.parse(call[1]!.body as string).draft.rows;
    expect(rows).toHaveLength(3); expect(rows[2].id).toMatch(/^manual-[a-f0-9]{32}$/); expect(rows[2].staff_id).toBe('p1s0');
  });

  it('adds another staff only after explicit scope selection and uses its own page image', async () => {
    await mount(); await check('2페이지 2번째 TAB 보표 포함');
    expect(container.querySelector('.tab-review-source image')?.getAttribute('href')).toContain('/preview-2.png');
    expect(container.textContent).toContain('입력 완료 0/4');
    expect(container.querySelector('.tab-review-submit')?.textContent).toContain('TAB 보표 2/2개 · 1, 2페이지');
    await click('검수 초안 저장'); const call = api.mock.calls.find(([, options]) => options?.method === 'PUT')!;
    const draft = JSON.parse(call[1]!.body as string).draft;
    expect(draft.staff_ids).toEqual(['p1s0', 'p2s0']); expect(draft.rows).toHaveLength(4);
  });

  it('preserves input and offers JSON backup after a stale revision failure; reload requires discard confirmation', async () => {
    api.mockImplementation(async (_path, options) => { if (options?.method === 'PUT') throw new Error('다른 창에서 초안이 변경됐어요.'); return state; });
    await mount(); await input('TAB 음표 프렛', '7'); await click('검수 초안 저장');
    expect(container.textContent).toContain('다른 창에서 초안이 변경됐어요');
    expect(container.querySelector<HTMLInputElement>('[aria-label="TAB 음표 프렛"]')!.value).toBe('7');
    expect([...container.querySelectorAll('a')].find(node => node.textContent?.includes('검수 초안 JSON 보관'))?.getAttribute('download')).toBe('tab-review-draft.json');
    const previous = api.mock.calls.length; await click('최신 초안 불러오기'); expect(api.mock.calls.length).toBe(previous);
    vi.mocked(window.confirm).mockReturnValue(true); await click('최신 초안 불러오기');
    expect(container.querySelector<HTMLInputElement>('[aria-label="TAB 음표 프렛"]')!.value).toBe('5'); expect(onDirty).toHaveBeenLastCalledWith(false);
  });

  it('protects unsaved drafts on unload and clears that guard after successful draft saving', async () => {
    await mount(); await input('TAB 음표 프렛', '7');
    const warning = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(warning); expect(warning.defaultPrevented).toBe(true);
    await click('검수 초안 저장'); const saved = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(saved); expect(saved.defaultPrevented).toBe(false);
  });

  it('locks writes while pending, deduplicates clicks, and ignores a save result after unmount', async () => {
    const pending = deferred<TabReviewState>(); api.mockImplementation((_path, options) => options?.method === 'PUT' ? pending.promise : Promise.resolve(state));
    await mount(); await input('TAB 음표 프렛', '8');
    await act(async () => { button('검수 초안 저장').click(); button('검수 초안 저장').click(); });
    expect(api.mock.calls.filter(([, options]) => options?.method === 'PUT')).toHaveLength(1); expect(onActivity).toHaveBeenLastCalledWith(true);
    const signal = api.mock.calls.find(([, options]) => options?.method === 'PUT')![1]!.signal;
    await act(async () => root.render(null)); expect(signal?.aborted).toBe(true);
    await act(async () => pending.resolve({ ...state, draft: completeDraft() })); expect(onImported).not.toHaveBeenCalled(); expect(onActivity).toHaveBeenLastCalledWith(false);
  });

  it('rejects cross-job and external original preview addresses', async () => {
    state.source_url = 'https://tracking.example/source.pdf'; state.preview_urls = ['https://tracking.example/preview-1.png']; await mount();
    expect(container.querySelector('.tab-review-source')).toBeNull(); expect([...container.querySelectorAll('a')].some(node => node.href.startsWith('https://tracking.example'))).toBe(false);
    for (const url of ['javascript:alert(1)', `/api/score-omr/${id}/../other/preview-1.png`, '/api/score-omr/other/files/a.png']) expect(tabReviewUrl(url, id)).toBeUndefined();
  });

  it('rejects a persisted draft associated with a different source', async () => {
    state.draft = { ...completeDraft(), coordinate_sha256: 'd'.repeat(64) }; await mount();
    expect(container.textContent).toContain('초안과 원본 PDF 좌표 자료가 일치하지 않아요');
    expect(container.querySelector('.tab-review-workbench')).toBeNull();
  });

  it('clears the previous source if a different recognition job fails to load', async () => {
    await mount(); expect(container.querySelector('.tab-review-workbench')).not.toBeNull();
    api.mockRejectedValueOnce(new Error('새 원본 검수 자료 없음'));
    await mount({ jobId: 'f'.repeat(32) });
    expect(container.textContent).toContain('새 원본 검수 자료 없음');
    expect(container.querySelector('.tab-review-workbench')).toBeNull();
    expect(container.querySelector('[aria-label="TAB 원본 대조 확인"]')).toBeNull();
  });
});
