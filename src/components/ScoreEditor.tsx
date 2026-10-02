import { useEffect, useRef, useState } from 'react';
import { Check, ChevronLeft, ChevronRight, Copy, Download, LoaderCircle, Pencil, Redo2, RotateCcw, Save, Trash2, Undo2, X } from 'lucide-react';
import { request } from '../api';
import { contentKey, drumLanes, editBody, pitchLabel, presetLabels, toggleNote, updateNote } from '../scoreEditing';
import type { Job, ScoreDocument, ScoreNote, ScorePreset, Stem } from '../types';
import ScoreViewer from './ScoreViewer';
import TabEditor from './TabEditor';
import LyricEditor from './LyricEditor';
import ScorePlayback from './ScorePlayback';
import BulkScoreEditor from './BulkScoreEditor';
import ScoreImport from './ScoreImport';
import { audioTickAtTime, audioTimeAtTick } from '../scorePlayback';

type Props = { job: Job; stem: Stem; onSaved: (job: Job) => void; onClose: () => void; onDirty: (dirty: boolean) => void };

export default function ScoreEditor({ job, stem, onSaved, onClose, onDirty }: Props) {
  const [document, setDocument] = useState<ScoreDocument | null>(null);
  const [baseline, setBaseline] = useState('');
  const [history, setHistory] = useState<ScoreDocument[]>([]);
  const [future, setFuture] = useState<ScoreDocument[]>([]);
  const [measure, setMeasure] = useState(1);
  const [octave, setOctave] = useState(stem.id === 'bass' ? 2 : 4);
  const [length, setLength] = useState(stem.id === 'drums' ? 2 : 4);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [saving, setSaving] = useState(false);
  const [preview, setPreview] = useState('');
  const [previewing, setPreviewing] = useState(false);
  const [draft, setDraft] = useState<ScoreDocument | null>(null);
  const [clipboard, setClipboard] = useState<ScoreNote[] | null>(null);
  const [pitchGrid, setPitchGrid] = useState(false);
  const [playTick, setPlayTick] = useState<number | null>(null);
  const [audioTick, setAudioTick] = useState<number | null>(null);
  const [audioSession, setAudioSession] = useState(0);
  const [bulkIds, setBulkIds] = useState<string[]>([]);
  const audio = useRef<HTMLAudioElement>(null);
  const latestDocument = useRef<ScoreDocument | null>(null);
  latestDocument.current = document;
  const saveLock = useRef(false);
  const previewGeneration = useRef(0);
  const endpoint = `/api/jobs/${job.id}/scores/${stem.id}`;
  const draftKey = `akbo-score-draft:${job.id}:${stem.id}`;
  const dirty = !!document && contentKey(document) !== baseline;

  useEffect(() => {
    const player = audio.current;
    if (!player || !document) return;
    let frame = 0;
    const update = () => {
      cancelAnimationFrame(frame);
      setAudioTick(player.paused || player.ended ? null : audioTickAtTime(document, player.currentTime));
      if (!player.paused && !player.ended) frame = requestAnimationFrame(update);
    };
    for (const event of ['play', 'pause', 'seeked', 'ended']) player.addEventListener(event, update);
    return () => { cancelAnimationFrame(frame); player.pause(); for (const event of ['play', 'pause', 'seeked', 'ended']) player.removeEventListener(event, update); };
  }, [document]);

  useEffect(() => {
    let disposed = false;
    request<ScoreDocument>(endpoint).then(doc => {
      if (disposed) return;
      setDocument(doc); setBaseline(contentKey(doc));
      if (doc.notes.length && stem.id !== 'drums') setOctave(Math.max(0, Math.min(9, Math.floor(doc.notes[0].pitch / 12) - 1)));
      try {
        const candidate = JSON.parse(localStorage.getItem(draftKey) || 'null');
        if (candidate?.instrument === doc.instrument && Array.isArray(candidate.notes) && candidate.layout && Array.isArray(candidate.annotations) && contentKey(candidate) !== contentKey(doc)) setDraft(candidate);
      } catch { /* Storage is optional. */ }
    }).catch(e => { if (!disposed) setError(e.message); });
    return () => { disposed = true; onDirty(false); };
  }, [endpoint, draftKey, stem.id, onDirty]);

  useEffect(() => { onDirty(dirty); }, [dirty, onDirty]);
  useEffect(() => {
    if (!document) return;
    if (!dirty) {
      if (!draft) { try { localStorage.removeItem(draftKey); } catch { /* optional */ } }
      return;
    }
    const prevent = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener('beforeunload', prevent);
    try { localStorage.setItem(draftKey, JSON.stringify(document)); } catch { /* Storage may be full. */ }
    return () => window.removeEventListener('beforeunload', prevent);
  }, [dirty, document, draftKey, draft]);

  useEffect(() => {
    if (!document) return;
    const generation = ++previewGeneration.current;
    const controller = new AbortController();
    setPreviewing(true);
    const timer = window.setTimeout(() => {
      request<{ musicxml: string }>(endpoint + '/preview', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(editBody(document)), signal: controller.signal })
        .then(result => { if (generation === previewGeneration.current) { setPreview(result.musicxml); setError(''); } })
        .catch(e => { if (!controller.signal.aborted && generation === previewGeneration.current) setError(e.message); })
        .finally(() => { if (generation === previewGeneration.current) setPreviewing(false); });
    }, 450);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [document, endpoint]);

  function change(next: ScoreDocument) {
    const current = latestDocument.current;
    if (!current || saveLock.current || contentKey(next) === contentKey(current)) return;
    setHistory(previous => [...previous.slice(-79), current]); setFuture([]);
    setDocument(next); setError(''); setNotice('');
  }
  function undo() {
    if (!document || !history.length || saveLock.current) return;
    setFuture(previous => [document, ...previous]); setDocument(history[history.length - 1]); setHistory(history.slice(0, -1));
  }
  function redo() {
    if (!document || !future.length || saveLock.current) return;
    setHistory(previous => [...previous, document]); setDocument(future[0]); setFuture(future.slice(1));
  }
  async function save() {
    if (!document || saveLock.current) return;
    saveLock.current = true; setSaving(true); setError('');
    try {
      const result = await request<{ document: ScoreDocument; job: Job }>(endpoint, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(editBody(document)) });
      setDocument(result.document); setBaseline(contentKey(result.document)); setHistory([]); setFuture([]);
      try { localStorage.removeItem(draftKey); } catch { /* Storage is optional. */ }
      setNotice('저장했어요. 악보 · MusicXML · MIDI에 모두 반영됐어요.'); onSaved(result.job);
    } catch (e) { setError((e as Error).message); }
    finally { saveLock.current = false; setSaving(false); }
  }
  async function restoreOriginal() {
    if (!document || !window.confirm('자동 채보 원본으로 되돌릴까요? 현재 편집 내용은 실행 취소로 복구할 수 있어요. 저장하기 전에는 서버의 수정본이 바뀌지 않아요.')) return;
    try {
      const original = await request<ScoreDocument>(endpoint + '?original=true');
      change({ ...original, revision: document.revision }); setSelectedId(null);
    } catch (e) { setError((e as Error).message); }
  }
  function backup(copy?: ScoreDocument) {
    const target = copy || document;
    if (!target) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(target, null, 2)], { type: 'application/json' }));
    const anchor = window.document.createElement('a'); anchor.href = url; anchor.download = `akbo-${stem.id}-edit.json`; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function editSelected(values: Partial<ScoreNote>) {
    if (!document || !selectedId) return;
    try { change(updateNote(document, selectedId, values)); } catch (e) { setError((e as Error).message); }
  }
  function annotate(field: 'section' | 'cue', value: string) {
    if (!document) return;
    const current = document.annotations.find(a => a.measure === measure) || { measure, section: '', cue: '' };
    const item = { ...current, [field]: value };
    change({ ...document, annotations: [...document.annotations.filter(a => a.measure !== measure), item].filter(a => a.section || a.cue) });
  }
  async function copyLyrics() {
    if (!document || dirty || saveLock.current) return;
    if (!window.confirm('저장한 가사를 다른 악기의 생성된 악보에 복사할까요? 대상의 기존 가사만 대체하고 음표·TAB·구간 메모는 보존합니다. 대상 악보를 다른 탭에서 편집 중이라면 먼저 저장해주세요.')) return;
    saveLock.current = true; setSaving(true);
    try {
      const result = await request<{ job: Job; copied: number }>(endpoint + '/copy-lyrics', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ base_revision: document.revision }) });
      onSaved(result.job); setNotice(`다른 악보 ${result.copied}개에 가사를 복사했어요.`);
    } catch (e) { setError((e as Error).message); }
    finally { saveLock.current = false; setSaving(false); }
  }
  if (!document) return <div className="score-editor"><div className="editor-loading">{error || <><LoaderCircle className="spin" size={18} /> 편집할 악보를 불러오는 중이에요</>}<button className="text-button" onClick={onClose}>닫기</button></div></div>;
  const barStart = (measure - 1) * 16;
  const barNotes = document.notes.filter(n => n.start < barStart + 16 && n.start + n.length > barStart);
  const selected = document.notes.find(n => n.id === selectedId);
  const annotation = document.annotations.find(a => a.measure === measure);
  const lanes = stem.id === 'drums' ? drumLanes : Array.from({ length: 12 }, (_, index) => {
    const pitch = (octave + 1) * 12 + 11 - index;
    return { pitch, label: pitchLabel(pitch) };
  }).filter(lane => lane.pitch >= 0 && lane.pitch <= 127);
  const nextMeasure = (value: number) => { setMeasure(Math.min(document.ticks / 16, Math.max(1, value))); setSelectedId(null); };

  return <div className="score-editor" aria-label={`${stem.label} 악보 편집기`} onKeyDown={event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); void save(); }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z' && !(event.target instanceof HTMLInputElement) && !(event.target instanceof HTMLTextAreaElement)) { event.preventDefault(); if (event.shiftKey) redo(); else undo(); }
  }}>
    <div className="editor-header"><div><Pencil size={18} /><strong>악보 직접 수정</strong><span className={dirty ? 'edit-state dirty' : 'edit-state'}>{dirty ? '저장 전 변경사항' : '저장된 악보'}</span></div><button className="icon-button" disabled={saving} onClick={onClose} aria-label="악보 편집기 닫기"><X size={18} /></button></div>
    {draft && <div className="draft-banner"><span>{draft.revision === document.revision ? '이 브라우저에 저장하지 않은 편집본이 있어요.' : '이전 버전의 임시 편집본이 있어요. 최신 악보와 충돌해 자동 복원할 수 없으니 JSON으로 보관해주세요.'}</span>{draft.revision === document.revision ? <button className="text-button" onClick={() => { change(draft); setDraft(null); }}>편집본 복원</button> : <button className="text-button" onClick={() => backup(draft)}>이전 편집본 JSON 보관</button>}<button className="text-button" onClick={() => { setDraft(null); try { localStorage.removeItem(draftKey); } catch { /* optional */ } }}>무시</button></div>}
    <div className="editor-actions"><div><button className="secondary small" onClick={undo} disabled={!history.length || saving}><Undo2 size={15} /> 실행 취소</button><button className="icon-button" onClick={redo} disabled={!future.length || saving} aria-label="다시 실행"><Redo2 size={16} /></button><button className="text-button" onClick={() => void restoreOriginal()} disabled={saving}><RotateCcw size={14} /> 채보 원본</button><button className="text-button" onClick={() => backup()}><Download size={14} /> JSON 보관</button></div><button className="primary small" disabled={!dirty || saving} onClick={() => void save()}>{saving ? <LoaderCircle size={15} className="spin" /> : <Save size={15} />} 저장하고 반영</button></div>
    {error && <p className="editor-error" role="alert">{error}</p>}{notice && <p className="editor-success" role="status"><Check size={15} />{notice}</p>}
    <fieldset className="editor-fields" disabled={saving}><label>악보 제목<input maxLength={180} value={document.title} onChange={e => change({ ...document, title: e.target.value })} /></label><label>출력 스타일<select value={document.layout.preset} onChange={e => change({ ...document, layout: { ...document.layout, preset: e.target.value as ScorePreset, measures_per_line: e.target.value === 'large' ? 2 : 4 } })}>{Object.entries(presetLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><label>한 줄 마디<select value={document.layout.measures_per_line} onChange={e => change({ ...document, layout: { ...document.layout, measures_per_line: Number(e.target.value) as 2 | 4 } })}><option value={4}>4마디</option><option value={2}>2마디</option></select></label><label>BPM<input type="number" min={40} max={240} value={document.bpm} onChange={e => change({ ...document, bpm: Math.max(40, Math.min(240, Number(e.target.value))) })} /></label><label className="check-label"><input type="checkbox" checked={document.layout.show_numbers} onChange={e => change({ ...document, layout: { ...document.layout, show_numbers: e.target.checked } })} />마디 번호</label></fieldset>
    <div className="notation-options"><label>음표 연결선(빔)<select value={document.layout.beam_group || (stem.id === 'drums' ? 'half' : 'beat')} disabled={saving} onChange={e => change({ ...document, layout: { ...document.layout, beam_group: e.target.value as 'beat' | 'half' } })}><option value="half">2박씩 · 8분음표 4개 연결</option><option value="beat">1박씩 · 8분음표 2개 연결</option></select></label><span>16분음표의 두 번째 선은 1박씩 나눠 읽기 쉽게 표시해요. 소리·길이는 유지합니다.</span></div>
    <p className="editor-help">빈칸을 누르면 음표가 들어갑니다. 음표나 이어지는 칸을 누르면 선택하고, 음표를 더블클릭하면 삭제합니다. 빈 구간은 악보에서 쉼표가 됩니다. <span>4/4 · 한 칸 = 16분음표</span></p>
    <div className="grid-legend"><span><b>● / TAB 숫자</b> 음표 시작</span><span><b>—</b> 앞 음표가 이어짐 · 쉼표 아님</span><span>— 넣기: 음표 선택 → 길이 늘리기</span></div>
    <div className="measure-navigation"><div><button className="icon-button" disabled={measure === 1} onClick={() => nextMeasure(measure - 1)} aria-label="이전 마디"><ChevronLeft size={17} /></button><label><input type="number" min={1} max={document.ticks / 16} value={measure} onChange={e => nextMeasure(Math.round(Number(e.target.value)) || 1)} aria-label="편집할 마디" /> / {document.ticks / 16} 마디</label><button className="icon-button" disabled={measure === document.ticks / 16} onClick={() => nextMeasure(measure + 1)} aria-label="다음 마디"><ChevronRight size={17} /></button></div><div><label>입력 길이<select value={length} onChange={e => setLength(Number(e.target.value))}><option value={1}>16분음표</option><option value={2}>8분음표</option><option value={4}>4분음표</option><option value={8}>2분음표</option><option value={16}>온음표</option></select></label>{stem.id !== 'drums' && <label>옥타브<select value={octave} onChange={e => setOctave(Number(e.target.value))}>{Array.from({ length: 11 }, (_, i) => i - 1).map(i => <option key={i} value={i}>{i}</option>)}</select></label>}</div></div>
    {document.tab && <><TabEditor document={document} measure={measure} length={length} disabled={saving} onChange={change} onSelect={setSelectedId} onError={setError} /><button className="text-button pitch-grid-toggle" onClick={() => setPitchGrid(!pitchGrid)}>{pitchGrid ? '음정 격자 접기' : '운지 없는 음표도 보기 · 음정 격자 열기'}</button></>}
    {(!document.tab || pitchGrid) && <fieldset disabled={saving} className="grid-fieldset"><div className="note-grid-scroll"><div className="note-grid" role="group" aria-label={`${measure}마디 음표 입력`}>
      <span className="lane-label grid-corner">{stem.id === 'drums' ? '드럼 파트' : '음정'}</span>{Array.from({ length: 16 }, (_, tick) => <span key={tick} className={`grid-tick ${tick % 4 === 0 ? 'beat' : ''}`}>{tick % 4 === 0 ? tick / 4 + 1 : ['e', '&', 'a'][tick % 4 - 1]}</span>)}
      {lanes.map(lane => <div className="grid-lane" key={lane.pitch}><span className="lane-label">{lane.label}</span>{Array.from({ length: 16 }, (_, tick) => {
        const start = barStart + tick;
        const note = barNotes.find(n => n.pitch === lane.pitch && n.start === start);
        const sustain = !note ? barNotes.find(n => n.pitch === lane.pitch && n.start < start && n.start + n.length > start) : undefined;
        const sustained = !!sustain;
        return <button key={tick} className={`note-cell ${tick % 4 === 0 ? 'beat-start' : ''} ${note ? 'on' : sustained ? 'held' : ''} ${(note || sustain)?.id === selectedId ? 'selected-cell' : ''}`} title={note ? '음표 시작 · 클릭하여 길이 수정, 더블클릭하여 삭제' : sustained ? '앞 음표가 이어지는 칸 · 클릭하여 해당 음표 선택' : '음표 추가'} aria-label={`${lane.label} ${measure}마디 ${tick + 1}번째 칸${note ? ' 음표 시작' : sustained ? ' 앞 음표 이어짐' : ' 빈칸'}`} aria-pressed={!!note} onClick={() => { if (note || sustain) setSelectedId((note || sustain)!.id); else { const id = crypto.randomUUID(); change(toggleNote(document, lane.pitch, start, length, id)); setSelectedId(id); } }} onDoubleClick={() => { if (note) { change({ ...document, notes: document.notes.filter(n => n.id !== note.id) }); setSelectedId(null); } }} onKeyDown={e => {
          const delta = { ArrowRight: 1, ArrowLeft: -1, ArrowUp: -16, ArrowDown: 16 }[e.key];
          if (delta !== undefined) { e.preventDefault(); const cells = Array.from(e.currentTarget.closest('.note-grid')!.querySelectorAll<HTMLButtonElement>('.note-cell')); cells[cells.indexOf(e.currentTarget) + delta]?.focus(); }
        }}>{note ? '●' : sustained ? '—' : ''}</button>;
      })}</div>)}
    </div></div></fieldset>}
    <BulkScoreEditor document={document} measure={measure} ids={bulkIds} disabled={saving} onSelect={setBulkIds} onChange={change} onError={setError} />
    <ScoreImport disabled={saving} document={document} endpoint={endpoint} onDocument={doc => { if (doc.revision !== latestDocument.current?.revision) return setError('가져오는 동안 악보 버전이 변경됐어요. 최신 악보에서 다시 가져와주세요.'); change(doc); setMeasure(1); setSelectedId(null); setBulkIds([]); }} />
    <div className="editor-details"><div className="note-selection"><span className="editor-label">이 마디의 음표 · {barNotes.length}개 · Shift+클릭으로 여러 개 선택</span><div className="note-chips">{barNotes.map(n => <button key={n.id} className={`note-chip ${selectedId === n.id || bulkIds.includes(n.id) ? 'selected' : ''}`} aria-pressed={selectedId === n.id || bulkIds.includes(n.id)} onClick={e => { setSelectedId(n.id); setBulkIds(previous => e.shiftKey ? previous.includes(n.id) ? previous.filter(id => id !== n.id) : [...previous, n.id] : [n.id]); }}>{stem.id === 'drums' ? drumLanes.find(l => l.pitch === n.pitch)?.label : pitchLabel(n.pitch)} <small>{((n.start % 16) / 4 + 1).toFixed(2)}박</small></button>)}{!barNotes.length && <span className="no-notes">쉼표 마디 · 위 격자에서 음표를 넣어보세요.</span>}</div>
    {selected && <fieldset className="note-inspector" disabled={saving}>{document.tab && <><label>TAB 줄<select value={selected.string ?? ''} onChange={e => {
      if (!e.target.value) return editSelected({ string: null, fret: null });
      const string = Number(e.target.value), fret = selected.pitch - document.tab!.tuning[string - 1] - (document.tab!.capo || 0);
      editSelected({ string, fret });
    }}><option value="">미배정</option>{document.tab.tuning.map((pitch, i) => <option value={i + 1} key={i} disabled={selected.pitch - pitch - (document.tab!.capo || 0) < 0 || selected.pitch - pitch - (document.tab!.capo || 0) > 24}>{i + 1}번 · {pitchLabel(pitch)}</option>)}</select></label><label>프렛<input type="number" min={0} max={24} disabled={selected.string == null} value={selected.fret ?? ''} onChange={e => {
      const fret = Number(e.target.value); if (selected.string) editSelected({ fret, pitch: document.tab!.tuning[selected.string - 1] + (document.tab!.capo || 0) + fret, string: selected.string });
    }} /></label></>}<label>{stem.id === 'drums' ? '드럼 종류' : 'MIDI 음정'}{stem.id === 'drums' ? <select value={selected.pitch} onChange={e => editSelected({ pitch: Number(e.target.value) })}>{drumLanes.map(l => <option value={l.pitch} key={l.pitch}>{l.label}</option>)}</select> : <input type="number" min={0} max={127} value={selected.pitch} onChange={e => editSelected({ pitch: Number(e.target.value) })} />}</label><label>시작 칸<input type="number" min={1} max={16} value={selected.start % 16 + 1} onChange={e => editSelected({ start: Math.floor(selected.start / 16) * 16 + Number(e.target.value) - 1 })} /></label><label>길이(칸)<input type="number" min={1} max={document.ticks - selected.start} value={selected.length} onChange={e => editSelected({ length: Math.max(1, Math.round(Number(e.target.value))) })} /></label><label>세기<input type="number" min={1} max={127} value={selected.velocity} onChange={e => editSelected({ velocity: Math.min(127, Math.max(1, Math.round(Number(e.target.value)))) })} /></label><button className="icon-button" aria-label="선택한 음표 삭제" onClick={() => { change({ ...document, notes: document.notes.filter(n => n.id !== selected.id) }); setSelectedId(null); }}><Trash2 size={16} /></button></fieldset>}
    <div className="measure-copy"><button className="text-button" onClick={() => { setClipboard(barNotes.map(n => ({ ...n, start: Math.max(0, n.start - barStart), length: Math.min(n.start + n.length, barStart + 16) - Math.max(n.start, barStart) }))); setNotice(`${measure}마디 음표를 복사했어요.`); }}><Copy size={14} /> 마디 복사</button><button className="text-button" disabled={!clipboard || saving} onClick={() => {
      if (!clipboard) return;
      // Preserve tails outside this measure when replacing its notes.
      const outside = document.notes.flatMap(n => {
        if (n.start >= barStart + 16 || n.start + n.length <= barStart) return [n];
        const result = [];
        if (n.start < barStart) result.push({ ...n, length: barStart - n.start });
        if (n.start + n.length > barStart + 16) result.push({ ...n, id: crypto.randomUUID(), start: barStart + 16, length: n.start + n.length - barStart - 16 });
        return result;
      });
      change({ ...document, notes: [...outside, ...clipboard.map(n => ({ ...n, id: crypto.randomUUID(), start: n.start + barStart }))] });
      setSelectedId(null);
    }}>여기에 붙여넣기</button></div></div>
    <fieldset className="measure-annotations" disabled={saving}><label>구간 표시<input placeholder="Intro / A / B / Chorus" maxLength={24} value={annotation?.section || ''} onChange={e => annotate('section', e.target.value)} /></label><label>마디 아래 메모<input placeholder="가사 힌트, 필인, 연주 메모 등 직접 입력" maxLength={100} value={annotation?.cue || ''} onChange={e => annotate('cue', e.target.value)} /></label><span>구간과 메모는 인쇄·MusicXML에도 반영됩니다.</span></fieldset></div>
    {selected && <div className="duration-shortcuts"><span>선택한 음표 길이 · {selected.length}칸 {selected.length > 1 ? `→ 뒤 ${selected.length - 1}칸에 — 표시` : '→ 이어짐 없음'}</span><button className="secondary small" disabled={saving || selected.length <= 1} onClick={() => editSelected({ length: selected.length - 1 })}>1칸 줄이기</button><button className="secondary small" disabled={saving || selected.start + selected.length >= document.ticks} onClick={() => editSelected({ length: selected.length + 1 })}>1칸 늘리기 · — 추가</button></div>}
    {selected && <fieldset className="note-marks bulk-row" disabled={saving}><label>연주 표시<select value={selected.articulation || 'none'} onChange={e => editSelected({ articulation: e.target.value as ScoreNote['articulation'] })}><option value="none">없음</option><option value="accent">악센트</option><option value="staccato">스타카토</option><option value="tenuto">테누토</option></select></label>{document.tab && <><label className="check-label"><input type="checkbox" checked={selected.muted || false} onChange={e => editSelected({ muted: e.target.checked })} />뮤트 · X 음표</label><label>벤딩 · 반음<input type="number" min={0} max={Math.min(12, 127 - selected.pitch)} value={selected.bend || 0} onChange={e => editSelected({ bend: Number(e.target.value) })} /></label></>}<span className="editor-help">연주 표시는 MusicXML에 반영됩니다. MIDI는 기본 음정·세기를 유지해요.</span></fieldset>}
    <fieldset className="manual-breaks bulk-row" disabled={saving || measure === 1}><span>{measure}마디에서 새로 시작</span>{(['system_breaks', 'page_breaks'] as const).map(key => <label className="check-label" key={key}><input type="checkbox" checked={document.layout[key]?.includes(measure) || false} onChange={e => change({ ...document, layout: { ...document.layout, [key]: e.target.checked ? [...(document.layout[key] || []), measure].sort((a, b) => a - b) : (document.layout[key] || []).filter(n => n !== measure) } })} />{key === 'system_breaks' ? '새 줄' : '새 페이지'}</label>)}<small>기본 2/4마디 배치에 추가로 적용 · 인쇄에도 반영</small></fieldset>
    <LyricEditor document={document} measure={measure} disabled={saving} onChange={change} onError={setError} onNotice={setNotice} />
    <div className="lyric-share"><span>전체 가사 {document.lyrics?.length || 0}개 · 저장하면 악보와 MusicXML에 반영됩니다.</span><button className="text-button" disabled={dirty || saving || !document.lyrics?.length} onClick={() => void copyLyrics()}>저장한 가사를 다른 악보에 복사</button></div>
    <ScorePlayback document={document} measure={measure} stopKey={audioSession} onTick={tick => { if (tick !== null) audio.current?.pause(); setPlayTick(tick); }} onError={setError} />
    {(stem.audio_url || job.original_url) && <div className="editor-audio"><span>{stem.audio_url ? '분리 음원과 비교' : '원본 음원과 비교'}{audioTick !== null ? ` · ${Math.floor(audioTick / 16) + 1}마디` : ''}</span><audio controls preload="metadata" ref={audio} onPlay={() => setAudioSession(value => value + 1)} src={stem.audio_url || job.original_url || undefined} /><button className="text-button" onClick={() => { if (audio.current) { audio.current.currentTime = audioTimeAtTick(document, barStart); void audio.current.play().catch(() => setError('음원 재생 버튼을 눌러주세요.')); } }}>현재 마디부터 듣기</button><small>최초 채보 BPM·시작 오프셋으로 마디를 표시합니다. 변속/변박 음원에서는 위치 보정이 필요해요.</small></div>}
    <div className="editor-preview-heading"><span>악보 미리보기</span><small>{previewing ? '변경사항을 그리는 중…' : '마디를 누르면 해당 마디 편집으로 이동해요'}</small></div>
    <div className={`score-paper editor-preview preset-${document.layout.preset}`} aria-busy={previewing}><div className="paper-heading"><span>AKBO MAKER · {presetLabels[document.layout.preset]}</span><h3>{document.title}</h3><p>{stem.label} · ♩ {document.bpm} · {dirty ? '저장 전 미리보기' : '저장된 악보'}</p></div>{preview && <ScoreViewer stem={stem} xml={preview} layout={document.layout} scale={document.layout.preset === 'large' ? 1.3 : 1.1} spacious={document.layout.preset !== 'standard'} measureNumbers={document.layout.show_numbers} onMeasureSelect={nextMeasure} activeMeasure={(playTick ?? audioTick) === null ? null : Math.floor((playTick ?? audioTick)! / 16) + 1} />}</div>
    <p className="editor-footnote">BPM 수정은 악보·MIDI 템포만 바꾸며 음원과 가사 칸 위치는 유지합니다. 자동 운지는 제안이며 실제 연주법과 다를 수 있어요. TAB만 출력할 때 미배정 음이 없는지 확인해주세요. 출력은 저장 후 편집기를 닫아주세요.</p>
  </div>;
}
