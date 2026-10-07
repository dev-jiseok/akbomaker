import { useCallback, useEffect, useRef, useState } from 'react';
import { Download, FileMusic, LoaderCircle, Printer } from 'lucide-react';
import { request } from '../api';
import type { Instrument, Job, ScoreDocument, ScoreLayout, ScorePreset } from '../types';
import ScoreRecognition, { recognitionSessionKey, type RecognitionSource } from './ScoreRecognition';
import ScoreViewer from './ScoreViewer';

type Inspection = { title: string; parts: { id: string; name: string; measures: number }[] };
type PreservedPreview = { xml: string; warnings: string[]; mode: 'layout-only'; layout: ScoreLayout; title: string };
type Props = { disabled: boolean; expanded?: boolean; onImported?: (job: Job) => void; onDirty?: (dirty: boolean) => void; endpoint?: string; document?: ScoreDocument; onDocument?: (doc: ScoreDocument) => void };

export function readSourceAttribution(xml: string, title: string): { lines: string[]; truncated: boolean } {
  const parsed = new DOMParser().parseFromString(xml, 'application/xml');
  if (parsed.getElementsByTagName('parsererror').length) return { lines: [], truncated: false };
  const normalize = (value: string) => value.replace(/\s+/g, ' ').trim();
  const seen = new Set([normalize(title)]), lines: string[] = [];
  let truncated = false, characters = 0;
  const add = (value: string, label = '') => {
    const text = normalize(value);
    if (!text || seen.has(text)) return;
    seen.add(text);
    if (lines.length >= 24 || characters >= 5000) { truncated = true; return; }
    const maximum = Math.min(400, 5000 - characters);
    if (text.length > maximum) truncated = true;
    const line = `${label}${text.slice(0, maximum)}${text.length > maximum ? '…' : ''}`;
    lines.push(line); characters += line.length;
  };
  const labels: Record<string, string> = { composer: '작곡: ', lyricist: '작사: ', arranger: '편곡: ', editor: '편집: ', transcriber: '채보: ' };
  for (const identification of parsed.getElementsByTagName('identification')) {
    for (const node of identification.children) {
      if (node.tagName === 'creator') add(node.textContent ?? '', labels[node.getAttribute('type') ?? ''] ?? '원저작자: ');
      if (node.tagName === 'rights') add(node.textContent ?? '', '권리 표기: ');
    }
  }
  for (const credit of parsed.getElementsByTagName('credit')) {
    for (const words of credit.getElementsByTagName('credit-words')) add(words.textContent ?? '');
  }
  return { lines, truncated };
}

