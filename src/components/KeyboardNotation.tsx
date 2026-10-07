import type { ScoreDocument, ScoreNote } from '../types';
import { pitchLabel } from '../scoreEditing';

type Props = { document: ScoreDocument; selected?: ScoreNote; disabled: boolean; onChange: (doc: ScoreDocument) => void; onNoteChange: (values: Partial<ScoreNote>) => void };

export default function KeyboardNotation({ document, selected, disabled, onChange, onNoteChange }: Props) {
  if (!['piano', 'synthesizer'].includes(document.instrument)) return null;
  const keyboard = document.keyboard || { mode: 'single' as const, split_pitch: 60 };
  return <fieldset className="keyboard-notation" disabled={disabled}>
    <div className="bulk-row"><label>건반 악보 형식<select value={keyboard.mode} onChange={e => onChange({ ...document, keyboard: { ...keyboard, mode: e.target.value as 'grand' | 'single' } })}>
      <option value="grand">대보표 · 오른손 + 왼손</option><option value="single">단일 보표 · 리드/멜로디</option>
    </select></label>{keyboard.mode === 'grand' && <label>자동 배정 경계음<select value={keyboard.split_pitch} onChange={e => onChange({ ...document, keyboard: { ...keyboard, split_pitch: Number(e.target.value) } })}>{Array.from({ length: 88 }, (_, i) => i + 21).map(p => <option key={p} value={p}>{pitchLabel(p)}</option>)}</select></label>}
    {selected && keyboard.mode === 'grand' && <label>선택 음표의 손<select value={selected.hand || 'auto'} onChange={e => onNoteChange({ hand: e.target.value as ScoreNote['hand'] })}><option value="auto">음역으로 자동 배정</option><option value="right">오른손 · 위 보표</option><option value="left">왼손 · 아래 보표</option></select></label>}</div>
    <p>{keyboard.mode === 'grand' ? '위는 높은음자리, 아래는 낮은음자리로 표시합니다. 자동 배정은 연주법의 정답이 아니므로 음표를 선택해 손을 바꿀 수 있어요.' : '리드·멜로디를 높은음자리 한 보표로 표시합니다. 저장된 손 배정은 대보표로 돌아가면 다시 사용해요.'} 보표를 바꿔도 음정·길이·재생은 그대로예요.</p>
  </fieldset>;
}
