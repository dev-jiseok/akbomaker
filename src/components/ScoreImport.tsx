import { useRef, useState } from 'react';
import { FileMusic, LoaderCircle } from 'lucide-react';
import { request } from '../api';
import type { Instrument, Job, ScoreDocument } from '../types';

type Inspection = { title: string; parts: { id: string; name: string; measures: number }[] };
type Props = { disabled: boolean; onImported?: (job: Job) => void; endpoint?: string; document?: ScoreDocument; onDocument?: (doc: ScoreDocument) => void };
export default function ScoreImport({ disabled, onImported, endpoint, document: doc, onDocument }: Props) {
  const [file, setFile] = useState<File | null>(null), [listing, setListing] = useState<Inspection | null>(null);
  const [part, setPart] = useState(''), [instrument, setInstrument] = useState<Instrument>('bass');
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const input = useRef<HTMLInputElement>(null);
  const lock = useRef(false);
  async function inspect(value?: File) {
    if (!value || lock.current) return;
    setFile(null); setListing(null); setError(''); setNotice('');
    if (!/\.(musicxml|xml|mxl)$/i.test(value.name) || !value.size || value.size > 2 * 1024 * 1024) return setError('2MB 이하의 .musicxml · .xml · .mxl 파일을 선택해주세요. PDF는 아직 지원하지 않아요.');
    lock.current = true; setBusy(true);
    try {
      const data = new FormData(); data.append('file', value);
      const result = await request<Inspection>('/api/score-import/inspect', { method: 'POST', body: data });
      setFile(value); setListing(result); setPart(result.parts[0].id);
    } catch (err) { setError((err as Error).message); }
    finally { lock.current = false; setBusy(false); }
  }
  async function apply() {
    if (!file || !listing || lock.current) return;
    if (doc && !window.confirm('선택한 MusicXML 파트로 현재 편집 음표·가사·메모를 대체할까요? 원본 음원·재생 기준·출력 스타일은 유지합니다. 실행 취소로 복원할 수 있고, 저장하기 전에는 서버 파일을 바꾸지 않습니다.')) return;
    lock.current = true; setBusy(true); setError('');
    try {
      const data = new FormData(); data.append('file', file); data.append('part_id', part);
      if (doc && endpoint) {
        data.append('base_revision', doc.revision);
        const result = await request<{ document: ScoreDocument; warnings: string[] }>(endpoint + '/import-preview', { method: 'POST', body: data });
        onDocument?.(result.document); setNotice('편집 초안에 가져왔어요. 미리보기 확인 후 저장해주세요. ' + result.warnings.join(' '));
      } else {
        data.append('instrument', instrument);
        onImported?.(await request<Job>('/api/score-import', { method: 'POST', body: data }));
      }
    } catch (err) { setError((err as Error).message); }
    finally { lock.current = false; setBusy(false); }
  }
  return <details className={`score-import ${doc ? '' : 'standalone'}`}><summary><FileMusic size={16} /> 기존 악보 가져와서 스타일 바꾸기 · MusicXML</summary><p className="editor-help">음원 없이도 시작할 수 있어요. 현재는 고정 템포·4/4·16분음표 격자로 보존 가능한 파트를 지원합니다. 변박·셋잇단음표·도돌이표 등 미지원 표기는 안내 후 중단해요. PDF/이미지 인식은 후속 기능입니다.</p>
    <input ref={input} type="file" className="visually-hidden" aria-label="MusicXML 악보 파일 선택" accept=".musicxml,.xml,.mxl" disabled={disabled || busy} onChange={event => { void inspect(event.target.files?.[0]); event.target.value = ''; }} />
    <div className="bulk-row"><button className="secondary small" disabled={disabled || busy} onClick={() => input.current?.click()}>{busy ? <LoaderCircle className="spin" size={15} /> : <FileMusic size={15} />} {file ? '다른 악보 선택' : '악보 파일 선택'}</button>{file && <span>{file.name}</span>}{listing && <><label>가져올 파트<select value={part} disabled={disabled || busy} onChange={e => setPart(e.target.value)}>{listing.parts.map(p => <option key={p.id} value={p.id}>{p.name} · {p.measures}마디</option>)}</select></label>{!doc && <label>악기<select value={instrument} disabled={disabled || busy} onChange={e => setInstrument(e.target.value as Instrument)}><option value="bass">베이스 · TAB 우선</option><option value="guitar">기타 · TAB 우선</option><option value="drums">드럼</option><option value="piano">피아노</option><option value="synthesizer">키보드 / 신디사이저</option><option value="vocal">보컬</option></select></label>}<button className="primary small" disabled={disabled || busy} onClick={() => void apply()}>{doc ? '편집 초안에 가져오기' : '악보 프로젝트 열기'}</button></>}</div>
    {error && <p className="editor-error" role="alert">{error}</p>}{notice && <p className="editor-success" role="status">{notice}</p>}
  </details>;
}
