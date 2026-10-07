import { useEffect, useRef, useState } from 'react';
import { FileImage, LoaderCircle } from 'lucide-react';
import { request } from '../api';
import type { Job } from '../types';
import TabScoreReview from './TabScoreReview';

type Capability = { available: boolean; engine: string; issues: string[]; limits: { upload_mb: number; max_pages: number }; tablature_supported: boolean; tab_review_available?: boolean; tab_review_issues?: string[] };
type RecognitionResult = { id: string; filename: string; download_url: string; sha256: string; raw_download_url?: string; normalizations?: string[] };
export type RecognitionJob = {
  id: string; status: 'queued' | 'running' | 'ready' | 'error' | 'cancelled'; progress: number;
  message: string; error?: string | null; warnings: string[]; source_url: string;
  preview_urls: string[]; results: RecognitionResult[]; notation?: 'staff' | 'drums' | 'tab';
  source_coordinates?: { download_url: string; sha256: string; pages: number[]; tab_staffs: number; digit_candidates: number; matched_digits: number; rhythm_known: false; editable_musicxml: false };
};
export type RecognitionSource = { id: string; resultId: string; warnings: string[]; notation?: 'staff' | 'drums' | 'tab' };
type Props = {
  disabled: boolean;
  onPrepared: (file: File, source: RecognitionSource) => Promise<void>;
  onReset: () => void;
  onActivity: (active: boolean) => void;
  onImported?: (job: Job) => void;
  onDirty?: (dirty: boolean) => void;
};
export const recognitionSessionKey = 'akbo:score-omr:last-job';
export const recognitionRecentKey = 'akbo:score-omr:recent-v1';
type RecentRecognition = { id: string; title: string; status: RecognitionJob['status']; notation: 'staff' | 'drums' | 'tab'; opened_at: number };
const recentLimit = 12;
export function recentRecognitions(): RecentRecognition[] {
  try {
    const stored: unknown = JSON.parse(window.localStorage.getItem(recognitionRecentKey) ?? '[]');
    if (!Array.isArray(stored)) return [];
    const seen = new Set<string>();
    return stored.filter((entry): entry is RecentRecognition => {
      if (!entry || typeof entry !== 'object') return false;
      const value = entry as Partial<RecentRecognition>;
      if (typeof value.id !== 'string' || !/^[a-f0-9]{32}$/.test(value.id) || seen.has(value.id) || typeof value.title !== 'string' || !value.title.trim() || value.title.length > 160 ||
        !['queued', 'running', 'ready', 'error', 'cancelled'].includes(value.status ?? '') || !['staff', 'drums', 'tab'].includes(value.notation ?? '') ||
        typeof value.opened_at !== 'number' || !Number.isFinite(value.opened_at) || value.opened_at < 0 || value.opened_at > 8_640_000_000_000_000) return false;
      seen.add(value.id); return true;
    }).sort((a, b) => b.opened_at - a.opened_at).slice(0, recentLimit).map(({ id, title, status, notation, opened_at }) => ({ id, title, status, notation, opened_at }));
  } catch { return []; }
}
function storeRecentRecognitions(records: RecentRecognition[]) {
  try { window.localStorage.setItem(recognitionRecentKey, JSON.stringify(records)); }
  catch { /* Recovery metadata is optional when browser storage is unavailable. */ }
}
const recentStatus: Record<RecognitionJob['status'], string> = { queued: '대기', running: '인식 중', ready: '검토 가능', error: '인식 오류', cancelled: '인식 취소' };
function previousRecognition(): string {
  try { const id = sessionStorage.getItem(recognitionSessionKey) ?? ''; return /^[a-f0-9]{32}$/.test(id) ? id : ''; }
  catch { return ''; }
}
function rememberRecognition(id: string | null) {
  try { if (id && /^[a-f0-9]{32}$/.test(id)) sessionStorage.setItem(recognitionSessionKey, id); else sessionStorage.removeItem(recognitionSessionKey); }
  catch { /* Private browsing can disable optional recovery storage. */ }
}

