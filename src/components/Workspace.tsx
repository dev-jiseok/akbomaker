import { useEffect, useState } from 'react';
import { ArrowLeft, ArrowUpRight, Check, Download, FileMusic, LoaderCircle, Plus, Printer, SlidersHorizontal, X, AlertCircle, Pencil } from 'lucide-react';
import { request } from '../api';
import { formatTime, isProcessing, type DrumEngine, type Health, type Instrument, type Job, type PitchedEngine, type ScoreDocument, type ScorePreset } from '../types';
import { editBody, presetLabels } from '../scoreEditing';
import { meterOptions, meterSummary } from '../scoreRhythm';
import Mixer, { instrumentIcons } from './Mixer';
import ScoreViewer, { EmptyScore } from './ScoreViewer';
import ScoreEditor from './ScoreEditor';
import LyricsWorkbench from './LyricsWorkbench';
import RhythmWorkbench from './RhythmWorkbench';
import DrumTranscriptionControls from './DrumTranscriptionControls';
import PitchedTranscriptionControls, { supportsPitchedComparison, supportsPitchedReview } from './PitchedTranscriptionControls';
import TranscriptionReview from './TranscriptionReview';
import AudioInputNotice from './AudioInputNotice';
import { SeparationNotice } from './SeparationOptions';
import { recognitionUrl } from './ScoreRecognition';
import SourceScoreEditor from './SourceScoreEditor';

type Props = { job: Job; health: Health | null; onJob: (job: Job) => void; onNew: () => void; onError: (message: string) => void; onEditorDirty: (dirty: boolean) => void };

export default function Workspace(props: Props) {
  return props.job.score_preserved ? <SourceScoreEditor key={props.job.id} job={props.job} onJob={props.onJob} onNew={props.onNew} onEditorDirty={props.onEditorDirty} /> : <AudioWorkspace {...props} />;
}

