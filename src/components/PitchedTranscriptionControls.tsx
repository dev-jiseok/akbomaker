import type { Instrument, PitchedEngine, TranscriptionInfo } from '../types';

export function supportsPitchedReview(instrument: Instrument): instrument is 'guitar' | 'piano' | 'synthesizer' {
  return instrument === 'guitar' || instrument === 'piano' || instrument === 'synthesizer';
}

export function supportsPitchedComparison(instrument: Instrument): instrument is 'synthesizer' {
  return instrument === 'synthesizer';
}

type Props = {
  jobId: string; instrument: 'guitar' | 'piano' | 'synthesizer'; revision?: string;
  info?: TranscriptionInfo | null; demo: boolean; disabled: boolean;
  engine: PitchedEngine; onEngine: (value: PitchedEngine) => void;
};

export default function PitchedTranscriptionControls({ jobId, instrument, revision, info, demo, disabled, engine, onEngine }: Props) {
  const review = info?.pitched_postprocessing;
  const canCompare = supportsPitchedComparison(instrument);
  if (!canCompare && info?.pitched_review !== true) return null;
  return <section className="drum-transcription-controls" aria-label={canCompare ? '신디 지속음 채보 비교 설정' : '채보 당시 검토 데이터'}>
    {canCompare && <div className="drum-transcription-options">
      <label>신디 지속음 채보 방식<select aria-label="신디 지속음 채보 방식" value={engine} disabled={disabled || demo} onChange={e => onEngine(e.target.value as PitchedEngine)}>
        <option value="standard">기존 채보</option>
        <option value="adaptive">지속음 보강 (실험)</option>
      </select></label>
    </div>}
    {canCompare && <p>{demo ? '샘플 악보는 작곡 데이터입니다. 실제 모델 비교는 업로드한 음원에서 실행해주세요.' : '선택 후 다시 채보하기를 누르면 현재 악보를 대체합니다. 모든 악기 함께 채보는 기존 방식으로 실행해요.'}</p>}
    {!canCompare && <p>기타·피아노의 보강 방식은 합성 음원 검증에서 오인식이 늘어 선택할 수 없어요. 아래에는 이전 채보 당시의 근거만 보관됩니다.</p>}
    {canCompare && !demo && engine === 'adaptive' && <small>음 시작점(온셋)과 지속음 근거를 함께 검토하는 신디 실험 방식입니다. 약한 타격이나 천천히 시작하는 패드 음이 빠질 수 있고 음이 지나치게 길게 남을 수도 있어요. 실제 곡에서 정확도 향상이 입증된 것은 아니며, 옥타브가 겹쳤다는 이유만으로 음표를 삭제하거나 음정을 바꾸지 않습니다.</small>}
    {info && !demo && <p className="drum-result-summary">채보 당시 방식: {info.engine === 'basic-pitch-adaptive-v1' ? '지속음 보강 (실험)' : info.engine === 'basic-pitch-instrument-v2' ? '기존 채보' : info.engine}
      {review && <> · 박자 정렬 전 기존 후보 {review.baseline_count}개 / 보강 결과 {review.output_count}개 · 옥타브 겹침 {review.octave_overlap_count}개{review.octave_overlap_truncated ? ' 이상 (기록 상한 도달)' : ''} (오류 판정 아님)</>}
    </p>}
    {info?.pitched_review === true && <details>
      <summary>음표 검출 근거·검토 데이터</summary>
      <p>채보 당시 기존·보강 음표와 온셋·지속음 반응값을 보관합니다. 반응값은 정답 확률이나 신뢰도가 아니며, 이후 악보 수정은 포함하지 않아요. 옥타브 겹침은 정상 화음일 수 있으니 음원을 함께 확인해주세요.</p>
      <div className="drum-raw-downloads"><a href={`/api/jobs/${jobId}/files/${instrument}.transcription.json?download=true&v=${revision || ''}`}>채보 근거·검토 JSON</a></div>
    </details>}
  </section>;
}
