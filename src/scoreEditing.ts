import type { ScoreDocument, ScoreNote } from './types';
import { defaultMeters } from './scoreRhythm';

export const drumLanes = [
  { pitch: 49, label: '크래시' }, { pitch: 51, label: '라이드' }, { pitch: 46, label: '오픈 하이햇' },
  { pitch: 42, label: '하이햇' }, { pitch: 50, label: '하이 탐' }, { pitch: 47, label: '미드 탐' },
  { pitch: 38, label: '스네어' }, { pitch: 45, label: '플로어 탐' }, { pitch: 36, label: '킥' },
];
export const presetLabels = { practice: '합주용 · 넓은 간격', standard: '기본 · 간결하게', large: '큰 악보 · 가독성' };
export function editBody(doc: ScoreDocument, revision = doc.revision) {
  return { base_revision: revision, title: doc.title, bpm: doc.bpm, ticks: doc.ticks, meters: doc.meters || defaultMeters, notes: doc.notes, annotations: doc.annotations, layout: doc.layout, tab: doc.tab ? { ...doc.tab, order: doc.tab.order || 'staff-first', capo: doc.tab.capo || 0 } : null, lyrics: doc.lyrics || [] };
}
export function pitchLabel(pitch: number) {
  return `${['C', 'C♯', 'D', 'E♭', 'E', 'F', 'F♯', 'G', 'A♭', 'A', 'B♭', 'B'][pitch % 12]}${Math.floor(pitch / 12) - 1}`;
}
export function contentKey(doc: ScoreDocument) { return JSON.stringify(editBody(doc, '')); }
export function toggleNote(doc: ScoreDocument, pitch: number, start: number, length: number, id: string): ScoreDocument {
  const existing = doc.notes.find(n => n.pitch === pitch && n.start === start);
  if (existing) return { ...doc, notes: doc.notes.filter(n => n.id !== existing.id) };
  const end = Math.min(doc.ticks, start + length);
  // A cell toggle trims an earlier note and stops at the next onset, avoiding
  // ambiguous overlapping same-pitch notes in MusicXML and MIDI.
  const notes = doc.notes.map(n => n.pitch === pitch && n.start < start && n.start + n.length > start ? { ...n, length: start - n.start } : n);
  const next = Math.min(end, ...notes.filter(n => n.pitch === pitch && n.start > start).map(n => n.start));
  return { ...doc, notes: [...notes, { id, pitch, start, length: next - start, velocity: 80 }].sort((a, b) => a.start - b.start || a.pitch - b.pitch) };
}
export function updateNote(doc: ScoreDocument, id: string, values: Partial<ScoreNote>): ScoreDocument {
  const current = doc.notes.find(n => n.id === id);
  if (!current) return doc;
  const note = { ...current, ...values, id };
  if (values.pitch !== undefined && values.pitch !== current.pitch && values.string === undefined) { note.string = null; note.fret = null; }
  if (!Number.isInteger(note.start) || note.start < 0 || !Number.isInteger(note.length) || note.length < 1 || note.start + note.length > doc.ticks) throw new Error('음표가 곡의 범위를 벗어나요.');
  if (!Number.isInteger(note.pitch) || note.pitch < 0 || note.pitch > 127) throw new Error('MIDI 음정은 0~127의 정수로 입력해주세요.');
  if (doc.notes.some(n => n.id !== id && n.pitch === note.pitch && n.start < note.start + note.length && n.start + n.length > note.start)) throw new Error('같은 음정의 음표와 겹쳐요. 위치 또는 길이를 조절해주세요.');
  if (doc.tab && note.string != null) {
    if (!Number.isInteger(note.string) || note.string < 1 || note.string > doc.tab.tuning.length || !Number.isInteger(note.fret) || note.fret! < 0 || note.fret! > 24 || doc.tab.tuning[note.string - 1] + (doc.tab.capo || 0) + note.fret! !== note.pitch) throw new Error('줄·카포·프렛과 음정이 일치하지 않아요.');
    if (doc.notes.some(n => n.id !== id && n.string === note.string && n.start < note.start + note.length && n.start + n.length > note.start)) throw new Error('같은 줄의 음표와 겹쳐요. 다른 줄을 선택하거나 길이를 줄여주세요.');
  }
  return { ...doc, notes: doc.notes.map(n => n.id === id ? note : n) };
}

export function toggleTabNote(doc: ScoreDocument, string: number, fret: number, start: number, length: number, id: string): ScoreDocument {
  if (!doc.tab || !Number.isInteger(fret) || fret < 0 || fret > 24 || string < 1 || string > doc.tab.tuning.length) throw new Error('프렛은 0~24로 입력해주세요.');
  const pitch = doc.tab.tuning[string - 1] + (doc.tab.capo || 0) + fret;
  if (pitch > 127) throw new Error('이 튜닝·카포·프렛 조합은 MIDI 음역을 벗어나요.');
  const existing = doc.notes.find(n => n.string === string && n.start === start);
  if (existing) return { ...doc, notes: doc.notes.filter(n => n.id !== existing.id) };
  const conflict = (n: ScoreNote) => n.string === string || n.pitch === pitch;
  const notes = doc.notes.map(n => conflict(n) && n.start < start && n.start + n.length > start ? { ...n, length: start - n.start } : n);
  const end = Math.min(doc.ticks, start + length, ...notes.filter(n => conflict(n) && n.start > start).map(n => n.start));
  return { ...doc, notes: [...notes, { id, string, fret, pitch, start, length: end - start, velocity: 80 }].sort((a, b) => a.start - b.start || a.pitch - b.pitch) };
}

