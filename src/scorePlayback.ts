import type { ScoreDocument } from './types';

export function tickSeconds(ticks: number, bpm: number, speed = 1) { return ticks / 4 * 60 / bpm / speed; }
export function audioTimeAtTick(doc: ScoreDocument, tick: number) { return (doc.audio_offset || 0) + tickSeconds(tick, doc.timing_bpm || doc.bpm); }
export function audioTickAtTime(doc: ScoreDocument, seconds: number) {
  const tick = (seconds - (doc.audio_offset || 0)) * (doc.timing_bpm || doc.bpm) / 60 * 4;
  return tick < 0 || tick >= doc.ticks ? null : tick;
}
export function playbackNotes(doc: ScoreDocument, start: number, end: number, speed = 1) {
  return doc.notes.filter(n => n.start < end && n.start + n.length > start).map(n => ({
    ...n, at: tickSeconds(Math.max(start, n.start) - start, doc.bpm, speed),
    duration: tickSeconds(Math.min(end, n.start + n.length) - Math.max(start, n.start), doc.bpm, speed),
  })).sort((a, b) => a.at - b.at || a.pitch - b.pitch);
}
