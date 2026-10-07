import type { Instrument } from './types';
import { reviewLayers, type ReviewData, type ReviewEvent, type ReviewLayer } from './transcriptionReview';

export const annotationLabels = { missed: '누락된 음표', extra: '불필요한 음표', pitch: '음정·드럼 종류', onset: '시작 시점', offset: '종료 길이', other: '기타 확인 사항' } as const;
export type AnnotationKind = keyof typeof annotationLabels;
export type ReviewAnnotation = { id: string; kind: AnnotationKind; layer: ReviewLayer | null; event_id: string | null; start: number; end: number; note: string };
export type ReviewReference = { events: ReviewEvent[]; reviewer: string; basis: string; coverage_complete: boolean; seed_layer?: ReviewLayer | null };
export type CaseDraft = { reference: ReviewReference; annotations: ReviewAnnotation[] };
export type CaseSummary = { id: string; instrument: Instrument; revision: string; status: 'draft' | 'reviewed'; created_at: string; updated_at: string; window: { start: number; end: number }; snapshot_id: string; reference_event_count: number; annotation_count: number };
export type CaseMetric = { reference_present: boolean; reference_notes: number; estimated_notes: number; matched_notes: number; missing_notes: number; extra_notes: number; precision: number; recall: number; f1: number; median_onset_error_ms: number | null; median_offset_error_ms?: number | null; duration_diagnostics?: { onset_matches_with_wrong_offset: number }; confusion_counts?: Record<string, number> };
export type CaseReport = { metrics: Record<ReviewLayer, CaseMetric>; warnings?: string[] };
export type ReviewCase = CaseDraft & {
  schema: 'akbo.review-case'; schema_version: 1; id: string; job_id: string; instrument: Instrument; revision: string;
  created_at: string; updated_at: string; status: 'draft' | 'reviewed'; snapshot_id: string;
  snapshot: Omit<ReviewData, 'audio'>;
  confirmation: { revision: string; confirmed_at: string; reference_sha256: string } | null;
  report: CaseReport | null; freshness: { status: 'current' | 'stale' | 'unavailable'; message: string };
};

export function caseDraft(record: ReviewCase): CaseDraft {
  return { reference: structuredClone(record.reference), annotations: structuredClone(record.annotations) };
}
export function draftChanged(draft: CaseDraft, record: ReviewCase) {
  return JSON.stringify(draft) !== JSON.stringify({ reference: record.reference, annotations: record.annotations });
}
/** Every content change revokes prior full-window confirmation, including copied predictions. */
export function reviseDraft(draft: CaseDraft, change: Partial<CaseDraft>): CaseDraft {
  const result = { ...draft, ...change };
  return { ...result, reference: { ...result.reference, coverage_complete: false } };
}
export function referenceFromLayer(record: ReviewCase, layer: ReviewLayer, id: () => string): ReviewEvent[] {
  if (!reviewLayers.includes(layer)) throw new Error('비교 단계를 선택해주세요.');
  const { start, end } = record.snapshot.window;
  return record.snapshot.layers[layer].events.filter(event => event.start >= start && event.start < end)
    .map(event => ({ ...event, id: id(), end: Math.min(event.end, record.snapshot.duration) }))
    .filter(event => event.end > event.start);
}
export function validateReferenceEvent(event: ReviewEvent, record: ReviewCase) {
  const { start, end } = record.snapshot.window;
  if (!event.id || ![event.start, event.end, event.pitch, event.amplitude].every(Number.isFinite)
      || event.start < start || event.start >= end || event.end <= event.start || event.end > record.snapshot.duration
      || !Number.isInteger(event.pitch) || event.pitch < 0 || event.pitch > 127 || event.amplitude <= 0 || event.amplitude > 1) {
    throw new Error('음표 시작은 검수 구간 안에, 종료는 시작 이후·원음 길이 이내로 입력해주세요. 음정/GM 번호는 0~127입니다.');
  }
  return event;
}
export function canConfirmCase(record: ReviewCase, draft: CaseDraft) {
  return record.status === 'draft' && !draftChanged(draft, record) && draft.reference.coverage_complete
    && !!draft.reference.reviewer.trim() && !!draft.reference.basis.trim();
}
export function caseSummary(record: ReviewCase): CaseSummary {
  return { id: record.id, instrument: record.instrument, revision: record.revision, status: record.status,
    created_at: record.created_at, updated_at: record.updated_at, window: record.snapshot.window,
    snapshot_id: record.snapshot_id, reference_event_count: record.reference.events.length, annotation_count: record.annotations.length };
}

export type LocalCaseDraft = { schema: 'akbo.review-case-local-draft'; case_id: string; base_revision: string; draft: CaseDraft };
/** Local storage is untrusted; malformed or oversized backups never get silently restored. */
export function parseCaseBackup(raw: string | null, record: ReviewCase): LocalCaseDraft | null {
  if (!raw || raw.length > 2_000_000) return null;
  try {
    const value = JSON.parse(raw) as LocalCaseDraft;
    if (value.schema !== 'akbo.review-case-local-draft' || value.case_id !== record.id || typeof value.base_revision !== 'string') return null;
    const { reference, annotations } = value.draft;
    if (!reference || !Array.isArray(reference.events) || reference.events.length > 1500 || !Array.isArray(annotations) || annotations.length > 300
        || typeof reference.reviewer !== 'string' || reference.reviewer.length > 80 || typeof reference.basis !== 'string' || reference.basis.length > 2000
        || typeof reference.coverage_complete !== 'boolean'
        || !(reference.seed_layer == null || reviewLayers.includes(reference.seed_layer))) return null;
    const ids = new Set<string>();
    for (const event of reference.events) {
      if (typeof event.id !== 'string' || !/^[\w-]{1,80}$/.test(event.id) || ids.has(event.id)) return null;
      validateReferenceEvent(event, record); ids.add(event.id);
    }
    const marks = new Set<string>();
    for (const annotation of annotations) {
      if (!annotation || typeof annotation.id !== 'string' || !/^[\w-]{1,80}$/.test(annotation.id) || marks.has(annotation.id)
          || !Object.hasOwn(annotationLabels, annotation.kind) || typeof annotation.note !== 'string' || annotation.note.length > 1000
          || !Number.isFinite(annotation.start) || !Number.isFinite(annotation.end) || annotation.start < record.snapshot.window.start
          || annotation.start >= record.snapshot.window.end || annotation.end < annotation.start || annotation.end > record.snapshot.duration
          || !(annotation.layer === null || reviewLayers.includes(annotation.layer))
          || !(annotation.event_id === null || typeof annotation.event_id === 'string')) return null;
      if (annotation.layer === null ? annotation.event_id !== null : !record.snapshot.layers[annotation.layer].events.some(event => event.id === annotation.event_id)) return null;
      marks.add(annotation.id);
    }
    return value;
  } catch { return null; }
}
