import { drumPlaybackVoice } from './drumPlayback';
import { pitchLabel } from './scoreEditing';
import type { Instrument } from './types';

export const reviewLayers = ['recognized', 'automatic', 'current'] as const;
export type ReviewLayer = typeof reviewLayers[number];
export const reviewLabels: Record<ReviewLayer, string> = {
  recognized: '인식한 음표', automatic: '최근 자동 생성 악보', current: '현재 저장된 악보',
};
export type ReviewEvent = { id: string; start: number; end: number; pitch: number; amplitude: number };
export type ReviewData = {
  schema: 'akbo.transcription-review'; schema_version: 1; instrument: Instrument; duration: number;
  snapshot_id?: string;
  window: { start: number; end: number };
  source: { kind: 'original' | 'stem'; file: string; sha256: string; verified: boolean };
  audio: { original_url: string | null; stem_url: string | null; input_url: string };
  score: { revision: string; edited: boolean; bpm: number; timing_bpm: number; audio_offset: number };
  layers: Record<ReviewLayer, { events: ReviewEvent[]; total: number; in_window: number }>;
  warnings: string[];
};

const drumNames: Record<number, string> = {
  35: '어쿠스틱 킥', 36: '킥', 37: '사이드 스틱', 38: '스네어', 39: '클랩', 40: '일렉트릭 스네어',
  41: '로우 플로어 탐', 42: '닫힌 하이햇', 43: '하이 플로어 탐', 44: '페달 하이햇', 45: '로우 탐',
  46: '열린 하이햇', 47: '미드 탐', 48: '하이 미드 탐', 49: '크래시', 50: '하이 탐', 51: '라이드',
  52: '차이나', 53: '라이드 벨', 54: '탬버린', 55: '스플래시', 56: '카우벨', 57: '크래시 2', 59: '라이드 2',
};
export function reviewPitchLabel(instrument: Instrument, pitch: number) {
  return instrument === 'drums' ? `${drumNames[pitch] || '타악기'} · GM ${pitch}` : `${pitchLabel(pitch)} · ${pitch}`;
}

export function reviewWindow(start: number, seconds: number, duration: number) {
  if (![start, seconds, duration].every(Number.isFinite) || duration <= 0 || duration > 600
      || start < 0 || start >= duration || seconds < .25 || seconds > 30) {
    throw new Error('시작 위치는 곡 안에서, 구간 길이는 0.25~30초로 지정해주세요.');
  }
  return { start, end: Math.min(duration, start + seconds) };
}

export function reviewGeometry(event: ReviewEvent, window: { start: number; end: number }) {
  if (event.start >= window.end || event.end <= window.start || event.end <= event.start) return null;
  const duration = window.end - window.start;
  return { left: (Math.max(event.start, window.start) - window.start) / duration,
           width: (Math.min(event.end, window.end) - Math.max(event.start, window.start)) / duration };
}

/** Clip pitched sustains, but never invent a new percussion attack at a crop boundary. */
export function reviewAuditionEvents(events: ReviewEvent[], window: { start: number; end: number }, instrument: Instrument, speed: number) {
  if (![.5, .75, 1].includes(speed)) throw new Error('지원하지 않는 재생 속도예요.');
  return events.filter(event => reviewGeometry(event, window) && (instrument !== 'drums' || event.start >= window.start))
    .map(event => ({ ...event, at: (Math.max(window.start, event.start) - window.start) / speed,
                    duration: (Math.min(window.end, event.end) - Math.max(window.start, event.start)) / speed }))
    .sort((a, b) => a.at - b.at || a.pitch - b.pitch);
}

export function unsupportedReviewVoices(events: ReviewEvent[], instrument: Instrument) {
  return instrument === 'drums' ? events.filter(event => !drumPlaybackVoice(event.pitch)).length : 0;
}

export function reviewAudioUrl(url: string | null, jobId: string) {
  // Review evidence is served by the current project's fixed local WAV routes.
  if (!/^[a-f0-9]{32}$/.test(jobId)) return null;
  return typeof url === 'string' && new RegExp(`^/api/jobs/${jobId}/files/(original|vocal|bass|drums|synthesizer|guitar|piano)\\.wav$`).test(url) ? url : null;
}
