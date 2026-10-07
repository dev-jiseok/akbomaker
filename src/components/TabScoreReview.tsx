import { useEffect, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, Download, LoaderCircle, Plus, Save } from 'lucide-react';
import { request } from '../api';
import type { Job } from '../types';
import TabRhythmSuggestions from './TabRhythmSuggestions';
import { applyRhythmProposal } from '../tabRhythmSuggestions';
import { applyTabRowFields, assignTabSequence, createTabDraft, parseTabTuning, rowsForTabStaff, standardTabTuning, tabDraftIssues, tabDraftSettingIssues, tabDurationLabel, tabEvidenceViewBox, tabReviewUrl, tabRowFields, tabRowIssues, tabStaffs, tabTypeLabels, tabTypes, type TabReviewDraft, type TabReviewRow, type TabReviewState, type TabRowFields } from '../tabReview';

type Props = { jobId: string; disabled: boolean; onImported: (job: Job) => void; onActivity?: (active: boolean) => void; onDirty?: (dirty: boolean) => void };
const pageSize = 20;
const emptyFields: TabRowFields = { decision: 'note', reason: '', measure: '', onset: '', type: '', dots: '0', string: '', fret: '', muted: false };

export default function TabScoreReview({ jobId, disabled, onImported, onActivity, onDirty }: Props) {
  const endpoint = `/api/score-omr/${encodeURIComponent(jobId)}/tab-review`;
  const [state, setState] = useState<TabReviewState | null>(null), [draft, setDraft] = useState<TabReviewDraft | null>(null);
  const [baseline, setBaseline] = useState(''), [tuning, setTuning] = useState('');
  const [activeStaff, setActiveStaff] = useState(''), [selectedId, setSelectedId] = useState(''), [fields, setFields] = useState<TabRowFields>(emptyFields);
  const [rowPage, setRowPage] = useState(0), [checked, setChecked] = useState<string[]>([]);
  const [sequenceMeasure, setSequenceMeasure] = useState('1'), [sequenceStart, setSequenceStart] = useState('0'), [sequenceType, setSequenceType] = useState<'eighth' | '16th'>('eighth');
  const [fullPage, setFullPage] = useState(false), [confirmed, setConfirmed] = useState(false), [backupUrl, setBackupUrl] = useState('');
  const [busy, setBusy] = useState(true), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [rhythmBusy, setRhythmBusy] = useState(false);
  const mounted = useRef(true), lock = useRef(false), operation = useRef<AbortController | null>(null), generation = useRef(0);
  const selected = draft?.rows.find(row => row.id === selectedId);
  const fieldsDirty = !!selected && JSON.stringify(fields) !== JSON.stringify(tabRowFields(selected));
  const dirty = !!draft && (JSON.stringify(draft) !== baseline || fieldsDirty || tuning !== draft.tuning.join(','));
  const locked = disabled || busy || rhythmBusy;
  const staffs = state ? tabStaffs(state.analysis) : [];
  const selectedPages = [...new Set(staffs.filter(item => draft?.staff_ids.includes(item.staff.id)).map(item => item.page.page))];
  const active = staffs.find(item => item.staff.id === activeStaff);
  const staffRows = draft?.rows.filter(row => row.staff_id === activeStaff) ?? [];
  const visibleRows = staffRows.slice(rowPage * pageSize, (rowPage + 1) * pageSize);
  let prepared: TabReviewDraft | null = draft, inputError = '';
  try {
    if (draft) {
      const corrected = selected && fieldsDirty ? applyTabRowFields(selected, fields) : null;
      prepared = { ...draft, tuning: parseTabTuning(tuning), rows: corrected ? draft.rows.map(row => row.id === corrected.id ? corrected : row) : draft.rows };
    }
  } catch (err) { prepared = null; inputError = (err as Error).message; }
  const issues = prepared && state ? tabDraftIssues(prepared, state.analysis) : [inputError || '검수 초안을 불러와주세요.'];
  const completeCount = prepared?.rows.filter(row => tabRowIssues(row, prepared!).length === 0).length ?? 0;
  const noteCount = prepared?.rows.filter(row => row.decision === 'note').length ?? 0;

  useEffect(() => { onActivity?.(busy || rhythmBusy); }, [busy, rhythmBusy, onActivity]);
  useEffect(() => { onDirty?.(dirty); return () => onDirty?.(false); }, [dirty, onDirty]);
  useEffect(() => { if (!dirty) return; const warn = (event: BeforeUnloadEvent) => event.preventDefault(); window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn); }, [dirty]);
  useEffect(() => {
    if (!draft || !state) return;
    const blob = new Blob([JSON.stringify({ review_job_id: jobId, base_revision: state.revision, draft: prepared ?? draft, ...(fieldsDirty ? { unapplied_row: { id: selectedId, fields } } : {}), tuning_input: tuning }, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob); setBackupUrl(url); return () => URL.revokeObjectURL(url);
  }, [draft, state?.revision, selectedId, fields, fieldsDirty, tuning, jobId]);
  useEffect(() => {
    mounted.current = true; const current = ++generation.current;
    setState(null); setDraft(null); setBaseline(''); setFields(emptyFields); setSelectedId(''); setActiveStaff(''); setConfirmed(false); setChecked([]); setRowPage(0);
    void load(current);
    return () => { mounted.current = false; generation.current++; operation.current?.abort(); onActivity?.(false); };
    // A different original is a different review session. Callback changes must
    // not discard an in-progress draft or start another fetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [endpoint]);

  function adopt(next: TabReviewState, fallback?: TabReviewDraft) {
    if (next.draft && (next.draft.source_sha256 !== next.source_sha256 || next.draft.coordinate_sha256 !== next.coordinate_sha256)) throw new Error('초안과 원본 PDF 좌표 자료가 일치하지 않아요. 원본을 다시 확인해주세요.');
    const value = next.draft ?? fallback ?? createTabDraft(next);
    setState(next); setDraft(value); setBaseline(JSON.stringify(value)); setTuning(value.tuning.join(','));
    const staffId = value.staff_ids.includes(activeStaff) ? activeStaff : value.staff_ids[0] ?? '';
    const first = value.rows.find(row => row.id === selectedId && row.staff_id === staffId) ?? value.rows.find(row => row.staff_id === staffId);
    setActiveStaff(staffId); setSelectedId(first?.id ?? ''); setFields(first ? tabRowFields(first) : emptyFields); setRowPage(0); setChecked([]); setConfirmed(false);
  }
  async function load(current = ++generation.current) {
    if (!/^[a-f0-9]{32}$/.test(jobId)) { setError('인식 작업 주소를 확인해주세요.'); setBusy(false); return; }
    operation.current?.abort(); const controller = new AbortController(); operation.current = controller;
    lock.current = true; setBusy(true); setError('');
    try {
      const result = await request<TabReviewState>(endpoint, { signal: controller.signal });
      if (!mounted.current || controller.signal.aborted || current !== generation.current) return;
      adopt(result); setNotice(result.draft ? '서버에 저장한 검수 초안을 불러왔어요. 확인 체크는 다시 해주세요.' : '숫자 좌표를 준비했어요. 오선 표기가 있으면 리듬 후보를 읽을 수 있어요. 후보가 없는 부분은 원본을 보며 입력해주세요.');
    } catch (err) { if (mounted.current && !controller.signal.aborted && current === generation.current) setError((err as Error).message); }
    finally { if (current === generation.current) { lock.current = false; if (mounted.current) setBusy(false); } }
  }
  function change(next: TabReviewDraft) { setDraft(next); setConfirmed(false); setNotice(''); setError(''); }
  function abandonFields() { return !fieldsDirty || window.confirm('아직 적용하지 않은 숫자 입력을 버리고 다른 항목으로 이동할까요? 초안 저장 버튼으로 현재 입력까지 저장할 수 있어요.'); }
  function choose(row: TabReviewRow) {
    if (locked || !abandonFields()) return;
    setSelectedId(row.id); setFields(tabRowFields(row)); setConfirmed(false);
  }
  function chooseStaff(id: string) {
    if (!draft || locked || !abandonFields()) return;
    const first = draft.rows.find(row => row.staff_id === id);
    setActiveStaff(id); setSelectedId(first?.id ?? ''); setFields(first ? tabRowFields(first) : emptyFields); setRowPage(0); setChecked([]); setConfirmed(false);
  }
  function updateField<K extends keyof TabRowFields>(key: K, value: TabRowFields[K]) { setFields(previous => ({ ...previous, [key]: value })); setConfirmed(false); setNotice(''); }
  function commitFields(): TabReviewDraft | null {
    if (!prepared) { setError(inputError); return null; }
    if (prepared.rows.some(row => row.reason.length > 200)) { setError('제외 이유는 200자 이하로 적어주세요.'); return null; }
    change(prepared); setTuning(prepared.tuning.join(',')); const row = prepared.rows.find(item => item.id === selectedId); if (row) setFields(tabRowFields(row));
    return prepared;
  }
  function scope(id: string, include: boolean) {
    if (!draft || !state || locked || !abandonFields()) return;
    if (!include && draft.staff_ids.length === 1) { setError('검수할 보표를 최소 한 개 남겨주세요.'); return; }
    if (!include && !window.confirm('이 보표를 검수 범위에서 빼고 해당 숫자의 초안 입력을 지울까요? 원본 PDF와 다른 보표의 입력은 유지합니다.')) return;
    const staff = staffs.find(item => item.staff.id === id)?.staff; if (!staff) return;
    const rows = include ? [...draft.rows, ...rowsForTabStaff(staff)] : draft.rows.filter(row => row.staff_id !== id);
    if (rows.length > 2000) { setError('검수 항목은 2,000개 이하로 나누어주세요.'); return; }
    const staffIds = include ? [...draft.staff_ids, id] : draft.staff_ids.filter(value => value !== id);
    const nextStaff = include ? id : activeStaff === id ? staffIds[0] : activeStaff;
    change({ ...draft, staff_ids: staffIds, rows }); setActiveStaff(nextStaff); const first = rows.find(row => row.staff_id === nextStaff);
    setSelectedId(first?.id ?? ''); setFields(first ? tabRowFields(first) : emptyFields); setRowPage(0); setChecked([]);
  }
  function addManual() {
    if (!draft || !active || locked || !abandonFields()) return;
    if (draft.rows.length >= 2000) { setError('검수 항목은 2,000개 이하로 나누어주세요.'); return; }
    const row: TabReviewRow = { id: `manual-${crypto.randomUUID().replace(/-/g, '')}`, staff_id: activeStaff, decision: 'note', reason: '', measure: null, onset: null, type: null, dots: 0, string: null, fret: null };
    change({ ...draft, rows: [...draft.rows, row] }); setSelectedId(row.id); setFields(tabRowFields(row)); setRowPage(Math.floor(staffRows.length / pageSize));
  }
  function sequence() {
    if (!prepared || locked) { if (inputError) setError(inputError); return; }
    if (!window.confirm('체크한 숫자를 목록 순서대로 같은 길이로 직접 배정할까요? 원본 리듬을 자동 인식한 결과가 아닙니다. 동시에 연주하는 음은 따로 같은 시작 위치로 입력해주세요.')) return;
    try { const next = assignTabSequence(prepared, checked, Number(sequenceMeasure), sequenceStart, sequenceType); change(next); const row = next.rows.find(item => item.id === selectedId); if (row) setFields(tabRowFields(row)); setNotice('선택한 숫자에 직접 지정한 순서를 적용했어요. 원본 리듬과 비교해주세요.'); }
    catch (err) { setError((err as Error).message); }
  }
  async function save(importProject = false) {
    if (!state || !draft || locked || lock.current || !prepared) { if (inputError) setError(inputError); return; }
    if (importProject && (!confirmed || issues.length)) return;
    const settingIssues = tabDraftSettingIssues(prepared, state.analysis); if (settingIssues.length) { setError(settingIssues.join(' ')); return; }
    const value = prepared; lock.current = true; setBusy(true); setError(''); setNotice('');
    const controller = new AbortController(); operation.current = controller; const current = generation.current;
    try {
      if (importProject) {
        const result = await request<Job>(endpoint + '/import', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ base_revision: state.revision, draft: value, confirmed: true }), signal: controller.signal });
        if (mounted.current && !controller.signal.aborted && current === generation.current) { setDraft(value); setBaseline(JSON.stringify(value)); setTuning(value.tuning.join(',')); const row = value.rows.find(item => item.id === selectedId); if (row) setFields(tabRowFields(row)); onDirty?.(false); onImported(result); }
      } else {
        const result = await request<TabReviewState>(endpoint, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ base_revision: state.revision, draft: value }), signal: controller.signal });
        if (mounted.current && !controller.signal.aborted && current === generation.current) { adopt(result, value); setNotice('입력 중인 검수 초안을 서버에 저장했어요. 리듬 입력이 덜 끝나도 나중에 이어갈 수 있습니다.'); }
      }
    } catch (err) { if (mounted.current && !controller.signal.aborted && current === generation.current) setError((err as Error).message + ' 입력은 이 화면에 남아 있어요. 초안 JSON을 먼저 보관하고 최신 초안을 불러올 수 있습니다.'); }
    finally { if (current === generation.current) { lock.current = false; if (mounted.current) setBusy(false); } }
  }

  if (!state || !draft) return <section className="tab-score-review" aria-label="PDF TAB 직접 검수">{busy ? <p><LoaderCircle className="spin" size={16} /> TAB 좌표와 검수 초안을 불러오는 중이에요…</p> : <><p className="editor-error" role="alert">{error}</p><button className="secondary small" disabled={disabled} onClick={() => void load()}>검수 자료 다시 불러오기</button></>}</section>;
  const evidence = selected && active?.staff.digits.find(digit => selected.id === `${activeStaff}:${digit.id}`);
  const measureHint = evidence && active?.staff.measures?.find(measure => measure.digit_ids.includes(evidence.id));
  const preview = active && state.preview_urls.map(url => tabReviewUrl(url, jobId)).find(url => url?.split('?')[0].endsWith(`/preview-${active.page.page}.png`));
  const viewBox = active ? tabEvidenceViewBox(active.page, active.staff, fullPage) : '';
  return <section className="tab-score-review" aria-label="PDF TAB 직접 검수">
    <div className="tab-review-heading"><h3>원본 TAB 숫자 확인 → 리듬 확인·수정</h3><span role="status">입력 완료 {completeCount}/{draft.rows.length} · 음표 {noteCount}개{dirty ? ' · 저장 전 변경사항' : state.draft ? ' · 저장된 초안' : ' · 새 초안'}</span></div>
    <p className="recognition-caution">출력 범위: 분석된 전체 TAB 보표 {staffs.length}개 중 선택한 {draft.staff_ids.length}개만 포함합니다. 선택 페이지: {selectedPages.join(', ') || '없음'}. 전체 PDF를 변환하려면 아래 ‘검수·출력에 포함할 TAB 보표’에서 범위를 확인해주세요.</p>
    <p className="recognition-caution">숫자 좌표만으로 완성된 악보를 만들지 않습니다. 아래 리듬 후보 읽기는 지원하는 오선 표기가 확인된 마디에만 제안하며, 실제 마디 번호와 후보는 직접 확인해야 합니다. 원본의 주법·가사·반복 등은 자동 복원하지 않으니 확인해주세요. 선택하지 않은 보표는 출력에 포함되지 않습니다.</p>
    <p className="editor-help">확인한 음 길이가 마디 경계를 넘으면 출력에서 타이로 나누어 표시합니다. 원본의 모든 타이·슬러 모양을 그대로 복사하는 기능은 아닙니다.</p>
    <details className="tab-review-warnings"><summary>원본 좌표 분석의 범위·주의 사항</summary><ul className="editor-help">{[...(state.analysis.warnings ?? []), ...(active?.page.warnings ?? []), ...(active?.staff.warnings ?? [])].slice(0, 30).map((warning, index) => <li key={index}>{warning}</li>)}</ul><p className="editor-help">읽히지 않은 숫자·기호가 있으면 원본을 보며 직접 추가하거나 다른 구간으로 나누어 확인해주세요.</p></details>
    <div className="bulk-row">{tabReviewUrl(state.source_url, jobId) && <a className="text-button" href={tabReviewUrl(state.source_url, jobId)} target="_blank" rel="noopener noreferrer">원본 PDF 열기</a>}{backupUrl && <a className="text-button" href={backupUrl} download="tab-review-draft.json"><Download size={14} /> 검수 초안 JSON 보관</a>}<button className="text-button" disabled={locked} onClick={() => { if (!dirty || window.confirm('저장하지 않은 입력을 버리고 서버의 최신 초안을 불러올까요? 필요한 경우 먼저 초안 JSON을 보관해주세요.')) void load(); }}>최신 초안 불러오기</button><button className="secondary small" disabled={locked || !prepared} onClick={() => void save()}><Save size={14} /> 검수 초안 저장</button></div>
    {error && <p className="editor-error" role="alert">{error}</p>}{notice && <p className="editor-success" role="status">{notice}</p>}
    <fieldset className="tab-review-settings" disabled={locked}><label>악보 제목<input aria-label="TAB 검수 악보 제목" value={draft.title} maxLength={160} onChange={event => change({ ...draft, title: event.target.value })} /></label><label>악기<select aria-label="TAB 검수 악기" value={draft.instrument} onChange={event => { const instrument = event.target.value as 'bass' | 'guitar'; change({ ...draft, instrument, tuning: [...standardTabTuning[instrument]] }); setTuning(standardTabTuning[instrument].join(',')); }}><option value="bass">베이스 · 기본 4현</option><option value="guitar">기타 · 기본 6현</option></select></label><label>박자표<select aria-label="TAB 검수 박자표" value={`${draft.beats}/${draft.beat_type}`} onChange={event => { const [beats, beat_type] = event.target.value.split('/').map(Number); change({ ...draft, beats, beat_type }); }}>{['2/4', '3/4', '4/4', '6/8', '9/8', '12/8'].map(value => <option key={value}>{value}</option>)}{!['2/4', '3/4', '4/4', '6/8', '9/8', '12/8'].includes(`${draft.beats}/${draft.beat_type}`) && <option>{draft.beats}/{draft.beat_type}</option>}</select></label><label>BPM<input aria-label="TAB 검수 BPM" type="number" min={20} max={300} value={draft.tempo} onChange={event => change({ ...draft, tempo: Number(event.target.value) })} /></label><label>카포<input aria-label="TAB 검수 카포" type="number" min={0} max={12} value={draft.capo} onChange={event => change({ ...draft, capo: Number(event.target.value) })} /></label></fieldset>
    <p className="editor-help">새 초안의 4/4·120 BPM·기본 튜닝은 입력 시작값입니다. 원본에서 읽은 값이 아니니 반드시 확인해주세요. 모든 시작 위치·길이의 1박은 4분음표 기준이며, 마디 맨 앞은 0입니다.</p>
    <details className="tab-review-tuning"><summary>튜닝 확인 · 높은 줄부터 MIDI 음 번호</summary><label>튜닝<input aria-label="TAB 검수 튜닝" disabled={locked} value={tuning} onChange={event => { setTuning(event.target.value); setConfirmed(false); }} /></label><p className="editor-help">베이스 4현 G2·D2·A1·E1 = 43,38,33,28 / 기타 6현 E4·B3·G3·D3·A2·E2 = 64,59,55,50,45,40. 튜닝·카포가 바뀌면 같은 프렛의 실제 음정도 달라집니다.</p></details>
    <details className="tab-review-scope"><summary>검수·출력에 포함할 TAB 보표 ({draft.staff_ids.length})</summary><div className="tab-review-staffs">{staffs.map(({ staff, page }, index) => <label className="check-label" key={staff.id}><input type="checkbox" aria-label={`${page.page}페이지 ${index + 1}번째 TAB 보표 포함`} checked={draft.staff_ids.includes(staff.id)} disabled={locked} onChange={event => scope(staff.id, event.target.checked)} />{page.page}페이지 · 보표 {index + 1} · {staff.line_count}줄 · 숫자 {staff.digits.length}개</label>)}</div></details>
    {active && <TabRhythmSuggestions key={`${jobId}:${activeStaff}`} jobId={jobId} staffId={activeStaff} state={state} draft={draft} disabled={disabled || busy || !prepared} onActivity={setRhythmBusy} onApply={(proposal, measure, start) => {
      if (!prepared || locked) return;
      try { const next = applyRhythmProposal(prepared, state.analysis, proposal, measure, start); change(next); const row = next.rows.find(item => item.id === selectedId); if (row) setFields(tabRowFields(row)); setNotice('원본 표기에서 읽은 리듬 후보를 초안에 적용했어요. 원본 대조 후 초안을 저장해주세요.'); }
      catch (err) { setError((err as Error).message); }
    }} />}
    {active ? <div className="tab-review-workbench"><div className="tab-review-evidence"><div className="bulk-row"><label>현재 검수 보표<select aria-label="현재 검수 TAB 보표" value={activeStaff} disabled={locked} onChange={event => chooseStaff(event.target.value)}>{staffs.filter(item => draft.staff_ids.includes(item.staff.id)).map(({ staff, page }) => <option key={staff.id} value={staff.id}>{page.page}페이지 · {staff.id} · {staff.line_count}줄</option>)}</select></label><label className="check-label"><input type="checkbox" checked={fullPage} onChange={event => setFullPage(event.target.checked)} />페이지 전체 보기</label></div>
      {preview ? <svg className="tab-review-source" viewBox={viewBox} role="img" aria-label={`${active.page.page}페이지 원본 TAB 숫자 위치`}><image href={preview} x={0} y={0} width={active.page.width} height={active.page.height} />{active.staff.digits.filter(digit => visibleRows.some(row => row.id === `${activeStaff}:${digit.id}`) || selectedId === `${activeStaff}:${digit.id}`).map(digit => <g key={digit.id} role="button" tabIndex={locked ? -1 : 0} aria-label={`원본 숫자 ${digit.text} 위치 선택`} onClick={() => { const row = draft.rows.find(item => item.id === `${activeStaff}:${digit.id}`); if (row) choose(row); }} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); const row = draft.rows.find(item => item.id === `${activeStaff}:${digit.id}`); if (row) choose(row); } }}><rect x={digit.bbox[0] - 1} y={digit.bbox[1] - 1} width={Math.max(2, digit.bbox[2] - digit.bbox[0] + 2)} height={Math.max(2, digit.bbox[3] - digit.bbox[1] + 2)} fill={selectedId === `${activeStaff}:${digit.id}` ? '#ffbf0040' : 'transparent'} stroke={selectedId === `${activeStaff}:${digit.id}` ? '#c45000' : '#7684ad'} strokeWidth={selectedId === `${activeStaff}:${digit.id}` ? 1.2 : .45} /></g>)}</svg> : <p className="editor-help">이 페이지의 안전한 원본 미리보기 주소가 없어요. 위 원본 PDF를 열어 비교해주세요.</p>}
      {!!active.staff.symbols?.length && <details><summary>별도 확인할 주법·문자 후보 ({active.staff.symbols.length})</summary><ul className="recognition-warnings">{active.staff.symbols.slice(0, 30).map((symbol, index) => <li key={index}>{symbol.text}: {symbol.reason}</li>)}</ul><p className="editor-help">이 주법·문자는 자동으로 음표에 반영되지 않습니다.</p></details>}
      <div className="tab-review-candidates" role="group" aria-label="TAB 숫자 검수 목록">{visibleRows.map(row => { const digit = active.staff.digits.find(item => row.id === `${activeStaff}:${item.id}`); const problems = tabRowIssues(row, draft); return <div key={row.id} className={row.id === selectedId ? 'tab-review-candidate selected' : 'tab-review-candidate'}><input type="checkbox" aria-label={`${row.id} 순서 배정 선택`} disabled={locked || row.decision === 'exclude'} checked={checked.includes(row.id)} onChange={event => setChecked(event.target.checked ? [...checked, row.id] : checked.filter(value => value !== row.id))} /><button type="button" disabled={locked} aria-pressed={row.id === selectedId} onClick={() => choose(row)}><strong>{digit ? `원본 ${digit.text}` : '직접 추가'} · {row.decision === 'exclude' ? '제외' : `${row.string ?? '?'}번 줄 ${row.fret ?? '?'}프렛`}</strong><span>{problems.length ? problems.join(' · ') : row.decision === 'exclude' ? row.reason : `${row.measure}마디 · ${row.onset}박 · ${tabDurationLabel(row.type!, row.dots)}`}</span></button></div>; })}</div>
      <div className="bulk-row"><button className="icon-button" aria-label="이전 숫자 목록" disabled={locked || rowPage === 0} onClick={() => setRowPage(rowPage - 1)}><ChevronLeft size={15} /></button><span>{rowPage + 1} / {Math.max(1, Math.ceil(staffRows.length / pageSize))}</span><button className="icon-button" aria-label="다음 숫자 목록" disabled={locked || (rowPage + 1) * pageSize >= staffRows.length} onClick={() => setRowPage(rowPage + 1)}><ChevronRight size={15} /></button><button className="text-button" disabled={locked} onClick={addManual}><Plus size={14} /> 누락된 음표 직접 추가</button></div>
    </div><aside className="tab-review-row-editor">
      {selected ? <><h4>{evidence ? `선택한 원본 숫자: ${evidence.text}` : '직접 추가한 음표'}</h4>{evidence?.reasons?.length ? <ul className="recognition-warnings">{evidence.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul> : <p className="editor-help">좌표상 줄·프렛 후보도 원본에서 확인해주세요.</p>}{measureHint && <p className="editor-help">좌표 경계 후보: 이 보표의 {measureHint.index}번째 마디. 실제 음악의 마디 번호를 직접 지정해주세요.</p>}
        <fieldset disabled={locked} className="tab-review-row-fields"><label>처리<select aria-label="TAB 숫자 처리" value={fields.decision} onChange={event => updateField('decision', event.target.value as 'note' | 'exclude')}><option value="note">음표로 사용 · 직접 확인</option><option value="exclude">음표가 아님 · 제외</option></select></label>{fields.decision === 'exclude' ? <label>제외 이유<input aria-label="TAB 숫자 제외 이유" maxLength={200} value={fields.reason} onChange={event => updateField('reason', event.target.value)} placeholder="예: 마디 번호, 중복 숫자, 다른 악기의 숫자" /></label> : <><label>실제 마디 번호<input aria-label="TAB 음표 마디" type="number" min={1} max={600} value={fields.measure} onChange={event => updateField('measure', event.target.value)} placeholder="직접 입력" /></label><label>마디 안 시작 위치 · 4분음표=1<input aria-label="TAB 음표 시작 위치" value={fields.onset} onChange={event => updateField('onset', event.target.value)} placeholder="0, 0.5, 1/2 등" /></label><label>음표 길이<select aria-label="TAB 음표 길이" value={fields.type} onChange={event => updateField('type', event.target.value)}><option value="">원본 확인 후 선택</option>{tabTypes.map(type => <option key={type} value={type}>{tabTypeLabels[type]}</option>)}</select></label><label>점음표<select aria-label="TAB 음표 점" value={fields.dots} onChange={event => updateField('dots', event.target.value)}><option value="0">점 없음</option><option value="1">점 1개 · 1.5배</option><option value="2">점 2개 · 1.75배</option></select></label><div className="bulk-row"><label>줄<input aria-label="TAB 음표 줄" type="number" min={1} max={draft.tuning.length} value={fields.string} onChange={event => updateField('string', event.target.value)} /></label><label>프렛<input aria-label="TAB 음표 프렛" type="number" min={0} max={36} value={fields.fret} onChange={event => updateField('fret', event.target.value)} /></label></div><label className="check-label"><input aria-label="TAB 음표 뮤트" type="checkbox" checked={fields.muted} onChange={event => updateField('muted', event.target.checked)} />원본에서 확인한 뮤트 · X 표시</label></>}
          <button className="secondary small" disabled={!fieldsDirty || !prepared} onClick={() => { if (commitFields()) setNotice('선택한 숫자의 입력을 초안에 적용했어요. 서버에 보관하려면 초안 저장을 눌러주세요.'); }}>선택 항목 적용</button></fieldset>{inputError && <p className="editor-error" role="alert">{inputError}</p>}
      </> : <p className="editor-help">읽힌 숫자가 없어요. 원본을 보며 누락된 음표를 직접 추가하거나 다른 보표를 선택해주세요.</p>}
      <details className="tab-review-sequence"><summary>체크한 숫자에 같은 길이로 직접 배정 ({checked.length})</summary><p className="recognition-caution">자동 리듬 인식이 아닙니다. 체크한 숫자를 목록 순서대로 연결해 입력하는 편의 기능입니다. 화음·쉼표·점음표가 섞인 구간에는 사용하지 말고 하나씩 확인해주세요.</p><fieldset disabled={locked}><label>시작 마디<input aria-label="연속 배정 시작 마디" type="number" min={1} max={600} value={sequenceMeasure} onChange={event => setSequenceMeasure(event.target.value)} /></label><label>시작 위치<input aria-label="연속 배정 시작 위치" value={sequenceStart} onChange={event => setSequenceStart(event.target.value)} /></label><label>직접 지정할 길이<select aria-label="연속 배정 음표 길이" value={sequenceType} onChange={event => setSequenceType(event.target.value as 'eighth' | '16th')}><option value="eighth">8분음표로 순서 배정</option><option value="16th">16분음표로 순서 배정</option></select></label><button className="secondary small" disabled={!checked.length || !prepared} onClick={sequence}>체크한 숫자에 직접 배정</button></fieldset></details>
    </aside></div> : <p className="editor-help">검수할 수 있는 TAB 보표가 없어요. 원본 PDF가 실제 숫자 문자와 TAB 선을 포함하는지 확인해주세요.</p>}
    <div className="tab-review-submit"><p>입력 완료 {completeCount}/{draft.rows.length} · 제외 후보도 이유를 입력해야 합니다. 실제 정확도 점수는 아닙니다.</p><p>이 프로젝트의 출력 범위: TAB 보표 {draft.staff_ids.length}/{staffs.length}개 · {selectedPages.join(', ')}페이지. 첫 입력 마디부터 시작하며, 중간에 입력하지 않은 마디 번호는 쉼표로 남습니다.</p>{issues.length > 0 && <ul className="editor-help">{issues.slice(0, 6).map(issue => <li key={issue}>{issue}</li>)}</ul>}<label className="check-label"><input aria-label="TAB 원본 대조 확인" type="checkbox" checked={confirmed} disabled={locked || issues.length > 0} onChange={event => setConfirmed(event.target.checked)} />선택한 모든 숫자와 제외 이유, 마디·시작 위치·길이, 튜닝·카포·박자·BPM을 원본과 대조했고, 자동 복원되지 않는 주법·가사·반복 표기가 있음을 확인했습니다.</label><button className="primary small" disabled={locked || !confirmed || issues.length > 0} onClick={() => void save(true)}>{busy && <LoaderCircle size={15} className="spin" />}확인한 TAB로 편집 프로젝트 만들기</button><p className="editor-help">확인한 숫자와 직접 입력한 리듬으로 새 프로젝트를 만듭니다. 원본 PDF는 바꾸지 않으며, 확인 범위 밖의 내용을 자동으로 채우지 않습니다.</p></div>
  </section>;
}
