import { useEffect, useState } from 'react';
import { ArrowLeft, ArrowUpRight, Check, Download, FileMusic, LoaderCircle, Plus, Printer, SlidersHorizontal, X, AlertCircle } from 'lucide-react';
import { request } from '../api';
import { formatTime, isProcessing, type Instrument, type Job } from '../types';
import Mixer, { instrumentIcons } from './Mixer';
import ScoreViewer, { EmptyScore } from './ScoreViewer';

type Props = { job: Job; onJob: (job: Job) => void; onNew: () => void; onError: (message: string) => void };

export default function Workspace({ job, onJob, onNew, onError }: Props) {
  const [selected, setSelected] = useState<Instrument>('piano');
  const [scale, setScale] = useState(1.1);
  const [spacious, setSpacious] = useState(true);
  const [numbers, setNumbers] = useState(true);
  const [bpm, setBpm] = useState(job.bpm || 120);
  const [busy, setBusy] = useState(false);
  const [settings, setSettings] = useState(false);
  const processing = isProcessing(job);
  const stem = job.stems.find(stem => stem.id === selected)!;
  useEffect(() => { if (stem.score_bpm || job.bpm) setBpm(stem.score_bpm || job.bpm!); }, [selected, stem.score_bpm, job.bpm]);

  async function makeScore(all = false) {
    setBusy(true);
    try {
      const updated = await request<Job>(`/api/jobs/${job.id}/transcribe`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ instruments: all ? job.stems.filter(s => s.status === 'ready').map(s => s.id) : [selected], bpm }) });
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
  return <div className="workspace-view">
    <div className="workspace-top"><button className="text-button" onClick={onNew}><ArrowLeft size={16} /> 작업실 홈</button><button className="secondary small" onClick={onNew}><Plus size={15} /> 새 음악 가져오기</button></div>
    <div className="project-heading"><div><div className="project-label"><span className="eyebrow">YOUR MUSIC PROJECT</span>{job.demo && <span className="sample-tag">샘플 프로젝트</span>}</div><h1>{job.title}</h1><p>{job.source_type === 'youtube' ? 'YouTube' : job.demo ? '직접 합성한 샘플 음악' : '업로드한 음악'} <i /> {formatTime(job.duration)} <i /> {job.stems.filter(s => s.status === 'ready').length}개의 악기</p></div>{!processing && job.stems.some(s => s.audio_url) && <a className="secondary" href={`/api/jobs/${job.id}/archive`}><Download size={16} /> 전체 파일 받기</a>}</div>
    {job.demo && <div className="demo-notice"><span>DEMO</span><p>작업 흐름을 체험하는 샘플이에요. 직접 합성한 음원과 악보이며 SAM Audio로 분리한 결과는 아니에요.</p></div>}
    {processing && <div className="progress-card" aria-live="polite"><div className="progress-title"><span><LoaderCircle size={20} className="spin" /><strong>{job.message}</strong></span><button className="text-button" onClick={() => void cancel()} disabled={busy}>중단 <X size={14} /></button></div><div className="progress-track"><div style={{ width: `${job.progress}%` }} /></div><div className="progress-meta"><span>{job.status === 'transcribing' ? '분리된 음원을 바탕으로 채보합니다' : '완료된 악기는 먼저 들어볼 수 있어요'}</span><span>{job.progress}%</span></div></div>}
    {job.error && <div className="inline-alert" role="alert"><AlertCircle size={18} /><div><strong>작업 중 문제가 생겼어요</strong><p>{job.error}</p></div></div>}
    {job.status === 'cancelled' && <div className="demo-notice"><span>중단됨</span><p>{job.message}</p></div>}
    <Mixer key={job.id} job={job} selected={selected} onSelect={setSelected} onError={onError} />
    <section className="score-section">
      <div className="score-section-heading"><div><span className="eyebrow">MAKE IT YOURS</span><h2>이제, 나에게 편한 악보로.</h2><p>보고 싶은 악기를 선택하고, 읽기 좋은 크기로 맞춰보세요.</p></div><button className={`secondary small ${settings ? 'active' : ''}`} onClick={() => setSettings(!settings)} aria-expanded={settings}><SlidersHorizontal size={16} /> 악보 설정</button></div>
      <div className="score-layout">
        <aside className="score-parts"><span className="parts-label">악기 선택</span>{job.stems.map(part => { const Icon = instrumentIcons[part.id]; return <button key={part.id} className={`part-button tone-${part.id} ${part.id === selected ? 'active' : ''}`} onClick={() => setSelected(part.id)} aria-pressed={part.id === selected}><Icon size={17} /><span>{part.label}</span>{part.score_status === 'ready' ? <Check size={14} /> : part.score_status === 'running' ? <LoaderCircle className="spin" size={14} /> : <span className="part-dot" />}</button>; })}<div className="parts-note"><FileMusic size={20} /><p>자동 채보는 초안이에요.<br />소리와 악보를 함께<br />확인해보세요.</p></div></aside>
        <div className="score-main">
          {settings && <div className="score-settings"><label>음표 크기 <select value={scale} onChange={event => setScale(Number(event.target.value))}><option value="0.85">작게</option><option value="1.1">편하게</option><option value="1.4">크게</option></select></label><label>보표 간격 <select value={String(spacious)} onChange={event => setSpacious(event.target.value === 'true')}><option value="true">여유롭게</option><option value="false">촘촘하게</option></select></label><label className="check-label"><input type="checkbox" checked={numbers} onChange={event => setNumbers(event.target.checked)} />마디 번호</label></div>}
          <div className="score-toolbar"><span><FileMusic size={16} /><strong>{stem.label} 악보</strong>{stem.score_status === 'ready' && <small>4/4 · ♩ {stem.score_bpm || job.bpm || bpm}</small>}</span><div>{stem.score_url && <><a className="text-button" href={stem.midi_url + '?download=true'}>MIDI <Download size={13} /></a><a className="text-button" href={stem.score_url + '?download=true'}>MusicXML <Download size={13} /></a><button className="icon-button" onClick={() => window.print()} aria-label="악보 인쇄 또는 PDF 저장"><Printer size={16} /></button></>}</div></div>
          <div className={`score-paper ${spacious ? 'spacious' : ''}`} id="print-score">
            {stem.score_url ? <><div className="paper-heading"><span>AKBO MAKER</span><h3>{job.title}</h3><p>{stem.label} · {stem.score_bpm || job.bpm || bpm} BPM · {job.demo ? '샘플 악보' : '자동 채보 초안'}</p></div><ScoreViewer stem={stem} scale={scale} spacious={spacious} measureNumbers={numbers} />{stem.note_count === 0 && <p className="empty-notes">뚜렷한 음표가 검출되지 않아 쉼표로 표시했어요. 원본 음원을 확인해주세요.</p>}</> : <EmptyScore message={stem.status !== 'ready' ? '악기 분리가 끝나면 악보를 만들 수 있어요.' : stem.score_status === 'running' ? '음정과 리듬을 분석하고 있어요. 잠시 기다려주세요.' : stem.score_error || '이 악기의 음원을 바탕으로 음정과 리듬을 옮겨보세요.'} />}
          </div>
          <div className="score-bottom"><div><label className="bpm-input">템포 <input aria-label="채보 템포 BPM" type="number" min="40" max="240" value={bpm} onChange={event => setBpm(Math.min(240, Math.max(40, Number(event.target.value))))} disabled={processing} /><span>BPM</span></label><span className="bpm-note">곡의 템포에 맞춰주세요 · 4/4 기준</span></div><button className="primary small" disabled={processing || busy || stem.status !== 'ready'} onClick={() => void makeScore()}>{busy || stem.score_status === 'running' ? <LoaderCircle size={16} className="spin" /> : <FileMusic size={16} />}{stem.score_url ? '다시 채보하기' : `${stem.label} 악보 만들기`}<ArrowUpRight size={16} /></button></div>
          {stem.score_warning && <p className="score-warning">{stem.score_warning}</p>}
          {stem.quiet && <p className="score-warning">이 악기에서 검출된 소리가 매우 작아요. 실제로 해당 악기가 있는지 들어보세요.</p>}
          {!processing && <button className="text-button all-scores" disabled={busy || !job.stems.some(s => s.status === 'ready')} onClick={() => void makeScore(true)}>분리된 모든 악기 악보 만들기 <ArrowUpRight size={14} /></button>}
        </div>
      </div>
    </section>
  </div>;
}
