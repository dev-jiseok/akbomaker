import { expect, it, vi } from 'vitest';
import { connectDrumSource, drumPlaybackVoice, drumPlaybackVoices } from './drumPlayback';
import { drumLanes } from './scoreEditing';

function audioMocks() {
  const oscillator = { type: '', frequency: { setValueAtTime: vi.fn(), exponentialRampToValueAtTime: vi.fn() }, connect: vi.fn() };
  const bufferSource = { buffer: null, connect: vi.fn() };
  const filter = { type: '', frequency: { value: 0 }, connect: vi.fn() };
  const factories = { createOscillator: vi.fn(() => oscillator), createBufferSource: vi.fn(() => bufferSource), createBiquadFilter: vi.fn(() => filter) };
  const ctx = factories as unknown as Pick<AudioContext, 'createBufferSource' | 'createBiquadFilter' | 'createOscillator'>;
  return { oscillator, bufferSource, filter, factories, ctx, noise: {} as AudioBuffer, destination: {} as AudioNode };
}

it('explicitly covers all eleven editable drum voices and no unknown-pitch fallback', () => {
  expect(Object.keys(drumPlaybackVoices).map(Number).sort((a, b) => a - b))
    .toEqual(drumLanes.map(n => n.pitch).sort((a, b) => a - b));
  expect(Object.keys(drumPlaybackVoices)).toHaveLength(11);
  for (const pitch of [0, 35, 39, 128, -1, NaN, 42.5]) expect(drumPlaybackVoice(pitch)).toBeUndefined();
  expect(Object.entries(drumPlaybackVoices).filter(([, voice]) => voice.family === 'kick').map(([pitch]) => Number(pitch)))
    .toEqual([36]);
});

it.each([42, 44, 46, 49, 51])('connects cymbal %i as high-pass noise, never a kick-like oscillator', pitch => {
  const m = audioMocks(), descriptor = drumPlaybackVoice(pitch)!;
  const result = connectDrumSource(m.ctx, m.noise, m.destination, descriptor, 2, descriptor.maxDuration);
  expect(descriptor.source).toBe('noise');
  expect(descriptor.family).toBe('cymbal');
  expect(m.factories.createOscillator).not.toHaveBeenCalled();
  expect(m.bufferSource.buffer).toBe(m.noise);
  expect(m.filter.type).toBe('highpass');
  expect(m.filter.frequency.value).toBeGreaterThanOrEqual(5500);
  expect(m.bufferSource.connect).toHaveBeenCalledWith(m.filter);
  expect(m.filter.connect).toHaveBeenCalledWith(m.destination);
  expect(result).toEqual({ source: m.bufferSource, filter: m.filter });
});

it('renders side-stick as a short positive-frequency filtered click, not the old negative-frequency tone', () => {
  const m = audioMocks(), descriptor = drumPlaybackVoice(37)!;
  connectDrumSource(m.ctx, m.noise, m.destination, descriptor, 2, descriptor.maxDuration);
  expect(descriptor.family).toBe('stick');
  expect(descriptor.maxDuration).toBeLessThan(.08);
  expect(m.filter.type).toBe('bandpass');
  expect(m.filter.frequency.value).toBe(2400);
  expect(m.factories.createOscillator).not.toHaveBeenCalled();
});

it.each([36, 45, 47, 50])('schedules safe positive oscillator frequencies for kick/tom %i', pitch => {
  const m = audioMocks(), descriptor = drumPlaybackVoice(pitch)!;
  if (descriptor.source !== 'oscillator') throw new Error('Expected oscillator descriptor');
  const result = connectDrumSource(m.ctx, m.noise, m.destination, descriptor, 2, .1);
  expect(descriptor.family).toBe(pitch === 36 ? 'kick' : 'tom');
  expect(Number.isFinite(descriptor.startFrequency)).toBe(true);
  expect(Number.isFinite(descriptor.endFrequency)).toBe(true);
  expect(descriptor.startFrequency).toBeGreaterThan(0);
  expect(descriptor.endFrequency).toBeGreaterThan(0);
  expect(m.oscillator.frequency.setValueAtTime).toHaveBeenCalledWith(descriptor.startFrequency, 2);
  expect(m.oscillator.frequency.exponentialRampToValueAtTime).toHaveBeenCalledWith(descriptor.endFrequency, 2.1);
  expect(m.oscillator.connect).toHaveBeenCalledWith(m.destination);
  expect(m.factories.createBufferSource).not.toHaveBeenCalled();
  expect(result).toEqual({ source: m.oscillator });
});

it('keeps a distinct snare noise path and short pedal-hat duration', () => {
  const m = audioMocks();
  connectDrumSource(m.ctx, m.noise, m.destination, drumPlaybackVoice(38)!, 1, .13);
  expect(m.filter.type).toBe('bandpass');
  expect(m.filter.frequency.value).toBe(1800);
  expect(drumPlaybackVoice(44)!.maxDuration).toBeLessThan(drumPlaybackVoice(42)!.maxDuration);
  expect(drumPlaybackVoice(42)!.maxDuration).toBeLessThan(drumPlaybackVoice(46)!.maxDuration);
  for (const descriptor of Object.values(drumPlaybackVoices)) {
    expect(descriptor.maxDuration).toBeGreaterThan(0);
    expect(descriptor.maxDuration).toBeLessThanOrEqual(.3);
  }
});
