export type TabBox = [number, number, number, number];
export type TabDigit = { id: string; text: string; fret: number | null; string: number | null; bbox: TabBox; accepted: boolean; reasons?: string[] };
export type TabStaff = { id: string; kind: string; line_count: number; bbox: TabBox; digits: TabDigit[]; warnings?: string[]; measure_boundaries_verified?: boolean; measures?: { id: string; index: number; bbox: TabBox; digit_ids: string[] }[]; symbols?: { text: string; bbox: TabBox; reason: string }[] };
export type TabPage = { page: number; width: number; height: number; staffs: TabStaff[]; warnings?: string[] };
export type TabAnalysis = { source_sha256: string; pages: TabPage[]; warnings?: string[]; rhythm_known?: false };
export const tabTypes = ['whole', 'half', 'quarter', 'eighth', '16th', '32nd', '64th'] as const;
export type TabType = typeof tabTypes[number];
export const tabTypeLabels: Record<TabType, string> = { whole: '온음표 · 4박', half: '2분음표 · 2박', quarter: '4분음표 · 1박', eighth: '8분음표 · 1/2박', '16th': '16분음표 · 1/4박', '32nd': '32분음표 · 1/8박', '64th': '64분음표 · 1/16박' };
export const standardTabTuning = { bass: [43, 38, 33, 28], guitar: [64, 59, 55, 50, 45, 40] };
export type TabReviewRow = { id: string; staff_id: string; decision: 'note' | 'exclude'; reason: string; measure: number | null; onset: string | null; type: TabType | null; dots: 0 | 1 | 2; string: number | null; fret: number | null; muted?: boolean };
export type TabReviewDraft = { source_sha256: string; coordinate_sha256: string; instrument: 'bass' | 'guitar'; title: string; tuning: number[]; capo: number; beats: number; beat_type: number; tempo: number; staff_ids: string[]; rows: TabReviewRow[] };
export type TabReviewState = { revision: string; coordinate_sha256: string; source_sha256: string; source_url: string; preview_urls: string[]; analysis: TabAnalysis; draft: TabReviewDraft | null };
export type TabRowFields = { decision: 'note' | 'exclude'; reason: string; measure: string; onset: string; type: string; dots: string; string: string; fret: string; muted: boolean };

