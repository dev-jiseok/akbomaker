import { describe, expect, it } from 'vitest';
import type { ScoreDocument } from './types';
import { bulkEdit, contentKey, editBody, parseLrc, pitchLabel, setCapo, toggleNote, toggleTabNote, updateNote } from './scoreEditing';

const doc: ScoreDocument = { version: 1, instrument: 'piano', title: 'test', bpm: 120, ticks: 32, revision: 'original', edited: false, notes: [], annotations: [], layout: { preset: 'standard', measures_per_line: 4, show_numbers: true } };
describe('score editing', () => {
  it('adds and removes notes without mutating the saved document', () => {
    const next = toggleNote(doc, 60, 0, 4, 'new');
    expect(next.notes[0]).toMatchObject({ start: 0, length: 4, pitch: 60 });
    expect(doc.notes).toEqual([]);
    expect(toggleNote(next, 60, 0, 4, 'unused').notes).toEqual([]);
  });
  it('trims sustained notes at a new onset and avoids the next onset', () => {
    const sustained = toggleNote(doc, 60, 0, 16, 'first');
    const next = toggleNote(sustained, 60, 4, 16, 'second');
    expect(next.notes[0].length).toBe(4);
    const inserted = toggleNote(next, 60, 2, 16, 'third');
    expect(inserted.notes.find(n => n.id === 'third')!.length).toBe(2);
  });
  it('rejects overlaps, out-of-range notes and fractional pitches', () => {
    const next = toggleNote(toggleNote(doc, 60, 0, 4, 'a'), 60, 8, 4, 'b');
    expect(() => updateNote(next, 'b', { start: 2 })).toThrow('겹쳐');
    expect(() => updateNote(next, 'b', { length: 30 })).toThrow('범위');
    expect(() => updateNote(next, 'b', { pitch: 60.5 })).toThrow('정수');
  });
  it('clips added notes to the final bar', () => {
    expect(toggleNote(doc, 60, 30, 16, 'last').notes[0].length).toBe(2);
  });
  it('excludes server metadata from dirty detection and retains revision for saves', () => {
    expect(contentKey(doc)).toBe(contentKey({ ...doc, revision: 'saved', edited: true }));
    expect(editBody(doc).base_revision).toBe('original');
    expect(pitchLabel(60)).toBe('C4');
  });
  it('saves keyboard notation and manual hands without changing pitches or timing', () => {
    const initial = toggleNote({ ...doc, keyboard: { mode: 'grand', split_pitch: 60 } }, 48, 0, 4, 'a');
    const assigned = updateNote(initial, 'a', { hand: 'right' });
    expect(assigned.notes[0]).toMatchObject({ hand: 'right', pitch: 48, start: 0, length: 4 });
    expect(initial.notes[0].hand).toBeUndefined();
    expect(contentKey(initial)).not.toBe(contentKey(assigned));
    expect(editBody(assigned).keyboard).toEqual({ mode: 'grand', split_pitch: 60 });
    expect(contentKey(assigned)).not.toBe(contentKey({ ...assigned, keyboard: { mode: 'single', split_pitch: 60 } }));
    expect(contentKey(assigned)).toBe(contentKey({ ...assigned, transcription: { engine: 'pyin', profile: 'instrument', warning: 'draft', timing_reviewed: false } }));
  });
  it('enters frets in concert pitch, trims the same string and validates manual movement', () => {
    const bass = { ...doc, instrument: 'bass' as const, tab: { mode: 'both' as const, tuning: [43, 38, 33, 28] } };
    const first = toggleTabNote(bass, 4, 3, 0, 16, 'a');
    const second = toggleTabNote(first, 4, 5, 4, 4, 'b');
    expect(second.notes[0]).toMatchObject({ pitch: 31, string: 4, fret: 3, length: 4 });
    expect(() => updateNote(second, 'b', { start: 2 })).toThrow('같은 줄');
    expect(updateNote(second, 'b', { pitch: 36 }).notes[1].string).toBeNull();
    expect(() => updateNote(second, 'b', { string: 1 })).toThrow('일치');
  });
  it('parses timed LRC including offsets, repeated timestamps and song bounds', () => {
    const result = parseLrc('[ti:합주]\n[offset:250]\n[00:00.00][00:01.00]시작\n[00:20.00]끝', 120, 32);
    expect(result.lyrics.map(l => l.start)).toEqual([2, 10]);
    expect(result.skipped).toBe(1);
  });
  it('moves a selection atomically, rejects collisions and preserves other notes', () => {
    const original = toggleNote(toggleNote(doc, 60, 0, 4, 'a'), 60, 4, 4, 'b');
    const moved = bulkEdit(original, ['a', 'b'], { shift: 4 });
    expect(moved.notes.map(n => n.start)).toEqual([4, 8]);
    expect(original.notes.map(n => n.start)).toEqual([0, 4]);
    expect(() => bulkEdit(original, ['a'], { shift: 1 })).toThrow('겹쳐');
    expect(() => bulkEdit(original, ['a'], { transpose: -61 })).toThrow('정수');
    expect(bulkEdit(original, ['a'], { remove: true }).notes.map(n => n.id)).toEqual(['b']);
  });
  it('preserves concert pitches on capo changes and enters frets relative to capo', () => {
    const bass = { ...doc, instrument: 'bass' as const, tab: { mode: 'both' as const, tuning: [43, 38, 33, 28] } };
    const original = toggleTabNote(bass, 4, 5, 0, 4, 'a');
    const capo = setCapo(original, 2);
    expect(capo.notes[0]).toMatchObject({ pitch: 33, string: null, fret: null });
    expect(toggleTabNote(capo, 4, 0, 4, 4, 'b').notes[1].pitch).toBe(30);
    expect(() => setCapo(original, 13)).toThrow('0~12');
  });
  it('rejects unaligned, colliding, extended or oversized lyrics', () => {
    expect(() => parseLrc('시간 없음', 120, 32)).toThrow('배치');
    expect(() => parseLrc('[00:00.01]첫\n[00:00.02]둘', 120, 32)).toThrow('겹쳐');
    expect(() => parseLrc('[00:00]<00:01>가사', 120, 32)).toThrow('확장');
    expect(() => parseLrc('[00:00]' + '가'.repeat(81), 120, 32)).toThrow('80자');
  });
});
