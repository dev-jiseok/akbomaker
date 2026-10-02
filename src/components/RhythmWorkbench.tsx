import { useState } from 'react';
import { formatTime, type Job } from '../types';

type Props = { job: Job; bpm: number; offset: number; disabled: boolean; onBpm: (bpm: number) => void; onOffset: (seconds: number) => void; onAnalyze: () => void };
export default function RhythmWorkbench({ job, bpm, offset, disabled, onBpm, onOffset, onAnalyze }: Props) {
  const [expanded, setExpanded] = useState(!!job.analysis_only);
  const result = job.rhythm_analysis;
  return <section className="rhythm-workbench">
    <div className="analysis-heading"><div><span className="eyebrow">RHYTHM FIRST</span><h2>템포와 악보 시작 위치</h2><p>{result ? `추천 ${result.bpm} BPM · 첫 검출 박 ${result.first_beat_seconds}초` : '자동 분석은 참고값이에요. 마디의 첫 박은 원본을 듣고 직접 맞춰주세요.'}</p></div><button className="secondary small" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? '설정 접기' : '분석 · 설정 열기'}</button></div>
    <div hidden={!expanded}>
      <div className="analysis-controls"><label>다음 채보 BPM<input aria-label="분석 채보 BPM" type="number" min={40} max={240} value={bpm} onChange={e => onBpm(Math.min(240, Math.max(40, Math.round(Number(e.target.value)))))} disabled={disabled} /></label><label>악보 첫 박의 원본 위치 (초)<input aria-label="악보 시작 오프셋" type="number" step={.01} min={0} max={job.duration || 600} value={offset} onChange={e => onOffset(Math.min(job.duration || 600, Math.max(0, Number(e.target.value))))} disabled={disabled} /></label><button className="secondary small" disabled={disabled} onClick={onAnalyze}>BPM · 박 위치 분석</button></div>
      <audio controls src={job.original_url || undefined} preload="metadata" aria-label="템포·시작 위치 확인용 원본 음원" />
      {result && <div className="rhythm-result"><strong>추천 {result.bpm} BPM · 첫 검출 박 {result.first_beat_seconds}초</strong><span>분석 구간 {formatTime(result.analyzed_seconds)} · 박 간격 규칙성 {Math.round(result.regularity * 100)}% (정확도 점수가 아닙니다)</span><div>{result.alternatives.map(tempo => <button className="secondary small" key={tempo} disabled={disabled} onClick={() => { onBpm(tempo); onOffset(result.first_beat_seconds); }}>{tempo} BPM 사용</button>)}</div><p className="editor-help">{result.warning}</p></div>}
      <p className="editor-help">이 설정은 다음 채보·빈 악보 생성에만 적용됩니다. 기존 악보는 자동 변경하지 않습니다. 시작 위치 이전 구간은 악보에서 제외되므로 인트로도 표시하려면 0초를 유지해주세요. BPM은 4분음표 기준이고 박자는 아래에서 선택합니다. 마디별 변박은 악보 직접 수정에서 지정해주세요.</p>
    </div>
  </section>;
}
