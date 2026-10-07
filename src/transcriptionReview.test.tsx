import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import TranscriptionReview, { ReviewTimeline } from './components/TranscriptionReview';
import { reviewAudioUrl, reviewAuditionEvents, reviewGeometry, reviewPitchLabel, reviewWindow, unsupportedReviewVoices, type ReviewData, type ReviewEvent } from './transcriptionReview';
import { instruments, type Job, type Stem } from './types';

const note: ReviewEvent = { id: 'n', start: 1, end: 3, pitch: 42, amplitude: .7 };
const layers = (events: ReviewEvent[]): ReviewData['layers'] => {
  const layer = { events, total: events.length, in_window: events.length };
  return { recognized: layer, automatic: layer, current: layer };
};
const data: ReviewData = {
  schema: 'akbo.transcription-review', schema_version: 1, instrument: 'drums', duration: 8,
  window: { start: 0, end: 4 }, source: { kind: 'stem', file: 'drums.wav', sha256: 'a'.repeat(64), verified: true },
  audio: { original_url: null, stem_url: null, input_url: '' },
  score: { revision: 'r', edited: false, bpm: 120, timing_bpm: 110, audio_offset: .5 },
  layers: layers([note]), warnings: [],
};

describe('source-time transcription inspection', () => {
  it('validates start/length and clips the last window to the source duration', () => {
    expect(reviewWindow(7, 8, 8)).toEqual({ start: 7, end: 8 });
    for (const args of [[-1, 8, 8], [8, 8, 8], [NaN, 8, 8], [0, Infinity, 8], [0, .1, 8], [0, 31, 40], [0, 8, 0]]) {
      expect(() => reviewWindow(...args as [number, number, number])).toThrow();
    }
  });
  it('uses half-open overlap and bounds the visual rectangle without changing source events', () => {
    const original = { ...note };
    expect(reviewGeometry(note, { start: 2, end: 4 })).toEqual({ left: 0, width: .5 });
    expect(reviewGeometry(note, { start: 3, end: 4 })).toBeNull();
    expect(reviewGeometry(note, { start: 0, end: 1 })).toBeNull();
    expect(note).toEqual(original);
  });
  it('does not create a percussion attack at the left crop edge but clips pitched sustains', () => {
    const window = { start: 2, end: 4 };
    expect(reviewAuditionEvents([note], window, 'drums', 1)).toEqual([]);
    expect(reviewAuditionEvents([note], window, 'guitar', .5)[0]).toMatchObject({ at: 0, duration: 2, start: 1, end: 3 });
    const hit = { ...note, start: 2 };
    expect(reviewAuditionEvents([hit], window, 'drums', 1)).toHaveLength(1);
    expect(() => reviewAuditionEvents([note], window, 'guitar', 0)).toThrow();
  });
  it('preserves GM identities and counts unknown preview timbres without relabeling', () => {
    expect(reviewPitchLabel('drums', 35)).toContain('GM 35');
    expect(reviewPitchLabel('drums', 36)).toContain('GM 36');
    expect(reviewPitchLabel('drums', 42)).toContain('닫힌 하이햇');
    expect(reviewPitchLabel('drums', 44)).toContain('페달 하이햇');
    expect(reviewPitchLabel('drums', 33)).toBe('타악기 · GM 33');
    expect(unsupportedReviewVoices([{ ...note, pitch: 33 }, note], 'drums')).toBe(1);
    expect(unsupportedReviewVoices([{ ...note, pitch: 33 }], 'bass')).toBe(0);
  });
  it('only accepts fixed WAV routes for this project', () => {
    const id = 'a'.repeat(32), base = `/api/jobs/${id}/files/`;
    for (const inst of [...instruments, 'original']) expect(reviewAudioUrl(`${base}${inst}.wav`, id)).toBe(`${base}${inst}.wav`);
    for (const url of [null, 'https://example.com/a.wav', `${base}../original.wav`, `${base}residual.wav`, `${base}bass.wav?x=1`, `/api/jobs/${'b'.repeat(32)}/files/bass.wav`]) expect(reviewAudioUrl(url, id)).toBeNull();
  });
  it('draws three separate source-time lanes with accessible details and a playback cursor', () => {
    const html = renderToStaticMarkup(<ReviewTimeline data={data} position={2} onSelect={() => {}} />);
    expect(html.match(/class="review-note /g)).toHaveLength(3);
    expect(html).toContain('인식한 음표, 닫힌 하이햇');
    expect(html).toContain('최근 자동 생성 악보, 닫힌 하이햇');
    expect(html).toContain('현재 저장된 악보, 닫힌 하이햇');
    expect(html).toContain('1.000초부터 3.000초');
    expect(html).toContain('review-cursor');
    expect(html).not.toContain('정확도 100');
  });
  it('does not describe an empty prediction window as silence or success', () => {
    const empty = { ...data, layers: layers([]) };
    expect(renderToStaticMarkup(<ReviewTimeline data={empty} position={null} onSelect={() => {}} />)).toContain('실제 소리가 있는지는 원음으로 확인');
  });
  it('offers review for all six instruments but not demos, imported scores, or stale availability flags', () => {
    const job = { id: 'a'.repeat(32), demo: false, source_type: 'upload', duration: 8 } as Job;
    for (const inst of instruments) {
      const stem = { id: inst, label: inst, score_url: '/score', score_status: 'ready', score_transcription: { raw_events: true } } as Stem;
      const html = renderToStaticMarkup(<TranscriptionReview job={job} stem={stem} disabled={false} onEdit={() => {}} />);
      expect(html).toContain('구간 비교 열기');
      expect(html.match(/<button[^>]*aria-expanded="false"[^>]*>/)?.[0]).not.toContain('disabled=""');
      for (const status of ['error', 'running', 'pending'] as const) {
        const unavailable = renderToStaticMarkup(<TranscriptionReview job={job} stem={{ ...stem, score_status: status } as Stem} disabled={false} onEdit={() => {}} />);
        expect(unavailable.match(/<button[^>]*aria-expanded="false"[^>]*>/)?.[0]).toContain('disabled=""');
        if (status === 'error') expect(unavailable).toContain('서로 다른 시점의 기록이 섞이지 않도록');
      }
      for (const badJob of [{ ...job, demo: true }, { ...job, source_type: 'musicxml' }]) expect(renderToStaticMarkup(<TranscriptionReview job={badJob} stem={stem} disabled={false} onEdit={() => {}} />)).toBe('');
      for (const flag of [undefined, false, 'true', 1]) {
        const oldStem = { ...stem, score_transcription: { raw_events: flag } } as Stem;
        expect(renderToStaticMarkup(<TranscriptionReview job={job} stem={oldStem} disabled={false} onEdit={() => {}} />).match(/<button[^>]*aria-expanded="false"[^>]*>/)?.[0]).toContain('disabled=""');
      }
    }
  });
});
