/** Approximate preview timbres, not sampled instruments or transcription evidence. */
type NoiseVoice = Readonly<{
  source: 'noise';
  family: 'snare' | 'stick' | 'cymbal';
  filter: 'bandpass' | 'highpass';
  frequency: number;
  maxDuration: number;
}>;
type OscillatorVoice = Readonly<{
  source: 'oscillator';
  family: 'kick' | 'tom';
  startFrequency: number;
  endFrequency: number;
  maxDuration: number;
}>;
export type DrumPlaybackVoice = NoiseVoice | OscillatorVoice;

export const drumPlaybackVoices = {
  36: { source: 'oscillator', family: 'kick', startFrequency: 110, endFrequency: 42, maxDuration: .13 },
  37: { source: 'noise', family: 'stick', filter: 'bandpass', frequency: 2400, maxDuration: .055 },
  38: { source: 'noise', family: 'snare', filter: 'bandpass', frequency: 1800, maxDuration: .13 },
  42: { source: 'noise', family: 'cymbal', filter: 'highpass', frequency: 6500, maxDuration: .08 },
  44: { source: 'noise', family: 'cymbal', filter: 'highpass', frequency: 7000, maxDuration: .055 },
  45: { source: 'oscillator', family: 'tom', startFrequency: 130, endFrequency: 82, maxDuration: .13 },
  46: { source: 'noise', family: 'cymbal', filter: 'highpass', frequency: 6500, maxDuration: .3 },
  47: { source: 'oscillator', family: 'tom', startFrequency: 175, endFrequency: 110, maxDuration: .13 },
  49: { source: 'noise', family: 'cymbal', filter: 'highpass', frequency: 5500, maxDuration: .3 },
  50: { source: 'oscillator', family: 'tom', startFrequency: 230, endFrequency: 145, maxDuration: .13 },
  51: { source: 'noise', family: 'cymbal', filter: 'highpass', frequency: 5500, maxDuration: .18 },
} as const satisfies Record<number, DrumPlaybackVoice>;

export function drumPlaybackVoice(pitch: number): DrumPlaybackVoice | undefined {
  // Unknown pitches must not silently become a kick/tom or a negative frequency.
  return (drumPlaybackVoices as Partial<Record<number, DrumPlaybackVoice>>)[pitch];
}

type DrumContext = Pick<AudioContext, 'createBufferSource' | 'createBiquadFilter' | 'createOscillator'>;

/** Connect only; the caller owns the envelope, scheduling and node cleanup. */
export function connectDrumSource(ctx: DrumContext, noise: AudioBuffer, destination: AudioNode,
  voice: DrumPlaybackVoice, when: number, length: number): {
    source: OscillatorNode | AudioBufferSourceNode; filter?: BiquadFilterNode;
  } {
  if (voice.source === 'noise') {
    const source = ctx.createBufferSource();
    source.buffer = noise;
    const filter = ctx.createBiquadFilter();
    filter.type = voice.filter;
    filter.frequency.value = voice.frequency;
    source.connect(filter); filter.connect(destination);
    return { source, filter };
  }
  const source = ctx.createOscillator();
  source.type = 'sine';
  source.frequency.setValueAtTime(voice.startFrequency, when);
  source.frequency.exponentialRampToValueAtTime(voice.endFrequency, when + length);
  source.connect(destination);
  return { source };
}
