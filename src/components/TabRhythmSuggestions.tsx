import { useEffect, useRef, useState } from 'react';
import { request } from '../api';
import type { TabReviewDraft, TabReviewState } from '../tabReview';
import { isRhythmProposal, type RhythmProposal } from '../tabRhythmSuggestions';

type Props = { jobId: string; staffId: string; state: TabReviewState; draft: TabReviewDraft; disabled: boolean;
  onActivity: (active: boolean) => void; onApply: (proposal: RhythmProposal, measure: number, firstMeasure: number) => void };

export default function TabRhythmSuggestions({ jobId, staffId, state, draft, disabled, onActivity, onApply }: Props) {
  const [proposal, setProposal] = useState<RhythmProposal | null>(null);
  const [meterChecked, setMeterChecked] = useState(false), [firstMeasure, setFirstMeasure] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const controller = useRef<AbortController | null>(null), lock = useRef(false), generation = useRef(0);
  useEffect(() => { onActivity(busy); return () => onActivity(false); }, [busy, onActivity]);
  useEffect(() => {
    generation.current++; controller.current?.abort(); lock.current = false; setBusy(false); setProposal(null); setMeterChecked(false); setFirstMeasure(''); setError('');
    return () => { generation.current++; controller.current?.abort(); };
  }, [jobId, staffId, state.revision, draft.beats, draft.beat_type]);
  async function analyze() {
    if (disabled || busy || lock.current || !meterChecked) return;
    lock.current = true; setBusy(true); setError(''); setProposal(null);
    const current = generation.current, abort = new AbortController(); controller.current = abort;
    try {
      const result = await request<RhythmProposal>(`/api/score-omr/${encodeURIComponent(jobId)}/tab-review/rhythm-suggestions`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ base_revision: state.revision, staff_id: staffId, beats: draft.beats, beat_type: draft.beat_type, meter_confirmed: true }), signal: abort.signal,
      });
      if (abort.signal.aborted || current !== generation.current) return;
      if (!isRhythmProposal(result) || result.base_revision !== state.revision || result.source_sha256 !== state.source_sha256 || result.coordinate_sha256 !== state.coordinate_sha256 || result.staff_id !== staffId || result.meter.beats !== draft.beats || result.meter.beat_type !== draft.beat_type) throw new Error('현재 검수 자료와 다른 응답입니다. 다시 시도해주세요.');
      setProposal(result);
    } catch (err) { if (!abort.signal.aborted && current === generation.current) setError((err as Error).message); }
    finally { if (current === generation.current) { lock.current = false; setBusy(false); } }
  }
  return <details className="tab-review-rhythm"><summary>오선 표기에서 리듬 후보 자동 읽기 · 검토 후 적용</summary>
    <p className="editor-help">TAB 위 오선의 음표머리·기둥·연결선·점을 직접 읽습니다. 숫자 사이 간격으로 음 길이를 채우지 않습니다. 지원하지 않는 기호, 모호한 연결이나 마디 길이 불일치는 그 마디 전체를 보류합니다. 스캔 PDF·TAB 단독에는 후보가 없을 수 있어요.</p>
    <label className="check-label"><input aria-label="리듬 후보 박자 확인" type="checkbox" disabled={disabled || busy} checked={meterChecked} onChange={event => setMeterChecked(event.target.checked)} />원본의 박자표가 현재 입력한 {draft.beats}/{draft.beat_type}임을 확인했어요.</label>
    <button className="secondary small" disabled={disabled || busy || !meterChecked} onClick={() => void analyze()}>{busy ? '원본 리듬 표기 읽는 중…' : '현재 보표의 리듬 후보 읽기'}</button>
    {error && <p className="editor-error" role="alert">{error}</p>}
    {proposal && <>
      <p role="status">리듬 후보가 있는 마디 {proposal.measures.filter(item => item.status === 'suggested').length}/{proposal.measures.length}개. 원본 대조 전에는 정확한 악보로 확정하지 않습니다.</p>
      <label>이 TAB 보표의 실제 시작 마디<input aria-label="리듬 후보 실제 시작 마디" type="number" min={1} max={600} disabled={disabled || busy} value={firstMeasure} onChange={event => setFirstMeasure(event.target.value)} placeholder="원본 마디 번호를 입력" /></label>
      <ul className="recognition-warnings">{proposal.measures.map(item => <li key={item.staff_measure_id}><strong>보표 안 {item.measure_index}번째 마디</strong>{item.status === 'suggested' ? <> · 숫자 {item.rows.length}개 대응 <button className="text-button" disabled={disabled || busy || !firstMeasure.trim()} onClick={() => { if (window.confirm('이 마디의 기존 리듬 입력을 후보로 교체할까요? 직접 수정한 줄·프렛은 유지합니다. 적용 후 원본을 다시 확인하고 초안을 저장해주세요.')) onApply(proposal, item.measure_index, Number(firstMeasure)); }}>이 마디 후보 적용</button></> : <> · 자동 적용 보류: {item.unresolved.join(' ')}</>}</li>)}</ul>
      <p className="editor-help">후보 적용은 화면의 초안만 바꾸며 서버 저장·프로젝트 생성·최종 확인을 대신하지 않습니다.</p>
    </>}
  </details>;
}
