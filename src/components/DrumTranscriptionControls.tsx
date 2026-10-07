import type { DrumEngine, Health, TranscriptionInfo } from '../types';

type Props = {
  jobId: string; revision?: string; info?: TranscriptionInfo | null; health: Health | null;
  demo: boolean; disabled: boolean; hasStem: boolean; engine: DrumEngine; source: 'stem' | 'original';
  onEngine: (value: DrumEngine) => void; onSource: (value: 'stem' | 'original') => void;
};

export default function DrumTranscriptionControls({ jobId, revision, info, health, demo, disabled, hasStem, engine, source, onEngine, onSource }: Props) {
  const ready = health?.drum_engine?.paths_ready;
  const review = info?.review;
  const files = `/api/jobs/${jobId}/files/`;
  return <section className="drum-transcription-controls" aria-label="드럼 채보 비교 설정">
    <div className="drum-transcription-options">
      <label>드럼 채보 엔진<select aria-label="드럼 채보 엔진" value={engine} disabled={disabled || demo} onChange={e => onEngine(e.target.value as Props['engine'])}>
        <option value="auto">자동 · {ready ? '전용 AI' : '경량 검출'}</option>
        <option value="neural" disabled={!ready}>전용 AI · ADT_STR{!ready ? ' (서버 설정 필요)' : ''}</option>
        <option value="consensus" disabled={!ready}>교차검증 AI · 3회 분석 (실험){!ready ? ' (서버 설정 필요)' : ''}</option>
        <option value="hybrid" disabled={!ready}>보강 AI · 하이햇 보완 (실험){!ready ? ' (서버 설정 필요)' : ''}</option>
        <option value="spectral">경량 검출 · 킥/스네어/하이햇</option>
      </select></label>
      <label>비교할 음원<select aria-label="드럼 채보 입력 음원" value={source} disabled={disabled || demo} onChange={e => onSource(e.target.value as Props['source'])}>
        <option value="stem" disabled={!hasStem}>분리된 드럼{!hasStem ? ' (분리 전)' : ''}</option><option value="original">원본 전체 음원</option>
      </select></label>
    </div>
    <p>{demo ? '샘플 악보는 작곡 데이터로 생성돼요. 실제 모델 비교는 업로드한 음원에서 실행해주세요.' : '설정을 고른 뒤 다시 채보하기를 누르면 현재 악보를 대체해요. 분리 과정에서 타격이 빠졌다면 원본 음원으로도 비교해보세요.'}</p>
    {!demo && !ready && <small>전용 AI는 별도 실행 환경 설정이 필요해요. 경량 검출은 탐·심벌 종류를 자동으로 구분하지 못합니다.</small>}
    {!demo && engine === 'hybrid' && <small>입력 음량 보정·하이햇 보완을 적용합니다. 원본 합주 음원에서는 다른 악기를 하이햇으로 오인할 수 있어요. 분리된 드럼이나 드럼 단독 녹음으로 비교해주세요.</small>}
    {!demo && engine === 'consensus' && <small>구간 경계를 바꿔 3번 분석하고, 같은 종류·시각이 2번 이상 일치한 타격을 남깁니다. 더 오래 걸리고 실제 타격도 빠질 수 있어요. 하이햇을 임의로 추가하거나 킥을 다른 종류로 바꾸지 않습니다.</small>}
    {info && !demo && <p className="drum-result-summary">현재 결과: {info.engine === 'adt-str-consensus-v1' ? '교차검증 AI (실험)' : info.engine === 'adt-str-hybrid-v1' ? '보강 AI (실험)' : info.engine === 'adt-str' ? 'ADT_STR 전용 AI' : info.engine === 'multiband-onsets-v2' ? '경량 검출' : info.engine} · {info.source === 'original' ? '원본 전체 음원' : '분리된 드럼'}{review && <> · AI 타격 {review.raw_note_count}개{review.unsupported_count > 0 && <> · 악보 미표시 {review.unsupported_count}개</>}</>}{info.recovery && <> · 보완 하이햇 {info.recovery.added_count}개</>}{info.consensus?.method && <> · 일치 부족 후보 {info.consensus.rejected_count}개 보류</>}</p>}
    {info?.raw_midi && <details><summary>AI 원본 타격·검토 데이터</summary>
      <p>박자 격자에 맞추기 전의 결과예요. 세부 탐·심벌 변종과 악보에서 지원하지 않는 타악기도 MIDI에 남아 있어요. 수정한 악보의 음표 수와 다를 수 있습니다.</p>
      {info.recovery && <p>원본 MIDI에는 보완 하이햇이 포함되지 않습니다. 검토 JSON의 recovery.added_events에는 추가된 타격, score_events에는 보완 후 악보용 타격이 별도로 남습니다.</p>}
      {info.consensus?.method && <p>이 모드의 MIDI는 교차검증 통과 타격입니다. 검토 JSON의 runtime.consensus.pass_events와 candidates에 3회 전체 결과와 보류 후보가 남습니다. 일치 횟수는 정답 확률이 아닙니다.</p>}
      <div className="drum-raw-downloads"><a href={`${files}drums.raw.mid?download=true&v=${revision || ''}`}>원본 타격 MIDI</a><a href={`${files}drums.transcription.json?download=true&v=${revision || ''}`}>검토 JSON</a></div>
      {review && Object.keys(review.simplified_counts).length > 0 && <small>GM 변종 통합: {Object.entries(review.simplified_counts).map(([mapping, count]) => `${mapping} (${count}개)`).join(', ')}</small>}
    </details>}
  </section>;
}