function AudioWorkspace({ job, health, onJob, onNew, onError, onEditorDirty }: Props) {
  const [selected, setSelected] = useState<Instrument>(() => job.source_type === 'musicxml' ? job.stems.find(s => s.score_url)?.id || 'drums' : 'drums');
  const [scale, setScale] = useState(1.1);
  const [spacious, setSpacious] = useState(true);
  const [numbers, setNumbers] = useState(true);
  const [bpm, setBpm] = useState(job.bpm || 120);
  const [meter, setMeter] = useState('4/4');
  const [busy, setBusy] = useState(false);
  const [settings, setSettings] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editorStart, setEditorStart] = useState<number | undefined>();
  const [editorDirty, setEditorDirty] = useState(false);
  const [lyricDirty, setLyricDirty] = useState(false);
  const [reviewDirty, setReviewDirty] = useState(false);
  const [audioOffset, setAudioOffset] = useState(0);
  const [profile, setProfile] = useState<'instrument' | 'polyphonic'>('instrument');
  const [drumEngine, setDrumEngine] = useState<DrumEngine>('auto');
  const [drumSource, setDrumSource] = useState<'stem' | 'original'>('stem');
  const [pitchedEngine, setPitchedEngine] = useState<PitchedEngine>('standard');
  const processing = isProcessing(job);
  const stem = job.stems.find(stem => stem.id === selected)!;
  const canTranscribe = stem.status === 'ready' || (selected === 'drums' && drumSource === 'original' && !!job.original_url);
  const sourceLabel = job.source_type === 'musicxml' ? '가져온 MusicXML' : job.source_type === 'youtube' ? 'YouTube' : job.demo ? '직접 합성한 샘플 음악' : '업로드한 음악';
  useEffect(() => { if (stem.score_bpm || job.bpm) setBpm(stem.score_bpm || job.bpm!); }, [selected, stem.score_bpm, job.bpm]);
  useEffect(() => { onEditorDirty(editorDirty || lyricDirty || reviewDirty); }, [editorDirty, lyricDirty, reviewDirty, onEditorDirty]);
  useEffect(() => () => onEditorDirty(false), [onEditorDirty]);
  useEffect(() => { const first = stem.score_meters?.[0]; setMeter(first ? `${first.beats}/${first.beat_type}` : '4/4'); }, [selected, stem.score_revision]);
  useEffect(() => { setAudioOffset(stem.score_audio_offset ?? 0); }, [selected, stem.score_revision, stem.score_audio_offset]);
  useEffect(() => { setProfile(stem.score_transcription?.profile === 'polyphonic' ? 'polyphonic' : 'instrument'); }, [selected, stem.score_revision]);
  useEffect(() => { setPitchedEngine(stem.score_transcription?.engine === 'basic-pitch-adaptive-v1' ? 'adaptive' : 'standard'); }, [selected, stem.score_revision, stem.score_transcription?.engine]);
  useEffect(() => { if (selected === 'drums') { const info = stem.score_transcription; setDrumEngine(info?.engine === 'adt-str-consensus-v1' ? 'consensus' : info?.engine === 'adt-str-hybrid-v1' ? 'hybrid' : info?.engine === 'adt-str' ? 'neural' : info?.engine === 'multiband-onsets-v2' ? 'spectral' : 'auto'); setDrumSource(info?.source || (stem.status === 'ready' ? 'stem' : 'original')); } }, [selected, stem.score_revision, stem.status]);
  const generationMeter = { measure: 1, beats: Number(meter.split('/')[0]), beat_type: Number(meter.split('/')[1]) };
  const leaveEditor = () => !(editorDirty || reviewDirty) || window.confirm('저장하지 않은 악보·검수 변경사항이 있어요. 먼저 저장하는 것을 권장해요. 입력 중인 내용이 사라질 수 있는데 이동할까요?');
  function selectPart(inst: Instrument) { if (inst !== selected && leaveEditor()) { setEditing(false); setEditorStart(undefined); setEditorDirty(false); setSelected(inst); } }
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
      const updated = await request<Job>(`/api/jobs/${job.id}/transcribe`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ instruments: all ? job.stems.filter(s => s.status === 'ready').map(s => s.id) : [selected], bpm, audio_offset: audioOffset, meter: generationMeter, profile: all ? 'instrument' : profile, drum_engine: drumEngine, drum_source: drumSource, pitched_engine: all || job.demo || !supportsPitchedComparison(selected) ? 'standard' : pitchedEngine, overwrite_edits: overwrite }) });
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
    try { onJob(await request<Job>(`/api/jobs/${job.id}/scores/${selected}/new`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ bpm, audio_offset: audioOffset, meter: generationMeter }) })); setEditing(true); }
    catch (error) { onError((error as Error).message); }
    finally { setBusy(false); }
  }
  return <div className="workspace-view">
    <div className="workspace-top"><button className="text-button" onClick={newProject}><ArrowLeft size={16} /> 작업실 홈</button><button className="secondary small" onClick={newProject}><Plus size={15} /> 새 음악 가져오기</button></div>
    <div className="project-heading"><div><div className="project-label"><span className="eyebrow">YOUR MUSIC PROJECT</span>{job.demo && <span className="sample-tag">샘플 프로젝트</span>}</div><h1>{job.title}</h1><p>{sourceLabel} <i /> {formatTime(job.duration)} <i /> {job.source_type === 'musicxml' ? `${job.stems.filter(s => s.score_status === 'ready').length}개의 악보` : `${job.stems.filter(s => s.status === 'ready').length}개의 악기`}</p></div>{!processing && job.stems.some(s => s.audio_url || s.score_url) && <a className="secondary" href={`/api/jobs/${job.id}/archive`} onClick={e => { if (editorDirty) { e.preventDefault(); onError('편집 내용을 먼저 저장해주세요. ZIP에는 저장된 악보가 포함됩니다.'); } }}><Download size={16} /> 전체 파일 받기</a>}</div>
    {job.demo && <div className="demo-notice"><span>DEMO</span><p>작업 흐름을 체험하는 샘플이에요. 직접 합성한 음원과 악보이며 SAM Audio로 분리한 결과는 아니에요.</p></div>}
    {processing && <div className="progress-card" aria-live="polite"><div className="progress-title"><span><LoaderCircle size={20} className="spin" /><strong>{job.message}</strong></span><button className="text-button" onClick={() => void cancel()} disabled={busy}>중단 <X size={14} /></button></div><div className="progress-track"><div style={{ width: `${job.progress}%` }} /></div><div className="progress-meta"><span>{job.status === 'transcribing' ? '분리된 음원을 바탕으로 채보합니다' : '완료된 악기는 먼저 들어볼 수 있어요'}</span><span>{job.progress}%</span></div></div>}
    {job.error && <div className="inline-alert" role="alert"><AlertCircle size={18} /><div><strong>작업 중 문제가 생겼어요</strong><p>{job.error}</p></div></div>}
    {job.status === 'cancelled' && <div className="demo-notice"><span>중단됨</span><p>{job.message}</p></div>}
    {job.analysis_only && <div className="demo-notice"><span>원본 분석</span><p>SAM 악기 분리는 실행하지 않았어요. BPM·가사 분석, 원본 기반 드럼 채보 또는 빈 악보 직접 편집을 이용할 수 있습니다.</p></div>}
    {job.analysis_error && <p className="inline-alert" role="alert">{job.analysis_error}</p>}
    {job.original_url && <RhythmWorkbench job={job} bpm={bpm} offset={audioOffset} disabled={busy || processing} onBpm={setBpm} onOffset={setAudioOffset} onAnalyze={() => void analyzeBeats()} />}
    {job.original_url && <LyricsWorkbench key={`lyrics:${job.id}`} job={job} health={health} editing={editing} onJob={onJob} onError={onError} onDirty={setLyricDirty} />}
    {job.source_type === 'musicxml' && <div className="demo-notice"><span>{job.score_omr ? 'PDF·이미지 인식 초안' : '가져온 악보'}</span><p>{job.score_omr ? `${job.score_omr.engine}가 인식한 MusicXML에서 가져왔어요. 원본과 음정·리듬·드럼·가사를 대조해주세요. TAB은 음정에서 새로 배정한 운지이며 원본 TAB 복원이 아닙니다.` : 'MusicXML에서 가져왔어요. 음원 분리·자동 채보는 실행하지 않았습니다. 음표 합성 재생과 TAB·가사·스타일 편집을 사용할 수 있어요.'}</p></div>}
    {job.score_omr && <details className="workspace-omr-source"><summary>인식에 사용한 원본과 결과 다시 확인</summary><div className="bulk-row">{recognitionUrl(job.score_omr.source_url, job.score_omr.id) && <a className="text-button" href={recognitionUrl(job.score_omr.source_url, job.score_omr.id)} target="_blank" rel="noopener noreferrer">원본 PDF·이미지 열기</a>}{recognitionUrl(job.score_omr.result_url, job.score_omr.id) && <a className="text-button" href={recognitionUrl(job.score_omr.result_url, job.score_omr.id)} download>가져오기용 MusicXML</a>}{job.score_omr.raw_result_url && job.score_omr.raw_result_url !== job.score_omr.result_url && recognitionUrl(job.score_omr.raw_result_url, job.score_omr.id) && <a className="text-button" href={recognitionUrl(job.score_omr.raw_result_url, job.score_omr.id)} download>엔진 원본 MusicXML (미보정)</a>}</div><p className="editor-help">아래 원본과 가져오기에 사용한 결과는 현재 수정한 악보와 별도로 보관됩니다. 인식 완료는 정확도 검증이 아닙니다.</p>{!!job.score_omr.normalizations?.length && <p className="editor-help">호환성 보정은 드럼 악기 번호의 기준만 맞춥니다. 템포·음표 위치·길이를 바꾸거나 인식 오류를 고친 결과는 아니에요. 미보정 엔진 원본도 별도로 보관합니다.</p>}{job.score_omr.warnings.length > 0 && <ul className="recognition-warnings">{job.score_omr.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>}<div className="recognition-pages">{job.score_omr.preview_urls.map(url => recognitionUrl(url, job.score_omr!.id)).filter((url): url is string => !!url).map((url, index) => <a key={url} href={url} target="_blank" rel="noopener noreferrer"><img src={url} loading="lazy" alt={`인식 원본 악보 ${index + 1}번째 선택 페이지`} /></a>)}</div></details>}
    {stem.score_source_url && <a className="text-button" href={stem.score_source_url + '?download=true'}>처음 가져온 원본 MusicXML 보관 <Download size={14} /></a>}
    {job.source_type !== 'musicxml' && <Mixer key={job.id} job={job} selected={selected} onSelect={selectPart} onError={onError} />}
    <section className="score-section">
      <div className="score-section-heading"><div><span className="eyebrow">MAKE IT YOURS</span><h2>이제, 나에게 편한 악보로.</h2><p>보고 싶은 악기를 선택하고, 읽기 좋은 크기로 맞춰보세요.</p></div><button className={`secondary small ${settings ? 'active' : ''}`} onClick={() => setSettings(!settings)} aria-expanded={settings}><SlidersHorizontal size={16} /> 악보 설정</button></div>
      <div className="score-layout">
        <aside className="score-parts"><span className="parts-label">악기 선택</span>{job.stems.map(part => { const Icon = instrumentIcons[part.id]; return <button key={part.id} className={`part-button tone-${part.id} ${part.id === selected ? 'active' : ''}`} onClick={() => selectPart(part.id)} aria-pressed={part.id === selected}><Icon size={17} /><span>{part.label}</span>{part.score_status === 'ready' ? <Check size={14} /> : part.score_status === 'running' ? <LoaderCircle className="spin" size={14} /> : <span className="part-dot" />}</button>; })}<div className="parts-note"><FileMusic size={20} /><p>자동 채보는 초안이에요.<br />소리와 악보를 함께<br />확인해보세요.</p></div></aside>
        <div className="score-main">
          {settings && <div className="score-settings"><label>음표 크기 <select value={scale} onChange={event => setScale(Number(event.target.value))}><option value="0.85">작게</option><option value="1.1">편하게</option><option value="1.4">크게</option></select></label><label>보표 간격 <select value={String(spacious)} onChange={event => setSpacious(event.target.value === 'true')}><option value="true">여유롭게</option><option value="false">촘촘하게</option></select></label><label className="check-label"><input type="checkbox" checked={numbers} onChange={event => setNumbers(event.target.checked)} />마디 번호</label></div>}
          <div className="score-toolbar"><span><FileMusic size={16} /><strong>{stem.label} 악보</strong>{stem.score_status === 'ready' && <small>{meterSummary(stem.score_meters)} · ♩ {stem.score_bpm || job.bpm || bpm}{stem.score_edited ? ' · 수정본' : ''}</small>}</span><div>{stem.score_url && !editing && <><a className="text-button" href={stem.midi_url + `?download=true&v=${stem.score_revision || 0}`}>MIDI <Download size={13} /></a><a className="text-button" href={stem.score_url + `?download=true&v=${stem.score_revision || 0}`}>MusicXML <Download size={13} /></a><button className="icon-button" onClick={() => window.print()} aria-label="악보 인쇄 또는 PDF 저장"><Printer size={16} /></button></>}</div></div>
          {stem.score_url && !editing && <div className="score-style-bar"><label>출력 스타일<select value={stem.score_layout?.preset || (stem.id === 'drums' ? 'practice' : 'standard')} disabled={processing || busy} onChange={e => void changeStyle(e.target.value as ScorePreset)}>{Object.entries(presetLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><button className="secondary small" disabled={processing || busy} onClick={() => { if (leaveEditor()) { setEditorStart(undefined); setEditing(true); } }}><Pencil size={15} /> 악보 직접 수정</button></div>}
          {!editing && <TranscriptionReview key={job.id} job={job} stem={stem} disabled={processing || busy} onDirty={setReviewDirty} onEdit={seconds => { if (leaveEditor()) { setEditorStart(seconds); setEditing(true); } }} />}
          {editing ? <ScoreEditor key={`${job.id}:${selected}`} job={job} stem={stem} initialAudioTime={editorStart} onSaved={onJob} onDirty={setEditorDirty} onClose={() => { if (leaveEditor()) { setEditing(false); setEditorDirty(false); } }} /> : <><div className={`score-paper ${spacious ? 'spacious' : ''} preset-${stem.score_layout?.preset || 'standard'}`} id="print-score">
            {stem.score_url ? <><div className="paper-heading"><span>AKBO MAKER</span><h3>{stem.score_title || job.title}</h3><p>{stem.label} · {meterSummary(stem.score_meters)} · {stem.score_bpm || job.bpm || bpm} BPM · {stem.score_edited ? '사용자 수정본' : job.demo ? '샘플 악보' : '자동 채보 초안'}</p></div><ScoreViewer stem={stem} scale={scale} spacious={stem.score_layout?.preset === 'standard' ? spacious : true} measureNumbers={numbers && (stem.score_layout?.show_numbers ?? true)} />{stem.note_count === 0 && <p className="empty-notes">음표가 없는 악보예요. 직접 수정에서 음표를 넣거나 원본 음원을 확인해주세요.</p>}</> : <EmptyScore message={!canTranscribe ? selected === 'drums' && job.original_url ? '아래에서 원본 전체 음원을 선택하면 분리 없이 드럼 채보를 시험할 수 있어요.' : '악기 분리가 끝나면 악보를 만들 수 있어요.' : stem.score_status === 'running' ? '음정과 리듬을 분석하고 있어요. 잠시 기다려주세요.' : stem.score_error || '이 악기의 음원을 바탕으로 음정과 리듬을 옮겨보세요.'} />}
          </div>
          <div className="score-bottom"><div><label className="bpm-input">템포 <input aria-label="채보 템포 BPM" type="number" min="40" max="240" value={bpm} onChange={event => setBpm(Math.min(240, Math.max(40, Number(event.target.value))))} disabled={processing} /><span>BPM</span></label><label className="generation-meter">채보 박자<select aria-label="새 채보 박자" disabled={processing || busy} value={meter} onChange={e => setMeter(e.target.value)}>{meterOptions.map(m => <option key={m}>{m}</option>)}</select></label><span className="bpm-note">BPM은 4분음표 기준 · 변박은 직접 수정에서 지정</span></div><button className="primary small" disabled={processing || busy || !canTranscribe} onClick={() => void makeScore()}>{busy || stem.score_status === 'running' ? <LoaderCircle size={16} className="spin" /> : <FileMusic size={16} />}{stem.score_url ? '다시 채보하기' : `${stem.label} 악보 만들기`}<ArrowUpRight size={16} /></button></div></>}
          <AudioInputNotice info={job.audio_preprocessing} />
          <SeparationNotice job={job} />
          {stem.score_warning && <p className="score-warning">{stem.score_warning}</p>}
          {stem.score_error && <div className="inline-alert" role="alert"><AlertCircle size={18} /><div><strong>{stem.score_url ? '다시 채보하지 못했어요. 이전 악보는 그대로 보관되어 있어요.' : '악보를 만들지 못했어요.'}</strong><p>{stem.score_error}</p></div></div>}
          {!editing && stem.score_transcription?.raw_events === true && <p className="score-warning">
            <a className="text-button" href={`/api/jobs/${job.id}/files/${selected}.notes.json?download=true&v=${stem.score_revision || ''}`} aria-describedby="note-artifact-description">채보 당시 음표 데이터 (수정 전) <Download size={13} /></a>
            <br /><span id="note-artifact-description">박자 격자 정렬·첫 박 위치 보정 전의 음표 JSON입니다. 이후 악보 수정은 포함하지 않으며, 음표 강도는 정확도나 신뢰도가 아니에요.</span>
          </p>}
          {selected === 'drums' && !editing && <DrumTranscriptionControls jobId={job.id} revision={stem.score_revision} info={stem.score_transcription} health={health} demo={job.demo} disabled={processing || busy} hasStem={stem.status === 'ready'} engine={drumEngine} source={drumSource} onEngine={setDrumEngine} onSource={setDrumSource} />}
          {supportsPitchedReview(selected) && !editing && <PitchedTranscriptionControls jobId={job.id} instrument={selected} revision={stem.score_revision} info={stem.score_transcription} demo={job.demo} disabled={processing || busy} engine={pitchedEngine} onEngine={setPitchedEngine} />}
          {!editing && <div className="instrument-guidance"><strong>{selected === 'vocal' ? '보컬 · 멜로디와 가사' : selected === 'bass' ? '베이스 · 기본음과 TAB' : selected === 'guitar' ? '기타 · 화음과 TAB' : selected === 'drums' ? '드럼 · 동시 타격과 리듬' : '건반 · 오른손과 왼손'}</strong><p>{selected === 'vocal' ? '기본 채보는 주선율 한 음씩 추정해요. 겹친 보컬은 별도 확인이 필요하며 가사는 가사 타임라인에서 적용합니다.' : selected === 'bass' ? '기본 채보는 단선율입니다. 더블스톱·화음이 있는 연주는 화음 포함을 선택하고 TAB 운지를 확인해주세요.' : selected === 'guitar' ? '화음을 유지해 채보하며 TAB이 위에 나옵니다. 직접 수정에서 튜닝·카포를 실제 연주에 맞춰주세요.' : selected === 'drums' ? '킥과 하이햇처럼 함께 치는 소리를 별도로 추정합니다. 필인과 심벌은 음원을 들으며 확인해주세요.' : '새 채보는 높은음자리 + 낮은음자리 대보표가 기본입니다. 직접 수정에서 기존 악보도 대보표로 바꾸거나 신디 리드를 한 보표로 표시할 수 있어요.'}</p>{['vocal', 'bass'].includes(selected) && <label>채보 방식<select value={profile} disabled={busy || processing} onChange={e => setProfile(e.target.value as typeof profile)}><option value="instrument">{selected === 'vocal' ? '주선율 · 한 음씩' : '베이스 라인 · 한 음씩'}</option><option value="polyphonic">화음 포함 · 여러 음</option></select></label>}<small>재채보 전 템포와 마디 시작 위치를 확인해주세요. 리듬과 가사가 놓이는 마디 위치에 영향을 줍니다.</small></div>}
          {!stem.score_url && !editing && job.original_url && <label className="generation-meter">빈 악보 박자<select aria-label="빈 악보 박자" disabled={processing || busy} value={meter} onChange={e => setMeter(e.target.value)}>{meterOptions.map(m => <option key={m}>{m}</option>)}</select></label>}
          {!stem.score_url && !editing && job.original_url && <button className="secondary small blank-score-button" disabled={processing || busy} onClick={() => void emptyScore()}><Plus size={15} /> 빈 {stem.label} 악보 만들기 · 직접 입력</button>}
          {!!stem.score_tab_unassigned && stem.score_tab_mode !== 'staff' && <p className="score-warning" role="status">TAB 미배정 {stem.score_tab_unassigned}개 · 음역이나 줄 수를 벗어난 음은 TAB에서 제외됐어요. 오선 + TAB으로 확인하거나 운지를 수정해주세요. MIDI에는 원래 음정이 유지됩니다.</p>}
          {stem.quiet && <p className="score-warning">이 악기에서 검출된 소리가 매우 작아요. 실제로 해당 악기가 있는지 들어보세요.</p>}
          {!processing && !editing && <button className="text-button all-scores" disabled={busy || !job.stems.some(s => s.status === 'ready')} onClick={() => void makeScore(true)}>분리된 모든 악기 악보 만들기 <ArrowUpRight size={14} /></button>}
        </div>
      </div>
    </section>
  </div>;
}
