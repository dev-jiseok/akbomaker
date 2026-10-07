// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import RhythmWorkbench from './components/RhythmWorkbench';
import SeparationOptions, { SeparationNotice } from './components/SeparationOptions';
import Mixer from './components/Mixer';
import type { Job, RhythmEvidence } from './types';

let container: HTMLDivElement;
let root: Root;
const evidence: RhythmEvidence = {
  method: 'pulse-grid-review-v1', metrics_are_confidence: false, downbeat_known: false,
  grid_fit: { status: 'stable_fit', fitted_bpm: 100, candidate_bpm: 100, first_pulse_seconds: .2,
    p95_deviation_seconds: .01, baseline_end_drift_seconds: 1.8, beat_count: 250, interval_consistent: true,
    downbeat_known: false, automatically_applied: false },
  tempo_candidates: [{ bpm: 100, periodicity: .8 }], tempo_ambiguous: true,
  local_tempo: { status: 'no_large_variation_detected', range_bpm: [99.7, 100.2], segments: [] },
  warnings: ['반속·배속을 확인해주세요.'],
};
const job = {
  id: 'a'.repeat(32), demo: false, source_type: 'upload', original_url: null, duration: 180,
  rhythm_analysis: { bpm: 99, first_beat_seconds: .093, beat_times: [.093, .699, 1.305], regularity: .99,
    analyzed_seconds: 180, alternatives: [50, 99, 198], warning: '추정값이에요.', evidence },
} as Job;

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); });

describe('accuracy comparison controls', () => {
  it('shows evidence but changes no BPM/offset until explicitly selected', async () => {
    const onBpm = vi.fn(), onOffset = vi.fn();
    await act(async () => root.render(<RhythmWorkbench job={job} bpm={120} offset={0} disabled={false}
      onBpm={onBpm} onOffset={onOffset} onAnalyze={vi.fn()} />));
    expect(onBpm).not.toHaveBeenCalled(); expect(onOffset).not.toHaveBeenCalled();
    expect(container.textContent).toContain('1.800초');
    expect(container.textContent).toContain('정확도 점수가 아니며');
    const compare = [...container.querySelectorAll('button')].find(button => button.textContent === '박열 적합 100 BPM 비교')!;
    await act(async () => compare.click());
    expect(onBpm).toHaveBeenCalledWith(100); expect(onOffset).not.toHaveBeenCalled();
  });

  it('does not offer unstable fit for application and preserves legacy analysis rendering', async () => {
    const changed = structuredClone(job);
    changed.rhythm_analysis!.evidence!.grid_fit.status = 'review_required';
    changed.rhythm_analysis!.evidence!.local_tempo.status = 'variation_requires_review';
    const render = async (value: Job) => act(async () => root.render(<RhythmWorkbench job={value} bpm={120} offset={0}
      disabled={false} onBpm={vi.fn()} onOffset={vi.fn()} onAnalyze={vi.fn()} />));
    await render(changed);
    expect(container.textContent).not.toContain('BPM 비교');
    expect(container.textContent).toContain('자동 반영하지 않습니다');
    delete changed.rhythm_analysis!.evidence;
    await render(changed);
    expect(container.textContent).toContain('추천 99 BPM');
    expect(container.querySelector('[aria-label="박자 정확성 검토 근거"]')).toBeNull();
  });

  it('leaves sequential as the selected choice and explains independent overlap', async () => {
    const onChange = vi.fn();
    await act(async () => root.render(<SeparationOptions value="sequential" disabled={false} onChange={onChange} />));
    const select = container.querySelector('select')!;
    expect(select.value).toBe('sequential'); expect(onChange).not.toHaveBeenCalled();
    await act(async () => { select.value = 'independent'; select.dispatchEvent(new Event('change', { bubbles: true })); });
    expect(onChange).toHaveBeenCalledWith('independent');
    await act(async () => root.render(<SeparationOptions value="independent" disabled={true} onChange={onChange} />));
    expect(container.querySelector('select')!.disabled).toBe(true);
    expect(container.textContent).toContain('더 정확하다는 보장은 없고');
    expect(container.textContent).toContain('잔여 음원은 만들지 않습니다');
  });

  it('only labels explicitly selected real independent jobs', async () => {
    await act(async () => root.render(<SeparationNotice job={job} />)); expect(container.textContent).toBe('');
    await act(async () => root.render(<SeparationNotice job={{ ...job, separation_strategy: 'independent' }} />));
    expect(container.textContent).toContain('정확도 향상이 검증된 모드는 아니에요');
    await act(async () => root.render(<SeparationNotice job={{ ...job, separation_strategy: 'independent', analysis_only: true }} />));
    expect(container.textContent).toBe('');
  });

  it('independent stems are never all audible by default or after solo is clicked twice', async () => {
    const independentJob: Job = { ...job, separation_strategy: 'independent', original_url: '/original.wav', stems: [
      { id: 'vocal', label: '보컬', status: 'ready', score_status: 'pending', waveform: [], audio_url: '/vocal.wav' },
      { id: 'drums', label: '드럼', status: 'ready', score_status: 'pending', waveform: [], audio_url: '/drums.wav' },
    ] };
    await act(async () => root.render(<Mixer job={independentJob} selected="drums" onSelect={vi.fn()} onError={vi.fn()} />));
    const audible = () => [...container.querySelectorAll('audio')].filter(audio => !audio.muted).map(audio => audio.getAttribute('src'));
    expect(audible()).toEqual(['/original.wav']);
    const compare = [...container.querySelectorAll('button')].find(button => button.textContent === '원본 비교')!;
    await act(async () => compare.click());
    expect(audible()).toEqual(['/drums.wav']);
    const solo = container.querySelector<HTMLButtonElement>('[aria-label="보컬만 듣기"]')!;
    await act(async () => solo.click()); await act(async () => solo.click());
    expect(audible()).toEqual(['/vocal.wav']);
  });
});