export function recognitionUrl(value: string, id: string): string | undefined {
  try {
    const url = new URL(value, window.location.origin);
    if (url.origin !== window.location.origin || url.username || url.password ||
      !url.pathname.startsWith(`/api/score-omr/${encodeURIComponent(id)}/`)) return undefined;
    return url.href;
  } catch { return undefined; }
}

export function validRecognitionPages(value: string, maximum: number): boolean {
  const selected = new Set<number>();
  for (const section of value.replace(/\s/g, '').split(',')) {
    if (!/^\d{1,4}(?:-\d{1,4})?$/.test(section)) return false;
    const [start, end = start] = section.split('-').map(Number);
    if (start < 1 || end < start || end - start >= maximum) return false;
    for (let page = start; page <= end; page++) selected.add(page);
    if (selected.size > maximum) return false;
  }
  return selected.size > 0;
}

export default function ScoreRecognition({ disabled, onPrepared, onReset, onActivity, onImported, onDirty }: Props) {
  const [capability, setCapability] = useState<Capability | null>(null);
  const [checking, setChecking] = useState(true);
  const [file, setFile] = useState<File | null>(null);
  const [pages, setPages] = useState('1');
  const [notation, setNotation] = useState<'staff' | 'drums' | 'tab'>('staff');
  const [reviewOpen, setReviewOpen] = useState(false), [reviewBusy, setReviewBusy] = useState(false), [reviewDirty, setReviewDirty] = useState(false);
  const [job, setJob] = useState<RecognitionJob | null>(null);
  const [resultId, setResultId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [pollError, setPollError] = useState('');
  const [localPreview, setLocalPreview] = useState('');
  const [resumeId, setResumeId] = useState(previousRecognition);
  const [restoring, setRestoring] = useState(!!resumeId);
  const [restoreAttempt, setRestoreAttempt] = useState(0);
  const [recent, setRecent] = useState(recentRecognitions);
  const [missingId, setMissingId] = useState('');
  const [recentNotice, setRecentNotice] = useState('');
  const openReviewAfterRestore = useRef(false);
  const input = useRef<HTMLInputElement>(null);
  const operation = useRef<AbortController | null>(null);
  const operationLock = useRef(false);
  const generation = useRef(0);
  const mounted = useRef(true);
  const running = job?.status === 'queued' || job?.status === 'running';
  const locked = disabled || busy || running || restoring || reviewBusy || reviewDirty;
  const available = notation === 'tab' ? capability?.tab_review_available : capability?.available;
  const maximumPages = capability?.limits.max_pages ?? 4;
  const maximumMb = capability?.limits.upload_mb ?? 25;

  useEffect(() => { onActivity(busy || running || restoring || reviewBusy); }, [busy, running, restoring, reviewBusy, onActivity]);
  useEffect(() => { onDirty?.(reviewDirty); return () => onDirty?.(false); }, [reviewDirty, onDirty]);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; generation.current++; operation.current?.abort(); };
  }, []);
  useEffect(() => {
    if (!file || !/\.(png|jpe?g)$/i.test(file.name)) { setLocalPreview(''); return; }
    const url = URL.createObjectURL(file); setLocalPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  async function checkCapability(signal?: AbortSignal) {
    setChecking(true); setError('');
    try {
      const result = await request<Capability>('/api/score-omr/status', { signal });
      if (mounted.current && !signal?.aborted) setCapability(result);
    } catch (err) {
      if (mounted.current && !signal?.aborted) setError((err as Error).message);
    } finally { if (mounted.current && !signal?.aborted) setChecking(false); }
  }
  useEffect(() => {
    const controller = new AbortController(); void checkCapability(controller.signal);
    return () => controller.abort();
  }, []);
  useEffect(() => {
    const sync = (event: StorageEvent) => { if (event.key === recognitionRecentKey || event.key === null) setRecent(recentRecognitions()); };
    window.addEventListener('storage', sync); return () => window.removeEventListener('storage', sync);
  }, []);
  function rememberRecent(next: RecognitionJob, title?: string, opened = false) {
    if (!/^[a-f0-9]{32}$/.test(next.id)) return;
    const records = recentRecognitions(), previous = records.find(item => item.id === next.id) ?? recent.find(item => item.id === next.id);
    const record: RecentRecognition = { id: next.id, title: (title?.trim() || previous?.title || (next.notation === 'tab' ? 'PDF TAB 검수' : 'PDF·이미지 악보 인식')).slice(0, 160),
      status: next.status, notation: next.notation ?? previous?.notation ?? 'staff', opened_at: opened || !previous ? Date.now() : previous.opened_at };
    const updated = [record, ...records.filter(item => item.id !== next.id)].sort((a, b) => b.opened_at - a.opened_at).slice(0, recentLimit);
    storeRecentRecognitions(updated); setRecent(updated);
  }
  useEffect(() => {
    if (!resumeId) return;
    const controller = new AbortController(), current = generation.current;
    setRestoring(true); setError(''); setMissingId('');
    void (async () => {
      try {
        const restored = await request<RecognitionJob>(`/api/score-omr/${encodeURIComponent(resumeId)}`, { signal: controller.signal });
        if (controller.signal.aborted || !mounted.current || generation.current !== current) return;
        if (restored.id !== resumeId) throw new Error('요청한 작업과 서버의 응답이 일치하지 않아요.');
        setJob(restored); setNotation(restored.notation ?? 'staff'); setResultId(restored.status === 'ready' ? restored.results[0]?.id ?? '' : '');
        rememberRecent(restored, undefined, true); rememberRecognition(restored.id);
        setReviewOpen(openReviewAfterRestore.current); openReviewAfterRestore.current = false; setResumeId('');
      } catch (err) {
        if (!controller.signal.aborted && mounted.current && generation.current === current) {
          const failure = err as Error & { status?: number };
          if (failure.status === 404) { setMissingId(resumeId); setError('서버에서 이 인식 작업을 찾을 수 없어요. 서버 주소나 보관 상태를 확인하거나, 아래에서 이 브라우저의 최근 기록만 제거할 수 있어요.'); }
          else setError(`이전 인식 작업을 불러오지 못했어요. ${failure.message}`);
        }
      } finally { if (!controller.signal.aborted && mounted.current && generation.current === current) { operationLock.current = false; setRestoring(false); } }
    })();
    return () => controller.abort();
  }, [resumeId, restoreAttempt]);

  useEffect(() => {
    if (!job || !running || busy) return;
    const controller = new AbortController();
    const current = generation.current;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      let delay = 1200;
      try {
        const next = await request<RecognitionJob>(`/api/score-omr/${encodeURIComponent(job.id)}`, { signal: controller.signal });
        if (controller.signal.aborted || generation.current !== current) return;
        setJob(next); rememberRecent(next); setPollError('');
        if (next.status === 'ready') setResultId(next.results[0]?.id ?? '');
        if (next.status !== 'queued' && next.status !== 'running') return;
      } catch (err) {
        if (controller.signal.aborted || generation.current !== current) return;
        setPollError(`진행 상태를 확인하지 못했어요. 자동으로 다시 확인합니다. ${(err as Error).message}`);
        delay = 4000;
      }
      timer = setTimeout(() => void poll(), delay);
    };
    timer = setTimeout(() => void poll(), 1200);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [job?.id, job?.status, running, busy]);

  function reopenRecognition(record: RecentRecognition) {
    if (locked || operationLock.current) return;
    generation.current++; operation.current?.abort(); operationLock.current = true;
    setJob(null); setFile(null); setResultId(''); setReviewOpen(false); setReviewBusy(false); setReviewDirty(false); setError(''); setPollError(''); setMissingId(''); setRecentNotice(''); onReset();
    openReviewAfterRestore.current = true; rememberRecognition(record.id); setRestoring(true); setResumeId(record.id); setRestoreAttempt(value => value + 1);
  }
  function forgetRecent(id: string) {
    if (locked || operationLock.current) return;
    const updated = recentRecognitions().filter(record => record.id !== id);
    storeRecentRecognitions(updated); setRecent(updated); setRecentNotice('이 브라우저의 최근 목록에서만 제거했어요. 서버의 원본 파일과 검수 초안은 삭제하지 않았습니다.');
    if (id === previousRecognition()) rememberRecognition(null);
    if (id === missingId) { setMissingId(''); setError(''); }
    if (id === resumeId) { generation.current++; setResumeId(''); rememberRecognition(null); openReviewAfterRestore.current = false; }
  }

  function chooseFile(value?: File) {
    if (!value || locked || operationLock.current) return;
    generation.current++; operation.current?.abort();
    rememberRecognition(null); setResumeId(''); setMissingId(''); openReviewAfterRestore.current = false;
    setJob(null); setReviewOpen(false); setResultId(''); setFile(null); setError(''); setPollError(''); onReset();
    if (!/\.(pdf|png|jpe?g)$/i.test(value.name) || !value.size || value.size > maximumMb * 1024 * 1024) {
      setError(`${maximumMb}MB 이하의 PDF · PNG · JPG · JPEG 파일을 선택해주세요.`); return;
    }
    setFile(value); setPages('1');
  }

  function resetRecognition() {
    if (locked || operationLock.current) return;
    generation.current++; operation.current?.abort(); rememberRecognition(null); setResumeId(''); setMissingId(''); openReviewAfterRestore.current = false;
    setJob(null); setReviewOpen(false); setResultId(''); setFile(null); setPages('1'); setError(''); setPollError(''); onReset();
  }

  async function start() {
    if (!file || locked || operationLock.current || !available) return;
    if (notation === 'tab' && !/\.pdf$/i.test(file.name)) { setError('TAB 숫자 검토는 PDF 파일만 지원합니다.'); return; }
    if (/\.pdf$/i.test(file.name) && !validRecognitionPages(pages, maximumPages)) {
      setError(`페이지를 1, 3-4처럼 입력해주세요. 한 번에 최대 ${maximumPages}페이지입니다.`); return;
    }
    const current = ++generation.current;
    operationLock.current = true;
    const controller = new AbortController(); operation.current?.abort(); operation.current = controller;
    setBusy(true); setError(''); setPollError(''); setJob(null); setResultId(''); onReset();
    try {
      const data = new FormData(); data.append('file', file); data.append('pages', /\.pdf$/i.test(file.name) ? pages : '1'); data.append('notation', notation);
      const next = await request<RecognitionJob>('/api/score-omr', { method: 'POST', body: data, signal: controller.signal });
      if (!mounted.current || controller.signal.aborted || generation.current !== current) return;
      rememberRecognition(next.id); rememberRecent(next, file.name.replace(/\.(pdf|png|jpe?g)$/i, ''), true); setJob(next); if (next.status === 'ready') setResultId(next.results[0]?.id ?? '');
    } catch (err) {
      if (mounted.current && !controller.signal.aborted && generation.current === current) setError((err as Error).message);
    } finally { operationLock.current = false; if (mounted.current && generation.current === current) setBusy(false); }
  }

  async function cancel() {
    if (!job || busy || operationLock.current || disabled || !running) return;
    const current = ++generation.current;
    operationLock.current = true;
    const controller = new AbortController(); operation.current?.abort(); operation.current = controller;
    setBusy(true); setError('');
    try {
      const next = await request<RecognitionJob>(`/api/score-omr/${encodeURIComponent(job.id)}/cancel`, { method: 'POST', signal: controller.signal });
      if (mounted.current && !controller.signal.aborted && generation.current === current) { setJob(next); rememberRecent(next); setPollError(''); }
    } catch (err) {
      if (mounted.current && !controller.signal.aborted && generation.current === current) {
        setError((err as Error).message);
      }
    } finally { operationLock.current = false; if (mounted.current && generation.current === current) setBusy(false); }
  }

  async function prepare() {
    const result = job?.results.find(item => item.id === resultId);
    if (!job || job.status !== 'ready' || !result || locked || operationLock.current) return;
    const url = recognitionUrl(result.download_url, job.id);
    if (!url) { setError('인식 결과 파일 주소가 올바르지 않아요.'); return; }
    const current = ++generation.current;
    operationLock.current = true;
    const controller = new AbortController(); operation.current?.abort(); operation.current = controller;
    setBusy(true); setError(''); onReset();
    try {
      const response = await fetch(url, { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(20_000)]), cache: 'no-store' });
      if (!response.ok) throw new Error('인식 결과 MusicXML을 가져오지 못했어요.');
      const blob = await response.blob();
      if (!blob.size || blob.size > 2 * 1024 * 1024) throw new Error('편집기로 가져올 MusicXML은 비어 있지 않은 2MB 이하 파일이어야 해요.');
      if (!/\.(musicxml|xml|mxl)$/i.test(result.filename)) throw new Error('인식 결과가 지원하는 MusicXML 파일 형식이 아니에요.');
      if (!mounted.current || controller.signal.aborted || generation.current !== current) return;
      await onPrepared(new File([blob], result.filename, { type: blob.type }), { id: job.id, resultId: result.id, warnings: job.warnings, notation: job.notation });
    } catch (err) {
      if (mounted.current && !controller.signal.aborted && generation.current === current) setError((err as Error).message);
    } finally { operationLock.current = false; if (mounted.current && generation.current === current) setBusy(false); }
  }

  const result = job?.results.find(item => item.id === resultId);
  const sourceUrl = job && recognitionUrl(job.source_url, job.id);
  const previewUrls = job?.preview_urls.map(url => recognitionUrl(url, job.id)).filter((url): url is string => !!url) ?? [];
  const resultUrl = job && result && recognitionUrl(result.download_url, job.id);
  const rawResultUrl = job && result?.raw_download_url && result.raw_download_url !== result.download_url && recognitionUrl(result.raw_download_url, job.id);
  const coordinatesUrl = job?.source_coordinates && recognitionUrl(job.source_coordinates.download_url, job.id);
  return <section className="score-recognition" aria-label="PDF 이미지 악보 인식">
    <p className="editor-help">인쇄된 오선 악보를 MusicXML 초안으로 읽습니다. 사진의 기울기·저해상도, 드럼 음표와 가사에서 오류가 날 수 있어요. 원본과 대조한 뒤 직접 수정해주세요. 외부 AI 서비스에 파일을 보내지 않고 서버의 Audiveris로 처리합니다.</p>
    <p className="recognition-caution">숫자 TAB PDF는 아래 원본 표기에서 ‘TAB 숫자 검토’를 선택하세요. 원본의 문자·줄·마디를 읽고 직접 확인한 리듬으로 편집 악보를 만듭니다. 스캔 TAB·붙임줄·주법·누락까지 자동 복원하는 기능은 아닙니다.</p>
    {recent.length > 0 && <details className="recognition-original" open><summary>최근 PDF·이미지 검수 작업 ({recent.length})</summary><p className="editor-help">이 브라우저에서 최근 연 작업 {recentLimit}개까지 보관합니다. 제목·작업 ID·인식 상태·열어본 시각만 저장하며 악보 내용은 저장하지 않습니다. 서버에 저장한 TAB 초안은 ‘다시 열기’로 이어서 검수할 수 있어요. 인식 상태는 마지막 확인 기준입니다.</p>{recent.map(record => <div className="bulk-row" key={record.id}><span><strong>{record.title}</strong> · {record.notation === 'tab' ? 'TAB 검수' : record.notation === 'drums' ? '드럼 오선' : '오선 인식'} · {recentStatus[record.status]} · 최근 열기 <time dateTime={new Date(record.opened_at).toISOString()}>{new Date(record.opened_at).toLocaleString('ko-KR')}</time>{job?.id === record.id && ' · 현재 선택됨'}</span><button className="text-button" aria-label={`${record.title} 다시 열기`} disabled={locked} onClick={() => reopenRecognition(record)}>다시 열기</button><button className="text-button" aria-label={`${record.title} 최근 목록에서 제거`} disabled={locked} onClick={() => forgetRecent(record.id)}>최근 목록에서 제거</button></div>)}{(reviewDirty || reviewBusy) && <p className="editor-help">검수 초안을 저장하고 진행 중인 요청이 끝난 뒤 다른 작업을 열어주세요.</p>}</details>}
    {recentNotice && <p className="editor-help" role="status">{recentNotice}</p>}
    {restoring && <p role="status"><LoaderCircle className="spin" size={15} /> 이전 인식 작업을 불러오는 중…</p>}
    {resumeId && !restoring && <div className="bulk-row"><button className="text-button" disabled={locked} onClick={() => setRestoreAttempt(value => value + 1)}>이전 인식 다시 불러오기</button><button className="text-button" disabled={locked} onClick={resetRecognition}>이전 기록 잊고 새 인식</button></div>}
    {missingId && !restoring && <button className="text-button" disabled={locked} onClick={() => forgetRecent(missingId)}>찾을 수 없는 작업의 최근 기록 제거</button>}
    {checking ? <p role="status"><LoaderCircle className="spin" size={15} /> 인식 엔진 확인 중…</p> : !available && <div className="recognition-unavailable" role="status"><strong>선택한 인식 방식의 서버 준비가 필요해요.</strong>{(notation === 'tab' ? capability?.tab_review_issues : capability?.issues)?.map((issue, index) => <p key={index}>{issue}</p>)}<p>MusicXML 가져오기는 계속 사용할 수 있어요.{capability?.tab_review_available && ' PDF TAB 숫자 검토는 Audiveris 없이도 사용할 수 있습니다.'}</p><button className="text-button" disabled={busy || disabled} onClick={() => void checkCapability()}>엔진 상태 다시 확인</button></div>}
    <input ref={input} className="visually-hidden" type="file" aria-label="PDF 이미지 악보 파일 선택" accept=".pdf,.png,.jpg,.jpeg" disabled={locked} onChange={event => { chooseFile(event.target.files?.[0]); event.target.value = ''; }} />
    <div className="bulk-row">
      <button className="secondary small" disabled={locked} onClick={() => input.current?.click()}><FileImage size={15} />{file ? '다른 PDF·이미지 선택' : 'PDF·이미지 선택'}</button>
      {file && <span className="recognition-filename">{file.name} · {(file.size / 1024 / 1024).toFixed(1)}MB</span>}
      {file && /\.pdf$/i.test(file.name) && <label>인식할 PDF 페이지<input aria-label="인식할 PDF 페이지" value={pages} maxLength={50} placeholder="1 또는 1, 3-4" disabled={locked} onChange={event => { setPages(event.target.value); setJob(null); setResultId(''); rememberRecognition(null); onReset(); }} /></label>}
      {file && <label>원본 표기<select aria-label="원본 표기" value={notation} disabled={locked} onChange={event => { setNotation(event.target.value as 'staff' | 'drums' | 'tab'); setJob(null); setReviewOpen(false); setResultId(''); rememberRecognition(null); onReset(); }}><option value="staff">일반 오선 · 보컬 / 기타 / 베이스 / 건반</option><option value="drums">드럼 오선 · 타격 종류 확인 필요</option><option value="tab" disabled={!/\.pdf$/i.test(file.name)}>PDF TAB 숫자 검토 · 기타 / 베이스</option></select></label>}
      <button className="primary small" disabled={locked || checking || !file || !available} onClick={() => void start()}>{busy && !running ? <LoaderCircle className="spin" size={15} /> : null} 악보 인식 시작</button>
    </div>
    <p className="editor-help">최대 {maximumMb}MB · 한 번에 {maximumPages}페이지. 처음에는 1페이지로 결과를 확인하는 것을 권장해요.</p>
    {job && <div className="recognition-progress" role="status" aria-live="polite"><strong>{job.status === 'ready' ? '인식 초안 준비됨 · 검토 필요' : job.status === 'cancelled' ? '인식을 취소했어요' : job.status === 'error' ? '악보 인식 실패' : '악보 인식 중'}</strong><p>{job.message}</p>{running && <><progress max={100} value={Math.max(0, Math.min(100, job.progress))} aria-label="악보 인식 진행률" /><button className="text-button" disabled={disabled || busy} onClick={() => void cancel()}>인식 취소</button><small>다른 화면으로 이동해도 서버 인식은 계속됩니다. 중단하려면 취소해주세요.</small></>}</div>}
    {job && !running && <button className="text-button recognition-reset" disabled={locked} onClick={resetRecognition}>새 인식 · 이전 결과 선택 해제</button>}
    {(sourceUrl || previewUrls.length > 0 || localPreview) && <details className="recognition-original" open><summary>원본 악보 확인</summary>{sourceUrl && <a className="text-button" href={sourceUrl} target="_blank" rel="noopener noreferrer">업로드한 원본 파일 열기</a>}<div className="recognition-pages">{previewUrls.length ? previewUrls.map((url, index) => <a key={url} href={url} target="_blank" rel="noopener noreferrer"><img src={url} loading="lazy" alt={`인식 대상으로 선택한 원본 악보 ${index + 1}번째 페이지`} /></a>) : localPreview ? <img src={localPreview} alt="선택한 원본 악보 이미지" /> : <p className="editor-help">원본 링크에서 PDF를 확인해주세요. 페이지 미리보기는 준비되면 표시됩니다.</p>}</div></details>}
    {job && job.warnings.length > 0 && <ul className="recognition-warnings" aria-label="악보 인식 경고">{job.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>}
    {coordinatesUrl && job?.source_coordinates && <details className="recognition-original"><summary>PDF 원본의 TAB 숫자·줄 확인 자료</summary><p className="editor-help">원본 PDF에서 TAB 보표 {job.source_coordinates.tab_staffs}개, 숫자 후보 {job.source_coordinates.digit_candidates}개를 직접 읽었고 그중 {job.source_coordinates.matched_digits}개의 줄 위치를 연결했어요. 정확도 점수가 아니며 이미지·특수 기호는 빠질 수 있어요. 이 자료에는 음표 길이가 없어 완성된 악보로 가져오지 않습니다.</p><a className="text-button" href={coordinatesUrl} download>원본 숫자·좌표 JSON 다운로드</a></details>}
    {coordinatesUrl && job && !!job.source_coordinates?.tab_staffs && !running && onImported && <div className="tab-review-entry"><button className="secondary small" disabled={disabled || busy || reviewBusy || reviewDirty} onClick={() => setReviewOpen(value => !value)}>{reviewOpen ? 'TAB 검토 접기' : '원본 TAB 확인·수정해서 악보 만들기'}</button>{reviewOpen && <TabScoreReview key={job.id} jobId={job.id} disabled={disabled || busy} onImported={onImported} onActivity={setReviewBusy} onDirty={setReviewDirty} />}</div>}
    {job?.status === 'ready' && job.notation !== 'tab' && <div className="recognition-results"><p className="editor-help">결과 {job.results.length}개를 확인해주세요. 여러 악곡·페이지가 나뉜 경우 각각 선택해 가져올 수 있어요. 인식 완료가 정확한 악보를 뜻하지는 않습니다.</p>{job.results.length > 0 ? <><div className="bulk-row"><label>가져올 인식 결과<select aria-label="가져올 인식 결과" value={resultId} disabled={locked} onChange={event => { setResultId(event.target.value); onReset(); }}>{job.results.map(item => <option value={item.id} key={item.id}>{item.filename}</option>)}</select></label>{resultUrl && <a className="text-button" href={resultUrl} download>가져오기용 MusicXML</a>}{rawResultUrl && <a className="text-button" href={rawResultUrl} download>엔진 원본 MusicXML (미보정)</a>}<button className="secondary small" disabled={locked || !result} onClick={() => void prepare()}>선택한 결과의 파트 확인</button></div>{!!result?.normalizations?.length && <p className="editor-help">호환성 보정은 드럼 악기 번호의 기준만 맞춥니다. 템포·음표 위치·길이를 바꾸거나 인식 오류를 고친 결과는 아니에요. 미보정 엔진 원본도 별도로 보관합니다.</p>}</> : <p className="editor-error" role="alert">가져올 수 있는 MusicXML 결과가 없어요. 원본의 해상도와 페이지를 확인해주세요.</p>}</div>}
    {pollError && <p className="recognition-caution" role="status">{pollError}</p>}
    {(error || job?.error) && <p className="editor-error" role="alert">{error || job?.error}</p>}
  </section>;
}
