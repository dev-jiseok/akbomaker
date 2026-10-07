import type { Job, SeparationStrategy } from '../types';

export default function SeparationOptions({ value, disabled, onChange }: {
  value: SeparationStrategy; disabled: boolean; onChange: (value: SeparationStrategy) => void;
}) {
  return <div className="separation-options">
    <label>악기 분리 방식<select aria-label="악기 분리 방식" value={value} disabled={disabled}
      onChange={event => onChange(event.target.value as SeparationStrategy)}>
      <option value="sequential">순차 분리 · 기존 방식</option>
      <option value="independent">원곡에서 각각 분리 · 비교용 실험</option>
    </select></label>
    <p className="editor-help">{value === 'independent'
      ? '여섯 악기를 각각 원곡에서 추출해 앞 단계의 소리 제거 영향을 비교해요. 더 정확하다는 보장은 없고, 같은 소리가 여러 파트에 남을 수 있어요. 합산용 잔여 음원은 만들지 않습니다.'
      : '앞에서 분리한 소리를 뺀 뒤 다음 악기를 추출해요. 원곡 독립 분리와 비교하려면 같은 곡으로 새 프로젝트를 만들어주세요.'}</p>
  </div>;
}

export function SeparationNotice({ job }: { job: Job }) {
  if (job.demo || job.analysis_only || job.source_type === 'musicxml' || job.separation_strategy !== 'independent') return null;
  return <p className="score-warning" role="status">원곡 독립 분리 실험: 각 악기를 원곡에서 따로 추출하도록 선택했어요.
    같은 소리가 여러 파트에 남을 수 있어 합산용 잔여 음원은 제공하지 않습니다. 정확도 향상이 검증된 모드는 아니에요.</p>;
}
