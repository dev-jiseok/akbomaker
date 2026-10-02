import { useEffect, useState } from 'react';
import { ArrowLeft, ArrowUpRight, Check, Download, FileMusic, LoaderCircle, Plus, Printer, SlidersHorizontal, X, AlertCircle, Pencil } from 'lucide-react';
import { request } from '../api';
import { formatTime, isProcessing, type Health, type Instrument, type Job, type ScoreDocument, type ScorePreset } from '../types';
import { editBody, presetLabels } from '../scoreEditing';
import Mixer, { instrumentIcons } from './Mixer';
import ScoreViewer, { EmptyScore } from './ScoreViewer';
import ScoreEditor from './ScoreEditor';
import LyricsWorkbench from './LyricsWorkbench';
import RhythmWorkbench from './RhythmWorkbench';

type Props = { job: Job; health: Health | null; onJob: (job: Job) => void; onNew: () => void; onError: (message: string) => void; onEditorDirty: (dirty: boolean) => void };

export default function Workspace({ job, health, onJob, onNew, onError, onEditorDirty }: Props) {
  const [selected, setSelected] = useState<Instrument>('drums');
  const [scale, setScale] = useState(1.1);
  const [spacious, setSpacious] = useState(true);
  const [numbers, setNumbers] = useState(true);
  const [bpm, setBpm] = useState(job.bpm || 120);
  const [busy, setBusy] = useState(false);
  const [settings, setSettings] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editorDirty, setEditorDirty] = useState(false);
  const [lyricDirty, setLyricDirty] = useState(false);
  const [audioOffset, setAudioOffset] = useState(0);
  const processing = isProcessing(job);
  const stem = job.stems.find(stem => stem.id === selected)!;
  useEffect(() => { if (stem.score_bpm || job.bpm) setBpm(stem.score_bpm || job.bpm!); }, [selected, stem.score_bpm, job.bpm]);
  useEffect(() => { onEditorDirty(editorDirty || lyricDirty); }, [editorDirty, lyricDirty, onEditorDirty]);
  useEffect(() => () => onEditorDirty(false), [onEditorDirty]);
  const leaveEditor = () => !editorDirty || window.confirm('저장하지 않은 변경사항이 있어요. 편집기를 나갈까요? 이 브라우저의 임시 편집본은 보관됩니다.');
  function selectPart(inst: Instrument) { if (inst !== selected && leaveEditor()) { setEditing(false); setEditorDirty(false); setSelected(inst); } }
  function newProject() { onNew(); }

  async function changeStyle(preset: ScorePreset) {
    if (busy || processing) return;
    setBusy(true);
    try {
      const doc = await request<ScoreDocument>(`/api/jobs/${job.id}/scores/${selected}`);
      const updated = { ...doc, layout: { ...doc.layout, preset, measures_per_line: (preset === 'large' ? 2 : 4) as 2 | 4 } };
      const result = await request<{ job: Job }>(`/api/jobs/${job.id}/scores/${selected}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(editBody(updated)) });
      onJob(result.job);
    } catch (error) { onError((error as Error).message); }
    finally { setBusy(false); }
  }

  async function makeScore(all = false) {
    const overwrite = job.stems.some(s => s.score_edited && (all || s.id === selected));
    if (overwrite && !window.confirm('직접 수정한 악보가 있어요. 다시 채보하면 음표·TAB·가사·메모·스타일이 자동 채보 결과로 대체됩니다. 계속할까요? 먼저 MusicXML을 내려받아 보관할 수 있어요.')) return;
    setBusy(true);
    try {
      const updated = await request<Job>(`/api/jobs/${job.id}/transcribe`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ instruments: all ? job.stems.filter(s => s.status === 'ready').map(s => s.id) : [selected], bpm, audio_offset: audioOffset, overwrite_edits: overwrite }) });
      onJob(updated);
    } catch (error) { onError((error as Error).message); }
    finally { setBusy(false); }
  }

  async function cancel() {
    setBusy(true);
    try { onJob(await request<Job>(`/api/jobs/${job.id}/cancel`, { method: 'POST' })); }
    catch (error) { onError((error as Error).message); }
    finally { setBusy(false); }
  }
  async function analyzeBeats() {
    setBusy(true);
    try { onJob(await request<Job>(`/api/jobs/${job.id}/analyze-beats`, { method: 'POST' })); }
    catch (error) { onError((error as Error).message); }
    finally { setBusy(false); }
  }
  async function emptyScore() {
    setBusy(true);
    try { onJob(await request<Job>(`/api/jobs/${job.id}/scores/${selected}/new`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ bpm, audio_offset: audioOffset }) })); setEditing(true); }
    catch (error) { onError((error as Error).message); }
    finally { setBusy(false); }
  }
  return <div className="workspace-view">
    <div className="workspace-top"><button className="text-button" onClick={newProject}><ArrowLeft size={16} /> 작업실 홈</button><button className="secondary small" onClick={newProject}><Plus size={15} /> 새 음악 가져오기</button></div>
    <div className="project-heading"><div><div className="project-label"><span className="eyebrow">YOUR MUSIC PROJECT</span>{job.demo && <span className="sample-tag">샘플 프로젝트</span>}</div><h1>{job.title}</h1><p>{job.source_type === 'youtube' ? 'YouTube' : job.demo ? '직접 합성한 샘플 음악' : '업로드한 음악'} <i /> {formatTime(job.duration)} <i /> {job.stems.filter(s => s.status === 'ready').length}개의 악기</p></div>{!processing && job.stems.some(s => s.audio_url) && <a className="secondary" href={`/api/jobs/${job.id}/archive`} onClick={e => { if (editorDirty) { e.preventDefault(); onError('편집 내용을 먼저 저장해주세요. ZIP에는 저장된 악보가 포함됩니다.'); } }}><Download size={16} /> 전체 파일 받기</a>}</div>
    {job.demo && <div className="demo-notice"><span>DEMO</span><p>작업 흐름을 체험하는 샘플이에요. 직접 합성한 음원과 악보이며 SAM Audio로 분리한 결과는 아니에요.</p></div>}
    {processing && <div className="progress-card" aria-live="polite"><div className="progress-title"><span><LoaderCircle size={20} className="spin" /><strong>{job.message}</strong></span><button className="text-button" onClick={() => void cancel()} disabled={busy}>중단 <X size={14} /></button></div><div className="progress-track"><div style={{ width: `${job.progress}%` }} /></div><div className="progress-meta"><span>{job.status === 'transcribing' ? '분리된 음원을 바탕으로 채보합니다' : '완료된 악기는 먼저 들어볼 수 있어요'}</span><span>{job.progress}%</span></div></div>}
    {job.error && <div className="inline-alert" role="alert"><AlertCircle size={18} /><div><strong>작업 중 문제가 생겼어요</strong><p>{job.error}</p></div></div>}
    {job.status === 'cancelled' && <div className="demo-notice"><span>중단됨</span><p>{job.message}</p></div>}
    {job.analysis_only && <div className="demo-notice"><span>원본 분석</span><p>SAM 악기 분리는 실행하지 않았어요. 원본으로 BPM·가사를 분석하거나 빈 악보를 만들어 직접 편집할 수 있습니다.</p></div>}
    {job.analysis_error && <p className="inline-alert" role="alert">{job.analysis_error}</p>}
    {job.original_url && <RhythmWorkbench job={job} bpm={bpm} offset={audioOffset} disabled={busy || processing} onBpm={setBpm} onOffset={setAudioOffset} onAnalyze={() => void analyzeBeats()} />}
    {job.original_url && <LyricsWorkbench key={`lyrics:${job.id}`} job={job} health={health} editing={editing} onJob={onJob} onError={onError} onDirty={setLyricDirty} />}
    <Mixer key={job.id} job={job} selected={selected} onSelect={selectPart} onError={onError} />
    <section className="score-section">
      <div className="score-section-heading"><div><span className="eyebrow">MAKE IT YOURS</span><h2>이제, 나에게 편한 악보로.</h2><p>보고 싶은 악기를 선택하고, 읽기 좋은 크기로 맞춰보세요.</p></div><button className={`secondary small ${settings ? 'active' : ''}`} onClick={() => setSettings(!settings)} aria-expanded={settings}><SlidersHorizontal size={16} /> 악보 설정</button></div>
      <div className="score-layout">
        <aside className="score-parts"><span className="parts-label">악기 선택</span>{job.stems.map(part => { const Icon = instrumentIcons[part.id]; return <button key={part.id} className={`part-button tone-${part.id} ${part.id === selected ? 'active' : ''}`} onClick={() => selectPart(part.id)} aria-pressed={part.id === selected}><Icon size={17} /><span>{part.label}</span>{part.score_status === 'ready' ? <Check size={14} /> : part.score_status === 'running' ? <LoaderCircle className="spin" size={14} /> : <span className="part-dot" />}</button>; })}<div className="parts-note"><FileMusic size={20} /><p>자동 채보는 초안이에요.<br />소리와 악보를 함께<br />확인해보세요.</p></div></aside>
        <div className="score-main">
          {settings && <div className="score-settings"><label>음표 크기 <select value={scale} onChange={event => setScale(Number(event.target.value))}><option value="0.85">작게</option><option value="1.1">편하게</option><option value="1.4">크게</option></select></label><label>보표 간격 <select value={String(spacious)} onChange={event => setSpacious(event.target.value === 'true')}><option value="true">여유롭게</option><option value="false">촘촘하게</option></select></label><label className="check-label"><input type="checkbox" checked={numbers} onChange={event => setNumbers(event.target.checked)} />마디 번호</label></div>}
          <div className="score-toolbar"><span><FileMusic size={16} /><strong>{stem.label} 악보</strong>{stem.score_status === 'ready' && <small>4/4 · ♩ {stem.score_bpm || job.bpm || bpm}{stem.score_edited ? ' · 수정본' : ''}</small>}</span><div>{stem.score_url && !editing && <><a className="text-button" href={stem.midi_url + `?download=true&v=${stem.score_revision || 0}`}>MIDI <Download size={13} /></a><a className="text-button" href={stem.score_url + `?download=true&v=${stem.score_revision || 0}`}>MusicXML <Download size={13} /></a><button className="icon-button" onClick={() => window.print()} aria-label="악보 인쇄 또는 PDF 저장"><Printer size={16} /></button></>}</div></div>
          {stem.score_url && !editing && <div className="score-style-bar"><label>출력 스타일<select value={stem.score_layout?.preset || (stem.id === 'drums' ? 'practice' : 'standard')} disabled={processing || busy} onChange={e => void changeStyle(e.target.value as ScorePreset)}>{Object.entries(presetLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><button className="secondary small" disabled={processing || busy} onClick={() => setEditing(true)}><Pencil size={15} /> 악보 직접 수정</button></div>}
          {editing ? <ScoreEditor key={`${job.id}:${selected}`} job={job} stem={stem} onSaved={onJob} onDirty={setEditorDirty} onClose={() => { if (leaveEditor()) { setEditing(false); setEditorDirty(false); } }} /> : <><div className={`score-paper ${spacious ? 'spacious' : ''} preset-${stem.score_layout?.preset || 'standard'}`} id="print-score">
            {stem.score_url ? <><div className="paper-heading"><span>AKBO MAKER</span><h3>{stem.score_title || job.title}</h3><p>{stem.label} · {stem.score_bpm || job.bpm || bpm} BPM · {stem.score_edited ? '사용자 수정본' : job.demo ? '샘플 악보' : '자동 채보 초안'}</p></div><ScoreViewer stem={stem} scale={scale} spacious={stem.score_layout?.preset === 'standard' ? spacious : true} measureNumbers={numbers && (stem.score_layout?.show_numbers ?? true)} />{stem.note_count === 0 && <p className="empty-notes">음표가 없는 악보예요. 직접 수정에서 음표를 넣거나 원본 음원을 확인해주세요.</p>}</> : <EmptyScore message={stem.status !== 'ready' ? '악기 분리가 끝나면 악보를 만들 수 있어요.' : stem.score_status === 'running' ? '음정과 리듬을 분석하고 있어요. 잠시 기다려주세요.' : stem.score_error || '이 악기의 음원을 바탕으로 음정과 리듬을 옮겨보세요.'} />}
          </div>
          <div className="score-bottom"><div><label className="bpm-input">템포 <input aria-label="채보 템포 BPM" type="number" min="40" max="240" value={bpm} onChange={event => setBpm(Math.min(240, Math.max(40, Number(event.target.value))))} disabled={processing} /><span>BPM</span></label><span className="bpm-note">곡의 템포에 맞춰주세요 · 4/4 기준</span></div><button className="primary small" disabled={processing || busy || stem.status !== 'ready'} onClick={() => void makeScore()}>{busy || stem.score_status === 'running' ? <LoaderCircle size={16} className="spin" /> : <FileMusic size={16} />}{stem.score_url ? '다시 채보하기' : `${stem.label} 악보 만들기`}<ArrowUpRight size={16} /></button></div></>}
          {stem.score_warning && <p className="score-warning">{stem.score_warning}</p>}
          {!stem.score_url && !editing && job.original_url && <button className="secondary small blank-score-button" disabled={processing || busy} onClick={() => void emptyScore()}><Plus size={15} /> 빈 {stem.label} 악보 만들기 · 직접 입력</button>}
          {!!stem.score_tab_unassigned && stem.score_tab_mode !== 'staff' && <p className="score-warning" role="status">TAB 미배정 {stem.score_tab_unassigned}개 · 음역이나 줄 수를 벗어난 음은 TAB에서 제외됐어요. 오선 + TAB으로 확인하거나 운지를 수정해주세요. MIDI에는 원래 음정이 유지됩니다.</p>}
          {stem.quiet && <p className="score-warning">이 악기에서 검출된 소리가 매우 작아요. 실제로 해당 악기가 있는지 들어보세요.</p>}
          {!processing && !editing && <button className="text-button all-scores" disabled={busy || !job.stems.some(s => s.status === 'ready')} onClick={() => void makeScore(true)}>분리된 모든 악기 악보 만들기 <ArrowUpRight size={14} /></button>}
        </div>
      </div>
    </section>
  </div>;
}
