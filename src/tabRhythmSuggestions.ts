import { tabBeats, tabDuration, tabOnset, tabRowIssues, tabTypes, type TabAnalysis, type TabReviewDraft, type TabReviewRow, type TabType } from './tabReview';

export type RhythmProposal = {
  schema_version: 1; method: 'paired-vector-staff-rhythm'; requires_review: true;
  source_sha256: string; coordinate_sha256: string; base_revision: string; staff_id: string;
  meter: { beats: number; beat_type: number; source: string };
  measures: { staff_id: string; staff_measure_id: string; measure_index: number; status: 'suggested' | 'unresolved';
    rows: { row_id: string; staff_id: string; measure_index: number; onset: string; duration: string; type: TabType; dots: 0 | 1 | 2; string: number; fret: number }[];
    unresolved: string[] }[];
};

export function isRhythmProposal(value: unknown): value is RhythmProposal {
  if (!value || typeof value !== 'object') return false;
  const proposal = value as RhythmProposal;
  if (proposal.schema_version !== 1 || proposal.method !== 'paired-vector-staff-rhythm' || proposal.requires_review !== true ||
      [proposal.source_sha256, proposal.coordinate_sha256, proposal.base_revision, proposal.staff_id].some(value => typeof value !== 'string') ||
      !/^[a-f0-9]{64}$/.test(proposal.source_sha256) || !/^[a-f0-9]{64}$/.test(proposal.coordinate_sha256) ||
      !/^[a-f0-9]{32}$/.test(proposal.base_revision) || !/^p\d+s\d+$/.test(proposal.staff_id) ||
      !proposal.meter || proposal.meter.source !== 'caller-confirmed' || !Number.isInteger(proposal.meter.beats) ||
      proposal.meter.beats < 1 || proposal.meter.beats > 12 || ![2, 4, 8, 16].includes(proposal.meter.beat_type) ||
      !Array.isArray(proposal.measures) || proposal.measures.length > 600) return false;
  const ids = new Set<string>(), indices = new Set<number>();
  return proposal.measures.every(measure => {
    if (!measure || measure.staff_id !== proposal.staff_id || !Number.isInteger(measure.measure_index) || measure.measure_index < 1 ||
        measure.measure_index > 600 || typeof measure.staff_measure_id !== 'string' || ids.has(measure.staff_measure_id) || indices.has(measure.measure_index) ||
        !['suggested', 'unresolved'].includes(measure.status) || !Array.isArray(measure.rows) || measure.rows.length > 2000 ||
        !Array.isArray(measure.unresolved) || measure.unresolved.some(reason => typeof reason !== 'string') ||
        (measure.status === 'unresolved' && measure.rows.length !== 0) || (measure.status === 'suggested' && (!measure.rows.length || measure.unresolved.length))) return false;
    ids.add(measure.staff_measure_id); indices.add(measure.measure_index);
    return measure.rows.every(row => row && typeof row.row_id === 'string' && row.staff_id === proposal.staff_id && row.measure_index === measure.measure_index &&
      typeof row.onset === 'string' && typeof row.duration === 'string' && tabTypes.includes(row.type) && [0, 1, 2].includes(row.dots) &&
      Number.isInteger(row.string) && row.string >= 1 && row.string <= 6 && Number.isInteger(row.fret) && row.fret >= 0 && row.fret <= 36);
  });
}

export function applyRhythmProposal(draft: TabReviewDraft, analysis: TabAnalysis, proposal: RhythmProposal, staffMeasure: number, firstMeasure: number): TabReviewDraft {
  if (!isRhythmProposal(proposal) ||
      proposal.source_sha256 !== draft.source_sha256 || proposal.coordinate_sha256 !== draft.coordinate_sha256 ||
      proposal.meter.beats !== draft.beats || proposal.meter.beat_type !== draft.beat_type || !draft.staff_ids.includes(proposal.staff_id)) {
    throw new Error('현재 원본·보표·박자와 다른 리듬 후보입니다. 다시 분석해주세요.');
  }
  const measure = proposal.measures.find(item => item.measure_index === staffMeasure && item.staff_id === proposal.staff_id);
  const staff = analysis.pages.flatMap(page => page.staffs).find(item => item.id === proposal.staff_id);
  const geometry = staff?.measures?.find(item => item.id === measure?.staff_measure_id);
  if (!measure || measure.status !== 'suggested' || !geometry || geometry.index !== measure.measure_index || !measure.rows.length || !Number.isInteger(firstMeasure) || firstMeasure < 1 || firstMeasure + measure.measure_index - 1 > 600) {
    throw new Error('적용할 리듬 후보와 이 보표의 실제 시작 마디(1~600)를 확인해주세요.');
  }
  const ids = new Set(measure.rows.map(row => row.row_id));
  if (ids.size !== measure.rows.length || ids.size !== geometry.digit_ids.length || geometry.digit_ids.some(id => !ids.has(`${proposal.staff_id}:${id}`))) {
    throw new Error('마디 안의 모든 TAB 숫자와 대응하지 않는 후보입니다. 직접 확인해주세요.');
  }
  // The current recognizer promises complete monophonic bars. Keep manual
  // cross-bar editing separate: malformed proposals cannot silently add ties,
  // rests or overlapping notes across a bar line.
  let end = 0;
  const timing = measure.rows.map(row => ({ onset: tabOnset(row.onset), duration: tabOnset(row.duration) }))
    .sort((a, b) => tabBeats(a.onset ?? '0') - tabBeats(b.onset ?? '0'));
  for (const row of timing) {
    if (row.onset == null || row.duration == null || tabBeats(row.onset) !== end || tabBeats(row.duration) <= 0) {
      throw new Error('후보의 마디 안 리듬이 연속하지 않아요. 원본을 직접 확인해주세요.');
    }
    end += tabBeats(row.duration);
  }
  if (end !== draft.beats * 4 / draft.beat_type) throw new Error('리듬 후보의 합이 박자표와 다릅니다. 원본을 직접 확인해주세요.');
  const replacements = new Map<string, TabReviewRow>();
  for (const candidate of measure.rows) {
    const row = draft.rows.find(item => item.id === candidate.row_id && item.staff_id === proposal.staff_id);
    if (!row || row.decision !== 'note' || candidate.staff_id !== proposal.staff_id || candidate.measure_index !== measure.measure_index ||
        !tabTypes.includes(candidate.type) || ![0, 1, 2].includes(candidate.dots) ||
        tabDuration(candidate.type, candidate.dots) !== tabBeats(tabOnset(candidate.duration) ?? '0')) {
      throw new Error('제외한 숫자 또는 지원하지 않는 길이가 포함된 후보는 자동 적용하지 않습니다.');
    }
    const next = { ...row, measure: firstMeasure + measure.measure_index - 1, onset: tabOnset(candidate.onset), type: candidate.type, dots: candidate.dots };
    if (tabRowIssues(next, draft).length) throw new Error('리듬 후보의 위치·길이 또는 현재 줄·프렛을 확인해주세요.');
    replacements.set(row.id, next);
  }
  return { ...draft, rows: draft.rows.map(row => replacements.get(row.id) ?? row) };
}
