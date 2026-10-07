import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import PitchedTranscriptionControls, { supportsPitchedComparison, supportsPitchedReview } from './components/PitchedTranscriptionControls';
import Workspace from './components/Workspace';
import type { Job, TranscriptionInfo } from './types';

const info: TranscriptionInfo = {
  engine: 'basic-pitch-adaptive-v1', profile: 'instrument', warning: 'Experimental', pitched_review: true,
  pitched_postprocessing: { method: 'test', baseline_count: 12, output_count: 10, octave_overlap_count: 2 },
};
const noop = () => {};

describe('synth-only optional decoder controls', () => {
  it('offers new comparison only for synth while preserving historical review support', () => {
    expect(supportsPitchedComparison('synthesizer')).toBe(true);
    for (const instrument of ['guitar', 'piano', 'bass', 'vocal', 'drums'] as const) expect(supportsPitchedComparison(instrument)).toBe(false);
    for (const instrument of ['guitar', 'piano', 'synthesizer'] as const) expect(supportsPitchedReview(instrument)).toBe(true);
    for (const instrument of ['bass', 'vocal', 'drums'] as const) expect(supportsPitchedReview(instrument)).toBe(false);
  });
  it('labels limitations and default selection without claiming confidence', () => {
    const html = renderToStaticMarkup(<PitchedTranscriptionControls jobId="job" instrument="synthesizer" info={info} demo={false} disabled={false} engine="adaptive" onEngine={noop} />);
    expect(html).toContain('신디 지속음 채보 방식');
    expect(html).toContain('지속음 보강 (실험)');
    expect(html).toContain('약한 타격');
    expect(html).toContain('음이 지나치게 길게');
    expect(html).toContain('실제 곡에서 정확도 향상이 입증된 것은 아니며');
    expect(html).toContain('신뢰도가 아니며');
    expect(html).toContain('오류 판정 아님');
    expect(html).toContain('현재 악보를 대체');
    expect(html).toContain('synthesizer.transcription.json?download=true');
  });
  it('keeps historical guitar/piano JSON links but never renders their adaptive selector', () => {
    for (const instrument of ['guitar', 'piano'] as const) {
      const html = renderToStaticMarkup(<PitchedTranscriptionControls jobId="job" instrument={instrument} info={info} demo={false} disabled={false} engine="adaptive" onEngine={noop} />);
      expect(html).not.toContain('<select');
      expect(html).toContain(`${instrument}.transcription.json?download=true`);
      expect(html).toContain('선택할 수 없어요');
      expect(renderToStaticMarkup(<PitchedTranscriptionControls jobId="job" instrument={instrument} demo={false} disabled={false} engine="standard" onEngine={noop} />)).toBe('');
    }
  });
  it('requires a strict review availability flag and disables demo/busy selection', () => {
    for (const flag of [undefined, false, 'true', 1]) {
      const unsafeInfo = { ...info, pitched_review: flag } as TranscriptionInfo;
      const html = renderToStaticMarkup(<PitchedTranscriptionControls jobId="job" instrument="synthesizer" info={unsafeInfo} demo={false} disabled={false} engine="standard" onEngine={noop} />);
      expect(html).not.toContain('.transcription.json');
    }
    for (const state of [{ demo: true, disabled: false }, { demo: false, disabled: true }]) {
      const html = renderToStaticMarkup(<PitchedTranscriptionControls jobId="job" instrument="synthesizer" {...state} engine="standard" onEngine={noop} />);
      expect(html).toContain('disabled=""');
      expect(html).toContain('<option value="standard" selected="">');
    }
  });
  it('discloses truncated octave review counts as a lower bound', () => {
    const truncated = { ...info, pitched_postprocessing: { ...info.pitched_postprocessing!, octave_overlap_count: 10000, octave_overlap_truncated: true } };
    const html = renderToStaticMarkup(<PitchedTranscriptionControls jobId="job" instrument="synthesizer" info={truncated} demo={false} disabled={false} engine="standard" onEngine={noop} />);
    expect(html).toContain('10000개 이상 (기록 상한 도달)');
  });
});

describe('failed re-transcription with a retained score', () => {
  it('shows the error and explicitly says the previous score was preserved', () => {
    const job: Job = {
      id: 'job', title: 'Retained score', source_type: 'musicxml', demo: false, status: 'completed', stage: 'done',
      progress: 100, message: 'done', error: null, created_at: '', duration: 8, bpm: 120, original_url: null, residual_url: null,
      stems: [{ id: 'piano', label: '피아노', status: 'ready', score_status: 'error', waveform: [], score_url: '/piano.musicxml',
                midi_url: '/piano.mid', score_error: 'mock adaptive failure', score_transcription: info }],
    };
    const html = renderToStaticMarkup(<Workspace job={job} health={null} onJob={noop} onNew={noop} onError={noop} onEditorDirty={noop} />);
    expect(html).toContain('role="alert"');
    expect(html).toContain('다시 채보하지 못했어요. 이전 악보는 그대로 보관되어 있어요.');
    expect(html).toContain('mock adaptive failure');
    expect(html).toContain('/piano.musicxml?download=true');
  });
});