export function bulkEdit(doc: ScoreDocument, ids: string[], values: { transpose?: number; shift?: number; velocity?: number; length?: number; remove?: boolean }): ScoreDocument {
  const selected = new Set(ids);
  if (!ids.length) return doc;
  if (doc.instrument === 'drums' && values.transpose) throw new Error('드럼 종류는 음정 이동 대신 개별 파트에서 수정해주세요.');
  const next = { ...doc, notes: doc.notes.filter(n => !(selected.has(n.id) && values.remove)).map(n => {
    if (!selected.has(n.id)) return n;
    return { ...n, start: n.start + (values.shift || 0), pitch: n.pitch + (values.transpose || 0),
      length: values.length ?? n.length, velocity: values.velocity ?? n.velocity,
      ...(values.transpose ? { string: null, fret: null } : {}) };
  }) };
  // Validate the final state so adjacent selected notes can move together.
  // Rejected edits leave the original document unchanged.
  const pitches = new Map<number, ScoreNote[]>(), strings = new Map<number, ScoreNote[]>();
  for (const note of next.notes) {
    if (!Number.isInteger(note.velocity) || note.velocity < 1 || note.velocity > 127) throw new Error('세기는 1~127로 입력해주세요.');
    if (!Number.isInteger(note.start) || !Number.isInteger(note.length) || note.start < 0 || note.length < 1 || note.start + note.length > doc.ticks) throw new Error('음표가 곡의 범위를 벗어나요.');
    if (!Number.isInteger(note.pitch) || note.pitch < 0 || note.pitch > 127) throw new Error('MIDI 음정은 0~127의 정수로 입력해주세요.');
    if (!pitches.has(note.pitch)) pitches.set(note.pitch, []);
    pitches.get(note.pitch)!.push(note);
    if (note.string != null) { if (!strings.has(note.string)) strings.set(note.string, []); strings.get(note.string)!.push(note); }
  }
  for (const [groups, message] of [[pitches, '같은 음정의 음표와 겹쳐요.'], [strings, '같은 줄의 음표와 겹쳐요.']] as const) {
    for (const notes of groups.values()) {
      const ordered = [...notes].sort((a, b) => a.start - b.start);
      if (ordered.some((n, i) => i > 0 && ordered[i - 1].start + ordered[i - 1].length > n.start)) throw new Error(message);
    }
  }
  return next;
}

export function setCapo(doc: ScoreDocument, capo: number): ScoreDocument {
  if (!doc.tab || !Number.isInteger(capo) || capo < 0 || capo > 12) throw new Error('카포는 0~12로 입력해주세요.');
  // Preserve sounding pitches; regenerate only fret suggestions on save.
  return { ...doc, tab: { ...doc.tab, capo }, notes: doc.notes.map(n => ({ ...n, string: null, fret: null })) };
}

export function parseLrc(source: string, bpm: number, ticks: number, audioOffset = 0) {
  if (source.length > 512_000) throw new Error('가사 파일은 512KB 이하로 선택해주세요.');
  if (/<\d+:\d+/.test(source)) throw new Error('음절 시간 태그가 있는 확장 LRC는 아직 지원하지 않아요. 일반 LRC로 내보내주세요.');
  const offset = Number(source.match(/^\[offset:\s*([+-]?\d+)\]\s*$/mi)?.[1] || 0) / 1000;
  const result: { id: string; start: number; text: string }[] = [];
  const used = new Set<number>();
  let skipped = 0;
  for (const line of source.replace(/^\uFEFF/, '').split(/\r?\n/)) {
    const times = [...line.matchAll(/\[(\d+):([0-5]?\d)(?:\.(\d{1,3}))?\]/g)];
    if (!times.length) continue;
    const text = line.replace(/\[[^\]]*\]/g, '').trim();
    if (!text) continue;
    if (text.length > 80) throw new Error('한 가사 항목은 80자 이하로 나눠주세요.');
    for (const time of times) {
      const seconds = Number(time[1]) * 60 + Number(time[2]) + Number(`0.${time[3] || '0'}`) + offset;
      const start = Math.round((seconds - audioOffset) * bpm / 60 * 4);
      if (start < 0 || start >= ticks) { skipped++; continue; }
      if (used.has(start)) throw new Error('같은 16분음표 칸에 두 가사가 겹쳐요. LRC 시간 정보를 조절해주세요.');
      used.add(start); result.push({ id: `lrc-${start}`, start, text });
    }
  }
  if (!result.length) throw new Error('곡 안에 배치할 가사가 없어요. [00:12.34]가사 형식과 BPM을 확인해주세요.');
  return { lyrics: result.sort((a, b) => a.start - b.start), skipped };
}
