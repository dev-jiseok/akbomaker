import { describe, expect, it } from 'vitest';
import { createTabDraft, type TabReviewState } from './tabReview';
import { applyRhythmProposal, isRhythmProposal, type RhythmProposal } from './tabRhythmSuggestions';

function fixture() {
  const state: TabReviewState = { revision: 'a'.repeat(32), source_sha256: 'b'.repeat(64), coordinate_sha256: 'c'.repeat(64), source_url: '', preview_urls: [], draft: null,
    analysis: { source_sha256: 'b'.repeat(64), pages: [{ page: 1, width: 600, height: 800, staffs: [{ id: 'p1s1', kind: 'tab', line_count: 4, bbox: [20, 100, 580, 118],
      measures: [{ id: 'p1s1m1', index: 1, bbox: [20, 100, 580, 118], digit_ids: ['d0', 'd1'] }], digits: [0, 1].map(i => ({ id: `d${i}`, text: '5', fret: 5, string: 4, bbox: [100 + i * 20, 100, 104 + i * 20, 110], accepted: true })) }] }] } };
  const proposal: RhythmProposal = { schema_version: 1, method: 'paired-vector-staff-rhythm', requires_review: true, source_sha256: state.source_sha256,
    coordinate_sha256: state.coordinate_sha256, base_revision: state.revision, staff_id: 'p1s1', meter: { beats: 4, beat_type: 4, source: 'caller-confirmed' },
    measures: [{ staff_id: 'p1s1', staff_measure_id: 'p1s1m1', measure_index: 1, status: 'suggested', unresolved: [], rows: [0, 1].map(i => ({ row_id: `p1s1:d${i}`, staff_id: 'p1s1', measure_index: 1, onset: String(i * 2), duration: '2', type: 'half', dots: 0, string: 4, fret: 5 })) }] };
  return { state, draft: createTabDraft(state), proposal };
}

describe('reviewed vector rhythm proposal application', () => {
  it('changes only selected rhythm, keeps manual frets/strings and leaves source/draft immutable', () => {
    const { state, draft, proposal } = fixture();
    draft.rows[0] = { ...draft.rows[0], fret: 8, string: 3, muted: true, reason: '원본 확인' };
    draft.rows.push({ ...draft.rows[0], id: 'manual-1', measure: 25, onset: '0', type: 'whole' });
    const before = JSON.stringify({ state, draft, proposal });
    const result = applyRhythmProposal(draft, state.analysis, proposal, 1, 21);
    expect(result.rows[0]).toMatchObject({ measure: 21, onset: '0', type: 'half', dots: 0, fret: 8, string: 3, muted: true, reason: '원본 확인' });
    expect(result.rows[1]).toMatchObject({ measure: 21, onset: '2', type: 'half' });
    expect(result.rows[2]).toBe(draft.rows[2]);
    expect(JSON.stringify({ state, draft, proposal })).toBe(before);
  });
  it('preserves exact dotted durations and canonicalizes onsets', () => {
    const { state, draft, proposal } = fixture();
    Object.assign(proposal.measures[0].rows[0], { duration: '7/4', type: 'quarter', dots: 2 });
    Object.assign(proposal.measures[0].rows[1], { onset: '1.75', duration: '1/4', type: '16th' });
    draft.beats = 2; proposal.meter.beats = 2;
    expect(applyRhythmProposal(draft, state.analysis, proposal, 1, 1).rows).toMatchObject([{ dots: 2, type: 'quarter' }, { onset: '7/4', type: '16th' }]);
  });
  it.each(['source_sha256', 'coordinate_sha256', 'staff_id'] as const)('rejects stale %s', key => {
    const { state, draft, proposal } = fixture(); proposal[key] = key === 'staff_id' ? 'p2s1' : 'e'.repeat(64);
    expect(() => applyRhythmProposal(draft, state.analysis, proposal, 1, 1)).toThrow();
  });
  it('rejects a changed meter and unknown method', () => {
    const { state, draft, proposal } = fixture(); draft.beats = 3;
    expect(() => applyRhythmProposal(draft, state.analysis, proposal, 1, 1)).toThrow();
    expect(isRhythmProposal({ ...proposal, method: 'digit-spacing' })).toBe(false);
  });
  it.each([0, -1, 1.5, 601, NaN])('requires an explicit legal actual start measure: %s', start => {
    const { state, draft, proposal } = fixture(); expect(() => applyRhythmProposal(draft, state.analysis, proposal, 1, start)).toThrow();
  });
  it.each(['missing', 'duplicate', 'outside', 'excluded', 'unresolved', 'wrong-length', 'malformed-length', 'wrong-index', 'outside-onset', 'bar-crossing', 'overlap', 'empty-onset'])('rejects unsafe %s proposals without partial mutation', mode => {
    const { state, draft, proposal } = fixture(); const measure = proposal.measures[0];
    if (mode === 'missing') measure.rows.pop();
    if (mode === 'duplicate') measure.rows[1] = { ...measure.rows[0] };
    if (mode === 'outside') measure.rows[1].row_id = 'p1s1:d99';
    if (mode === 'excluded') draft.rows[1].decision = 'exclude';
    if (mode === 'unresolved') { measure.status = 'unresolved'; measure.rows = []; measure.unresolved = ['모호한 연결']; }
    if (mode === 'wrong-length') measure.rows[1].duration = '3';
    if (mode === 'malformed-length') measure.rows[1].duration = '2/1/junk';
    if (mode === 'wrong-index') state.analysis.pages[0].staffs[0].measures![0].index = 2;
    if (mode === 'outside-onset') measure.rows[1].onset = '4';
    if (mode === 'bar-crossing') measure.rows[1].onset = '3';
    if (mode === 'overlap') measure.rows[1].onset = '1';
    if (mode === 'empty-onset') measure.rows[1].onset = '';
    const before = JSON.stringify(draft);
    expect(() => applyRhythmProposal(draft, state.analysis, proposal, 1, 1)).toThrow(); expect(JSON.stringify(draft)).toBe(before);
  });
  it.each([null, {}, { measures: [null] }, { measures: [{}] }])('rejects malformed responses safely', value => expect(isRhythmProposal(value)).toBe(false));
  it('rejects duplicate measures and inconsistent unresolved records', () => {
    const { proposal } = fixture(); expect(isRhythmProposal(proposal)).toBe(true);
    expect(isRhythmProposal({ ...proposal, measures: [...proposal.measures, ...proposal.measures] })).toBe(false);
    proposal.measures[0].unresolved = ['아직 확인 안 됨']; expect(isRhythmProposal(proposal)).toBe(false);
  });
  it('does not coerce malformed identity objects', () => {
    const { proposal } = fixture(); expect(isRhythmProposal({ ...proposal, source_sha256: { toString: null } })).toBe(false);
  });
});
