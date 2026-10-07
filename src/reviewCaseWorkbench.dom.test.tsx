// @vitest-environment jsdom
import { act, StrictMode, type ComponentProps } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ReviewCaseWorkbench from './components/ReviewCaseWorkbench';
import { caseSummary, type CaseDraft, type CaseReport, type ReviewCase } from './reviewCases';
import { reviewLayers, type ReviewData, type ReviewEvent } from './transcriptionReview';

const { requestMock } = vi.hoisted(() => ({ requestMock: vi.fn() }));
vi.mock('./api', () => ({ request: requestMock }));

type Props = ComponentProps<typeof ReviewCaseWorkbench>;
type PutBody = CaseDraft & { base_revision: string; status: 'draft' | 'reviewed' };
const jobId = 'b'.repeat(32);
const endpoint = `/api/jobs/${jobId}/review-cases/drums`;
const note: ReviewEvent = { id: 'raw-0', start: 2.2, end: 2.8, pitch: 42, amplitude: .8 };

function fixture(): ReviewCase {
  const layer = () => ({ events: [{ ...note }], total: 1, in_window: 1 });
  return {
    schema: 'akbo.review-case', schema_version: 1, id: 'a'.repeat(32), job_id: jobId, instrument: 'drums',
    revision: 'r1', created_at: '2026-10-06T00:00:00Z', updated_at: '2026-10-06T00:00:00Z',
    status: 'draft', snapshot_id: 'c'.repeat(64),
    snapshot: {
      schema: 'akbo.transcription-review', schema_version: 1, instrument: 'drums', duration: 8,
      window: { start: 2, end: 4 }, source: { kind: 'stem', file: 'drums.wav', sha256: 'd'.repeat(64), verified: true },
      score: { revision: 's1', edited: false, bpm: 120, timing_bpm: 120, audio_offset: 0 },
      layers: { recognized: layer(), automatic: layer(), current: layer() }, warnings: [],
    },
    reference: { events: [], reviewer: '', basis: '', coverage_complete: false, seed_layer: null },
    annotations: [], confirmation: null, report: null, freshness: { status: 'current', message: '' },
  };
}

function liveData(record = fixture()): ReviewData {
  return { ...structuredClone(record.snapshot), snapshot_id: record.snapshot_id,
    audio: { original_url: `/api/jobs/${jobId}/files/original.wav`, stem_url: `/api/jobs/${jobId}/files/drums.wav`,
      input_url: `/api/jobs/${jobId}/files/drums.wav` } };
}

function report(): CaseReport {
  const metric = { reference_present: true, reference_notes: 1, estimated_notes: 1, matched_notes: 1,
    missing_notes: 0, extra_notes: 0, precision: 1, recall: 1, f1: 1, median_onset_error_ms: 0 };
  return { metrics: Object.fromEntries(reviewLayers.map(layer => [layer, { ...metric }])) as CaseReport['metrics'] };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

/** A mock persistence boundary, not a replacement for backend validation tests. */
function useServer(initial = fixture()) {
  let server = structuredClone(initial);
  let created = false;
  const writes: PutBody[] = [];
  requestMock.mockImplementation(async (url: string, options?: RequestInit) => {
    const method = options?.method || 'GET';
    if (url === endpoint && method === 'POST') { created = true; return structuredClone(server); }
    if (url === endpoint && method === 'GET') return { cases: created ? [caseSummary(server)] : [] };
    if (url === `${endpoint}/${server.id}` && method === 'GET') return structuredClone(server);
    if (url === `${endpoint}/${server.id}` && method === 'PUT') {
      const body = JSON.parse(String(options?.body)) as PutBody;
      if (body.base_revision !== server.revision) throw new Error('Stale case revision');
      writes.push(structuredClone(body));
      const revision = `r${writes.length + 1}`;
      server = { ...server, reference: structuredClone(body.reference), annotations: structuredClone(body.annotations),
        revision, status: body.status, report: body.status === 'reviewed' ? report() : null,
        confirmation: body.status === 'reviewed' ? { revision, confirmed_at: '2026-10-06T00:01:00Z', reference_sha256: 'e'.repeat(64) } : null };
      return structuredClone(server);
    }
    throw new Error(`Unexpected mock request: ${method} ${url}`);
  });
  return { writes, current: () => structuredClone(server) };
}

let container: HTMLDivElement;
let root: Root;
let confirmMock: ReturnType<typeof vi.spyOn>;
let dirtyCallback = vi.fn<(dirty: boolean) => void>();

function button(text: string) {
  const element = [...container.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent?.trim() === text);
  if (!element) throw new Error(`Button not found: ${text}`);
  return element;
}

function labeled<T extends HTMLElement>(label: string): T {
  const element = container.querySelector<T>(`[aria-label="${label}"]`);
  if (!element) throw new Error(`Element not found: ${label}`);
  return element;
}

async function mount(overrides: Partial<Props> = {}) {
  await act(async () => {
    root.render(<StrictMode><ReviewCaseWorkbench jobId={jobId} instrument="drums" data={liveData()}
      selection={null} disabled={false} onDirty={dirtyCallback} {...overrides} /></StrictMode>);
  });
}

async function click(element: HTMLElement) {
  await act(async () => { element.click(); });
}

async function setValue(label: string, value: string) {
  const element = labeled<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(label);
  const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
    : element instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  await act(async () => {
    // Native setter bypasses React's value tracker, as a real browser edit does.
    Object.getOwnPropertyDescriptor(prototype, 'value')!.set!.call(element, value);
    element.dispatchEvent(new Event(element instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }));
  });
}