export function tabReviewUrl(value: string, jobId: string): string | undefined {
  try {
    const url = new URL(value, window.location.origin);
    if (!/^[a-f0-9]{32}$/.test(jobId) || url.origin !== window.location.origin || url.username || url.password || !url.pathname.startsWith(`/api/score-omr/${jobId}/`)) return undefined;
    return url.pathname + url.search;
  } catch { return undefined; }
}
export function tabStaffs(analysis: TabAnalysis): { staff: TabStaff; page: TabPage }[] {
  return analysis.pages.flatMap(page => page.staffs.filter(staff => staff.kind === 'tab').map(staff => ({ staff, page })));
}
export function tabEvidenceViewBox(page: TabPage, staff: TabStaff, full = false): string {
  if (full) return `0 0 ${page.width} ${page.height}`;
  const preceding = page.staffs.filter(item => item.kind === 'staff' && item.line_count === 5
    && item.bbox[3] <= staff.bbox[1] && staff.bbox[1] - item.bbox[3] <= 100
    && Math.min(item.bbox[2], staff.bbox[2]) - Math.max(item.bbox[0], staff.bbox[0]) >= (staff.bbox[2] - staff.bbox[0]) * .6)
    .sort((a, b) => b.bbox[3] - a.bbox[3])[0];
  const left = Math.max(0, Math.min(staff.bbox[0], preceding?.bbox[0] ?? staff.bbox[0]) - 12);
  const top = Math.max(0, preceding ? preceding.bbox[1] - 28 : staff.bbox[1] - 80);
  const right = Math.min(page.width, Math.max(staff.bbox[2], preceding?.bbox[2] ?? staff.bbox[2]) + 12);
  const bottom = Math.min(page.height, staff.bbox[3] + 36);
  return `${left} ${top} ${Math.max(1, right - left)} ${Math.max(1, bottom - top)}`;
}
export function rowsForTabStaff(staff: TabStaff): TabReviewRow[] {
  return staff.digits.map(digit => ({ id: `${staff.id}:${digit.id}`, staff_id: staff.id, decision: 'note', reason: '', measure: null, onset: null, type: null, dots: 0, string: digit.string, fret: digit.fret }));
}
export function createTabDraft(state: TabReviewState): TabReviewDraft {
  const first = tabStaffs(state.analysis)[0]?.staff;
  const instrument = first?.line_count === 6 ? 'guitar' : 'bass';
  return { source_sha256: state.source_sha256, coordinate_sha256: state.coordinate_sha256, instrument, title: '직접 검수한 TAB 악보', tuning: [...standardTabTuning[instrument]], capo: 0, beats: 4, beat_type: 4, tempo: 120, staff_ids: first ? [first.id] : [], rows: first ? rowsForTabStaff(first) : [] };
}
export function tabRowFields(row: TabReviewRow): TabRowFields {
  return { decision: row.decision, reason: row.reason, measure: row.measure == null ? '' : String(row.measure), onset: row.onset ?? '', type: row.type ?? '', dots: String(row.dots), string: row.string == null ? '' : String(row.string), fret: row.fret == null ? '' : String(row.fret), muted: !!row.muted };
}
function gcd(a: number, b: number): number { while (b) [a, b] = [b, a % b]; return a; }
export function tabOnset(value: string): string | null {
  const clean = value.trim();
  if (!clean) return null;
  let top: number, bottom: number;
  if (/^\d{1,6}(?:\/\d{1,6})?$/.test(clean)) { const pieces = clean.split('/'); top = Number(pieces[0]); bottom = Number(pieces[1] ?? 1); }
  else if (/^\d{1,6}\.\d{1,6}$/.test(clean)) { const [whole, tail] = clean.split('.'); bottom = 10 ** tail.length; top = Number(whole) * bottom + Number(tail); }
  else throw new Error('시작 위치는 0, 0.5, 1/2처럼 0 이상인 박 수로 입력해주세요.');
  if (!bottom || !Number.isSafeInteger(top)) throw new Error('시작 위치의 분수를 확인해주세요.');
  const divisor = gcd(top, bottom); top /= divisor; bottom /= divisor;
  if (64 % bottom !== 0) throw new Error('시작 위치는 1/64박 단위까지 정확히 표현되는 값으로 입력해주세요. 0.5는 1/2박이에요.');
  return bottom === 1 ? String(top) : `${top}/${bottom}`;
}
export function tabBeats(value: string): number { const [a, b = '1'] = value.split('/'); return Number(a) / Number(b); }
export function tabDuration(type: TabType, dots: number): number {
  const units: Record<TabType, number> = { whole: 4, half: 2, quarter: 1, eighth: .5, '16th': .25, '32nd': .125, '64th': .0625 };
  return units[type] * (dots === 2 ? 1.75 : dots === 1 ? 1.5 : 1);
}
export function tabDurationLabel(type: TabType, dots: number): string {
  if (!dots) return tabTypeLabels[type];
  return `${dots === 2 ? '겹점' : '점'} ${tabTypeLabels[type].split(' · ')[0]} · ${tabDuration(type, dots)}박`;
}
function integerField(value: string, low: number, high: number, name: string): number | null {
  if (!value.trim()) return null;
  const number = Number(value);
  if (!/^\d+$/.test(value.trim()) || !Number.isSafeInteger(number) || number < low || number > high) throw new Error(`${name}은 ${low}~${high} 정수로 입력해주세요.`);
  return number;
}
export function applyTabRowFields(row: TabReviewRow, fields: TabRowFields): TabReviewRow {
  if (fields.decision === 'exclude') return { ...row, decision: 'exclude', reason: fields.reason, measure: null, onset: null, type: null, dots: 0, string: null, fret: null, muted: false };
  if (fields.type && !tabTypes.includes(fields.type as TabType)) throw new Error('음표 길이를 선택해주세요.');
  return { ...row, decision: 'note', reason: fields.reason, measure: integerField(fields.measure, 1, 600, '마디'), onset: tabOnset(fields.onset), type: (fields.type || null) as TabType | null, dots: (integerField(fields.dots, 0, 2, '점 개수') ?? 0) as 0 | 1 | 2, string: integerField(fields.string, 1, 6, '줄'), fret: integerField(fields.fret, 0, 36, '프렛'), muted: fields.muted };
}
export function parseTabTuning(value: string): number[] {
  const pieces = value.split(',').map(item => item.trim());
  if (pieces.length < 4 || pieces.length > 6 || pieces.some(item => !/^\d{1,3}$/.test(item))) throw new Error('튜닝은 높은 줄부터 MIDI 음 번호 4~6개를 쉼표로 나누어 입력해주세요.');
  const tuning = pieces.map(Number);
  if (tuning.some((value, i) => value < 12 || value > 127 || i > 0 && value >= tuning[i - 1])) throw new Error('튜닝은 12~127 사이의 MIDI 음 번호를 높은 줄부터 낮은 줄 순서로 입력해주세요.');
  return tuning;
}
export function tabRowIssues(row: TabReviewRow, draft: TabReviewDraft): string[] {
  if (row.decision === 'exclude') return row.reason.trim() ? [] : ['제외 이유 필요'];
  const issues: string[] = [];
  if (!Number.isInteger(row.measure) || row.measure == null || row.measure < 1 || row.measure > 600) issues.push('마디 필요');
  let onset: number | null = null;
  try { const canonical = tabOnset(row.onset ?? ''); if (canonical == null) issues.push('시작 위치 필요'); else onset = tabBeats(canonical); } catch { issues.push('시작 위치 확인'); }
  if (!row.type || !tabTypes.includes(row.type)) issues.push('길이 필요');
  if (row.string == null || !Number.isInteger(row.string) || row.string < 1 || row.string > draft.tuning.length) issues.push('줄 확인');
  if (row.fret == null || !Number.isInteger(row.fret) || row.fret < 0 || row.fret > 36) issues.push('프렛 확인');
  const barLength = draft.beats * 4 / draft.beat_type;
  if (onset != null && onset >= barLength) issues.push('시작 위치는 마디 안으로 지정');
  if (row.type && onset != null && row.measure != null && (row.measure - 1) * barLength + onset + tabDuration(row.type, row.dots) > 600 * barLength) issues.push('최대 600마디 범위 초과');
  return issues;
}
export function tabDraftSettingIssues(draft: TabReviewDraft, analysis: TabAnalysis): string[] {
  const issues: string[] = [], staffs = tabStaffs(analysis);
  if (!draft.staff_ids.length) issues.push('검수할 TAB 보표를 선택해주세요.');
  if (!Number.isInteger(draft.tempo) || draft.tempo < 20 || draft.tempo > 300) issues.push('BPM은 20~300 정수로 입력해주세요.');
  if (!Number.isInteger(draft.capo) || draft.capo < 0 || draft.capo > 12) issues.push('카포는 0~12 정수로 입력해주세요.');
  if (!Number.isInteger(draft.beats) || draft.beats < 1 || draft.beats > 12 || ![2, 4, 8, 16].includes(draft.beat_type)) issues.push('박자표를 확인해주세요.');
  if (draft.staff_ids.some(id => staffs.find(item => item.staff.id === id)?.staff.line_count !== draft.tuning.length)) issues.push('선택한 보표의 줄 수와 튜닝의 줄 수가 일치해야 해요.');
  if (draft.rows.length > 2000) issues.push('검수 항목은 최대 2,000개까지 나누어 작업해주세요.');
  return issues;
}
export function tabDraftIssues(draft: TabReviewDraft, analysis: TabAnalysis): string[] {
  const issues = tabDraftSettingIssues(draft, analysis), staffs = tabStaffs(analysis);
  if (!draft.title.trim()) issues.push('악보 제목을 입력해주세요.');
  const ids = new Set<string>();
  for (const row of draft.rows) { if (ids.has(row.id)) issues.push('중복된 검수 항목이 있어요.'); ids.add(row.id); }
  for (const id of draft.staff_ids) {
    const staff = staffs.find(item => item.staff.id === id)?.staff;
    if (!staff || staff.digits.some(digit => !ids.has(`${id}:${digit.id}`))) issues.push('선택한 보표의 모든 숫자를 검토해주세요.');
  }
  const incomplete = draft.rows.filter(row => tabRowIssues(row, draft).length).length;
  if (incomplete) issues.push(`아직 입력이 끝나지 않은 항목 ${incomplete}개가 있어요.`);
  const notes = draft.rows.filter(row => row.decision === 'note' && !tabRowIssues(row, draft).length);
  if (!notes.length) issues.push('악보로 만들 음표가 한 개 이상 필요해요.');
  const perString = new Map<string, TabReviewRow[]>();
  const absoluteOnset = (note: TabReviewRow) => (note.measure! - 1) * draft.beats * 4 / draft.beat_type + tabBeats(note.onset!);
  for (const note of notes) { const key = String(note.string); const list = perString.get(key) ?? []; list.push(note); perString.set(key, list); }
  for (const values of perString.values()) {
    const sorted = values.sort((a, b) => absoluteOnset(a) - absoluteOnset(b));
    let previousEnd = -Infinity;
    if (sorted.some(note => { const start = absoluteOnset(note); const overlap = start < previousEnd; previousEnd = Math.max(previousEnd, start + tabDuration(note.type!, note.dots)); return overlap; })) { issues.push('같은 줄에서 음표 시간이 겹칩니다. 시작 위치·길이를 확인해주세요.'); break; }
  }
  return [...new Set(issues)];
}
export function assignTabSequence(draft: TabReviewDraft, selected: string[], measure: number, start: string, type: 'eighth' | '16th'): TabReviewDraft {
  if (!selected.length) throw new Error('직접 순서를 배정할 숫자를 먼저 체크해주세요.');
  const selectedSet = new Set(selected), rows = draft.rows.filter(row => selectedSet.has(row.id));
  if (rows.length !== selectedSet.size || rows.some(row => row.decision !== 'note')) throw new Error('음표로 사용할 항목만 선택해주세요.');
  if (!Number.isInteger(measure) || measure < 1 || measure > 600) throw new Error('시작 마디를 1~600 중에서 입력해주세요.');
  const parsed = tabOnset(start); if (parsed == null) throw new Error('시작 위치를 입력해주세요.');
  let onset = tabBeats(parsed), bar = measure;
  const barLength = draft.beats * 4 / draft.beat_type, length = tabDuration(type, 0);
  if (onset < 0 || onset >= barLength) throw new Error('마디 안의 시작 위치를 입력해주세요.');
  const replacements = new Map<string, TabReviewRow>();
  for (const row of rows) {
    if (bar > 600 || onset + length > barLength) throw new Error('선택한 길이가 마디 경계를 넘어요. 시작 위치·박자표를 확인해주세요.');
    replacements.set(row.id, { ...row, measure: bar, onset: tabOnset(String(onset)), type, dots: 0 });
    onset += length; if (onset === barLength) { bar++; onset = 0; }
  }
  return { ...draft, rows: draft.rows.map(row => replacements.get(row.id) ?? row) };
}
