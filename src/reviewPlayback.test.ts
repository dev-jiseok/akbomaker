import { afterEach, describe, expect, it, vi } from 'vitest';
import { playReviewAudio, playReviewNotes } from './reviewPlayback';

class FakeAudio {
  static last: FakeAudio;
  duration = 10; currentTime = 0; volume = 0; playbackRate = 1; preload = ''; ended = false;
  onloadedmetadata: (() => void) | null = null; onerror: (() => void) | null = null;
  onseeked: (() => void) | null = null;
  ontimeupdate: (() => void) | null = null; onended: (() => void) | null = null;
  play = vi.fn(() => Promise.resolve()); pause = vi.fn(); removeAttribute = vi.fn(); load = vi.fn();
  constructor(public src: string) { FakeAudio.last = this; }
}

function fakePage(hidden = false) {
  const page = Object.assign(new EventTarget(), { hidden });
  const remove = vi.spyOn(page, 'removeEventListener');
  vi.stubGlobal('document', page);
  return { page, remove, hide: () => { page.hidden = true; page.dispatchEvent(new Event('visibilitychange')); } };
}

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('bounded source audio audition', () => {
  function setup(loop = false) {
    let frame: (() => void) | undefined;
    vi.stubGlobal('Audio', FakeAudio);
    vi.stubGlobal('requestAnimationFrame', (fn: () => void) => { frame = fn; return 1; });
    vi.stubGlobal('cancelAnimationFrame', vi.fn());
    const options = { window: { start: 3, end: 5 }, speed: .75, loop, onPosition: vi.fn(), onStop: vi.fn(), onError: vi.fn() };
    const stop = playReviewAudio('/local.wav', options);
    return { audio: FakeAudio.last, options, stop, tick: () => frame?.() };
  }
  it('stays inaudible until metadata establishes the exact source start', () => {
    const { audio, stop } = setup();
    expect(audio.volume).toBe(0); expect(audio.playbackRate).toBe(.75);
    audio.onloadedmetadata?.();
    expect(audio.volume).toBe(0); // Seeking is asynchronous; do not leak pre-window audio.
    audio.onseeked?.();
    expect(audio.currentTime).toBe(3); expect(audio.volume).toBe(.8); stop();
  });
  it('stops at the selected end and releases audio/network resources', () => {
    const { audio, options, tick, stop } = setup();
    audio.onloadedmetadata?.(); audio.onseeked?.(); audio.currentTime = 5; tick();
    expect(audio.pause).toHaveBeenCalled(); expect(audio.removeAttribute).toHaveBeenCalledWith('src');
    expect(options.onStop).toHaveBeenCalledTimes(1); stop(); expect(options.onStop).toHaveBeenCalledTimes(1);
  });
  it('loops to the same source start rather than the start of the file', () => {
    const { audio, options, tick, stop } = setup(true);
    audio.onloadedmetadata?.(); audio.onseeked?.(); audio.currentTime = 5; tick();
    expect(audio.currentTime).toBe(3); expect(options.onStop).not.toHaveBeenCalled(); stop();
  });
  it('ignores stale metadata and play completion after a focus stop', async () => {
    const { audio, options, stop } = setup();
    stop(); audio.onloadedmetadata?.(); await Promise.resolve();
    expect(audio.currentTime).toBe(0); expect(audio.volume).toBe(0);
    expect(options.onError).not.toHaveBeenCalled(); expect(audio.pause).toHaveBeenCalled();
  });
  it('refuses an audio source shorter than the verified window', () => {
    const { audio, options } = setup();
    audio.duration = 4; audio.onloadedmetadata?.();
    expect(options.onError).toHaveBeenCalledOnce(); expect(audio.volume).toBe(0);
  });
  it('stops a hidden page without requiring another animation frame', () => {
    const { hide, remove } = fakePage();
    const { audio, options } = setup(true);
    audio.onloadedmetadata?.(); audio.onseeked?.(); hide();
    expect(audio.pause).toHaveBeenCalledOnce();
    expect(options.onStop).toHaveBeenCalledOnce();
    expect(remove).toHaveBeenCalledWith('visibilitychange', expect.any(Function));
    hide(); expect(options.onStop).toHaveBeenCalledOnce();
  });
  it('checks the end on native time updates even without animation frames', () => {
    const { audio, options } = setup();
    audio.onloadedmetadata?.(); audio.onseeked?.(); audio.currentTime = 5;
    audio.ontimeupdate?.();
    expect(options.onStop).toHaveBeenCalledOnce();
  });
  it('handles the native ended event and removes the visibility listener', () => {
    const { remove } = fakePage();
    const { audio, options } = setup();
    audio.onloadedmetadata?.(); audio.onseeked?.(); audio.ended = true; audio.onended?.();
    expect(options.onStop).toHaveBeenCalledOnce();
    expect(remove).toHaveBeenCalledOnce();
  });
  it('does not issue duplicate loop seeks from native events and animation frames', () => {
    const { audio, options, tick, stop } = setup(true);
    audio.onloadedmetadata?.(); audio.onseeked?.(); audio.currentTime = 5;
    audio.ontimeupdate?.(); audio.ended = true; audio.onended?.(); tick();
    expect(audio.play).toHaveBeenCalledTimes(2);
    expect(audio.volume).toBe(0); expect(audio.currentTime).toBe(3);
    expect(options.onStop).not.toHaveBeenCalled();
    audio.ended = false; audio.onseeked?.(); expect(audio.volume).toBe(.8); stop();
  });
  it('does not start audio in an already hidden page', () => {
    const { remove } = fakePage(true);
    const { audio, options } = setup();
    expect(audio.play).not.toHaveBeenCalled(); expect(options.onStop).toHaveBeenCalledOnce();
    expect(remove).toHaveBeenCalledOnce();
  });
  it('removes its page listener on an explicit stop or source failure', () => {
    const { remove } = fakePage();
    const first = setup(); first.stop();
    expect(remove).toHaveBeenCalledOnce();
    const second = setup(); second.audio.onerror?.();
    expect(remove).toHaveBeenCalledTimes(2); expect(second.options.onError).toHaveBeenCalledOnce();
  });
});

