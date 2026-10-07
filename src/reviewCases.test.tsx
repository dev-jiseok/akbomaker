import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { canConfirmCase, caseDraft, draftChanged, parseCaseBackup, referenceFromLayer, reviseDraft, validateReferenceEvent, type CaseReport, type ReviewCase } from './reviewCases';
import ReviewCaseWorkbench, { ReviewCaseResults } from './components/ReviewCaseWorkbench';
import { instruments } from './types';
import { reviewLayers, type ReviewEvent } from './transcriptionReview';

const event: ReviewEvent = { id: 'raw-1', start: 2.2, end: 2.8, pitch: 42, amplitude: .8 };
const layer = { events: [event], total: 1, in_window: 1 };
const record: ReviewCase = {
  schema: 'akbo.review-case', schema_version: 1, id: 'a'.repeat(32), job_id: 'b'.repeat(32), instrument: 'drums',
  revision: 'r1', created_at: '', updated_at: '', status: 'draft', snapshot_id: 'c'.repeat(64),
  snapshot: { schema: 'akbo.transcription-review', schema_version: 1, instrument: 'drums', duration: 8, window: { start: 2, end: 4 }, source: { kind: 'stem', file: 'drums.wav', sha256: 'd'.repeat(64), verified: true }, score: { revision: 's1', edited: false, bpm: 120, timing_bpm: 120, audio_offset: 0 }, layers: { recognized: layer, automatic: layer, current: layer }, warnings: [] },
  reference: { events: [], reviewer: '', basis: '', coverage_complete: false, seed_layer: null }, annotations: [], confirmation: null,
  report: null, freshness: { status: 'current', message: '' },
};
const copy = () => structuredClone(record);
const backup = (value = record, revision = value.revision) => JSON.stringify({ schema: 'akbo.review-case-local-draft', case_id: value.id, base_revision: revision, draft: caseDraft(value) });

describe('separate review-case drafts', () => {
  it('never seeds a reference or confirms an empty draft automatically', () => {
    const draft = caseDraft(record); draft.reference.events.push({ ...event });
    expect(record.reference.events).toEqual([]);
    expect(canConfirmCase(record, caseDraft(record))).toBe(false);
    expect(draftChanged(draft, record)).toBe(true);
  });
  it('requires a saved, explicit full-window acknowledgement and reviewer/basis', () => {
    const saved = copy(); saved.reference = { events: [], reviewer: '검수자', basis: '원음 전 구간 청취, 해당 악기 무음 확인', coverage_complete: true };
    expect(canConfirmCase(saved, caseDraft(saved))).toBe(true);
    expect(canConfirmCase(record, caseDraft(saved))).toBe(false);
    for (const key of ['reviewer', 'basis'] as const) { const value = structuredClone(saved); value.reference[key] = ' '; expect(canConfirmCase(value, caseDraft(value))).toBe(false); }
    expect(canConfirmCase({ ...saved, status: 'reviewed' }, caseDraft(saved))).toBe(false);
  });
  it('revokes confirmation on reference or annotation edits without mutating old content', () => {
    const draft = caseDraft(record); draft.reference.coverage_complete = true;
    expect(reviseDraft(draft, { annotations: [] }).reference.coverage_complete).toBe(false);
    expect(draft.reference.coverage_complete).toBe(true);
  });
  it('copies only in-window onsets, clamps a padded score end, and gives new reference IDs', () => {
    const value = copy(); value.snapshot.layers.current.events = [{ ...event, start: 1, end: 3 }, event, { ...event, id: 'tail', start: 3, end: 9 }, { ...event, id: 'edge', start: 4, end: 5 }];
    let n = 0;
    const result = referenceFromLayer(value, 'current', () => `ref-${n++}`);
    expect(result.map(item => [item.id, item.start, item.end])).toEqual([['ref-0', 2.2, 2.8], ['ref-1', 3, 8]]);
    expect(value.snapshot.layers.current.events[2].end).toBe(9);
  });
  it('preserves raw GM and simultaneous notes; no automatic kick relabeling', () => {
    const value = copy(); value.snapshot.layers.recognized.events = [event, { ...event, id: 'other', pitch: 33 }];
    expect(referenceFromLayer(value, 'recognized', () => crypto.randomUUID()).map(item => item.pitch)).toEqual([42, 33]);
  });
  it('rejects invalid reference times, pitches, and amplitudes', () => {
    expect(validateReferenceEvent(event, record)).toEqual(event);
    for (const partial of [{ start: 1.99 }, { start: 4 }, { end: 9 }, { end: 2.2 }, { start: NaN }, { pitch: 42.5 }, { pitch: 128 }, { amplitude: 0 }]) expect(() => validateReferenceEvent({ ...event, ...partial }, record)).toThrow();
  });
  it('preserves conflicting revision backups for explicit download, never silently restoring', () => {
    expect(parseCaseBackup(backup(record, 'old-revision'), record)?.base_revision).toBe('old-revision');
    const wrong = copy(); wrong.id = 'e'.repeat(32);
    expect(parseCaseBackup(backup(wrong), record)).toBeNull();
  });
  it('rejects malformed, duplicate and oversized local backups', () => {
    expect(parseCaseBackup('{', record)).toBeNull(); expect(parseCaseBackup('x'.repeat(2_000_001), record)).toBeNull();
    const value = copy(); value.reference.events = [event, event]; expect(parseCaseBackup(backup(value), record)).toBeNull();
    value.reference.events = [{ ...event, start: 1 }]; expect(parseCaseBackup(backup(value), record)).toBeNull();
  });
  it('rejects an annotation link to a nonexistent frozen note', () => {
    const value = copy(); value.annotations = [{ id: 'mark', kind: 'pitch', layer: 'recognized', event_id: 'fake', start: 2.2, end: 2.2, note: '확인' }];
    expect(parseCaseBackup(backup(value), record)).toBeNull();
    value.annotations[0].event_id = event.id; expect(parseCaseBackup(backup(value), record)).not.toBeNull();
  });
  it('exposes past case access for all instruments without current evidence or inference', () => {
    for (const instrument of instruments) {
      const html = renderToStaticMarkup(<ReviewCaseWorkbench jobId={record.job_id} instrument={instrument} data={null} selection={null} disabled={false} onDirty={() => {}} />);
      expect(html).toContain('저장된 검수 열기'); expect(html).toContain('다시 채보한 뒤에도'); expect(html).toContain('disabled=""');
      expect(html).not.toContain('F1');
    }
  });
  it('hides saved results while editing and does not present empty references as perfect', () => {
    const metric = { reference_present: false, reference_notes: 0, estimated_notes: 0, matched_notes: 0, missing_notes: 0, extra_notes: 0, precision: 0, recall: 0, f1: 0, median_onset_error_ms: null };
    const report: CaseReport = { metrics: Object.fromEntries(reviewLayers.map(layer => [layer, metric])) as CaseReport['metrics'] };
    const clean = renderToStaticMarkup(<ReviewCaseResults report={report} dirty={false} instrument="drums" />);
    expect(clean).toContain('참조 음표 없음'); expect(clean).not.toContain('종료 길이 불일치');
    const dirty = renderToStaticMarkup(<ReviewCaseResults report={report} dirty={true} instrument="drums" />);
    expect(dirty).not.toContain('<table'); expect(dirty).toContain('초안에는 점수를 표시하지');
  });
});
