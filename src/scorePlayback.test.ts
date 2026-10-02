import { expect, it } from 'vitest';
import { audioTickAtTime, audioTimeAtTick, playbackNotes, tickSeconds } from './scorePlayback';
import { parseLrc, updateNote } from './scoreEditing';
import type { ScoreDocument } from './types';

const doc: ScoreDocument = { version: 1, instrument: 'bass', title: 'Test', bpm: 120, timing_bpm: 100, audio_offset: 2, ticks: 48, revision: 'r', edited: false, notes: [{ id: 'held', start: 12, length: 12, pitch: 40, velocity: 80 }, { id: 'next', start: 28, length: 8, pitch: 43, velocity: 90 }], annotations: [], layout: { preset: 'practice', measures_per_line: 4, show_numbers: true } };
it('clips notes at playback window boundaries without changing original notes', () => {
  const notes = playbackNotes(doc, 16, 32);
  expect(notes.map(n => [n.at, n.duration])).toEqual([[0, 1], [1.5, .5]]);
  expect(doc.notes[0].length).toBe(12);
  expect(playbackNotes(doc, 16, 32, .5)[0].duration).toBe(2);
});
it('uses editable BPM for synth playback and original BPM/offset for audio comparison', () => {
  expect(tickSeconds(16, doc.bpm)).toBe(2);
  expect(audioTimeAtTick(doc, 16)).toBeCloseTo(4.4);
  expect(audioTimeAtTick({ ...doc, bpm: 200 }, 16)).toBeCloseTo(4.4);
});
it('maps LRC absolute time into the score using its first-beat offset', () => {
  const result = parseLrc('[00:01.00]intro\n[00:02.00]enter\n[00:03.00]sing', 120, 32, 2);
  expect(result.lyrics.map(l => l.start)).toEqual([0, 8]);
  expect(result.skipped).toBe(1);
});
it('maps media time back into the score without following edited BPM', () => {
  expect(audioTickAtTime(doc, 4.4)).toBeCloseTo(16);
  expect(audioTickAtTime({ ...doc, bpm: 200 }, 4.4)).toBeCloseTo(16);
  expect(audioTickAtTime(doc, 1)).toBeNull();
  expect(audioTickAtTime(doc, 10)).toBeNull();
});
it('extends a note into continuation cells and rejects collisions', () => {
  expect(updateNote(doc, 'held', { length: 15 }).notes[0].length).toBe(15);
  expect(() => updateNote({ ...doc, notes: [...doc.notes, { id: 'same', start: 28, length: 4, pitch: 40, velocity: 90 }] }, 'held', { length: 17 })).toThrow('겹쳐');
});
