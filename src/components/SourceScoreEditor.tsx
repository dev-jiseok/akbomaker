import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, Download, LoaderCircle, Printer, Redo2, Save, Undo2 } from 'lucide-react';
import { request } from '../api';
import type { Job, ScorePreset } from '../types';
import { presetLabels } from '../scoreEditing';
import { sourceScoreUrl, type ScoreIntegrityIssue, type SourceConnection, type SourceArticulation, type SourceNoteType, type SourceScoreDocument, type SourceScoreNote, type SourceScoreOperation, type SourceScorePatch, type SourceScoreResult } from '../sourceScore';
import ScoreViewer from './ScoreViewer';
import { readSourceAttribution } from './ScoreImport';
import { recognitionUrl } from './ScoreRecognition';
import SourceScoreNoteFields, { sourceOperationLabels as labels, quarterBeatLabel, type SourceEditFields as Fields } from './SourceScoreNoteFields';
import SourceIntegrityReport from './SourceIntegrityReport';

type Props = { job: Job; onJob: (job: Job) => void; onNew: () => void; onEditorDirty: (dirty: boolean) => void };
const commandOperations: SourceScoreOperation[] = ['insert', 'chord', 'delete', 'lyric_add', 'lyric_delete', 'articulation', 'connection'];
function noteFields(note?: SourceScoreNote, lyricIndex?: string, operation: SourceScoreOperation = 'pitch', score?: SourceScoreDocument): Fields {
  const lyrics = note?.lyrics.filter(item => operation === 'lyric_delete' ? item.deletable : item.editable) ?? [];
  const lyric = lyrics.find(item => String(item.index) === lyricIndex) ?? lyrics[0];
  const kinds = operation === 'chord' ? note?.chord_kinds : note?.insert_kinds;
  const connection = note?.connection_targets?.[0] ?? note?.connections?.[0];
  return { step: note?.pitch?.step ?? 'C', alter: String(note?.pitch?.alter ?? 0), octave: String(note?.pitch?.octave ?? 4), string: String(note?.fingering?.string ?? 1), fret: String(note?.fingering?.fret ?? 0), drum_id: note?.drum_id ?? score?.drum_options[0]?.id ?? '', lyric_index: String(lyric?.index ?? 0), text: operation === 'lyric_add' ? '' : lyric?.text ?? '', kind: kinds?.[0] ?? 'pitched', type: note?.notation?.type ?? 'quarter', dots: String(note?.notation?.dots ?? 0), mark: operation === 'connection' ? connection?.mark ?? 'tie' : 'accent', action: operation === 'connection' && !note?.connection_targets?.length ? 'remove' : 'add', target_note_id: connection?.target_note_id ?? '' };
}
function operations(note?: SourceScoreNote): SourceScoreOperation[] {
  if (!note) return [];
  return (['tab_pitch', 'fingering', 'pitch', 'drum', 'rhythm', 'insert', 'chord', 'delete', 'articulation', 'connection', 'lyric', 'lyric_add', 'lyric_delete'] as const).filter(value => {
    if (value === 'lyric') return note.lyrics.some(item => item.editable);
    if (value === 'insert' || value === 'chord') return note.editable[value] && !!note[`${value}_kinds`]?.length;
    if (value === 'lyric_delete') return note.editable[value] && note.lyrics.some(item => item.deletable);
    return note.editable[value];
  });
}
function patchFor(note: SourceScoreNote, operation: SourceScoreOperation, fields: Fields): SourceScorePatch {
  const base = { note_id: note.id, operation };
  if (operation === 'articulation') return { ...base, mark: fields.mark as SourceArticulation, action: fields.action as 'add' | 'remove' };
  if (operation === 'connection') return { ...base, mark: fields.mark as SourceConnection, action: fields.action as 'add' | 'remove', target_note_id: fields.target_note_id };
  if (operation === 'pitch') return { ...base, step: fields.step, alter: Number(fields.alter), octave: Number(fields.octave) };
  if (operation === 'fingering' || operation === 'tab_pitch') return { ...base, string: Number(fields.string), fret: Number(fields.fret) };
  if (operation === 'drum') return { ...base, drum_id: fields.drum_id };
  if (operation === 'delete') return base;
  if (operation === 'rhythm') return { ...base, type: fields.type as SourceNoteType, dots: Number(fields.dots) };
  if (operation === 'lyric_add') return { ...base, text: fields.text };
  if (operation === 'lyric_delete') return { ...base, lyric_index: Number(fields.lyric_index) };
  if (operation === 'insert' || operation === 'chord') {
    const fieldsByKind = fields.kind === 'pitched' ? { step: fields.step, alter: Number(fields.alter), octave: Number(fields.octave) } : fields.kind === 'tab' ? { string: Number(fields.string), fret: Number(fields.fret) } : { drum_id: fields.drum_id };
    return { ...base, kind: fields.kind, ...fieldsByKind };
  }
  return { ...base, lyric_index: Number(fields.lyric_index), text: fields.text };
}