describe('synthesized review page visibility', () => {
  class FakeAudioContext {
    static last: FakeAudioContext;
    state = 'running'; currentTime = 0; sampleRate = 16; destination = {};
    resume = vi.fn(() => Promise.resolve());
    close = vi.fn(() => { this.state = 'closed'; return Promise.resolve(); });
    createGain = vi.fn(() => ({ gain: { value: 0 }, connect: vi.fn() }));
    createDynamicsCompressor = vi.fn(() => ({ connect: vi.fn() }));
    createBuffer = vi.fn(() => ({ getChannelData: () => new Float32Array(16) }));
    constructor() { FakeAudioContext.last = this; }
  }
  function setup(hidden = false) {
    vi.useFakeTimers();
    const page = fakePage(hidden);
    vi.stubGlobal('AudioContext', FakeAudioContext);
    const options = { window: { start: 3, end: 5 }, speed: 1, loop: true,
      onPosition: vi.fn(), onStop: vi.fn(), onError: vi.fn() };
    const stop = playReviewNotes([], 'piano', options);
    return { ...page, context: FakeAudioContext.last, options, stop };
  }
  it('cancels pending context resume on hide without creating or restarting voices', async () => {
    const { hide, remove, context, options } = setup();
    hide(); await Promise.resolve();
    expect(context.close).toHaveBeenCalledOnce(); expect(context.createGain).not.toHaveBeenCalled();
    expect(options.onStop).toHaveBeenCalledOnce(); expect(options.onError).not.toHaveBeenCalled();
    expect(remove).toHaveBeenCalledOnce(); expect(vi.getTimerCount()).toBe(0);
  });
  it('closes an active synthesizer and clears its scheduler on hide', async () => {
    const { hide, remove, context, options, stop } = setup();
    await Promise.resolve(); expect(context.createGain).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(1);
    hide(); stop();
    expect(context.close).toHaveBeenCalledOnce(); expect(vi.getTimerCount()).toBe(0);
    expect(options.onStop).toHaveBeenCalledOnce(); expect(remove).toHaveBeenCalledOnce();
  });
  it('does not resume a synthesizer in an already hidden page', () => {
    const { context, options, remove } = setup(true);
    expect(context.resume).not.toHaveBeenCalled(); expect(context.close).toHaveBeenCalledOnce();
    expect(options.onStop).toHaveBeenCalledOnce(); expect(remove).toHaveBeenCalledOnce();
  });
});
