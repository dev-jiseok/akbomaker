import { describe, expect, it } from 'vitest';
import { applyTabRowFields, assignTabSequence, createTabDraft, parseTabTuning, tabDraftIssues, tabEvidenceViewBox, tabOnset, tabRowFields, tabRowIssues, type TabReviewState } from './tabReview';

function state(): TabReviewState {
  return { revision: 'a'.repeat(32), source_sha256: 'b'.repeat(64), coordinate_sha256: 'c'.repeat(64), source_url: '/source.pdf', preview_urls: [], draft: null, analysis: { source_sha256: 'b'.repeat(64), pages: [{ page: 1, width: 600, height: 800, staffs: [{ id: 'p1s0', kind: 'tab', line_count: 4, bbox: [20, 190, 580, 208], digits: [{ id: 'd0', text: '5', fret: 5, string: 1, bbox: [100, 188, 104, 194], accepted: true }, { id: 'd1', text: '3', fret: 3, string: 2, bbox: [140, 194, 144, 200], accepted: true }] }] }] } };
}

describe('PDF TAB review helpers', () => {
  it('starts from source digits without guessed measures, onsets, or lengths', () => {
    const draft = createTabDraft(state());
    expect(draft.staff_ids).toEqual(['p1s0']); expect(draft.instrument).toBe('bass');
    expect(draft.rows.map(row => [row.string, row.fret, row.measure, row.onset, row.type])).toEqual([[1, 5, null, null, null], [2, 3, null, null, null]]);
    expect(draft.source_sha256).toBe(state().source_sha256); expect(draft.coordinate_sha256).toBe(state().coordinate_sha256);
  });
  it.each([['0', '0'], ['0.5', '1/2'], ['1.25', '5/4'], ['2/4', '1/2'], ['0002', '2'], ['0.015625', '1/64']])('normalizes %s to exact quarter-beat fraction %s', (input, expected) => expect(tabOnset(input)).toBe(expected));
  it.each(['-1', '1/3', '0.1', '1/0', 'NaN', '1e2', '0.000001'])('rejects unsupported onset %s instead of quantizing it', value => expect(() => tabOnset(value)).toThrow());
  it('leaves blank onset incomplete and permits partial field saves', () => {
    const draft = createTabDraft(state()), row = draft.rows[0];
    expect(tabOnset('')).toBeNull();
    const edited = applyTabRowFields(row, { ...tabRowFields(row), measure: '1', onset: '0.5' });
    expect(edited.onset).toBe('1/2'); expect(edited.type).toBeNull(); expect(tabRowIssues(edited, draft)).toContain('길이 필요');
  });
  it('requires an exclusion reason and clears unneeded timing fields explicitly', () => {
    const draft = createTabDraft(state()), row = draft.rows[0];
    const excluded = applyTabRowFields(row, { ...tabRowFields(row), decision: 'exclude', reason: '' });
    expect(excluded.string).toBeNull(); expect(tabRowIssues(excluded, draft)).toEqual(['제외 이유 필요']);
    expect(tabRowIssues({ ...excluded, reason: '마디 번호' }, draft)).toEqual([]);
  });
  it('assigns selected notes only after an explicit sequence operation and wraps at bar boundaries', () => {
    const draft = createTabDraft(state());
    const updated = assignTabSequence(draft, draft.rows.map(row => row.id), 3, '3.5', 'eighth');
    expect(updated.rows.map(row => [row.measure, row.onset, row.type])).toEqual([[3, '7/2', 'eighth'], [4, '0', 'eighth']]);
    expect(draft.rows.every(row => row.onset === null)).toBe(true);
  });
  it('does not turn excluded candidates into notes or guess timing across an invalid sequence boundary', () => {
    const draft = createTabDraft(state()); draft.rows[0].decision = 'exclude';
    expect(() => assignTabSequence(draft, [draft.rows[0].id], 1, '0', 'eighth')).toThrow('음표로 사용할');
    expect(() => assignTabSequence(draft, [draft.rows[1].id], 1, '3.75', 'eighth')).toThrow('마디 경계');
  });
  it('allows user-entered long notes and detects same-string overlaps across bar boundaries', () => {
    const source = state(), draft = createTabDraft(source);
    draft.rows = draft.rows.map((row, i) => ({ ...row, measure: i + 1, onset: i ? '0' : '3', type: 'half', string: 1 }));
    expect(tabRowIssues(draft.rows[0], draft)).toEqual([]);
    expect(tabDraftIssues(draft, source.analysis)).toContain('같은 줄에서 음표 시간이 겹칩니다. 시작 위치·길이를 확인해주세요.');
    draft.rows[1].string = 2;
    expect(tabDraftIssues(draft, source.analysis)).toEqual([]);
  });
  it('rejects unaccounted source digits and incompatible tuning without hiding them', () => {
    const source = state(), draft = createTabDraft(source); draft.rows.pop(); draft.tuning = [64, 59, 55, 50, 45, 40];
    expect(tabDraftIssues(draft, source.analysis)).toContain('선택한 보표의 모든 숫자를 검토해주세요.');
    expect(tabDraftIssues(draft, source.analysis)).toContain('선택한 보표의 줄 수와 튜닝의 줄 수가 일치해야 해요.');
    expect(parseTabTuning('43, 38,33,28')).toEqual([43, 38, 33, 28]); expect(() => parseTabTuning('0,1,2,3')).toThrow();
  });
  it('includes the preceding ordinary staff and its stems in a cropped original view', () => {
    const page = state().analysis.pages[0], staff = page.staffs[0];
    page.staffs.unshift({ id: 'ordinary', kind: 'staff', line_count: 5, bbox: [20, 158.8, 580, 182.8], digits: [] });
    const box = tabEvidenceViewBox(page, staff).split(' ').map(Number);
    expect(box[1]).toBeCloseTo(130.8); expect(box[1] + box[3]).toBeGreaterThan(staff.bbox[3] + 28);
    expect(tabEvidenceViewBox(page, staff, true)).toBe('0 0 600 800');
  });
});
