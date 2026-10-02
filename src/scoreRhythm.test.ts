import { describe, expect, it } from 'vitest';
import { audioTickAtTime, audioTimeAtTick, playbackNotes } from './scorePlayback';
import { barAtTick, beatTicks, gridLabel, measureMap, meterSummary, rebar, setMeter } from './scoreRhythm';
import type { ScoreDocument } from './types';

const doc: ScoreDocument = { version: 1, instrument: 'bass', title: 'test', bpm: 120, ticks: 32, revision: 'saved', edited: false, notes: [{ id: 'n', start: 14, length: 6, pitch: 43, velocity: 80 }], lyrics: [{ id: 'l', start: 16, text: '같이' }], annotations: [{ measure: 2, section: 'B', cue: '시작' }], layout: { preset: 'practice', measures_per_line: 4, show_numbers: true, system_breaks: [2], page_breaks: [2] } };
describe('time signatures and reflow', () => {
  it('keeps legacy 4/4 and respects every supported measure length', () => {
    expect(measureMap(doc).map(b => b.start)).toEqual([0, 16]);
    for (const [beats, beat_type] of [[2, 4], [3, 4], [4, 4], [6, 8], [9, 8], [12, 8]]) {
      const length = beats * 16 / beat_type;
      expect(measureMap({ ticks: length * 2, meters: [{ measure: 1, beats, beat_type }] }).map(b => b.end)).toEqual([length, length * 2]);
    }
  });
  it('changes bar geometry without changing note or lyric timing and supports undo by retaining the old value', () => {
    const updated = setMeter(doc, 1, '3/4');
    expect(updated.ticks).toBe(36);
    expect(updated.notes).toEqual(doc.notes); expect(updated.lyrics).toEqual(doc.lyrics);
    expect(doc.ticks).toBe(32); expect(doc.meters).toBeUndefined();
    expect(updated.annotations[0].measure).toBe(2);
    expect(updated.layout.system_breaks).toEqual([2]);
    expect(audioTimeAtTick(updated, 16)).toBe(audioTimeAtTick(doc, 16));
  });
  it('handles mixed meters, boundary highlighting and playback range clipping', () => {
    const updated = setMeter(setMeter(doc, 1, '3/4'), 3, '6/8');
    const bars = measureMap(updated);
    expect(bars.map(b => b.start)).toEqual([0, 12, 24]);
    expect(barAtTick(bars, 11.9)?.number).toBe(1); expect(barAtTick(bars, 12)?.number).toBe(2);
    expect(barAtTick(bars, 36)).toBeUndefined();
    expect(barAtTick(bars, audioTickAtTime(updated, 1.5)!)?.number).toBe(2);
    expect(playbackNotes(updated, bars[1].start, bars[1].end)[0]).toMatchObject({ at: .25, duration: .75 });
    expect(meterSummary(updated.meters)).toBe('3/4 · 변박 1회');
  });
  it('shows compound beats in groups of three eighths', () => {
    const bar = measureMap({ ticks: 12, meters: [{ measure: 1, beats: 6, beat_type: 8 }] })[0];
    expect(beatTicks(bar)).toBe(6);
    expect(Array.from({ length: 12 }, (_, i) => gridLabel(bar, i))).toEqual(['1', '&', '2', '&', '3', '&', '4', '&', '5', '&', '6', '&']);
  });
  it('rejects unsupported, duplicate, out-of-range, fractional and too-long geometry', () => {
    expect(() => setMeter(doc, 1, '5/8')).toThrow();
    expect(() => setMeter(doc, 4, '3/4')).toThrow();
    expect(() => measureMap({ ticks: 16, meters: [{ measure: 1, beats: 3, beat_type: 4 }] })).toThrow('완전한');
    expect(() => measureMap({ ticks: 7212, meters: [{ measure: 1, beats: 3, beat_type: 4 }] })).toThrow('600');
    expect(() => rebar(doc, [{ measure: 1, beats: 3, beat_type: 4 }, { measure: 1, beats: 4, beat_type: 4 }])).toThrow();
  });
  it('moves and merges markers without silently dropping text or changing the saved layout', () => {
    const original = { ...doc, ticks: 48, meters: [{ measure: 1, beats: 2, beat_type: 4 }], annotations: [{ measure: 2, section: 'A', cue: '첫째' }, { measure: 3, section: 'B', cue: '둘째' }], layout: { ...doc.layout, system_breaks: [2, 3] } };
    const changed = setMeter(original, 1, '12/8');
    expect(changed.annotations).toEqual([{ measure: 1, section: 'A / B', cue: '첫째 / 둘째' }]);
    expect(changed.layout.system_breaks).toEqual([]);
    expect(original.annotations).toHaveLength(2);
    expect(() => setMeter({ ...original, annotations: [{ measure: 2, section: 'A'.repeat(24), cue: '' }, { measure: 3, section: 'B', cue: '' }] }, 1, '12/8')).toThrow('메모');
  });
});
