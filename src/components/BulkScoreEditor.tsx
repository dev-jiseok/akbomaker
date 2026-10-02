import { useState } from 'react';
import { bulkEdit } from '../scoreEditing';
import { measureMap } from '../scoreRhythm';
import type { ScoreDocument } from '../types';

type Props = { document: ScoreDocument; measure: number; ids: string[]; disabled: boolean; onSelect: (ids: string[]) => void; onChange: (doc: ScoreDocument) => void; onError: (message: string) => void };
export default function BulkScoreEditor({ document: doc, measure, ids, disabled, onSelect, onChange, onError }: Props) {
  const [from, setFrom] = useState(1), [to, setTo] = useState(1);
  const [transpose, setTranspose] = useState(0), [shift, setShift] = useState(0), [velocity, setVelocity] = useState(80), [length, setLength] = useState(4);
  const bars = measureMap(doc), bar = bars[measure - 1];
  const available = new Set(doc.notes.map(n => n.id));
  const validIds = ids.filter(id => available.has(id));
  function apply(values: Parameters<typeof bulkEdit>[2]) {
    try { onChange(bulkEdit(doc, validIds, values)); if (values.remove) onSelect([]); }
    catch (error) { onError((error as Error).message); }
  }
  return <details className="bulk-editor"><summary>여러 음표 일괄 수정 · {validIds.length}개 선택</summary><fieldset disabled={disabled}>
    <p className="editor-help">음표 목록에서 Shift+클릭으로 여러 음표를 선택해요. 범위 선택은 해당 마디에서 시작하는 음표를 선택합니다. 변경은 한 번의 실행 취소로 복원할 수 있어요.</p>
    <div className="bulk-row"><label>시작 마디<input type="number" min={1} max={bars.length} value={from} onChange={e => setFrom(Number(e.target.value))} /></label><label>끝 마디<input type="number" min={from} max={bars.length} value={to} onChange={e => setTo(Number(e.target.value))} /></label><button className="secondary small" onClick={() => {
      if (!Number.isInteger(from) || !Number.isInteger(to) || from < 1 || to < from || to > bars.length) return onError('선택할 마디 범위를 확인해주세요.');
      onSelect(doc.notes.filter(n => n.start >= bars[from - 1].start && n.start < bars[to - 1].end).map(n => n.id));
    }}>범위 선택</button><button className="text-button" onClick={() => onSelect(doc.notes.filter(n => n.start >= bar.start && n.start < bar.end).map(n => n.id))}>현재 마디 선택</button><button className="text-button" onClick={() => onSelect([])}>선택 해제</button></div>
    <div className="bulk-row"><label>음정 이동 · 반음<input type="number" min={-24} max={24} disabled={doc.instrument === 'drums'} value={transpose} onChange={e => setTranspose(Number(e.target.value))} /></label><button className="secondary small" disabled={!validIds.length || doc.instrument === 'drums'} onClick={() => apply({ transpose })}>음정 이동</button><label>위치 이동 · 칸<input type="number" min={-doc.ticks} max={doc.ticks} value={shift} onChange={e => setShift(Number(e.target.value))} /></label><button className="secondary small" disabled={!validIds.length} onClick={() => apply({ shift })}>위치 이동</button></div>
    <div className="bulk-row"><label>세기<input type="number" min={1} max={127} value={velocity} onChange={e => setVelocity(Number(e.target.value))} /></label><button className="secondary small" disabled={!validIds.length} onClick={() => apply({ velocity })}>세기 적용</button><label>길이 · 칸<input type="number" min={1} max={doc.ticks} value={length} onChange={e => setLength(Number(e.target.value))} /></label><button className="secondary small" disabled={!validIds.length} onClick={() => apply({ length })}>길이 적용</button><button className="text-button" disabled={!validIds.length} onClick={() => { if (window.confirm(`선택한 음표 ${validIds.length}개를 삭제할까요? 실행 취소로 복원할 수 있어요.`)) apply({ remove: true }); }}>선택 음표 삭제</button></div>
  </fieldset></details>;
}
