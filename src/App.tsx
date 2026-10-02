import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowDownToLine, ArrowRight, ArrowUpRight, AudioLines, Check, ChevronRight, CircleHelp, Clock3, FileAudio2, FileMusic, FolderHeart, Headphones, LayoutDashboard, Link2, LoaderCircle, Menu, Music2, Plus, Radio, ShieldCheck, Sparkles, UploadCloud, X, Youtube, AlertCircle } from 'lucide-react';
import { recentProjects, rememberProject, request, upload, type RecentProject } from './api';
import { formatTime, instruments, isProcessing, isYoutubeUrl, validateFile, type Health, type Job } from './types';
import { StudioArtwork } from './components/Artwork';
import { instrumentIcons } from './components/Mixer';
import Workspace from './components/Workspace';
import ScoreImport from './components/ScoreImport';

type View = 'home' | 'projects' | 'guide';
const names = ['보컬', '베이스', '드럼', '신디사이저', '기타', '피아노'];

export default function App() {
  const [view, setView] = useState<View>('home');
  const [job, setJob] = useState<Job | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [checking, setChecking] = useState(true);
  const [source, setSource] = useState<'file' | 'youtube'>('file');
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState('');
  const [url, setUrl] = useState('');
  const [analysisOnly, setAnalysisOnly] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [formError, setFormError] = useState('');
  const [toast, setToast] = useState('');
  const [engineModal, setEngineModal] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [projects, setProjects] = useState<RecentProject[]>(recentProjects);
  const [pollError, setPollError] = useState(false);
  const [scoreDirty, setScoreDirty] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const abortUpload = useRef<AbortController | null>(null);
  const uploadBusy = useRef(false);
  const openBusy = useRef(false);
  const acceptJob = useCallback((next: Job) => {
    setJob(next);
    rememberProject(next);
    setProjects(recentProjects());
    setView('home');
    history.replaceState(null, '', `#project=${next.id}`);
  }, []);

  const checkHealth = useCallback(async () => {
    setChecking(true);
    try { setHealth(await request<Health>('/api/health')); }
    catch { setHealth(null); }
    finally { setChecking(false); }
  }, []);
  useEffect(() => { void checkHealth(); const timer = setInterval(() => void checkHealth(), 30_000); return () => clearInterval(timer); }, [checkHealth]);
  useEffect(() => {
    let controller: AbortController | undefined;
    function restore() {
      controller?.abort();
      const expected = location.hash;
      const id = expected.match(/^#project=([a-f0-9]{32})$/)?.[1];
      if (!id) { setJob(null); return; }
      controller = new AbortController();
      request<Job>(`/api/jobs/${id}`, { signal: controller.signal }).then(next => {
        if (location.hash === expected) acceptJob(next);
      }).catch(error => { if (error.name !== 'AbortError') setToast(error.message); });
    }
    restore();
    window.addEventListener('hashchange', restore);
    return () => { controller?.abort(); window.removeEventListener('hashchange', restore); };
  }, [acceptJob]);
  useEffect(() => {
    if (!job || !isProcessing(job)) return;
    let alive = true;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const id = job.id;
    async function poll() {
      try {
        const next = await request<Job>(`/api/jobs/${id}`, { signal: controller.signal });
        if (!alive) return;
        setPollError(false);
        acceptJob(next);
        if (isProcessing(next)) timer = setTimeout(poll, 1200);
      } catch {
        if (alive) { setPollError(true); timer = setTimeout(poll, 4000); }
      }
    }
    timer = setTimeout(poll, 600);
    return () => { alive = false; controller.abort(); clearTimeout(timer); };
  }, [job?.id, job?.status, acceptJob]);
  useEffect(() => {
    if (!file) { setPreview(''); return; }
    const value = URL.createObjectURL(file);
    setPreview(value);
    return () => URL.revokeObjectURL(value);
  }, [file]);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(''), 9000);
    return () => clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    if (!engineModal) return;
    const handler = (event: KeyboardEvent) => { if (event.key === 'Escape') setEngineModal(false); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [engineModal]);

  function navigate(next: View) {
    if (uploadBusy.current || !canLeaveScore()) return;
    setView(next);
    setJob(null);
    setPollError(false);
    setSidebarOpen(false);
    history.replaceState(null, '', location.pathname);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
  function canLeaveScore() { return !scoreDirty || window.confirm('저장하지 않은 악보·가사 변경사항이 있어요. 이 화면을 나갈까요? 임시 편집본은 이 브라우저에 보관됩니다.'); }
  function chooseFile(value: File | undefined) {
    if (!value) return;
    const error = validateFile(value, health?.limits.max_upload_mb || 200);
    if (error) { setFormError(error); return; }
    setFile(value);
    setFormError('');
  }
  async function start() {
    if (uploadBusy.current) return;
    if (source === 'file' && !file) { setFormError('먼저 음악 파일을 선택해주세요.'); return; }
    if (source === 'youtube' && !isYoutubeUrl(url)) { setFormError('개별 유튜브 영상의 https 링크를 입력해주세요.'); return; }
    uploadBusy.current = true;
    setLoading(true);
    setFormError('');
    setUploadProgress(0);
    const controller = new AbortController();
    abortUpload.current = controller;
    try {
      const data = new FormData();
      data.append('analysis_only', String(analysisOnly));
      if (source === 'file') data.append('file', file!);
      else data.append('url', url.trim());
      const next = await upload(data, setUploadProgress, controller.signal);
      acceptJob(next);
      setFile(null);
      setUrl('');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (error) { if ((error as Error).name !== 'AbortError') setFormError((error as Error).message); }
    finally { uploadBusy.current = false; setLoading(false); setUploadProgress(null); abortUpload.current = null; }
  }
  async function demo() {
    if (uploadBusy.current || openBusy.current || !canLeaveScore()) return;
    openBusy.current = true;
    setLoading(true);
    try { acceptJob(await request<Job>('/api/demo', { method: 'POST' })); window.scrollTo({ top: 0, behavior: 'smooth' }); }
    catch (error) { setToast((error as Error).message); }
    finally { openBusy.current = false; setLoading(false); }
  }
  async function openProject(id: string) {
    if (openBusy.current || uploadBusy.current || !canLeaveScore()) return;
    openBusy.current = true;
    setLoading(true);
    try { acceptJob(await request<Job>(`/api/jobs/${id}`)); window.scrollTo({ top: 0, behavior: 'smooth' }); }
    catch (error) { setToast((error as Error).message); }
    finally { openBusy.current = false; setLoading(false); }
  }
  const engineReady = !!health?.engine.available;
  const recent = projects.slice(0, 3);
  const heading = view === 'projects' ? '내 프로젝트' : view === 'guide' ? '이용 안내' : '음악 작업실';

  return <div className="app-shell">
    {sidebarOpen && <button className="sidebar-backdrop" aria-label="메뉴 닫기" onClick={() => setSidebarOpen(false)} />}
    <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`}>
      <button className="brand" onClick={() => navigate('home')}><span className="brand-symbol"><Music2 size={23} strokeWidth={1.8} /></span><span>악보 메이커<small>AKBO MAKER</small></span></button>
      <div className="sidebar-section-label">MY LITTLE STUDIO</div>
      <nav aria-label="주 메뉴"><button className={view === 'home' ? 'nav-item active' : 'nav-item'} onClick={() => navigate('home')}><LayoutDashboard size={19} /> 음악 작업실 <span className="nav-active-dot" /></button><button className={view === 'projects' ? 'nav-item active' : 'nav-item'} onClick={() => navigate('projects')}><FolderHeart size={19} /> 내 프로젝트 {projects.length > 0 && <span className="nav-count">{projects.length}</span>}</button><button className={view === 'guide' ? 'nav-item active' : 'nav-item'} onClick={() => navigate('guide')}><CircleHelp size={19} /> 이용 안내</button></nav>
      <div className="sidebar-line" />
      <div className="sidebar-section-label">RECENT PROJECTS</div>
      <div className="sidebar-recents">{recent.length ? recent.map(project => <button key={project.id} onClick={() => void openProject(project.id)} disabled={loading}><span className="recent-icon"><Music2 size={15} /></span><span>{project.title}<small>{project.demo ? '샘플 프로젝트' : '음악 프로젝트'}</small></span></button>) : <p>첫 번째 음악을 가져오면<br />여기에 차곡차곡 쌓여요.</p>}</div>
      <div className="sidebar-spacer" />
      <div className="sidebar-note"><Sparkles size={20} /><h3>악보에도, 내 취향을.</h3><p>듣는 음악부터 보는 악보까지<br />나만의 방식으로 만들어봐요.</p><button onClick={() => void demo()} disabled={loading}>샘플로 시작해보기 <ArrowUpRight size={15} /></button></div>
      <div className="sidebar-bottom"><span className="local-avatar">나</span><span>나의 음악 공간<small>이 브라우저의 작업실</small></span><span className="local-dot" /></div>
    </aside>
    <div className="main-shell">
      <header className="topbar"><div><button className="icon-button mobile-menu" aria-label="메뉴 열기" onClick={() => setSidebarOpen(true)}><Menu size={21} /></button><span className="topbar-studio">Workspace</span><ChevronRight size={13} /><span>{heading}</span></div><div><button className={`engine-badge ${engineReady ? 'ready' : ''}`} onClick={() => setEngineModal(true)}><i />{checking && !health ? '연결 확인 중' : engineReady ? 'SAM Audio 연결됨' : health ? '샘플 체험 가능' : '서버 연결 필요'}<ChevronRight size={12} /></button><button className="help-button icon-button" aria-label="이용 안내" onClick={() => navigate('guide')}><CircleHelp size={19} /></button></div></header>
      <main>
        {pollError && <div className="inline-alert" role="status"><Radio size={18} /><span>서버 연결이 잠시 끊겼어요. 자동으로 다시 연결하고 있어요.</span></div>}
        {job ? <Workspace key={job.id} job={job} health={health} onJob={acceptJob} onNew={() => navigate('home')} onError={setToast} onEditorDirty={setScoreDirty} /> : view === 'projects' ? <Projects projects={projects} loading={loading} onOpen={openProject} onNew={() => navigate('home')} /> : view === 'guide' ? <Guide onDemo={demo} loading={loading} /> : <>
          <section className="hero"><div className="hero-copy"><div className="hero-eyebrow"><span /> YOUR MUSIC, YOUR WAY</div><h1>좋아하는 음악을,<br /><span>나만의 악보로.</span><span className="title-star">✳</span></h1><p>음악 속 악기를 하나씩 꺼내고,<br />내가 읽기 편한 악보로 만들어보세요.</p><button className="hero-demo" onClick={() => void demo()} disabled={loading}>{loading && uploadProgress === null ? <LoaderCircle size={15} className="spin" /> : <Headphones size={15} />} 샘플 작업실 둘러보기 <ArrowRight size={15} /></button><div className="hero-footnote">조금 더 자유롭게, 조금 더 나답게.</div></div><StudioArtwork /></section>
          <div className="creation-layout"><section className="card import-card"><div className="panel-heading"><div><span className="eyebrow">LET'S GET STARTED</span><h2>어떤 음악을 가져올까요?</h2></div><span className="step-label">STEP 01</span></div>
            <div className="source-tabs" role="tablist" aria-label="음악 가져오기 방식"><button role="tab" aria-selected={source === 'file'} aria-controls="source-panel" id="file-tab" className={source === 'file' ? 'active' : ''} onClick={() => { setSource('file'); setFormError(''); }} disabled={loading}><UploadCloud size={16} /> 파일 업로드</button><button role="tab" aria-selected={source === 'youtube'} aria-controls="source-panel" id="youtube-tab" className={source === 'youtube' ? 'active' : ''} onClick={() => { setSource('youtube'); setFormError(''); }} disabled={loading}><Youtube size={17} /> YouTube 링크</button></div>
            <div id="source-panel" role="tabpanel" aria-labelledby={source === 'file' ? 'file-tab' : 'youtube-tab'}>
              {source === 'file' ? <div className={`dropzone ${dragging ? 'dragging' : ''} ${file ? 'has-file' : ''}`} onDragOver={event => { event.preventDefault(); if (!loading) setDragging(true); }} onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragging(false); }} onDrop={event => { event.preventDefault(); setDragging(false); if (!loading) { if (event.dataTransfer.files.length > 1) setFormError('음악 파일은 한 번에 하나씩 업로드해주세요.'); else chooseFile(event.dataTransfer.files[0]); } }}>
                <input ref={fileInput} type="file" className="visually-hidden" aria-label="음악 파일 선택" accept=".mp3,.wav,.flac,.m4a,.aac,.ogg,.opus,.mp4,.mov,.webm,.mkv,.aiff,.aif" disabled={loading} onChange={event => { chooseFile(event.target.files?.[0]); event.target.value = ''; }} />
                {file ? <div className="selected-file"><span className="upload-symbol"><FileAudio2 size={29} strokeWidth={1.5} /></span><div className="file-info"><h3>{file.name}</h3><p>{(file.size / 1024 / 1024).toFixed(1)} MB · 업로드 준비 완료</p></div><button className="icon-button" aria-label="선택한 파일 지우기" disabled={loading} onClick={() => setFile(null)}><X size={18} /></button>{preview && <audio className="file-preview" src={preview} controls preload="metadata" aria-label="업로드 전 음악 미리듣기" onLoadedMetadata={event => { if (event.currentTarget.duration > (health?.limits.max_audio_seconds || 600)) setFormError(`최대 ${(health?.limits.max_audio_seconds || 600) / 60}분 길이의 음악을 선택해주세요.`); }} />}<button className="text-button" disabled={loading} onClick={() => fileInput.current?.click()}>다른 파일 선택</button></div> : <button className="dropzone-button" disabled={loading} onClick={() => fileInput.current?.click()}><span className="upload-symbol"><UploadCloud size={29} strokeWidth={1.5} /><span className="upload-plus"><Plus size={11} strokeWidth={2.5} /></span></span><h3>음악 파일을 여기에 놓아주세요</h3><p>또는 <span>파일 선택하기</span></p><small>MP3, WAV, FLAC, M4A, MP4 등<br />최대 {health?.limits.max_upload_mb || 200}MB · {(health?.limits.max_audio_seconds || 600) / 60}분 이내</small></button>}
              </div> : <div className="youtube-panel"><span className="youtube-symbol"><Youtube size={30} strokeWidth={1.4} /></span><h3>좋아하는 음악의 링크를 붙여넣어주세요</h3><p>{analysisOnly ? '영상에서 음악을 가져와 원본을 분석해요.' : '영상에서 음악을 가져와 악기별로 분리해요.'}</p><label htmlFor="youtube-url" className="visually-hidden">유튜브 영상 링크</label><div className="url-input"><Link2 size={17} /><input id="youtube-url" type="url" inputMode="url" value={url} disabled={loading} onChange={event => { setUrl(event.target.value); setFormError(''); }} placeholder="https://www.youtube.com/watch?v=…" onKeyDown={event => { if (event.key === 'Enter' && health && (engineReady || analysisOnly)) void start(); }} />{url && <button className="icon-button" aria-label="링크 지우기" disabled={loading} onClick={() => setUrl('')}><X size={15} /></button>}</div><small>개별 영상 링크를 지원해요 · 재생목록과 라이브 제외</small></div>}
            </div>
            {formError && <div className="form-error" role="alert"><AlertCircle size={15} />{formError}</div>}
            <div className="instrument-label"><span>여섯 가지 소리를 차례로 분리해요</span><span>SAM Audio</span></div><div className="instrument-chips">{instruments.map((inst, i) => { const Icon = instrumentIcons[inst]; return <span key={inst} className={`instrument-chip tone-${inst}`}><Icon size={14} />{names[i]}</span>; })}</div>
            {uploadProgress !== null && <div className="upload-progress" aria-live="polite"><div><span>{uploadProgress === 100 ? '서버에서 업로드를 확인하고 있어요' : `음악을 가져오는 중 · ${uploadProgress}%`}</span><button className="text-button" onClick={() => abortUpload.current?.abort()}>취소</button></div><div className="progress-track"><div style={{ width: `${uploadProgress}%` }} /></div></div>}
            <label className="analysis-mode check-label"><input type="checkbox" checked={analysisOnly} disabled={loading} onChange={e => setAnalysisOnly(e.target.checked)} /><span>분리 없이 원본만 분석 <small>GPU 없이 BPM·가사 인식·직접 악보 입력</small></span></label>
            <button className="primary start-button" disabled={loading || !health || (!engineReady && !analysisOnly) || (source === 'file' ? !file : !url.trim())} onClick={() => void start()}>{loading && uploadProgress !== null ? <LoaderCircle className="spin" size={18} /> : <AudioLines size={18} />}{analysisOnly ? '원본 가져와서 분석하기' : '악기 분리 시작'}<ArrowRight size={17} /></button>
            {!engineReady && !checking && <p className="engine-hint">{health ? '실제 음악 분리는 SAM Audio 서버가 준비되면 사용할 수 있어요.' : '음악 처리 서버에 연결하면 사용할 수 있어요.'}<button onClick={() => setEngineModal(true)}>연결 상태 <ArrowUpRight size={12} /></button></p>}
            <div className="upload-footnote"><ShieldCheck size={13} /><span>직접 제작했거나 사용할 권한이 있는 음악을 가져와주세요.</span></div>
          </section><aside className="workflow-card"><span className="eyebrow">FROM SOUND TO SHEET</span><h2>음악이 악보가 되는 순간</h2><p className="workflow-intro">복잡한 과정은 덜고,<br />음악에 더 가까이.</p><div className="workflow-steps"><div><span className="workflow-icon"><UploadCloud size={20} /></span><div><small>01 · BRING YOUR MUSIC</small><h3>음악 가져오기</h3><p>파일이나 YouTube 링크 하나면 충분해요.</p></div></div><div><span className="workflow-icon"><AudioLines size={20} /></span><div><small>02 · FIND EACH SOUND</small><h3>악기별로 나누기</h3><p>여섯 악기를 분리하고 따로 들어보세요.</p></div></div><div><span className="workflow-icon"><FileMusic size={20} /></span><div><small>03 · MAKE IT YOURS</small><h3>나에게 맞는 악보 만들기</h3><p>악보를 읽기 편하게 맞추고 저장하세요.</p></div></div></div><div className="workflow-footer"><span>♩</span><p>완벽한 악보보다,<br /><strong>내가 읽기 편한 악보.</strong></p></div></aside></div>
          <section className="recent-section"><div className="recent-heading"><h2>최근 작업한 음악 <span>{projects.length ? String(projects.length).padStart(2, '0') : '00'}</span></h2><button className="text-button" onClick={() => navigate('projects')}>모든 프로젝트 <ArrowUpRight size={14} /></button></div>{recent.length ? <div className="project-grid">{recent.map(project => <ProjectCard key={project.id} project={project} onOpen={openProject} disabled={loading} />)}</div> : <div className="empty-recent"><span className="empty-recent-icon"><Music2 size={23} strokeWidth={1.4} /></span><div><strong>아직은 빈 작업실이에요</strong><p>첫 음악을 가져오거나, 샘플로 가볍게 시작해보세요.</p></div><button className="text-button" disabled={loading} onClick={() => void demo()}>샘플 열어보기 <ArrowRight size={15} /></button></div>}</section>
          <ScoreImport disabled={loading || !health} onImported={acceptJob} />
          <footer className="page-footer"><span>AKBO MAKER <i /> A LITTLE MORE YOU.</span><span>음악을 듣는 또 하나의 방법.</span></footer>
        </>}
      </main>
    </div>
    {toast && <div className="toast" role="alert"><AlertCircle size={19} /><span>{toast}</span><button className="icon-button" aria-label="알림 닫기" onClick={() => setToast('')}><X size={17} /></button></div>}
    {engineModal && <EngineModal health={health} checking={checking} onClose={() => setEngineModal(false)} onRetry={checkHealth} />}
  </div>;
}

function ProjectCard({ project, onOpen, disabled }: { project: RecentProject; onOpen: (id: string) => Promise<void>; disabled: boolean }) {
  const status = project.status === 'completed' || project.status === 'separated' ? '작업 완료' : project.status === 'cancelled' ? '중단됨' : project.status === 'error' ? '확인 필요' : '작업 중';
  return <button className="project-card" onClick={() => void onOpen(project.id)} disabled={disabled}><span className={`project-art ${project.demo ? 'demo' : ''}`}><AudioLines size={27} strokeWidth={1.1} /></span><span className="project-card-copy"><strong>{project.title}</strong><small>{project.demo ? '샘플' : '음악'} · {formatTime(project.duration)} <i /> {status}</small></span><ArrowUpRight size={17} /></button>;
}

function Projects({ projects, loading, onOpen, onNew }: { projects: RecentProject[]; loading: boolean; onOpen: (id: string) => Promise<void>; onNew: () => void }) {
  return <section className="projects-view"><span className="eyebrow">YOUR MUSIC COLLECTION</span><div className="view-heading"><div><h1>내 프로젝트</h1><p>이 브라우저에서 작업한 음악을 다시 이어가세요.</p></div><button className="primary small" onClick={onNew}><Plus size={16} /> 새 음악 가져오기</button></div><div className="projects-notice"><FolderHeart size={16} />최근 20개의 작업을 이 브라우저에 기억해요. 음원과 악보 파일은 처리 서버에 보관돼요.</div>{projects.length ? <div className="project-grid">{projects.map(project => <ProjectCard key={project.id} project={project} onOpen={onOpen} disabled={loading} />)}</div> : <div className="empty-projects"><FolderHeart size={44} strokeWidth={1.1} /><h2>첫 번째 프로젝트를 기다리고 있어요</h2><p>음악 파일 하나로 나만의 작업실을 시작해보세요.</p><button className="primary small" onClick={onNew}>음악 가져오기 <ArrowRight size={15} /></button></div>}</section>;
}

function Guide({ onDemo, loading }: { onDemo: () => Promise<void>; loading: boolean }) {
  return <section className="guide-view"><span className="eyebrow">A SMALL GUIDE TO YOUR STUDIO</span><h1>가볍게 시작해보세요.</h1><p className="guide-lead">한 곡의 음악에서, 내가 보고 싶은 악보까지.</p><div className="guide-grid">{[{ n: '01', icon: UploadCloud, title: '음악을 가져와요', text: 'MP3·WAV 같은 음악 파일이나 MP4 동영상을 올려주세요. YouTube 개별 영상 링크도 사용할 수 있어요. 기본 제한은 200MB, 10분이에요.' }, { n: '02', icon: Headphones, title: '악기를 하나씩 들어봐요', text: '보컬, 베이스, 드럼, 신디사이저, 기타, 피아노 순서로 분리해요. 헤드폰 버튼은 솔로, 스피커 버튼은 음소거예요. 원본 비교로 차이를 들어보세요.' }, { n: '03', icon: FileMusic, title: '악보로 옮겨봐요', text: '보고 싶은 악기를 고르고 BPM과 박자를 선택해 악보를 만드세요. 3/4·6/8 등 여섯 박자를 지원하고 변박은 직접 수정에서 지정해요. 16분음표 기준 채보 초안이며 드럼은 실험적인 리듬 채보예요.' }, { n: '04', icon: ArrowDownToLine, title: '나에게 맞게 저장해요', text: '악보 설정에서 음표 크기, 보표 간격, 마디 번호를 조절해요. 인쇄 버튼으로 PDF를 저장하거나 MusicXML을 악보 편집기에 열어 수정할 수 있어요. MIDI와 악기별 WAV도 내려받을 수 있어요.' }].map(({ n, icon: Icon, title, text }) => <article className="card guide-card" key={n}><div><Icon size={23} /><span>{n}</span></div><h2>{title}</h2><p>{text}</p></article>)}</div><div className="guide-bottom"><div><Sparkles size={24} /><span><strong>먼저 직접 만져보는 게 제일 쉬워요.</strong><p>직접 합성한 샘플 음악으로 재생과 악보 설정을 체험해보세요.</p></span></div><button className="primary small" disabled={loading} onClick={() => void onDemo()}>샘플 작업실 열기 <ArrowUpRight size={16} /></button></div><p className="guide-note">자동 분리와 채보에는 오류가 있을 수 있어요. 특히 복잡한 화음과 타악기는 원음과 비교해 확인해주세요.</p></section>;
}

function EngineModal({ health, checking, onClose, onRetry }: { health: Health | null; checking: boolean; onClose: () => void; onRetry: () => Promise<void> }) {
  const close = useRef<HTMLButtonElement>(null);
  const previous = useRef<HTMLElement | null>(null);
  useEffect(() => { previous.current = document.activeElement as HTMLElement; close.current?.focus(); return () => { previous.current?.focus(); }; }, []);
  return <div className="modal-backdrop" onClick={onClose}><section className="engine-modal" role="dialog" aria-modal="true" aria-labelledby="engine-title" onClick={event => event.stopPropagation()} onKeyDown={event => { if (event.key === 'Tab') { event.preventDefault(); const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')); const index = buttons.indexOf(document.activeElement as HTMLButtonElement); buttons[(index + (event.shiftKey ? -1 : 1) + buttons.length) % buttons.length]?.focus(); } }}><button ref={close} className="icon-button modal-close" onClick={onClose} aria-label="연결 상태 닫기"><X size={20} /></button><span className="modal-icon"><Radio size={27} /></span><span className="eyebrow">STUDIO CONNECTION</span><h2 id="engine-title">음악 처리 서버 연결 상태</h2><p>실제 음악을 분리하려면 SAM Audio 엔진이 준비되어야 해요.</p><div className="engine-check"><span>{health ? <Check size={17} /> : <AlertCircle size={17} />}음악 처리 API</span><strong>{health ? '연결됨' : '연결 필요'}</strong></div><div className="engine-check"><span>{health?.engine.available ? <Check size={17} /> : <Clock3 size={17} />}SAM Audio 분리</span><strong>{health?.engine.available ? '사용 가능' : '준비 필요'}</strong></div><div className="engine-check"><span>{health?.engine.transcription_available ? <Check size={17} /> : <Clock3 size={17} />}Basic Pitch 채보</span><strong>{health?.engine.transcription_available ? '설치됨' : '설치 필요'}</strong></div><div className="engine-detail">{health ? health.engine.issues.length ? health.engine.issues.map(issue => <p key={issue}>{issue}</p>) : <p>첫 실행에는 모델 다운로드가 필요할 수 있어요.</p> : <p>음악 처리 서버를 실행하고 다시 확인해주세요.</p>}<p>분리 엔진이 준비되기 전에도 샘플 작업실을 체험할 수 있어요.</p></div><button className="primary" disabled={checking} onClick={() => void onRetry()}>{checking && <LoaderCircle className="spin" size={16} />}연결 다시 확인</button></section></div>;
}