export default function SourceScoreEditor({ job, onJob, onNew, onEditorDirty }: Props) {
  const endpoint = `/api/source-scores/${job.id}`;
  const [score, setScore] = useState<SourceScoreDocument | null>(null);
  const [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false), [measure, setMeasure] = useState(1), [selectedId, setSelectedId] = useState('');
  const [operation, setOperation] = useState<SourceScoreOperation>('pitch');
  const [editing, setEditing] = useState(false), [commandPending, setCommandPending] = useState(false);
  const [fields, setFields] = useState<Fields>(() => noteFields());
  const [preset, setPreset] = useState<ScorePreset>('practice'), [measures, setMeasures] = useState<2 | 4>(4);
  const [originalCache, setOriginalCache] = useState<{ key: string; xml: string } | null>(null);
  const [originalError, setOriginalError] = useState(''), [originalLoading, setOriginalLoading] = useState(false);
  const [showOriginal, setShowOriginal] = useState(false);
  const lock = useRef(false), mounted = useRef(true), mutation = useRef<AbortController | null>(null);
  const paper = useRef<HTMLDivElement>(null), printCleanup = useRef<(() => void) | null>(null);
  const selected = score?.notes.find(note => note.id === selectedId);
  const available = operations(selected);
  const canEdit = !!selected && available.includes(operation);
  const baselineFields = noteFields(selected, fields.lyric_index, operation, score ?? undefined);
  const noteDirty = !!selected && canEdit && (commandPending || JSON.stringify(patchFor(selected, operation, fields)) !== JSON.stringify(patchFor(selected, operation, baselineFields)));
  const canSaveNote = canEdit && (noteDirty || commandOperations.includes(operation) || operation === 'tab_pitch' && !!selected?.fingering_mismatch);
  const styleDirty = !!score && (preset !== score.layout.preset || measures !== score.layout.measures_per_line);
  const dirty = noteDirty || styleDirty;
  const originalKey = score ? `${score.original_url}:${score.layout.preset}:${score.layout.measures_per_line}` : '';
  const original = originalCache?.key === originalKey ? originalCache.xml : '';

  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    request<SourceScoreDocument>(endpoint, { signal: controller.signal }).then(doc => {
      if (controller.signal.aborted) return;
      setScore(doc); setPreset(doc.layout.preset); setMeasures(doc.layout.measures_per_line);
      const first = doc.notes[0]; setSelectedId(first?.id ?? ''); setMeasure(first?.measure_index ?? 1);
      const firstOperation = operations(first)[0] ?? 'pitch'; setOperation(firstOperation); setFields(noteFields(first, undefined, firstOperation, doc));
    }).catch(err => { if (!controller.signal.aborted) setError((err as Error).message); });
    return () => { mounted.current = false; controller.abort(); mutation.current?.abort(); printCleanup.current?.(); };
  }, [endpoint]);
  useEffect(() => { onEditorDirty(dirty); return () => onEditorDirty(false); }, [dirty, onEditorDirty]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);
  useEffect(() => {
    if (!showOriginal || !score || original) return;
    const url = sourceScoreUrl(score.original_url, job.id);
    if (!url) { setOriginalError('원본 MusicXML 주소를 확인할 수 없어요.'); return; }
    const controller = new AbortController(); setOriginalLoading(true); setOriginalError('');
    fetch(url, { signal: controller.signal, cache: 'no-store' }).then(async response => {
      if (!response.ok) throw new Error('원본 MusicXML을 불러오지 못했어요.');
      return response.text();
    }).then(xml => { if (!controller.signal.aborted) setOriginalCache({ key: originalKey, xml }); }).catch(err => { if (!controller.signal.aborted) setOriginalError((err as Error).message); }).finally(() => { if (!controller.signal.aborted) setOriginalLoading(false); });
    return () => controller.abort();
  }, [showOriginal, score?.original_url, originalKey, job.id, original]);

  function discardNote() { return !noteDirty || window.confirm('저장하지 않은 음표 수정 내용을 버리고 선택을 바꿀까요?'); }
  function selectNote(note?: SourceScoreNote) {
    if (lock.current || !discardNote()) return;
    const nextOperation = operations(note)[0] ?? 'pitch';
    setSelectedId(note?.id ?? ''); setOperation(nextOperation); setFields(noteFields(note, undefined, nextOperation, score ?? undefined)); setCommandPending(false); setNotice('');
  }
  function selectMeasure(value: number) {
    if (!score || lock.current || !discardNote()) return;
    const note = score.notes.find(item => item.measure_index === value);
    const nextOperation = operations(note)[0] ?? 'pitch';
    setMeasure(value); setSelectedId(note?.id ?? ''); setOperation(nextOperation); setFields(noteFields(note, undefined, nextOperation, score)); setCommandPending(false); setNotice('');
  }
  function inspectIssue(issue: ScoreIntegrityIssue) {
    if (!score || busy || lock.current || !discardNote()) return;
    const note = score.notes.find(item => item.id === issue.note_id && item.measure_index === issue.measure_index)
      ?? score.notes.find(item => item.measure_index === issue.measure_index);
    const nextOperation = operations(note)[0] ?? 'pitch';
    setMeasure(issue.measure_index); setSelectedId(note?.id ?? ''); setOperation(nextOperation);
    setFields(noteFields(note, undefined, nextOperation, score)); setCommandPending(false); setEditing(true);
    setNotice(`${issue.measure_number}마디 확인: ${issue.message}`);
  }
  function leave() { if (!busy && (!dirty || window.confirm('저장하지 않은 악보 수정 내용이 있어요. 저장하지 않고 이동할까요?'))) onNew(); }
  async function mutate(path: string, method: string, body: object, kind: 'note' | 'layout' | 'history') {
    if (!score || lock.current) return;
    lock.current = true; setBusy(true); setError(''); setNotice('');
    const controller = new AbortController(); mutation.current = controller;
    try {
      const result = await request<SourceScoreResult>(endpoint + path, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ base_revision: score.revision, ...body }), signal: controller.signal });
      if (!mounted.current || controller.signal.aborted) return;
      setScore(result.document);
      const note = result.document.notes.find(item => item.id === selectedId) ?? result.document.notes[0];
      const nextOperation = operations(note).includes(operation) ? operation : operations(note)[0] ?? 'pitch';
      setSelectedId(note?.id ?? ''); setMeasure(note?.measure_index ?? 1); setFields(noteFields(note, fields.lyric_index, nextOperation, result.document));
      setOperation(nextOperation); setCommandPending(false);
      if (kind !== 'note') { setPreset(result.document.layout.preset); setMeasures(result.document.layout.measures_per_line); }
      setNotice(kind === 'history' ? '저장된 수정 이력을 반영했어요.' : '저장했어요. 선택한 변경만 반영하고 다른 음악 표기는 유지했어요.'); onJob(result.job);
    } catch (err) { if (mounted.current && !controller.signal.aborted) setError((err as Error).message); }
    finally { lock.current = false; if (mounted.current) setBusy(false); }
  }
  function saveNote() {
    if (!selected || !canSaveNote || lock.current) return;
    const pitched = operation === 'pitch' || (operation === 'insert' || operation === 'chord') && fields.kind === 'pitched';
    const tab = operation === 'fingering' || operation === 'tab_pitch' || (operation === 'insert' || operation === 'chord') && fields.kind === 'tab';
    const numeric = pitched ? [fields.alter, fields.octave] : tab ? [fields.string, fields.fret] : [];
    if (numeric.some(value => !value.trim() || !Number.isFinite(Number(value)))) { setError('수정할 숫자를 올바르게 입력해주세요.'); return; }
    if ((operation === 'lyric' || operation === 'lyric_add') && !fields.text.trim()) { setError('가사를 입력해주세요. 지우려면 가사 삭제를 선택해주세요.'); return; }
    if (operation === 'connection' && !(fields.action === 'remove' ? selected.connections : selected.connection_targets)?.some(item => item.mark === fields.mark && item.target_note_id === fields.target_note_id)) { setError('안전하게 연결할 수 있는 기호와 끝 음표를 선택해주세요.'); return; }
    const confirmation: Partial<Record<SourceScoreOperation, string>> = {
      delete: '선택한 음표를 같은 길이의 쉼표로 바꿀까요? 뒤 음표 위치는 유지합니다. 실행 취소로 복구할 수 있어요.',
      insert: '선택한 쉼표를 입력한 음표로 바꿀까요? 길이와 뒤 음표 위치는 유지합니다.',
      chord: '선택한 음표와 동시에 울리는 음을 추가할까요?',
      lyric_delete: '선택한 가사 항목을 삭제할까요? 음표는 그대로 유지하며 실행 취소로 복구할 수 있어요.',
      tab_pitch: '입력한 줄·프렛에 맞게 음정도 변경할까요? 명확하게 연결된 오선·TAB 음표는 함께 수정합니다.',
      connection: '선택한 두 음표의 연결 기호를 변경할까요? 음정·길이는 유지하고 명확히 연결된 오선·TAB는 함께 반영합니다.',
    };
    if (confirmation[operation] && !window.confirm(confirmation[operation])) return;
    void mutate('/edit', 'POST', { patch: patchFor(selected, operation, fields) }, 'note');
  }
  function print() {
    if (!paper.current?.querySelector('.engraving svg')) { setError('악보 미리보기가 그려진 뒤 인쇄해주세요.'); return; }
    if (dirty) { setError('수정 내용을 먼저 저장해주세요. 인쇄에는 저장된 악보가 포함됩니다.'); return; }
    if (printCleanup.current) return;
    const target = paper.current; target.setAttribute('data-score-preserve-target', ''); document.body.setAttribute('data-score-preserve-print', '');
    const cleanup = () => { target.removeAttribute('data-score-preserve-target'); document.body.removeAttribute('data-score-preserve-print'); window.removeEventListener('afterprint', cleanup); printCleanup.current = null; };
    printCleanup.current = cleanup; window.addEventListener('afterprint', cleanup);
    try { window.print(); } catch { cleanup(); setError('인쇄 창을 열지 못했어요. MusicXML을 다운로드해주세요.'); }
  }

  if (!score) return <section className="source-score-editor"><button className="text-button" onClick={onNew}>작업실 홈</button>{error ? <p className="editor-error" role="alert">{error}</p> : <p><LoaderCircle className="spin" size={18} /> 원본 보존 악보를 불러오는 중이에요</p>}</section>;
  const stem = { id: score.instrument, label: '원본 보존 악보', status: 'ready' as const, score_status: 'ready' as const, waveform: [], score_url: sourceScoreUrl(score.current_url, job.id) };
  const barOptions = [...new Map(score.notes.map(note => [note.measure_index, note.measure_number])).entries()];
  const attribution = readSourceAttribution(score.xml, score.title);
  const notesUrl = sourceScoreUrl(`${endpoint}/files/notes.json`, job.id);
  return <section className="workspace-view source-score-editor" aria-label="원본 악보 보존 편집기">
    <div className="workspace-top"><button className="text-button" disabled={busy} onClick={leave}><ArrowLeft size={16} /> 작업실 홈</button><span className="source-score-status" role="status">{busy ? '변경사항 저장 중…' : dirty ? '저장 전 변경사항' : '저장된 원본 보존 악보'}</span></div>
    <div className="project-heading"><div><span className="eyebrow">SOURCE SCORE · 원본 보존</span><h1>{score.title}</h1><p>원본 음표 · TAB · 성부 · 연주 기호 유지 → 스타일 변경 → 필요한 부분만 수정</p></div></div>
    {score.content_check?.style_preserves_music && score.content_check.scope === 'selected-part-musicxml-not-pdf-recognition' && <p className="source-fidelity-status" role="status"><strong>스타일 변경 전후 음악 데이터 일치 확인</strong><span>{score.content_check.music_edited ? '직접 수정한 내용 반영' : '가져온 MusicXML의 음악 내용 유지'} · 선택한 MusicXML 파트 기준이며 PDF 인식 정확도 판정은 아닙니다.</span></p>}
    <p className="recognition-caution">스타일만 저장하면 음표·TAB·가사 내용은 바뀌지 않습니다. 필요한 경우에만 수정 도구를 열어 음정·리듬·드럼·가사를 고칠 수 있어요. 안전하게 연결을 보존할 수 없는 편집은 차단합니다. 미리보기·MusicXML 출력은 선택한 파트이며, 전체 파트는 업로드 원본 파일에 보관됩니다. 화면 표시는 뷰어 지원 범위에 따라 원본과 다를 수 있어요.</p>
    {job.score_omr && <p className="recognition-caution">PDF·이미지 인식 초안입니다. 보존 대상은 인식된 MusicXML이며, 원본 PDF의 누락·오인식까지 자동 복구하지 않습니다. 원본과 대조해주세요.</p>}
    {job.score_tab_review && <div className="recognition-caution"><p>PDF TAB의 숫자와 직접 확인한 리듬으로 만든 악보입니다. 원본 PDF 전체를 자동으로 복원한 결과는 아닙니다. 스타일 일치 검사는 확인 후 생성한 MusicXML을 기준으로 합니다.</p><div className="bulk-row">{score.attachments?.map(item => sourceScoreUrl(item.url, job.id) && <a key={item.name} className="text-button" href={sourceScoreUrl(item.url, job.id)} download>{item.name === 'reference.pdf' ? '보관된 원본 PDF' : item.name === 'reviewed-tab.json' ? '확인·수정한 TAB 자료' : '원본 TAB 숫자·좌표 자료'}</a>)}</div></div>}
    {score.warnings.length > 0 && <details className="source-score-warnings"><summary>보존 범위·주의 사항 ({score.warnings.length})</summary><ul className="recognition-warnings">{score.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul></details>}
    {score.integrity_report && <SourceIntegrityReport report={score.integrity_report} disabled={busy} onInspect={inspectIssue} />}
    {error && <p role="alert" className="editor-error">{error}</p>}{notice && <p role="status" className="editor-success">{notice}</p>}
    <div className="editor-actions"><div><button className="secondary small" disabled={busy || dirty || !score.history.undo} onClick={() => void mutate('/history', 'POST', { action: 'undo' }, 'history')}><Undo2 size={15} /> 실행 취소</button><button className="secondary small" disabled={busy || dirty || !score.history.redo} onClick={() => void mutate('/history', 'POST', { action: 'redo' }, 'history')}><Redo2 size={15} /> 다시 실행</button></div><div>{sourceScoreUrl(score.current_url, job.id) && <a className="text-button" href={sourceScoreUrl(score.current_url, job.id)} download onClick={event => { if (dirty || busy) { event.preventDefault(); setError('수정 내용을 먼저 저장해주세요. 다운로드에는 저장된 악보가 포함됩니다.'); } }}><Download size={15} /> 현재 악보 MusicXML</a>}<button className="text-button" disabled={busy || dirty} onClick={print}><Printer size={15} /> 인쇄 / PDF 저장</button></div></div>
    <fieldset className="editor-fields" disabled={busy}><label>출력 스타일<select aria-label="원본 보존 출력 스타일" value={preset} onChange={event => setPreset(event.target.value as ScorePreset)}>{Object.entries(presetLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label>한 줄 마디<select aria-label="원본 보존 한 줄 마디" value={measures} onChange={event => setMeasures(Number(event.target.value) as 2 | 4)}><option value={4}>4마디</option><option value={2}>2마디</option></select></label><button className="secondary small" disabled={!styleDirty || noteDirty} onClick={() => void mutate('/layout', 'PUT', { preset, measures_per_line: measures }, 'layout')}>스타일 저장</button></fieldset>
    {styleDirty && <p className="editor-help">스타일 저장 후 미리보기와 다운로드에 적용됩니다.{noteDirty ? ' 먼저 입력 중인 음표 수정을 저장하거나 취소해주세요.' : ''}</p>}
    <div className="source-score-edit-toggle"><button className="secondary small" aria-expanded={editing} aria-controls="source-score-edit-controls" disabled={busy} onClick={() => setEditing(!editing)}>{editing ? '수정 도구 접기' : '틀린 부분 수정하기'}</button><span className="editor-help">스타일 변경·출력만 할 때는 수정 도구를 열지 않아도 돼요.</span></div>
    <div className={`source-score-layout ${editing ? 'source-editor-open' : 'source-editor-closed'}`}>{editing && <aside className="source-score-controls" id="source-score-edit-controls">
      <label>수정할 마디<select aria-label="원본 악보 수정할 마디" value={measure} disabled={busy} onChange={event => selectMeasure(Number(event.target.value))}>{barOptions.map(([index, number]) => <option key={index} value={index}>{number}마디 · 원본 {index}번째 마디</option>)}</select></label>
      <div className="source-score-note-list" role="group" aria-label={`${measure}번째 마디 음표 목록`}>{score.notes.filter(note => note.measure_index === measure).map(note => <button key={note.id} className={`source-score-note-button ${note.id === selectedId ? 'selected' : ''}`} disabled={busy} aria-pressed={note.id === selectedId} onClick={() => selectNote(note)}><strong>{note.note_index}. {note.description}</strong><span>보표 {note.staff} · 성부 {note.voice} · 시작 {quarterBeatLabel(note.onset)} · 길이 {quarterBeatLabel(note.duration)}</span></button>)}</div>
      {selected ? <section aria-label="선택한 원본 음표 수정"><p className="editor-help">{selected.measure_number}마디 · {selected.note_index}번째 음표 · 보표 {selected.staff} · 성부 {selected.voice}. 위치·길이의 1박은 4분음표 기준입니다. 선택한 변경만 저장하며, 마디 전체 길이는 유지해요.</p>
        {!!selected.warnings?.length && <ul className="recognition-warnings source-note-warnings" role="status">{selected.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>}
        {available.length > 0 ? <fieldset className="source-score-fields" disabled={busy}><label>수정 항목<select aria-label="원본 음표 수정 항목" value={operation} onChange={event => { if (!discardNote()) return; const next = event.target.value as SourceScoreOperation; setOperation(next); setFields(noteFields(selected, undefined, next, score)); setCommandPending(commandOperations.includes(next)); }}>{available.map(value => <option value={value} key={value}>{labels[value]}</option>)}</select></label>
          <SourceScoreNoteFields note={selected} score={score} operation={operation} fields={fields} onField={(key, value) => setFields(current => ({ ...current, [key]: value }))} onLyricIndex={value => { if (operation !== 'lyric_delete' && !discardNote()) return; setFields(noteFields(selected, value, operation, score)); }} />
          {['pitch', 'tab_pitch', 'delete', 'insert', 'chord'].includes(operation) && <p className="editor-help">음정 변경·음표 추가·삭제 뒤에도 다른 음의 음정이 그대로 읽히도록 필요한 후행 임시표(♯·♭·♮)가 명시될 수 있어요. 안전하게 처리할 수 없는 연결은 변경하지 않습니다.</p>}
          <div className="bulk-row"><button className="primary small" disabled={!canSaveNote} onClick={saveNote}><Save size={15} /> 선택한 수정 저장</button><button className="text-button" disabled={!noteDirty} onClick={() => { setFields(noteFields(selected, fields.lyric_index, operation, score)); setCommandPending(false); }}>입력 취소</button></div>
        </fieldset> : <p className="editor-help">이 음표는 원본 표기를 유지해 표시하며 현재 직접 수정할 수 없습니다.</p>}
        <details className="source-note-limitations"><summary>이 음표에서 제한되는 수정</summary><ul className="editor-help">{Object.entries(selected.reasons).filter(([, reason]) => !!reason).map(([key, reason]) => <li key={key}>{labels[key as SourceScoreOperation] ?? key}: {reason}</li>)}{selected.lyrics.filter(item => !item.editable && item.reason).map(item => <li key={`lyric-${item.index}`}>가사 {item.index + 1}: {item.reason}</li>)}{selected.lyrics.filter(item => !item.deletable && item.delete_reason).map(item => <li key={`delete-lyric-${item.index}`}>가사 {item.index + 1} 삭제: {item.delete_reason}</li>)}</ul></details>
      </section> : <p className="editor-help">선택할 음표가 없습니다. 원본 표기는 미리보기와 다운로드에 유지됩니다.</p>}
    </aside>}<div ref={paper} className={`score-paper source-score-paper preset-${score.layout.preset}`}><div className="paper-heading"><span>AKBO MAKER · 원본 표기 보존</span><h2>{score.title}</h2></div>{attribution.lines.length > 0 && <div className="import-source-attribution">{attribution.lines.map((line, index) => <p key={index}>{line}</p>)}{attribution.truncated && <p>긴 원문은 MusicXML 다운로드에 보존됩니다.</p>}</div>}<ScoreViewer stem={stem} xml={score.xml} layout={score.layout} scale={1} spacious={score.layout.preset !== 'standard'} measureNumbers preserveNotation reviewedTabRhythm={!!job.score_tab_review} /></div></div>
    <details className="source-score-compare" open={showOriginal} onToggle={event => setShowOriginal(event.currentTarget.open)}><summary>원본과 비교 · 변경 전 악보 보기</summary><div className="bulk-row">{sourceScoreUrl(score.source_url, job.id) && <a className="text-button" href={sourceScoreUrl(score.source_url, job.id)} download>{job.score_tab_review ? '검토 후 생성한 최초 MusicXML' : '업로드 원본 파일'}</a>}{sourceScoreUrl(score.original_url, job.id) && <a className="text-button" href={sourceScoreUrl(score.original_url, job.id)} download>변경 전 MusicXML</a>}{job.score_omr && recognitionUrl(job.score_omr.source_url, job.score_omr.id) && <a className="text-button" href={recognitionUrl(job.score_omr.source_url, job.score_omr.id)} target="_blank" rel="noopener noreferrer">원본 PDF·이미지 열기</a>}</div>{job.score_omr && <div className="recognition-pages">{job.score_omr.preview_urls.map(url => recognitionUrl(url, job.score_omr!.id)).filter((url): url is string => !!url).map((url, index) => <a href={url} key={url} target="_blank" rel="noopener noreferrer"><img src={url} loading="lazy" alt={`원본 악보 ${index + 1}번째 선택 페이지`} /></a>)}</div>}{originalLoading && <p>원본 악보를 불러오는 중이에요…</p>}{originalError && <p role="alert">{originalError}</p>}{original && <ScoreViewer stem={{ ...stem, label: '변경 전 원본', score_url: sourceScoreUrl(score.original_url, job.id) }} xml={original} layout={score.layout} scale={1} spacious={score.layout.preset !== 'standard'} measureNumbers preserveNotation reviewedTabRhythm={!!job.score_tab_review} />}{notesUrl && <details className="source-score-backup"><summary>고급 · 현재 음표 데이터 보관</summary><p className="editor-help">저장된 음표 목록과 편집 지원 정보를 JSON으로 받습니다. 전체 악보 보관에는 위 MusicXML도 함께 내려받아주세요.</p><a className="text-button" href={notesUrl} download onClick={event => { if (dirty || busy) { event.preventDefault(); setError('음표 데이터에는 저장된 내용만 포함됩니다. 수정 내용을 먼저 저장해주세요.'); } }}>저장된 음표 데이터 JSON</a></details>}</details>
  </section>;
}
