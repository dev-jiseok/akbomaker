import { useEffect, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, LoaderCircle, Mic2, Play, Plus, Save, Trash2 } from 'lucide-react';
import { request } from '../api';
import { isProcessing, type Health, type Job, type LyricCue, type ScoreDocument } from '../types';

type Props = { job: Job; health: Health | null; editing: boolean; onJob: (job: Job) => void; onError: (message: string) => void; onDirty: (dirty: boolean) => void };
const key = (cues: LyricCue[]) => JSON.stringify(cues);

export default function LyricsWorkbench({ job, health, editing, onJob, onError, onDirty }: Props) {
  const server = job.lyric_candidate;
  const [revision, setRevision] = useState(server?.revision || '');
  const [cues, setCues] = useState<LyricCue[]>(server?.cues || []);
  const [baseline, setBaseline] = useState(key(server?.cues || []));
  const [source, setSource] = useState<'original' | 'vocal'>(job.stems.some(s => s.id === 'vocal' && s.audio_url) ? 'vocal' : 'original');
  const [language, setLanguage] = useState('auto');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [page, setPage] = useState(0);
  const [expanded, setExpanded] = useState(!!job.analysis_only || !!server);
  const [draft, setDraft] = useState<{ revision: string; cues: LyricCue[] } | null>(null);
  const audio = useRef<HTMLAudioElement>(null);
  const lock = useRef(false);
  const dirty = key(cues) !== baseline;
  const processing = isProcessing(job);
  const storageKey = `akbo-lyric-draft:${job.id}`;
  const audioUrl = source === 'vocal' ? job.stems.find(s => s.id === 'vocal')?.audio_url : job.original_url;

  useEffect(() => {
    if ((server?.revision || '') === revision || dirty) return;
    setRevision(server?.revision || ''); setCues(server?.cues || []); setBaseline(key(server?.cues || [])); setPage(0);
  }, [server, revision, dirty]);
  useEffect(() => {
    try {
      const stored = JSON.parse(localStorage.getItem(storageKey) || 'null');
      if (stored && Array.isArray(stored.cues) && typeof stored.revision === 'string' && key(stored.cues) !== baseline) setDraft(stored);
    } catch { /* Storage is optional. */ }
    // The component is keyed by project; read the draft only when mounting.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storageKey]);
  useEffect(() => { onDirty(dirty); return () => onDirty(false); }, [dirty, onDirty]);
  useEffect(() => {
    if (!dirty) return;
    const prevent = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener('beforeunload', prevent);
    try { localStorage.setItem(storageKey, JSON.stringify({ revision, cues })); } catch { /* optional */ }
    return () => window.removeEventListener('beforeunload', prevent);
  }, [dirty, cues, revision, storageKey]);

  function update(id: string, patch: Partial<LyricCue>) { setCues(cues.map(c => c.id === id ? { ...c, ...patch } : c)); }
  async function run(action: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setNotice('');
    try { await action(); } catch (error) { onError((error as Error).message); }
    finally { lock.current = false; setBusy(false); }
  }
  async function recognize() {
    if (dirty) return onError('가사 편집 내용을 먼저 저장해주세요.');
    if (server && !window.confirm('현재 가사 초안을 새 인식 결과로 대체할까요? 이미 악보에 적용한 가사는 변경하지 않습니다.')) return;
    await run(async () => { onJob(await request<Job>(`/api/jobs/${job.id}/lyrics/recognize`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ source, language, replace_candidate: !!server }) })); });
  }
  async function save() {
    if (cues.some(c => !c.text.trim() || !Number.isFinite(c.start) || !Number.isFinite(c.end) || c.start < 0 || c.end <= c.start || c.end > (job.duration || 0) + .001)) return onError('가사 내용과 시간을 확인해주세요. 시작 < 끝이어야 하며 원본 음원 길이 안에 있어야 해요.');
    await run(async () => {
      const updated = await request<Job>(`/api/jobs/${job.id}/lyrics/candidate`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ base_revision: revision, cues }) });
      setRevision(updated.lyric_candidate!.revision); setCues(updated.lyric_candidate!.cues); setBaseline(key(updated.lyric_candidate!.cues)); setDraft(null);
      try { localStorage.removeItem(storageKey); } catch { /* optional */ }
      onJob(updated); setNotice('가사 초안을 저장했어요. 확인 후 전체 파트에 적용해주세요.');
    });
  }
  async function apply() {
    if (!server || dirty || editing) return;
    if (!window.confirm('모든 생성된 악보의 기존 가사를 이 초안으로 대체할까요? 음표·TAB·메모는 유지합니다. 이후 만드는 악보에도 이 가사를 배치합니다. 다른 탭에서 편집 중인 악보는 먼저 저장해주세요.')) return;
    await run(async () => {
      const targets = job.stems.filter(s => s.score_url && s.score_status === 'ready');
      const revisions = await Promise.all(targets.map(async s => [s.id, (await request<ScoreDocument>(`/api/jobs/${job.id}/scores/${s.id}`)).revision]));
      const result = await request<{ job: Job; applied: number; skipped: number }>(`/api/jobs/${job.id}/lyrics/apply`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ base_revision: server.revision, score_revisions: Object.fromEntries(revisions) }) });
      onJob(result.job); setNotice(`악보 ${result.applied}개에 적용했어요. 이후 생성할 파트도 같은 가사를 사용해요.${result.skipped ? ` 시작 오프셋·곡 범위 밖 항목 ${result.skipped}건은 제외했어요(파트별 합계).` : ''}`);
    });
  }
  const currentPage = Math.min(page, Math.max(0, Math.ceil(cues.length / 40) - 1));
  const applied = job.lyric_guide?.revision === server?.revision && !!server;
  return <section className="lyrics-workbench" aria-label="전체 파트 가사 타임라인">
    <div className="analysis-heading"><div><span className="eyebrow">VOCAL CUES FOR THE BAND</span><h2><Mic2 size={20} /> 가사 타임라인 {dirty && <small>저장 전 변경사항</small>}</h2><p>원본 음원의 시간에 맞춰 확인한 뒤, 기타·키보드·베이스·드럼 등 모든 파트에 배치하세요.</p></div><button className="secondary small" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? '가사 접기' : '가사 인식 · 편집 열기'}</button></div>
    <div hidden={!expanded}>
    <div className="analysis-controls"><label>인식할 음원<select value={source} disabled={busy || processing} onChange={e => setSource(e.target.value as 'original' | 'vocal')}><option value="original">원본 음원</option><option value="vocal" disabled={!job.stems.find(s => s.id === 'vocal')?.audio_url}>분리된 보컬</option></select></label><label>노래 언어<select value={language} disabled={busy || processing} onChange={e => setLanguage(e.target.value)}><option value="auto">자동 감지</option><option value="ko">한국어</option><option value="en">영어</option><option value="ja">일본어</option><option value="zh">중국어</option></select></label><button className="secondary small" disabled={busy || processing || dirty || !health?.lyrics?.available || job.demo || !audioUrl} onClick={() => void recognize()}>{busy || (processing && job.stage === 'lyrics') ? <LoaderCircle size={15} className="spin" /> : <Mic2 size={15} />} 가사 자동 인식</button></div>
    <p className="editor-help">노래 인식은 오탈자·누락·시간 오차가 있을 수 있는 초안입니다. 반주가 섞인 원본보다 보컬 음원이 유리해요. 무보컬 구간에는 잘못 인식된 가사를 지워주세요. 첫 실행은 모델 다운로드가 필요하며 음원은 외부로 보내지 않습니다.{job.demo ? ' 이 샘플에는 실제 가사가 없어 자동 인식을 하지 않습니다. 수동 입력은 가능해요.' : !health?.lyrics?.available ? ' 서버에 requirements-asr.txt 패키지를 설치해주세요.' : ''}</p>
    {audioUrl && <audio controls preload="metadata" src={audioUrl} ref={audio} aria-label="가사 위치 확인용 음원" />}
    {draft && <div className="draft-banner"><span>저장 전 가사 편집본이 이 브라우저에 있어요.</span><button className="text-button" disabled={draft.revision !== revision} onClick={() => { setCues(draft.cues); setDraft(null); }}>복원</button>{draft.revision !== revision && <span>서버 버전이 달라 복원할 수 없어요.</span>}<button className="text-button" onClick={() => { setDraft(null); try { localStorage.removeItem(storageKey); } catch { /* optional */ } }}>무시</button></div>}
    {!!server?.warning && <p className="score-warning">{server.warning}</p>}
    {dirty && (server?.revision || '') !== revision && <p className="editor-error">다른 화면에서 초안이 변경됐어요. 현재 편집본은 임시 보관되며 서버의 최신 초안을 덮어쓸 수 없습니다.</p>}
    <fieldset className="cue-fieldset" disabled={busy || processing}>
      <div className="cue-heading"><span>{cues.length}개 · 시간 단위: 초 (원본 기준)</span><button className="text-button" disabled={cues.length >= 2000 || !job.duration} onClick={() => { const start = Math.min(audio.current?.currentTime || 0, Math.max(0, (job.duration || 1) - .2)); setCues([...cues, { id: crypto.randomUUID(), start: Number(start.toFixed(3)), end: Number(Math.min(job.duration || 1, start + .2).toFixed(3)), text: '' }]); setPage(Math.floor(cues.length / 40)); }}><Plus size={14} /> 현재 재생 위치에 가사 추가</button></div>
      {cues.slice(currentPage * 40, currentPage * 40 + 40).map(cue => <div className="cue-row" key={cue.id}><button type="button" className="icon-button" aria-label={`${cue.text || '가사'} 위치 듣기`} onClick={() => { if (audio.current) { audio.current.currentTime = cue.start; void audio.current.play().catch(() => onError('음원 재생 버튼을 눌러주세요.')); } }}><Play size={14} /></button><label>시작<input type="number" step="0.01" min={0} max={job.duration || 0} value={cue.start} onChange={e => update(cue.id, { start: Number(e.target.value) })} /></label><label>끝<input type="number" step="0.01" min={0} max={job.duration || 0} value={cue.end} onChange={e => update(cue.id, { end: Number(e.target.value) })} /></label><label className="cue-text">가사<input value={cue.text} maxLength={80} placeholder="단어 · 짧은 가사 · 보컬 진입" onChange={e => update(cue.id, { text: e.target.value })} /></label><button type="button" className="icon-button" aria-label="타임라인 가사 삭제" onClick={() => setCues(cues.filter(c => c.id !== cue.id))}><Trash2 size={15} /></button></div>)}
      {!cues.length && <p className="no-notes">자동 인식을 실행하거나, 직접 가사와 보컬 진입 시간을 넣어주세요.</p>}
      {cues.length > 40 && <div className="cue-pagination"><button className="icon-button" disabled={!currentPage} onClick={() => setPage(currentPage - 1)} aria-label="이전 가사 페이지"><ChevronLeft size={16} /></button>{currentPage + 1} / {Math.ceil(cues.length / 40)}<button className="icon-button" disabled={(currentPage + 1) * 40 >= cues.length} onClick={() => setPage(currentPage + 1)} aria-label="다음 가사 페이지"><ChevronRight size={16} /></button></div>}
    </fieldset>
    <div className="analysis-actions"><span>{dirty ? '저장 전 변경사항' : applied ? '전체 파트에 적용한 초안' : server ? '확인할 가사 초안' : '아직 저장한 가사 없음'}</span><button className="secondary small" disabled={busy || processing || !dirty} onClick={() => void save()}><Save size={14} /> 초안 저장</button><button className="primary small" disabled={busy || processing || dirty || !server || editing} onClick={() => void apply()}>전체 파트에 적용</button></div>
    {editing && <p className="editor-help">전체 파트에 적용하려면 현재 악보 편집기를 먼저 저장하고 닫아주세요.</p>}
    {notice && <p className="editor-success" role="status">{notice}</p>}
    <p className="editor-help">적용은 기존 가사만 대체합니다. 파트별로 따로 수정한 가사는 자동 동기화되지 않으며, 전체 적용을 다시 하면 공통 초안으로 바뀝니다.</p>
    </div>
  </section>;
}
