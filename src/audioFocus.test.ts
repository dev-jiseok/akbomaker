import { expect, it, vi } from 'vitest';
import { AUDIO_FOCUS_EVENT, claimAudioFocus, initialScoreMeasureAtAudioTime, listenForAudioFocus } from './audioFocus';
import type { ScoreDocument } from './types';

it('stops other owners synchronously while keeping a mixer playback group intact', () => {
  const target = new EventTarget(), mixer = vi.fn(), inspection = vi.fn(), score = vi.fn();
  const unsubscribers = [listenForAudioFocus('mixer', mixer, target), listenForAudioFocus('inspection', inspection, target), listenForAudioFocus('score', score, target)];
  claimAudioFocus('mixer', target);
  expect(mixer).not.toHaveBeenCalled();
  expect(inspection).toHaveBeenCalledTimes(1);
  expect(score).toHaveBeenCalledTimes(1);
  claimAudioFocus('inspection', target);
  expect(mixer).toHaveBeenCalledTimes(1);
  expect(inspection).toHaveBeenCalledTimes(1);
  expect(score).toHaveBeenCalledTimes(2);
  unsubscribers.forEach(unsubscribe => unsubscribe());
});

it('removes listeners and ignores malformed focus events', () => {
  const target = new EventTarget(), stop = vi.fn();
  const unsubscribe = listenForAudioFocus('owner', stop, target);
  target.dispatchEvent(new Event(AUDIO_FOCUS_EVENT));
  target.dispatchEvent(new CustomEvent(AUDIO_FOCUS_EVENT, { detail: { owner: 3 } }));
  target.dispatchEvent(new CustomEvent(AUDIO_FOCUS_EVENT, { detail: { owner: '' } }));
  claimAudioFocus('owner', target);
  expect(stop).not.toHaveBeenCalled();
  unsubscribe();
  claimAudioFocus('other', target);
  expect(stop).not.toHaveBeenCalled();
});

it('is harmless without a browser window', () => {
  expect(typeof window).toBe('undefined');
  expect(() => claimAudioFocus('ssr')).not.toThrow();
  const stop = vi.fn();
  expect(() => listenForAudioFocus('ssr', stop)()).not.toThrow();
  expect(stop).not.toHaveBeenCalled();
});

const doc: ScoreDocument = {
  version: 1, instrument: 'piano', title: 'Navigation fixture', bpm: 180, timing_bpm: 120,
  audio_offset: 2, ticks: 48, revision: 'test', edited: false, notes: [], annotations: [],
  layout: { preset: 'practice', measures_per_line: 4, show_numbers: true },
};

it('navigates with original timing BPM and source offset, not edited display BPM', () => {
  const before = JSON.stringify(doc);
  expect(initialScoreMeasureAtAudioTime(doc, 2)).toBe(1);
  expect(initialScoreMeasureAtAudioTime(doc, 4)).toBe(2);
  expect(initialScoreMeasureAtAudioTime({ ...doc, bpm: 60 }, 4)).toBe(2);
  expect(initialScoreMeasureAtAudioTime(doc, 6)).toBe(3);
  expect(JSON.stringify(doc)).toBe(before);
});

it('clamps outside audio times to first/last bar without inventing a score position', () => {
  expect(initialScoreMeasureAtAudioTime(doc, -10)).toBe(1);
  expect(initialScoreMeasureAtAudioTime(doc, 0)).toBe(1);
  expect(initialScoreMeasureAtAudioTime(doc, 100)).toBe(3);
  expect(initialScoreMeasureAtAudioTime(doc, undefined)).toBe(1);
  expect(initialScoreMeasureAtAudioTime(doc, Number.NaN)).toBe(1);
});

it('uses saved changing meter boundaries when opening an inspection time', () => {
  const changing: ScoreDocument = { ...doc, ticks: 36,
    meters: [{ measure: 1, beats: 3, beat_type: 4 }, { measure: 2, beats: 6, beat_type: 8 }] };
  expect(initialScoreMeasureAtAudioTime(changing, 3.49)).toBe(1);
  expect(initialScoreMeasureAtAudioTime(changing, 3.5)).toBe(2);
  expect(initialScoreMeasureAtAudioTime(changing, 5)).toBe(3);
});
