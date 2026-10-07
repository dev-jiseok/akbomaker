// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import TabRhythmSuggestions from './components/TabRhythmSuggestions';
import { request } from './api';
import { createTabDraft, type TabReviewState } from './tabReview';
import type { RhythmProposal } from './tabRhythmSuggestions';

vi.mock('./api', () => ({ request: vi.fn() }));
let root: Root, container: HTMLDivElement, state: TabReviewState, proposal: RhythmProposal;
const api = vi.mocked(request), apply = vi.fn(), activity = vi.fn();
const id = 'f'.repeat(32);
function button(label: string) { const item = [...container.querySelectorAll<HTMLButtonElement>('button')].find(node => node.textContent === label); if (!item) throw new Error(label); return item; }
async function click(label: string) { await act(async () => button(label).click()); }
async function check() { await act(async () => container.querySelector<HTMLInputElement>('[aria-label="리듬 후보 박자 확인"]')!.click()); }
async function start(value: string) { await act(async () => { const input = container.querySelector<HTMLInputElement>('[aria-label="리듬 후보 실제 시작 마디"]')!; Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value); input.dispatchEvent(new Event('input', { bubbles: true })); }); }
async function mount(overrides: Partial<React.ComponentProps<typeof TabRhythmSuggestions>> = {}) { await act(async () => root.render(<TabRhythmSuggestions jobId={id} staffId="p1s1" state={state} draft={createTabDraft(state)} disabled={false} onActivity={activity} onApply={apply} {...overrides} />)); }
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement('div'); document.body.append(container); root = createRoot(container);
  state = { revision: 'a'.repeat(32), source_sha256: 'b'.repeat(64), coordinate_sha256: 'c'.repeat(64), source_url: '', preview_urls: [], draft: null, analysis: { source_sha256: 'b'.repeat(64), pages: [] } };
  proposal = { schema_version: 1, method: 'paired-vector-staff-rhythm', requires_review: true, source_sha256: state.source_sha256, coordinate_sha256: state.coordinate_sha256, base_revision: state.revision,
    staff_id: 'p1s1', meter: { beats: 4, beat_type: 4, source: 'caller-confirmed' }, measures: [{ staff_id: 'p1s1', staff_measure_id: 'p1s1m1', measure_index: 1, status: 'suggested', unresolved: [],
      rows: [{ row_id: 'p1s1:d0', staff_id: 'p1s1', measure_index: 1, onset: '0', duration: '4', type: 'whole', dots: 0, string: 4, fret: 5 }] },
      { staff_id: 'p1s1', staff_measure_id: 'p1s1m2', measure_index: 2, status: 'unresolved', unresolved: ['모호한 기호'], rows: [] }] };
  api.mockReset(); apply.mockReset(); activity.mockReset(); api.mockResolvedValue(proposal); vi.spyOn(window, 'confirm').mockReturnValue(false);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); });

describe('PDF rhythm proposal review UI', () => {
  it('requires confirmed meter, uses read-only proposal endpoint, and never automatically applies or imports', async () => {
    await mount(); expect(button('현재 보표의 리듬 후보 읽기').disabled).toBe(true);
    await click('현재 보표의 리듬 후보 읽기'); expect(api).not.toHaveBeenCalled();
    await check(); await click('현재 보표의 리듬 후보 읽기');
    expect(api).toHaveBeenCalledOnce(); const [path, init] = api.mock.calls[0];
    expect(path).toBe(`/api/score-omr/${id}/tab-review/rhythm-suggestions`);
    expect(JSON.parse(init!.body as string)).toEqual({ base_revision: state.revision, staff_id: 'p1s1', beats: 4, beat_type: 4, meter_confirmed: true });
    expect(container.textContent).toContain('1/2개'); expect(container.textContent).toContain('자동 적용 보류: 모호한 기호');
    expect(button('이 마디 후보 적용').disabled).toBe(true); expect(apply).not.toHaveBeenCalled();
    await start('21'); await click('이 마디 후보 적용'); expect(apply).not.toHaveBeenCalled();
    vi.mocked(window.confirm).mockReturnValue(true); await click('이 마디 후보 적용'); expect(apply).toHaveBeenCalledExactlyOnceWith(proposal, 1, 21);
    expect(api).toHaveBeenCalledOnce();
  });
  it.each(['hash', 'revision', 'meter', 'malformed'])('rejects %s response instead of rendering stale candidates', async mode => {
    if (mode === 'hash') proposal.source_sha256 = 'd'.repeat(64);
    if (mode === 'revision') proposal.base_revision = 'e'.repeat(32);
    if (mode === 'meter') proposal.meter.beats = 3;
    if (mode === 'malformed') api.mockResolvedValue({ ...proposal, measures: [null] });
    await mount(); await check(); await click('현재 보표의 리듬 후보 읽기');
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('다른 응답'); expect(apply).not.toHaveBeenCalled();
    expect(container.querySelector('[aria-label="리듬 후보 실제 시작 마디"]')).toBeNull();
  });
  it('deduplicates concurrent requests, reports busy, and aborts stale revision results', async () => {
    let resolve!: (value: RhythmProposal) => void;
    api.mockImplementation(() => new Promise<RhythmProposal>(yes => { resolve = yes; }));
    await mount(); await check(); await act(async () => { const read = button('현재 보표의 리듬 후보 읽기'); read.click(); read.click(); });
    expect(api).toHaveBeenCalledOnce(); expect(activity).toHaveBeenLastCalledWith(true);
    const signal = api.mock.calls[0][1]!.signal;
    await mount({ state: { ...state, revision: 'e'.repeat(32) } }); expect(signal?.aborted).toBe(true);
    await act(async () => resolve(proposal)); expect(container.querySelector('[role="status"]')).toBeNull();
    expect(activity).toHaveBeenLastCalledWith(false); expect(button('현재 보표의 리듬 후보 읽기').disabled).toBe(true);
  });
  it('clears old measure numbering and confirmation when the selected staff changes', async () => {
    await mount(); await check(); await click('현재 보표의 리듬 후보 읽기'); await start('21');
    await mount({ staffId: 'p1s2' }); expect(container.querySelector('[role="status"]')).toBeNull();
    expect(container.querySelector<HTMLInputElement>('[aria-label="리듬 후보 박자 확인"]')!.checked).toBe(false);
    expect(apply).not.toHaveBeenCalled();
  });
  it('shows worker errors without losing permission to retry', async () => {
    api.mockRejectedValueOnce(new Error('분석 시간 제한')); await mount(); await check(); await click('현재 보표의 리듬 후보 읽기');
    expect(container.textContent).toContain('분석 시간 제한'); expect(button('현재 보표의 리듬 후보 읽기').disabled).toBe(false);
    await click('현재 보표의 리듬 후보 읽기'); expect(container.querySelector('[role="status"]')).not.toBeNull();
  });
  it('locks every mutation when the parent is busy', async () => {
    await mount({ disabled: true }); expect(button('현재 보표의 리듬 후보 읽기').disabled).toBe(true);
    expect(container.querySelector<HTMLInputElement>('[aria-label="리듬 후보 박자 확인"]')!.disabled).toBe(true); expect(api).not.toHaveBeenCalled();
  });
});
