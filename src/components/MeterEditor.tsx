import { useEffect, useState } from 'react';
import { defaultMeters, measureMap, meterOptions, rebar, setMeter } from '../scoreRhythm';
import type { ScoreDocument } from '../types';

type Props = { document: ScoreDocument; measure: number; disabled: boolean; onChange: (doc: ScoreDocument) => void; onError: (error: string) => void; onNotice: (notice: string) => void };
export default function MeterEditor({ document: doc, measure, disabled, onChange, onError, onNotice }: Props) {
  const bar = measureMap(doc)[measure - 1], meters = doc.meters || defaultMeters;
  const current = `${bar.beats}/${bar.beat_type}`;
  const [value, setValue] = useState(current);
  useEffect(() => { setValue(current); }, [current, measure]);
  function apply(next: () => ScoreDocument) {
    try {
      const updated = next();
      if (!window.confirm('박자를 변경할까요? 음표·가사는 시간 위치를 유지합니다. 구간 메모와 줄/페이지 시작은 기존 위치를 포함하는 새 마디로 옮기고, 마지막 마디의 부족한 길이는 쉼표로 채웁니다. 저장 전에는 실행 취소할 수 있어요.')) return;
      onChange(updated); onNotice('박자표를 변경했어요. 음표·가사와 구간 표시 위치를 미리보기에서 확인해주세요.');
    } catch (e) { onError((e as Error).message); }
  }
  return <fieldset className="meter-editor" disabled={disabled}><div className="bulk-row"><strong>박자표 · 변박</strong><label>{measure}마디부터<select aria-label="현재 마디부터 적용할 박자" value={value} onChange={e => setValue(e.target.value)}>{meterOptions.map(m => <option key={m}>{m}</option>)}</select></label><button className="secondary small" disabled={value === current} onClick={() => apply(() => setMeter(doc, measure, value))}>박자 적용</button></div><p className="editor-help">마디를 선택해 그 마디부터 박자를 바꿔요. BPM은 항상 4분음표 기준이며 6/8의 두 큰 박은 점4분음표입니다. 16분음표 격자는 유지하고, 잇단음표는 아직 지원하지 않아요.</p><div className="meter-changes">{meters.map(m => <span key={m.measure}>{m.measure}마디 · {m.beats}/{m.beat_type}{m.measure > 1 && <button className="text-button" aria-label={`${m.measure}마디 박자 변경 삭제`} onClick={() => apply(() => rebar(doc, meters.filter(item => item.measure !== m.measure)))}>변경 삭제</button>}</span>)}</div></fieldset>;
}