function LayoutPreview({ preview, instrument }: { preview: PreservedPreview; instrument: Instrument }) {
  const [url, setUrl] = useState('');
  const [printError, setPrintError] = useState('');
  const paper = useRef<HTMLDivElement>(null);
  const printCleanup = useRef<(() => void) | null>(null);
  const attribution = readSourceAttribution(preview.xml, preview.title);
  useEffect(() => () => printCleanup.current?.(), []);
  useEffect(() => {
    const next = URL.createObjectURL(new Blob([preview.xml], { type: 'application/vnd.recordare.musicxml+xml' }));
    setUrl(next); return () => URL.revokeObjectURL(next);
  }, [preview.xml]);
  function print() {
    const target = paper.current;
    if (!target?.querySelector('.engraving svg')) { setPrintError('악보 미리보기가 다 그려진 뒤 인쇄해주세요.'); return; }
    if (printCleanup.current) return;
    setPrintError('');
    const closedDetails: HTMLDetailsElement[] = [];
    for (let node = target.parentElement; node; node = node.parentElement) {
      if (node instanceof HTMLDetailsElement && !node.open) { closedDetails.push(node); node.open = true; }
    }
    target.setAttribute('data-score-preserve-target', '');
    document.body.setAttribute('data-score-preserve-print', '');
    const cleanup = () => {
      document.body.removeAttribute('data-score-preserve-print'); target.removeAttribute('data-score-preserve-target');
      closedDetails.forEach(node => { node.open = false; });
      window.removeEventListener('afterprint', cleanup); printCleanup.current = null;
    };
    printCleanup.current = cleanup;
    window.addEventListener('afterprint', cleanup);
    try { window.print(); } catch { cleanup(); setPrintError('인쇄 창을 열지 못했어요. MusicXML 다운로드를 이용해주세요.'); }
  }
  return <section className="import-layout-preview" aria-label="원본 표기 유지 스타일 미리보기">
    <div className="import-preview-heading"><p>원본 표기 유지 · 스타일 전용 미리보기</p><div className="bulk-row">{url && <a className="text-button" href={url} download="styled-score.musicxml"><Download size={14} /> 스타일 MusicXML 다운로드</a>}<button className="text-button" onClick={print}><Printer size={14} /> 이 미리보기 인쇄 / PDF 저장</button></div></div>
    <p className="recognition-caution">이 경로는 원본 MusicXML의 음악 표기를 유지하며 배치를 바꿉니다. 음표 편집 프로젝트를 만들거나 현재 편집본을 변경하지 않아요. 화면 표시에는 뷰어 지원 범위의 차이가 있을 수 있으니 원본과 비교해주세요. 인식 오류는 자동 수정되지 않습니다.</p>
    {preview.warnings.length > 0 && <ul className="recognition-warnings">{preview.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>}
    {printError && <p className="editor-error" role="alert">{printError}</p>}
    <div ref={paper} className="import-preserved-paper"><h3>{preview.title}</h3>{attribution.lines.length > 0 && <div className="import-source-attribution" aria-label="원본 저작자와 권리 표기">{attribution.lines.map((line, index) => <p key={index}>{line}</p>)}{attribution.truncated && <p>긴 원본 표기의 화면 표시를 줄였습니다. 전체 원문은 다운로드한 MusicXML에 보존됩니다.</p>}</div>}<ScoreViewer stem={{ id: instrument, label: '원본 표기 유지', status: 'ready', score_status: 'ready', waveform: [], score_url: url }} xml={preview.xml} layout={preview.layout} scale={1} spacious={preview.layout.preset !== 'standard'} measureNumbers preserveNotation /></div>
  </section>;
}

export default function ScoreImport({ disabled, expanded = false, onImported, onDirty, endpoint, document: doc, onDocument }: Props) {
  const [mode, setMode] = useState<'xml' | 'omr'>(() => {
    try { return /^[a-f0-9]{32}$/.test(sessionStorage.getItem(recognitionSessionKey) ?? '') ? 'omr' : 'xml'; }
    catch { return 'xml'; }
  });
  const [file, setFile] = useState<File | null>(null), [listing, setListing] = useState<Inspection | null>(null);
  const [part, setPart] = useState(''), [instrument, setInstrument] = useState<Instrument>('bass');
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [recognitionActive, setRecognitionActive] = useState(false);
  const [recognitionDirty, setRecognitionDirty] = useState(false);
  const [recognition, setRecognition] = useState<RecognitionSource | null>(null);
  const [reviewed, setReviewed] = useState(false);
  const [preset, setPreset] = useState<ScorePreset>('practice');
  const [measures, setMeasures] = useState<2 | 4>(4);
  const [preview, setPreview] = useState<PreservedPreview | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const lock = useRef(false);
  const operation = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const revision = useRef(doc?.revision); revision.current = doc?.revision;
  const locked = disabled || busy || recognitionActive || recognitionDirty;
  useEffect(() => { onDirty?.(recognitionDirty); return () => onDirty?.(false); }, [recognitionDirty, onDirty]);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; operation.current?.abort(); }; }, []);

  const clearSelection = useCallback(() => {
    setFile(null); setListing(null); setPart(''); setRecognition(null); setReviewed(false); setPreview(null); setError(''); setNotice('');
  }, []);

  async function inspect(value?: File, source?: RecognitionSource) {
    if (!value || lock.current || disabled) return;
    clearSelection();
    if (!/\.(musicxml|xml|mxl)$/i.test(value.name) || !value.size || value.size > 2 * 1024 * 1024) {
      setError('2MB 이하의 .musicxml · .xml · .mxl 파일을 선택해주세요. PDF·이미지는 옆의 인식 탭에서 시작할 수 있어요.'); return;
    }
    lock.current = true; setBusy(true);
    const controller = new AbortController(); operation.current = controller;
    try {
      const data = new FormData(); data.append('file', value);
      const result = await request<Inspection>('/api/score-import/inspect', { method: 'POST', body: data, signal: controller.signal });
      if (!mounted.current || controller.signal.aborted) return;
      if (!result.parts.length) throw new Error('가져올 파트가 없는 악보예요.');
      setFile(value); setListing(result); setPart(result.parts[0].id); setRecognition(source ?? null);
      if (!doc && source?.notation === 'drums') setInstrument('drums');
    } catch (err) { if (mounted.current && !controller.signal.aborted) setError((err as Error).message); }
    finally { lock.current = false; if (mounted.current) setBusy(false); }
  }

  function formData() {
    const data = new FormData(); data.append('file', file!); data.append('part_id', part);
    if (recognition) { data.append('omr_id', recognition.id); data.append('omr_result_id', recognition.resultId); }
    return data;
  }

  async function apply(preserveOriginal = false) {
    if (!file || !listing || lock.current || disabled || (recognition && !reviewed)) return;
    if (doc && !window.confirm('선택한 MusicXML 파트로 현재 편집 음표·가사·메모를 대체할까요? 원본 음원·재생 기준·출력 스타일은 유지합니다. 실행 취소로 복원할 수 있고, 저장하기 전에는 서버 파일을 바꾸지 않습니다.')) return;
    lock.current = true; setBusy(true); setError(''); setNotice('');
    const controller = new AbortController(); operation.current = controller;
    const baseRevision = doc?.revision;
    try {
      const data = formData();
      if (doc && endpoint) {
        data.append('base_revision', doc.revision);
        const result = await request<{ document: ScoreDocument; warnings: string[] }>(endpoint + '/import-preview', { method: 'POST', body: data, signal: controller.signal });
        if (!mounted.current || controller.signal.aborted) return;
        if (baseRevision !== revision.current) throw new Error('가져오는 동안 악보 버전이 변경됐어요. 최신 악보에서 다시 가져와주세요.');
        onDocument?.(result.document); setNotice('편집 초안에 가져왔어요. 미리보기 확인 후 저장해주세요. ' + (recognition ? '현재 프로젝트에는 이 인식 원본의 출처 연결이 저장되지 않으니, 위 원본과 인식 MusicXML을 따로 보관해주세요. ' : '') + result.warnings.join(' '));
      } else {
        data.append('instrument', instrument);
        if (preserveOriginal) { data.append('preset', preset); data.append('measures_per_line', String(measures)); }
        const result = await request<Job>(preserveOriginal ? '/api/source-scores' : '/api/score-import', { method: 'POST', body: data, signal: controller.signal });
        if (mounted.current && !controller.signal.aborted) onImported?.(result);
      }
    } catch (err) { if (mounted.current && !controller.signal.aborted) setError((err as Error).message); }
    finally { lock.current = false; if (mounted.current) setBusy(false); }
  }

  async function preserve() {
    if (!file || !listing || lock.current || disabled) return;
    lock.current = true; setBusy(true); setError(''); setNotice(''); setPreview(null);
    const controller = new AbortController(); operation.current = controller;
    try {
      const data = formData(); data.append('preset', preset); data.append('measures_per_line', String(measures));
      const result = await request<Omit<PreservedPreview, 'layout' | 'title'>>('/api/score-import/preserve-preview', { method: 'POST', body: data, signal: controller.signal });
      if (mounted.current && !controller.signal.aborted) setPreview({ ...result, title: listing.title, layout: { preset, measures_per_line: measures, show_numbers: true } });
    } catch (err) { if (mounted.current && !controller.signal.aborted) setError((err as Error).message); }
    finally { lock.current = false; if (mounted.current) setBusy(false); }
  }

  return <details className={`score-import ${doc ? '' : 'standalone'} ${expanded ? 'dedicated' : ''}`} open={expanded || undefined}><summary><FileMusic size={16} /> 기존 악보 가져와서 스타일 바꾸기 · MusicXML / PDF / 이미지</summary>
    <div className="score-import-modes" role="group" aria-label="가져올 악보 형식">{(['xml', 'omr'] as const).map(value => <button key={value} className="secondary small" aria-pressed={mode === value} disabled={locked} onClick={() => { if (mode !== value) { clearSelection(); setMode(value); } }}>{value === 'xml' ? 'MusicXML · 원본 악보 파일' : 'PDF·이미지 · 악보 인식'}</button>)}</div>
    {mode === 'xml' ? <>
      <p className="editor-help">음원 없이도 시작할 수 있어요. MusicXML은 원본 음표·TAB·성부·연주 기호를 유지하면서 스타일을 바꿉니다. 필요한 경우에만 수정 도구를 열어 음정·TAB 프렛·드럼 종류·리듬·가사를 고칠 수 있어요. PDF·이미지는 먼저 악보 인식과 원본 대조가 필요합니다.</p>
      <input ref={input} type="file" className="visually-hidden" aria-label="MusicXML 악보 파일 선택" accept=".musicxml,.xml,.mxl" disabled={locked} onChange={event => { void inspect(event.target.files?.[0]); event.target.value = ''; }} />
      <div className="bulk-row"><button className="secondary small" disabled={locked} onClick={() => input.current?.click()}>{busy ? <LoaderCircle className="spin" size={15} /> : <FileMusic size={15} />} {file ? '다른 악보 선택' : '악보 파일 선택'}</button>{file && <span>{file.name}</span>}</div>
    </> : <ScoreRecognition disabled={disabled || busy} onPrepared={inspect} onReset={clearSelection} onActivity={setRecognitionActive} onImported={doc ? undefined : onImported} onDirty={setRecognitionDirty} />}
    {listing && <section className="import-part-selection" aria-label="악보 파트와 가져오기 방식">
      <div className="bulk-row"><label>가져올 파트<select aria-label="가져올 파트" value={part} disabled={locked} onChange={event => { setPart(event.target.value); setReviewed(false); setPreview(null); setError(''); setNotice(''); }}>{listing.parts.map(item => <option key={item.id} value={item.id}>{item.name} · {item.measures}마디</option>)}</select></label>{!doc && <label>악기<select aria-label="가져올 악기" value={instrument} disabled={locked} onChange={event => { setInstrument(event.target.value as Instrument); setReviewed(false); }}><option value="bass">베이스 · TAB 우선</option><option value="guitar">기타 · TAB 우선</option><option value="drums">드럼</option><option value="piano">피아노</option><option value="synthesizer">키보드 / 신디사이저</option><option value="vocal">보컬</option></select></label>}</div>
      {recognition && <div className="recognition-review"><strong>원본 대조 후 가져와주세요</strong><p>음정·리듬·쉼표·드럼 타격 종류와 가사가 틀리거나 빠질 수 있어요. 인식되지 않은 가사는 자동으로 보완되지 않으며 원본 TAB 줄·프렛은 복원하지 않습니다. 원하는 스타일로 바뀌어도 인식 오류가 고쳐지는 것은 아니에요.</p>{recognition.warnings.length > 0 && <ul>{recognition.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>}<label className="check-label"><input type="checkbox" checked={reviewed} disabled={locked} onChange={event => setReviewed(event.target.checked)} /> 인식 오류·TAB 운지·가사 한계를 확인했으며, 원본과 비교하고 수정할 초안으로 가져옵니다.</label></div>}
      <div className="bulk-row"><button className="primary small" disabled={locked || (!!recognition && !reviewed)} onClick={() => void apply(!doc)}>{busy && <LoaderCircle className="spin" size={15} />}{doc ? '편집 초안에 가져오기' : '원본 유지 프로젝트 열기'}</button><span className="editor-help">{doc ? '지원하는 표기만 편집 격자로 변환합니다. 보존할 수 없는 표기는 오류로 안내하고 가져오지 않아요.' : '먼저 스타일 변경·출력 화면이 열립니다. 음악 내용은 유지하며, 틀린 부분이 있을 때만 수정 도구를 사용하세요.'}</span></div>
      {!doc && <details className="import-grid-options"><summary>고급 · 기존 16분음표 격자 편집기로 변환</summary><p className="editor-help">원본 유지 편집과 별도 경로입니다. 원본 보이스 분리·조표·이명동음 표기는 유지되지 않으며, 지원하지 않는 잇단음표·도돌이표 등은 가져오기를 거절합니다. TAB 운지가 없는 음은 자동 배정할 수 있어요.</p><button className="secondary small" disabled={locked || (!!recognition && !reviewed)} onClick={() => void apply()}>격자 편집 프로젝트 열기</button></details>}
      <details className="import-preserve-options"><summary>복잡한 표기도 원본 표기 유지 · 스타일 미리보기</summary><p className="editor-help">도돌이표·셋잇단음표 등을 편집 격자로 단순화하지 않고 MusicXML에서 배치만 조정합니다. 이 경로에서는 음표 직접 수정은 제공하지 않아요. 다운로드한 MusicXML은 지원하는 외부 악보 프로그램에서 편집·인쇄할 수 있습니다.</p><div className="bulk-row"><label>미리보기 스타일<select aria-label="미리보기 스타일" value={preset} disabled={locked} onChange={event => { setPreset(event.target.value as ScorePreset); setPreview(null); }}><option value="practice">합주용 · 가시성 우선</option><option value="standard">기본 악보</option><option value="large">큰 악보</option></select></label><label>한 줄 마디<select aria-label="미리보기 한 줄 마디" value={measures} disabled={locked} onChange={event => { setMeasures(Number(event.target.value) as 2 | 4); setPreview(null); }}><option value={4}>4마디</option><option value={2}>2마디</option></select></label><button className="secondary small" disabled={locked} onClick={() => void preserve()}>원본 표기 유지 · 스타일 미리보기</button></div></details>
    </section>}
    {error && <p className="editor-error" role="alert">{error}</p>}{notice && <p className="editor-success" role="status">{notice}</p>}
    {preview && <LayoutPreview preview={preview} instrument={doc?.instrument ?? instrument} />}
  </details>;
}
