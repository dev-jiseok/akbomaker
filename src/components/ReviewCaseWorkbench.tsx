import { useEffect, useRef, useState } from 'react';
import { ClipboardCheck, Download, Plus, Save } from 'lucide-react';
import { request } from '../api';
import { annotationLabels, canConfirmCase, caseDraft, caseSummary, draftChanged, parseCaseBackup, referenceFromLayer,
  reviseDraft, validateReferenceEvent, type AnnotationKind, type CaseDraft, type CaseReport, type CaseSummary, type LocalCaseDraft, type ReviewCase } from '../reviewCases';
import { reviewLabels, reviewLayers, reviewPitchLabel, type ReviewData, type ReviewEvent, type ReviewLayer } from '../transcriptionReview';
import type { Instrument } from '../types';

type Selection = { layer: ReviewLayer; event: ReviewEvent };
type Props = { jobId: string; instrument: Instrument; data: ReviewData | null; selection: Selection | null; disabled: boolean; onDirty: (dirty: boolean) => void };
type NoteForm = { id: string; start: string; end: string; pitch: string };
const jsonHeaders = { 'Content-Type': 'application/json' };

function downloadBackup(value: unknown, filename: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function ReviewCaseResults({ report, dirty, instrument }: { report: CaseReport | null; dirty: boolean; instrument: Instrument }) {
  if (!report || dirty) return <p className="review-help">초안에는 점수를 표시하지 않아요. 구간 전체를 확인하고 초안 저장 → 검수 확정을 마쳐주세요.</p>;
  return <section className="review-case-results" aria-label="검수 확정 구간의 비교 결과"><h4>이 검수 구간의 비교 결과</h4>
    <p className="review-help">음정/GM 번호 일치 + 시작 시점 ±50ms 기준. 검수자 기준과의 일치도이며 곡 전체의 정확도가 아닙니다. 현재 악보 점수에는 수동 수정이 포함될 수 있어요.</p>
    <div className="review-case-table"><table><thead><tr><th>고정된 비교 단계</th><th>참조 / 인식</th><th>일치</th><th>누락</th><th>추가</th><th>F1</th>{instrument !== 'drums' && <th>종료 길이 불일치</th>}</tr></thead><tbody>
      {reviewLayers.map(layer => { const metric = report.metrics[layer]; return <tr key={layer}><th scope="row">{reviewLabels[layer]}</th><td>{metric.reference_notes} / {metric.estimated_notes}</td><td>{metric.matched_notes}</td><td>{metric.missing_notes}</td><td>{metric.extra_notes}</td><td>{metric.reference_present ? metric.f1.toFixed(3) : '참조 음표 없음'}</td>{instrument !== 'drums' && <td>{metric.duration_diagnostics?.onset_matches_with_wrong_offset ?? '—'}</td>}</tr>; })}
    </tbody></table></div><p className="review-help">같은 자동 음표를 복사해 검수한 경우 독립적인 정답 평가가 아닙니다. 자세한 누락·추가·혼동 후보는 검수 JSON에 보관됩니다.</p>
  </section>;
}

export default function ReviewCaseWorkbench({ jobId, instrument, data, selection, disabled, onDirty }: Props) {
  const endpoint = `/api/jobs/${jobId}/review-cases/${instrument}`;
  const [opened, setOpened] = useState(false);
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [record, setRecord] = useState<ReviewCase | null>(null);
  const [draft, setDraft] = useState<CaseDraft | null>(null);
  const [backup, setBackup] = useState<LocalCaseDraft | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [copyLayer, setCopyLayer] = useState<ReviewLayer>('recognized');
  const [form, setForm] = useState<NoteForm>({ id: '', start: '', end: '', pitch: instrument === 'drums' ? '42' : '60' });
  const [formDirty, setFormDirty] = useState(false);
  const [markKind, setMarkKind] = useState<AnnotationKind>('pitch');
  const [markStart, setMarkStart] = useState('');
  const [markEnd, setMarkEnd] = useState('');
  const [markNote, setMarkNote] = useState('');
  const [markDirty, setMarkDirty] = useState(false);
  const [markLink, setMarkLink] = useState<Selection | null>(null);
  const mounted = useRef(true), requestBusy = useRef(false), listGeneration = useRef(0);
  const dirty = !!record && !!draft && (draftChanged(draft, record) || formDirty || markDirty);
  const locked = disabled || busy;
  const sameSnapshot = !!record && !!data?.snapshot_id && record.snapshot_id === data.snapshot_id;
  const storageKey = record ? `akbo-review-draft:${jobId}:${instrument}:${record.id}` : '';

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; listGeneration.current++; }; }, []);
  useEffect(() => { onDirty(dirty || busy); return () => onDirty(false); }, [dirty, busy, onDirty]);
  useEffect(() => {
    if (!dirty && !busy) return;
    const prevent = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', prevent);
    return () => window.removeEventListener('beforeunload', prevent);
  }, [dirty, busy]);
  useEffect(() => {
    if (!record || !draft || !draftChanged(draft, record)) return;
    try {
      const saved: LocalCaseDraft = { schema: 'akbo.review-case-local-draft', case_id: record.id, base_revision: record.revision, draft };
      localStorage.setItem(storageKey, JSON.stringify(saved));
    } catch { setNotice('브라우저 임시 보관에 실패했어요. 초안을 서버에 저장하거나 JSON으로 보관해주세요.'); }
  }, [record, draft, storageKey]);
  useEffect(() => {
    if (!opened) return;
    const controller = new AbortController();
    const generation = ++listGeneration.current;
    request<{ cases: CaseSummary[] }>(endpoint, { signal: controller.signal }).then(result => { if (!controller.signal.aborted && generation === listGeneration.current) setCases(result.cases); })
      .catch(reason => { if (!controller.signal.aborted) setError(reason.message); });
    return () => controller.abort();
  }, [endpoint, opened]);

  function resetForms(next: ReviewCase) {
    setForm({ id: '', start: String(next.snapshot.window.start), end: String(Math.min(next.snapshot.duration, next.snapshot.window.start + .1)), pitch: instrument === 'drums' ? '42' : '60' });
    setFormDirty(false); setMarkDirty(false); setMarkLink(null); setMarkNote(''); setMarkStart(String(next.snapshot.window.start)); setMarkEnd(String(next.snapshot.window.start));
  }
  function accept(next: ReviewCase, recover = false) {
    listGeneration.current++;
    setRecord(next); setDraft(caseDraft(next)); resetForms(next);
    setCases(previous => [caseSummary(next), ...previous.filter(item => item.id !== next.id)]);
    setBackup(null);
    if (recover) {
      try { setBackup(parseCaseBackup(localStorage.getItem(`akbo-review-draft:${jobId}:${instrument}:${next.id}`), next)); } catch { /* Server copy remains available. */ }
    }
  }
  async function run(action: () => Promise<void>) {
    if (requestBusy.current || disabled) return;
    requestBusy.current = true; setBusy(true); setError(''); setNotice('');
    try { await action(); } catch (reason) { if (mounted.current) setError((reason as Error).message); }
    finally { requestBusy.current = false; if (mounted.current) setBusy(false); }
  }
  function mayLeave() { return !dirty || window.confirm('저장하지 않은 검수 내용이 있어요. 이동하면 입력 중인 내용은 사라질 수 있습니다. 먼저 초안을 저장하는 것을 권장해요. 계속할까요?'); }
  async function create() {
    if (!data?.snapshot_id || !mayLeave()) return;
    setOpened(true);
    await run(async () => {
      const next = await request<ReviewCase>(endpoint, { method: 'POST', headers: jsonHeaders, body: JSON.stringify({ start: data.window.start, seconds: Math.max(.25, data.window.end - data.window.start), snapshot_id: data.snapshot_id }) });
      if (mounted.current) { accept(next); setNotice('현재 구간의 비교 자료를 고정했어요. 검수용 참조 음표는 아직 비어 있습니다.'); }
    });
  }
  async function openCase(id: string) {
    if (!id || !mayLeave()) return;
    await run(async () => { const next = await request<ReviewCase>(`${endpoint}/${id}`); if (mounted.current) accept(next, true); });
  }
  function change(next: CaseDraft) { setDraft(next); setNotice(''); }
  function chooseNote(id: string) {
    if (formDirty && !window.confirm('입력 중인 음표를 적용하지 않고 이동할까요?')) return;
    const event = draft?.reference.events.find(item => item.id === id);
    if (!record) return;
    setForm({ id: event?.id || '', start: String(event?.start ?? record.snapshot.window.start), end: String(event?.end ?? Math.min(record.snapshot.duration, record.snapshot.window.start + .1)), pitch: String(event?.pitch ?? (instrument === 'drums' ? 42 : 60)) });
    setFormDirty(false);
  }
  function applyNote() {
    if (!record || !draft) return;
    try {
      if ([form.start, form.end, form.pitch].some(value => !value.trim())) throw new Error('시작·종료·음정 번호를 모두 입력해주세요.');
      if (form.id && !draft.reference.events.some(item => item.id === form.id)) throw new Error('선택한 참조 음표가 더 이상 없어요. 목록에서 다시 선택해주세요.');
      if (!form.id && draft.reference.events.length >= 1500) throw new Error('한 검수 구간에 최대 1,500개 음표를 저장할 수 있어요.');
      const event = validateReferenceEvent({ id: form.id || crypto.randomUUID(), start: Number(form.start), end: Number(form.end), pitch: Number(form.pitch), amplitude: draft.reference.events.find(item => item.id === form.id)?.amplitude ?? .8 }, record);
      const events = form.id ? draft.reference.events.map(item => item.id === form.id ? event : item) : [...draft.reference.events, event];
      change(reviseDraft(draft, { reference: { ...draft.reference, events } })); setForm({ ...form, id: event.id }); setFormDirty(false); setError('');
    } catch (reason) { setError((reason as Error).message); }
  }
  function addMark() {
    if (!record || !draft) return;
    const start = Number(markStart), end = Number(markEnd);
    if (!markStart.trim() || !markEnd.trim() || !Number.isFinite(start) || !Number.isFinite(end) || start < record.snapshot.window.start || start >= record.snapshot.window.end || end < start || end > record.snapshot.duration || !markNote.trim()) { setError('검수 구간 안의 시각과 확인 내용을 입력해주세요. 종료는 시작 이후 또는 같은 시각이어야 해요.'); return; }
    if (draft.annotations.length >= 300) { setError('한 구간에는 최대 300개 오류 메모를 저장할 수 있어요.'); return; }
    const linked = markKind !== 'missed' ? markLink : null;
    change(reviseDraft(draft, { annotations: [...draft.annotations, { id: crypto.randomUUID(), kind: markKind, layer: linked?.layer ?? null, event_id: linked?.event.id ?? null, start, end, note: markNote.trim() }] }));
    setMarkNote(''); setMarkLink(null); setMarkDirty(false); setError('');
  }
  async function save(status: 'draft' | 'reviewed') {
    if (!record || !draft) return;
    if (formDirty || markDirty) { setError('입력 중인 음표/메모를 먼저 목록에 추가하거나 적용해주세요.'); return; }
    if (status === 'reviewed' && !canConfirmCase(record, draft)) { setError('검수자·근거·전체 확인 체크를 초안으로 저장한 뒤 확정해주세요.'); return; }
    if (status === 'reviewed' && !window.confirm(`검수용 참조 ${draft.reference.events.length}개를 이 구간의 비교 기준으로 확정할까요? 전체 음표와 실제 무음 여부까지 확인해야 합니다. 자동 음표 복사만으로는 검수가 완료되지 않습니다.`)) return;
    await run(async () => {
      const next = await request<ReviewCase>(`${endpoint}/${record.id}`, { method: 'PUT', headers: jsonHeaders, body: JSON.stringify({ base_revision: record.revision, status, ...draft }) });
      if (mounted.current) {
        try { localStorage.removeItem(storageKey); } catch { /* Optional backup only. */ }
        accept(next); setNotice(status === 'reviewed' ? '검수 확정과 이 구간의 비교 결과를 저장했어요.' : '검수 초안을 저장했어요. 악보 파일은 변경하지 않았습니다.');
      }
    });
  }
  const seedEvents = draft?.reference.events || [];
  return <section className="review-case-workbench" aria-label="구간 오류 검수 기록">
    <div className="review-case-heading"><div><span className="eyebrow">REVIEW, THEN MEASURE</span><h3>오류를 기록하고, 기준을 확인해요</h3><p>검수 기록은 악보와 별도로 저장됩니다. 자동 인식 결과를 정답으로 확정하지 않아요.</p></div>
      <div className="review-case-actions"><button className="secondary small" disabled={locked || !data?.snapshot_id || cases.length >= 32} onClick={() => void create()}><Plus size={14} />현재 구간 검수 시작</button><button className="text-button" disabled={busy} onClick={() => setOpened(!opened)} aria-expanded={opened}>{opened ? '기록 접기' : '저장된 검수 열기'}</button></div></div>
    {!data && <p className="review-help">위에서 구간 비교를 열면 새 검수를 시작할 수 있어요. 이전 검수 기록은 다시 채보한 뒤에도 열 수 있습니다.</p>}
    {error && <p className="inline-alert" role="alert">{error}</p>}{notice && <p className="review-case-notice" role="status">{notice}</p>}
    {opened && <div className="review-case-content">
      <label className="review-case-picker">저장된 구간<select aria-label="저장된 검수 구간" value={record?.id || ''} disabled={locked} onChange={event => void openCase(event.target.value)}><option value="">검수 기록 선택 ({cases.length}/32)</option>{cases.map(item => <option value={item.id} key={item.id}>{item.window.start.toFixed(2)}–{item.window.end.toFixed(2)}초 · {item.status === 'reviewed' ? '검수 확정' : '초안'} · {item.id.slice(0, 6)}</option>)}</select></label>
      {record && draft && <>
        <div className="review-case-identity"><strong>{record.snapshot.window.start.toFixed(2)}–{record.snapshot.window.end.toFixed(2)}초 · {record.status === 'reviewed' && !dirty ? '검수 확정본' : '검수 초안'}{dirty ? ' · 저장 전 변경' : ' · 서버 저장됨'}</strong><span>고정 자료 {record.snapshot_id.slice(0, 12)} · 원음 기준 초 · 구간 안에서 시작하는 음표만 평가</span></div>
        {(!sameSnapshot || record.freshness.status !== 'current') && <p className="score-warning">과거 고정 자료를 보고 있어요. 위 비교 화면과 같은 구간·같은 버전인지 확인해주세요. 현재 타임라인의 음표를 이 기록에 자동 연결하지 않습니다. {record.freshness.message}</p>}
        {backup && <div className="draft-banner"><span>{backup.base_revision === record.revision ? '저장하지 않은 브라우저 임시 검수본이 있어요.' : '이전 서버 버전의 임시본이 있어요. 자동으로 덮어쓰지 않습니다.'}</span>{backup.base_revision === record.revision && <button className="text-button" disabled={locked} onClick={() => { setDraft({ ...backup.draft, reference: { ...backup.draft.reference, coverage_complete: false } }); setBackup(null); }}>임시본 복원</button>}<button className="text-button" onClick={() => downloadBackup(backup, `review-draft-${record.id}.json`)}>임시본 JSON 보관</button><button className="text-button" disabled={locked} onClick={() => { setBackup(null); try { localStorage.removeItem(storageKey); } catch { /* optional */ } }}>임시본 닫기</button></div>}
        <fieldset disabled={locked} className="review-case-fieldset"><legend>1. 오류 메모</legend><p className="review-help">오류 메모는 관찰 기록이에요. 아래 참조 음표를 자동으로 고치거나 점수의 정답으로 사용하지 않습니다.</p>
          {sameSnapshot && selection && <button className="text-button" onClick={() => { setMarkStart(String(Math.max(record.snapshot.window.start, selection.event.start))); setMarkEnd(String(Math.min(record.snapshot.duration, selection.event.end))); setMarkLink(selection); setMarkDirty(true); }}>선택한 {reviewPitchLabel(instrument, selection.event.pitch)}의 시각 사용</button>}
          {markLink && markKind !== 'missed' && <p className="review-help">연결된 고정 음표: {reviewLabels[markLink.layer]} · {reviewPitchLabel(instrument, markLink.event.pitch)} · {markLink.event.start.toFixed(3)}초 <button className="text-button" onClick={() => { setMarkLink(null); setMarkDirty(true); }}>연결 해제</button></p>}
          <div className="review-case-form"><label>오류 종류<select aria-label="검수 오류 종류" value={markKind} onChange={event => { setMarkKind(event.target.value as AnnotationKind); setMarkDirty(true); }}>{Object.entries(annotationLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label>시작 (초)<input aria-label="오류 메모 시작 초" type="number" step="0.001" value={markStart} onChange={event => { setMarkStart(event.target.value); setMarkDirty(true); }} /></label><label>종료 (초)<input aria-label="오류 메모 종료 초" type="number" step="0.001" value={markEnd} onChange={event => { setMarkEnd(event.target.value); setMarkDirty(true); }} /></label></div>
          <label className="review-case-wide">확인 내용<textarea aria-label="오류 메모 내용" maxLength={1000} value={markNote} placeholder="예: 4.2초는 킥이 아니라 닫힌 하이햇으로 들림" onChange={event => { setMarkNote(event.target.value); setMarkDirty(true); }} /></label><div className="review-case-actions"><button className="secondary small" onClick={addMark}>메모 목록에 추가</button>{markDirty && <button className="text-button" onClick={() => { setMarkNote(''); setMarkLink(null); setMarkDirty(false); }}>메모 입력 취소</button>}</div>
          <ul className="review-case-marks">{draft.annotations.map(item => <li key={item.id}><span><strong>{annotationLabels[item.kind]} · {item.start.toFixed(3)}초</strong> {item.note}<small>{item.layer ? `${reviewLabels[item.layer]}의 선택 음표와 연결` : '특정 음표에 연결하지 않은 구간 메모'}</small></span><button className="text-button" aria-label={`${item.start.toFixed(3)}초 오류 메모 삭제`} onClick={() => change(reviseDraft(draft, { annotations: draft.annotations.filter(mark => mark.id !== item.id) }))}>삭제</button></li>)}</ul>
        </fieldset>
        <fieldset disabled={locked} className="review-case-fieldset"><legend>2. 검수용 참조 음표 ({seedEvents.length}개)</legend><p className="review-help">악보 원본은 바뀌지 않습니다. 구간 시작 이전부터 이어진 음은 이 시작점 평가에서 제외해요. 드럼 종료 길이는 실제 잔향이 아닌 기록용 길이입니다.</p>
          <div className="review-case-actions"><select aria-label="참조 초안 복사 단계" value={copyLayer} onChange={event => setCopyLayer(event.target.value as ReviewLayer)}>{reviewLayers.map(layer => <option key={layer} value={layer}>{reviewLabels[layer]}</option>)}</select><button className="secondary small" onClick={() => { if ((seedEvents.length || formDirty) && !window.confirm('현재 참조 음표 목록을 선택한 단계의 고정 자료로 교체할까요? 악보 자체는 바뀌지 않습니다.')) return; change(reviseDraft(draft, { reference: { ...draft.reference, seed_layer: copyLayer, events: referenceFromLayer(record, copyLayer, () => crypto.randomUUID()) } })); setForm({ id: '', start: String(record.snapshot.window.start), end: String(Math.min(record.snapshot.duration, record.snapshot.window.start + .1)), pitch: instrument === 'drums' ? '42' : '60' }); setFormDirty(false); }}>고정 음표를 초안으로 복사</button></div>
          {draft.reference.seed_layer && <p className="review-help">시작 자료: {reviewLabels[draft.reference.seed_layer]}. 복사는 검수가 아니에요. 원음과 대조해 추가·삭제·수정해주세요.</p>}
          <label className="review-case-wide">참조 음표 선택<select aria-label="검수 참조 음표 선택" size={Math.min(7, Math.max(3, seedEvents.length + 1))} value={form.id} onChange={event => chooseNote(event.target.value)}><option value="">＋ 새 참조 음표</option>{[...seedEvents].sort((a, b) => a.start - b.start || a.pitch - b.pitch).map(event => <option key={event.id} value={event.id}>{event.start.toFixed(3)}–{event.end.toFixed(3)}초 · {reviewPitchLabel(instrument, event.pitch)}</option>)}</select></label>
          <div className="review-case-form"><label>시작 (초)<input aria-label="참조 음표 시작 초" type="number" step="0.001" value={form.start} onChange={event => { setForm({ ...form, start: event.target.value }); setFormDirty(true); }} /></label><label>종료 (초)<input aria-label="참조 음표 종료 초" type="number" step="0.001" value={form.end} onChange={event => { setForm({ ...form, end: event.target.value }); setFormDirty(true); }} /></label><label>{instrument === 'drums' ? '드럼 GM 번호' : 'MIDI 음정'}<input aria-label="참조 음표 MIDI 번호" type="number" min="0" max="127" step="1" value={form.pitch} onChange={event => { setForm({ ...form, pitch: event.target.value }); setFormDirty(true); }} /></label><span>{Number.isInteger(Number(form.pitch)) && Number(form.pitch) >= 0 && Number(form.pitch) <= 127 ? reviewPitchLabel(instrument, Number(form.pitch)) : ''}</span></div>
          <div className="review-case-actions"><button className="secondary small" onClick={applyNote}>{form.id ? '참조 음표 수정 적용' : '참조 음표 추가'}</button>{form.id && <button className="text-button" onClick={() => { change(reviseDraft(draft, { reference: { ...draft.reference, events: seedEvents.filter(event => event.id !== form.id) } })); setForm({ ...form, id: '' }); setFormDirty(false); }}>선택한 참조 삭제</button>}{formDirty && <button className="text-button" onClick={() => { setFormDirty(false); const event = seedEvents.find(item => item.id === form.id); setForm({ id: event?.id || '', start: String(event?.start ?? record.snapshot.window.start), end: String(event?.end ?? Math.min(record.snapshot.duration, record.snapshot.window.start + .1)), pitch: String(event?.pitch ?? 60) }); }}>음표 입력 취소</button>}</div>
        </fieldset>
        <fieldset disabled={locked} className="review-case-fieldset"><legend>3. 확인 근거와 확정</legend><div className="review-case-form"><label>검수자<input aria-label="검수자 이름" maxLength={80} value={draft.reference.reviewer} onChange={event => change(reviseDraft(draft, { reference: { ...draft.reference, reviewer: event.target.value } }))} /></label></div><label className="review-case-wide">검수 근거<textarea aria-label="검수 근거" maxLength={2000} value={draft.reference.basis} placeholder="어떤 음원·악보를 어떻게 대조했는지 기록해주세요. 검수자 이름은 인증된 신원이 아닙니다." onChange={event => change(reviseDraft(draft, { reference: { ...draft.reference, basis: event.target.value } }))} /></label>
          <label className="check-label review-case-confirm"><input aria-label="검수 구간 전체 확인" type="checkbox" checked={draft.reference.coverage_complete} onChange={event => change({ ...draft, reference: { ...draft.reference, coverage_complete: event.target.checked } })} />이 구간에서 시작하는 모든 음표의 누락·추가·종류·시각을 확인했습니다. 음표가 0개라면 실제로 해당 악기 소리가 없는지도 확인했습니다.</label>
          <p className="review-help">초안 저장 후 별도로 검수 확정해야 점수가 계산돼요. 참조나 메모를 바꾸면 전체 확인을 다시 해야 합니다. 입력 중인 음표·메모는 먼저 목록에 적용해주세요.</p>
          <div className="review-case-actions"><button className="secondary small" onClick={() => void save('draft')} disabled={formDirty || markDirty}><Save size={14} />검수 초안 저장</button><button className="primary small" onClick={() => void save('reviewed')} disabled={!canConfirmCase(record, draft) || formDirty || markDirty || record.freshness.status !== 'current'}><ClipboardCheck size={14} />검수 확정</button><button className="text-button" disabled={formDirty || markDirty} onClick={() => downloadBackup({ schema: 'akbo.review-case-local-draft', case_id: record.id, base_revision: record.revision, draft }, `review-draft-${record.id}.json`)}>현재 초안 JSON 보관</button></div>
        </fieldset>
        <ReviewCaseResults report={record.report} dirty={dirty} instrument={instrument} />
        <div className="review-case-actions"><a className="text-button" href={`${endpoint}/${record.id}/export`} onClick={event => { if (dirty) { event.preventDefault(); setError('내보내기에는 서버 저장본만 포함됩니다. 먼저 초안을 저장해주세요.'); } }}><Download size={14} />저장된 검수 JSON 내보내기</a><small>음원 파일은 포함하지 않습니다. 고정된 구간 자료와 검수 기록만 내보냅니다.</small></div>
      </>}
    </div>}
  </section>;
}
