import { useState } from 'react';
import { pitchLabel, toggleTabNote } from '../scoreEditing';
import type { ScoreDocument } from '../types';

type Props = { document: ScoreDocument; measure: number; length: number; disabled: boolean; onChange: (doc: ScoreDocument) => void; onSelect: (id: string) => void; onError: (error: string) => void };

export default function TabEditor({ document: doc, measure, length, disabled, onChange, onSelect, onError }: Props) {
  const [fret, setFret] = useState(3);
  if (!doc.tab) return null;
  const start = (measure - 1) * 16;
  const presets = doc.instrument === 'bass' ? [
    ['4현 · E A D G', [43, 38, 33, 28]], ['4현 · Drop D', [43, 38, 33, 26]], ['5현 · B E A D G', [43, 38, 33, 28, 23]], ['6현 · B E A D G C', [48, 43, 38, 33, 28, 23]],
  ] as const : [['6현 · E A D G B E', [64, 59, 55, 50, 45, 40]], ['6현 · Drop D', [64, 59, 55, 50, 45, 38]], ['7현 · B E A D G B E', [64, 59, 55, 50, 45, 40, 35]]] as const;
  const unassigned = doc.notes.filter(n => n.string == null).length;
  return <fieldset className="tab-editor grid-fieldset" disabled={disabled}>
    <div className="tab-settings"><label>악보 표기<select value={doc.tab.mode} onChange={e => onChange({ ...doc, tab: { ...doc.tab!, mode: e.target.value as 'staff' | 'both' | 'tab' } })}><option value="both">오선 + TAB</option><option value="tab">TAB만</option><option value="staff">오선만</option></select></label><label>튜닝<select value={presets.findIndex(([, tuning]) => tuning.join() === doc.tab!.tuning.join())} onChange={e => {
      const tuning = [...presets[Number(e.target.value)][1]];
      onChange({ ...doc, tab: { ...doc.tab!, tuning }, notes: doc.notes.map(n => ({ ...n, string: null, fret: null })) });
    }}>{presets.map(([name], i) => <option value={i} key={name}>{name}</option>)}{!presets.some(([, t]) => t.join() === doc.tab!.tuning.join()) && <option value={-1}>사용자 튜닝</option>}</select></label><label>입력 프렛<input type="number" min={0} max={24} value={fret} onChange={e => setFret(Math.min(24, Math.max(0, Math.round(Number(e.target.value)))))} /></label></div>
    <p className="editor-help">빈칸을 누르면 선택한 프렛의 음표가 들어갑니다. 숫자를 누르면 선택하고, 더블클릭하면 삭제합니다. 1번 줄은 가장 높은 줄입니다. <span>0 = 개방현</span></p>
    {unassigned > 0 && <p className="tab-warning" role="status">운지 미배정 {unassigned}개 · 저장할 때 가능한 음은 자동 배정합니다. 음역·동시 발음 수를 벗어난 음은 오선에 남고 TAB에서는 빠집니다.</p>}
    <div className="note-grid-scroll"><div className="note-grid tab-grid" role="group" aria-label={`${measure}마디 TAB 입력`}><span className="lane-label grid-corner">줄 / 개방음</span>{Array.from({ length: 16 }, (_, tick) => <span className={`grid-tick ${tick % 4 === 0 ? 'beat' : ''}`} key={tick}>{tick % 4 === 0 ? tick / 4 + 1 : ['e', '&', 'a'][tick % 4 - 1]}</span>)}
      {doc.tab.tuning.map((pitch, index) => <div className="grid-lane" key={index}><span className="lane-label">{index + 1} · {pitchLabel(pitch)}</span>{Array.from({ length: 16 }, (_, tick) => {
        const position = start + tick;
        const note = doc.notes.find(n => n.string === index + 1 && n.start === position);
        const sustain = doc.notes.find(n => n.string === index + 1 && n.start < position && n.start + n.length > position);
        const held = !!sustain;
        const toggle = () => { try { onChange(toggleTabNote(doc, index + 1, fret, position, length, crypto.randomUUID())); } catch (e) { onError((e as Error).message); } };
        return <button className={`note-cell ${tick % 4 === 0 ? 'beat-start' : ''} ${note ? 'on' : held ? 'held' : ''}`} key={tick} title={held ? '앞 음표가 이어지는 칸 · 클릭하여 길이 수정' : note ? '음표 시작 · 클릭하여 수정, 더블클릭하여 삭제' : '음표 추가'} aria-label={`${index + 1}번 줄 ${measure}마디 ${tick + 1}번째 칸${note ? ` ${note.fret}프렛` : held ? ' 앞 음표 이어짐' : ''}`} aria-pressed={!!note} onClick={() => note || sustain ? onSelect((note || sustain)!.id) : toggle()} onDoubleClick={() => { if (note) toggle(); }}>{note ? note.fret : held ? '—' : ''}</button>;
      })}</div>)}
    </div></div><button className="text-button" type="button" onClick={() => onChange({ ...doc, notes: doc.notes.map(n => ({ ...n, string: null, fret: null })) })}>운지 초기화 · 저장 시 자동 배정</button>
  </fieldset>;
}
