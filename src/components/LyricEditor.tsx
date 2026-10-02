import { useRef, useState } from 'react';
import { Plus, Trash2, Upload } from 'lucide-react';
import { parseLrc } from '../scoreEditing';
import type { ScoreDocument, ScoreLyric } from '../types';

type Props = { document: ScoreDocument; measure: number; disabled: boolean; onChange: (doc: ScoreDocument) => void; onError: (error: string) => void; onNotice: (notice: string) => void };

export default function LyricEditor({ document: doc, measure, disabled, onChange, onError, onNotice }: Props) {
  const file = useRef<HTMLInputElement>(null);
  const [tick, setTick] = useState(1);
  const [text, setText] = useState('');
  const lyrics = doc.lyrics || [];
  const start = (measure - 1) * 16;
  const local = lyrics.filter(l => l.start >= start && l.start < start + 16).sort((a, b) => a.start - b.start);
  function update(id: string, patch: Partial<ScoreLyric>) {
    const next = lyrics.map(l => l.id === id ? { ...l, ...patch } : l);
    if (new Set(next.map(l => l.start)).size !== next.length) return onError('같은 칸에 가사가 있어요. 다른 위치를 골라주세요.');
    onChange({ ...doc, lyrics: next });
  }
  return <fieldset className="lyric-editor" disabled={disabled}><div className="lyric-heading"><div><strong>가사 · 보컬 진입</strong><span>현재 {measure}마디 · 악기 음표와 별개로 배치</span></div><button type="button" className="secondary small" onClick={() => file.current?.click()}><Upload size={14} /> LRC 불러오기</button><input type="file" ref={file} accept=".lrc,text/plain" hidden onChange={async e => {
    const selected = e.target.files?.[0]; e.target.value = '';
    if (!selected) return;
    if (selected.size > 512_000) return onError('가사 파일은 512KB 이하로 선택해주세요.');
    try {
      const result = parseLrc(await selected.text(), doc.timing_bpm || doc.bpm, doc.ticks, doc.audio_offset || 0);
      if (lyrics.length && !window.confirm('현재 가사 전체를 LRC 내용으로 대체할까요? 저장 전에는 실행 취소할 수 있어요.')) return;
      onChange({ ...doc, lyrics: result.lyrics });
      onNotice(`가사 ${result.lyrics.length}개를 불러왔어요.${result.skipped ? ` 곡 밖의 ${result.skipped}개는 제외했어요.` : ''} 위치는 16분음표로 맞췄으니 음원과 비교해주세요.`);
    } catch (error) { onError((error as Error).message); }
  }} /></div>
  <p className="editor-help">이곳은 파트별 가사 편집입니다. 자동 인식·전체 파트 적용은 위의 ‘가사 타임라인’을 사용해주세요. LRC 시간은 원본 음원 기준이며 채보 BPM과 시작 오프셋을 반영해 배치합니다. 긴 문장은 2마디 출력이 읽기 좋아요.</p>
  <div className="lyric-add"><label>시작 칸<select value={tick} onChange={e => setTick(Number(e.target.value))}>{Array.from({ length: 16 }, (_, i) => <option value={i + 1} key={i}>{i + 1}칸 · {(i / 4 + 1).toFixed(2)}박</option>)}</select></label><label>가사 / 진입 힌트<input value={text} maxLength={80} placeholder="예: 보컬 진입 / 여기서 시작해" onChange={e => setText(e.target.value)} /></label><button type="button" className="secondary small" disabled={!text.trim()} onClick={() => {
    const position = start + tick - 1;
    if (lyrics.some(l => l.start === position)) return onError('이 칸에 가사가 있어요. 아래 항목을 수정해주세요.');
    onChange({ ...doc, lyrics: [...lyrics, { id: crypto.randomUUID(), start: position, text: text.trim() }] }); setText('');
  }}><Plus size={14} /> 추가</button></div>
  <div className="lyric-list">{local.map(l => <div className="lyric-row" key={l.id}><select aria-label="가사 시작 칸" value={l.start - start + 1} onChange={e => update(l.id, { start: start + Number(e.target.value) - 1 })}>{Array.from({ length: 16 }, (_, i) => <option value={i + 1} key={i}>{i + 1}칸 · {(i / 4 + 1).toFixed(2)}박</option>)}</select><input aria-label="가사 내용" value={l.text} maxLength={80} onChange={e => update(l.id, { text: e.target.value })} /><button type="button" className="icon-button" aria-label="가사 삭제" onClick={() => onChange({ ...doc, lyrics: lyrics.filter(item => item.id !== l.id) })}><Trash2 size={15} /></button></div>)}{!local.length && <span className="no-notes">이 마디에 가사가 없어요. 무보컬 구간은 비워두세요.</span>}</div>
  </fieldset>;
}
