import { useEffect, useRef, useState } from 'react';
import { formatTime, type Job } from '../types';
import { useAudioFocus } from '../audioFocus';

type Props = { job: Job; bpm: number; offset: number; disabled: boolean; onBpm: (bpm: number) => void; onOffset: (seconds: number) => void; onAnalyze: () => void };
export default function RhythmWorkbench({ job, bpm, offset, disabled, onBpm, onOffset, onAnalyze }: Props) {
  const [expanded, setExpanded] = useState(!!job.analysis_only);
  const audio = useRef<HTMLAudioElement>(null);
  const claimFocus = useAudioFocus(() => audio.current?.pause());
  useEffect(() => { const player = audio.current; return () => player?.pause(); }, [job.original_url]);
  useEffect(() => { if (!expanded) audio.current?.pause(); }, [expanded]);
  const result = job.rhythm_analysis;
  const evidence = result?.evidence;
  return <section className="rhythm-workbench">
    <div className="analysis-heading"><div><span className="eyebrow">RHYTHM FIRST</span><h2>템포와 악보 시작 위치</h2><p>{result ? `추천 ${result.bpm} BPM · 첫 검출 박 ${result.first_beat_seconds}초` : '자동 분석은 참고값이에요. 마디의 첫 박은 원본을 듣고 직접 맞춰주세요.'}</p></div><button className="secondary small" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? '설정 접기' : '분석 · 설정 열기'}</button></div>
    <div hidden={!expanded}>
      <div className="analysis-controls"><label>다음 채보 BPM<input aria-label="분석 채보 BPM" type="number" min={40} max={240} value={bpm} onChange={e => onBpm(Math.min(240, Math.max(40, Math.round(Number(e.target.value)))))} disabled={disabled} /></label><label>악보 첫 박의 원본 위치 (초)<input aria-label="악보 시작 오프셋" type="number" step={.01} min={0} max={job.duration || 600} value={offset} onChange={e => onOffset(Math.min(job.duration || 600, Math.max(0, Number(e.target.value))))} disabled={disabled} /></label><button className="secondary small" disabled={disabled} onClick={onAnalyze}>BPM · 박 위치 분석</button></div>
      <audio controls ref={audio} onPlay={event => { if (!event.currentTarget.paused) claimFocus(); }} src={job.original_url || undefined} preload="metadata" aria-label="템포·시작 위치 확인용 원본 음원" />
      {result && <div className="rhythm-result"><strong>추천 {result.bpm} BPM · 첫 검출 박 {result.first_beat_seconds}초</strong><span>분석 구간 {formatTime(result.analyzed_seconds)} · 박 간격 규칙성 {Math.round(result.regularity * 100)}% (정확도 점수가 아닙니다)</span><div>{result.alternatives.map(tempo => <button className="secondary small" key={tempo} disabled={disabled} onClick={() => onBpm(tempo)}>{tempo} BPM 사용</button>)}<button className="secondary small" disabled={disabled} onClick={() => onOffset(result.first_beat_seconds)}>시작 위치 {result.first_beat_seconds}초 적용</button></div><p className="editor-help">BPM 버튼은 템포만 변경합니다. 시작 위치 적용은 앞 구간을 악보에서 제외하므로 인트로를 유지하려면 0초로 두세요. {result.warning}</p></div>}
      {evidence && <div className="rhythm-result" aria-label="박자 정확성 검토 근거">
        <strong>일정한 템포로 옮겨도 맞을까요?</strong>
        <span>분석 추천 {result?.bpm} BPM 기준 누적 밀림 {Math.abs(evidence.grid_fit.baseline_end_drift_seconds).toFixed(3)}초 · 격자와의 차이(95백분위) {evidence.grid_fit.p95_deviation_seconds.toFixed(3)}초</span>
        <p className="editor-help">검출된 박을 일정한 템포에 맞췄을 때의 차이예요. 원곡의 정답 오차나 정확도 점수가 아니며, 마디 첫 박도 확정하지 않습니다.</p>
        {evidence.grid_fit.status === 'stable_fit' && evidence.grid_fit.candidate_bpm !== null && evidence.grid_fit.candidate_bpm !== result?.bpm &&
          <button className="secondary small" disabled={disabled} onClick={() => onBpm(evidence.grid_fit.candidate_bpm!)}>박열 적합 {evidence.grid_fit.candidate_bpm} BPM 비교</button>}
        {evidence.local_tempo.status === 'variation_requires_review' && <p className="score-warning">구간마다 리듬 주기가 달라요. 템포 변화·반속/배속·리듬 변화 중 무엇인지 확인해주세요. 현재 악보는 일정 BPM이므로 이 변화를 자동 반영하지 않습니다.</p>}
        {evidence.local_tempo.range_bpm && <span>구간별 주기 후보 {evidence.local_tempo.range_bpm[0]}–{evidence.local_tempo.range_bpm[1]} BPM · 실제 템포 범위 확정 아님</span>}
        {evidence.warnings.map((warning, index) => <p className="editor-help" key={index}>{warning}</p>)}
      </div>}
      <p className="editor-help">이 설정은 다음 채보·빈 악보 생성에만 적용됩니다. 기존 악보는 자동 변경하지 않습니다. 시작 위치 이전 구간은 악보에서 제외되므로 인트로도 표시하려면 0초를 유지해주세요. BPM은 4분음표 기준이고 박자는 아래에서 선택합니다. 마디별 변박은 악보 직접 수정에서 지정해주세요.</p>
    </div>
  </section>;
}