async function createCase() {
  await click(button('현재 구간 검수 시작'));
  expect(labeled<HTMLSelectElement>('저장된 검수 구간').value).toBe(fixture().id);
}

async function resolve<T>(pending: ReturnType<typeof deferred<T>>, value: T) {
  await act(async () => { pending.resolve(value); await pending.promise; });
}

beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  requestMock.mockReset();
  // Node 25+ exposes an unavailable native localStorage without its CLI file
  // flag; use an isolated browser-like store rather than that host API.
  const entries = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    get length() { return entries.size; },
    clear: () => entries.clear(),
    getItem: (key: string) => entries.get(key) ?? null,
    key: (index: number) => [...entries.keys()][index] ?? null,
    removeItem: (key: string) => { entries.delete(key); },
    setItem: (key: string, value: string) => { entries.set(key, String(value)); },
  } satisfies Storage);
  localStorage.clear();
  dirtyCallback = vi.fn<(dirty: boolean) => void>();
  confirmMock = vi.spyOn(window, 'confirm').mockReturnValue(true);
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => { root?.unmount(); });
  container?.remove();
  localStorage?.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('mounted review-case workbench in React StrictMode', () => {
  it('keeps a clipped final sub-quarter-second window valid for the create API', async () => {
    useServer();
    const data = liveData(); data.window = { start: 7.9, end: 8 };
    await mount({ data }); await createCase();
    const options = requestMock.mock.calls.find(([, value]) => value?.method === 'POST')?.[1];
    expect(JSON.parse(String(options?.body))).toMatchObject({ start: 7.9, seconds: .25, snapshot_id: data.snapshot_id });
  });

  it('accepts the create response and clears busy after the StrictMode effect replay', async () => {
    const pending = deferred<ReviewCase>();
    requestMock.mockImplementation((_url: string, options?: RequestInit) => options?.method === 'POST'
      ? pending.promise : Promise.resolve({ cases: [] }));
    await mount();
    await click(button('현재 구간 검수 시작'));
    expect(button('현재 구간 검수 시작').disabled).toBe(true);
    expect(dirtyCallback).toHaveBeenLastCalledWith(true);
    await resolve(pending, fixture());
    expect(labeled<HTMLSelectElement>('저장된 검수 구간').value).toBe(fixture().id);
    expect(button('현재 구간 검수 시작').disabled).toBe(false);
    expect(button('검수 초안 저장').matches(':disabled')).toBe(false);
    expect(dirtyCallback).toHaveBeenLastCalledWith(false);
    expect(requestMock.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1);
    expect(container.textContent).toContain('검수용 참조 음표는 아직 비어 있습니다');
  });

  it('does not let a delayed pre-create list overwrite the accepted POST case', async () => {
    const post = deferred<ReviewCase>();
    const list = deferred<{ cases: ReturnType<typeof caseSummary>[] }>();
    requestMock.mockImplementation((_url: string, options?: RequestInit) => options?.method === 'POST' ? post.promise : list.promise);
    await mount();
    await click(button('현재 구간 검수 시작'));
    expect(requestMock.mock.calls.some(([, options]) => !options?.method)).toBe(true);
    await resolve(post, fixture());
    expect(labeled<HTMLSelectElement>('저장된 검수 구간').options).toHaveLength(2);
    await resolve(list, { cases: [] });
    const picker = labeled<HTMLSelectElement>('저장된 검수 구간');
    expect(picker.value).toBe(fixture().id);
    expect([...picker.options].map(option => option.value)).toContain(fixture().id);
    expect(picker.textContent).toContain('(1/32)');
  });

  it('replaces an edited note form with one confirmation and saves the newly copied reference', async () => {
    const server = useServer();
    await mount();
    await createCase();
    await click(button('고정 음표를 초안으로 복사'));
    const oldId = labeled<HTMLSelectElement>('검수 참조 음표 선택').options[1].value;
    await setValue('검수 참조 음표 선택', oldId);
    await setValue('참조 음표 MIDI 번호', '46');
    confirmMock.mockClear();
    await click(button('고정 음표를 초안으로 복사'));
    expect(confirmMock).toHaveBeenCalledTimes(1);
    const picker = labeled<HTMLSelectElement>('검수 참조 음표 선택');
    expect(picker.value).toBe('');
    expect(picker.options[1].value).not.toBe(oldId);
    expect(labeled<HTMLInputElement>('참조 음표 MIDI 번호').value).toBe('42');
    expect(button('검수 초안 저장').disabled).toBe(false);
    await setValue('검수 참조 음표 선택', picker.options[1].value);
    await setValue('참조 음표 MIDI 번호', '44');
    await click(button('참조 음표 수정 적용'));
    await click(button('검수 초안 저장'));
    expect(server.writes).toHaveLength(1);
    expect(server.writes[0].reference.events.map(event => event.pitch)).toEqual([44]);
    expect(server.writes[0].reference.coverage_complete).toBe(false);
    expect(container.querySelector('.review-case-results')).toBeNull();
  });

  it('requires saved full confirmation for metrics, then revokes them on reference edit and draft save', async () => {
    const server = useServer();
    await mount();
    await createCase();
    expect(button('검수 확정').disabled).toBe(true);
    expect(container.querySelector('table')).toBeNull();
    await click(button('고정 음표를 초안으로 복사'));
    await setValue('검수자 이름', 'Manual reviewer');
    await setValue('검수 근거', 'Listened to all notes in the source-time window.');
    await click(labeled<HTMLInputElement>('검수 구간 전체 확인'));
    expect(button('검수 확정').disabled).toBe(true); // Unsaved acknowledgement is not sufficient.
    expect(container.querySelector('table')).toBeNull();
    await click(button('검수 초안 저장'));
    expect(server.writes[0].status).toBe('draft');
    expect(server.writes[0].reference.coverage_complete).toBe(true);
    expect(button('검수 확정').disabled).toBe(false);
    expect(container.querySelector('table')).toBeNull();
    confirmMock.mockReturnValueOnce(false);
    await click(button('검수 확정'));
    expect(server.writes).toHaveLength(1);
    expect(container.querySelector('table')).toBeNull();
    await click(button('검수 확정'));
    expect(server.writes).toHaveLength(2);
    expect(server.writes[1].status).toBe('reviewed');
    expect(container.querySelector('table')).not.toBeNull();
    expect(container.textContent).toContain('곡 전체의 정확도가 아닙니다');
    const id = labeled<HTMLSelectElement>('검수 참조 음표 선택').options[1].value;
    await setValue('검수 참조 음표 선택', id);
    await setValue('참조 음표 MIDI 번호', '36');
    expect(container.querySelector('table')).toBeNull(); // Hide stale confirmed score immediately.
    await click(button('참조 음표 수정 적용'));
    expect(labeled<HTMLInputElement>('검수 구간 전체 확인').checked).toBe(false);
    await click(button('검수 초안 저장'));
    expect(server.writes[2].status).toBe('draft');
    expect(server.writes[2].reference.coverage_complete).toBe(false);
    expect(server.current().confirmation).toBeNull();
    expect(button('검수 확정').disabled).toBe(true);
    expect(container.querySelector('table')).toBeNull();
  });

  it('never offers a current-note link for a different historical snapshot', async () => {
    useServer();
    const selection: Props['selection'] = { layer: 'recognized', event: note };
    await mount({ selection });
    await createCase();
    expect([...container.querySelectorAll('button')].some(item => item.textContent?.includes('의 시각 사용'))).toBe(true);
    const mismatch = liveData();
    mismatch.snapshot_id = 'f'.repeat(64);
    mismatch.layers.recognized.events[0] = { ...note, start: 3, end: 3.5, pitch: 36 };
    await mount({ data: mismatch, selection: { layer: 'recognized', event: mismatch.layers.recognized.events[0] } });
    expect([...container.querySelectorAll('button')].some(item => item.textContent?.includes('의 시각 사용'))).toBe(false);
    expect(container.textContent).toContain('현재 타임라인의 음표를 이 기록에 자동 연결하지 않습니다');
    await click(button('고정 음표를 초안으로 복사'));
    const copied = labeled<HTMLSelectElement>('검수 참조 음표 선택').options[1];
    expect(copied.textContent).toContain('2.200'); // Uses immutable saved notes, not changed live data.
    expect(copied.textContent).toContain('GM 42');
  });

  it('uses a matching selected note only for an explicit memo, never as reference truth', async () => {
    const server = useServer();
    await mount({ selection: { layer: 'recognized', event: note } });
    await createCase();
    const link = [...container.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent?.includes('의 시각 사용'))!;
    await click(link);
    expect(labeled<HTMLInputElement>('오류 메모 시작 초').value).toBe('2.2');
    await setValue('오류 메모 내용', 'Check hi-hat classification.');
    await click(button('메모 목록에 추가'));
    await click(button('검수 초안 저장'));
    expect(server.writes[0].annotations[0]).toMatchObject({ layer: 'recognized', event_id: 'raw-0', kind: 'pitch' });
    expect(server.writes[0].reference.events).toEqual([]);
    expect(server.writes[0].reference.coverage_complete).toBe(false);
    expect(container.querySelector('table')).toBeNull();
  });

  it('keeps the current edited case when switching is declined', async () => {
    const first = fixture();
    const second = { ...fixture(), id: 'e'.repeat(32), revision: 'r-other' };
    requestMock.mockImplementation(async (url: string, options?: RequestInit) => {
      if (options?.method === 'POST') return structuredClone(first);
      if (url === endpoint) return { cases: [caseSummary(first), caseSummary(second)] };
      if (url === `${endpoint}/${second.id}`) return structuredClone(second);
      throw new Error('Unexpected request');
    });
    await mount();
    await createCase();
    // Reopen list after creation to fetch both records, without discarding draft.
    await click(button('기록 접기'));
    await click(button('저장된 검수 열기'));
    await setValue('검수자 이름', 'Unsaved reviewer');
    confirmMock.mockReturnValueOnce(false);
    await setValue('저장된 검수 구간', second.id);
    expect(labeled<HTMLSelectElement>('저장된 검수 구간').value).toBe(first.id);
    expect(labeled<HTMLInputElement>('검수자 이름').value).toBe('Unsaved reviewer');
    expect(requestMock.mock.calls.some(([url]) => url === `${endpoint}/${second.id}`)).toBe(false);
    expect(localStorage.getItem(`akbo-review-draft:${jobId}:drums:${first.id}`)).toContain('Unsaved reviewer');
  });
});
