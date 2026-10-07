/** Source-time audition only. Simple synthesized notes are not model evidence. */
import { connectDrumSource, drumPlaybackVoice } from './drumPlayback';
import { reviewAuditionEvents, type ReviewEvent } from './transcriptionReview';
import type { Instrument } from './types';

type Options = {
  window: { start: number; end: number }; speed: number; loop: boolean;
  onPosition: (seconds: number | null) => void; onStop: () => void; onError: (message: string) => void;
};

/** Browsers suspend animation frames in background tabs, but media can keep playing. */
function watchPageVisibility(stop: () => void) {
  const page = typeof document === 'undefined' ? null : document;
  const stopIfHidden = () => { if (page?.hidden) stop(); };
  page?.addEventListener('visibilitychange', stopIfHidden);
  return { stopIfHidden, remove: () => page?.removeEventListener('visibilitychange', stopIfHidden) };
}

export function playReviewAudio(url: string, options: Options): () => void {
  const audio = new Audio(url);
  let active = true, ready = false, positioned = false, frame = 0;
  let removeVisibilityListener = () => {};
  audio.preload = 'auto'; audio.playbackRate = options.speed;
  // Metadata must establish the crop position before any sound becomes audible.
  audio.volume = 0;
  const stop = () => {
    if (!active) return;
    active = false; removeVisibilityListener(); cancelAnimationFrame(frame); audio.pause();
    audio.removeAttribute('src'); audio.load();
    options.onPosition(null); options.onStop();
  };
  const fail = () => { if (active) { stop(); options.onError('구간 음원을 재생하지 못했어요. 파일과 브라우저 재생 허용 여부를 확인해주세요.'); } };
  const checkBoundary = () => {
    if (!active || !ready) return;
    if (audio.currentTime >= options.window.end || audio.ended) {
      if (!options.loop) { stop(); return; }
      // Clear readiness before seeking so timeupdate/ended/RAF cannot request
      // multiple loop seeks while the first asynchronous seek is in progress.
      audio.volume = 0; ready = false;
      try {
        audio.currentTime = options.window.start;
        void audio.play().then(() => { if (!active) audio.pause(); }).catch(fail);
      } catch { fail(); }
    }
  };
  const pump = () => {
    if (!active) return;
    checkBoundary();
    if (!active) return;
    if (ready) {
      options.onPosition(Math.min(options.window.end, Math.max(options.window.start, audio.currentTime)));
    }
    frame = requestAnimationFrame(pump);
  };
  audio.onloadedmetadata = () => {
    if (!active) return;
    if (!Number.isFinite(audio.duration) || audio.duration + .05 < options.window.end) { fail(); return; }
    try {
      positioned = true;
      if (options.window.start === 0 && audio.currentTime === 0) { audio.volume = .8; ready = true; }
      else audio.currentTime = options.window.start;
    }
    catch { fail(); }
  };
  audio.onseeked = () => {
    if (active && positioned && audio.currentTime >= options.window.start && audio.currentTime < options.window.end) {
      audio.volume = .8; ready = true;
    }
  };
  audio.onerror = fail;
  audio.ontimeupdate = checkBoundary;
  audio.onended = checkBoundary;
  const visibility = watchPageVisibility(stop);
  removeVisibilityListener = visibility.remove;
  visibility.stopIfHidden();
  if (!active) return stop;
  void audio.play().then(() => { if (!active) audio.pause(); }).catch(fail);
  frame = requestAnimationFrame(pump);
  return stop;
}

export function playReviewNotes(events: ReviewEvent[], instrument: Instrument, options: Options): () => void {
  const context = new AudioContext();
  let active = true, timer: ReturnType<typeof setInterval> | undefined;
  let removeVisibilityListener = () => {};
  const stop = () => {
    if (!active) return;
    active = false; removeVisibilityListener(); clearInterval(timer);
    if (context.state !== 'closed') void context.close().catch(() => {});
    options.onPosition(null); options.onStop();
  };
  const visibility = watchPageVisibility(stop);
  removeVisibilityListener = visibility.remove;
  visibility.stopIfHidden();
  if (!active) return stop;
  void context.resume().then(() => {
    if (!active) return;
    const notes = reviewAuditionEvents(events, options.window, instrument, options.speed);
    const duration = (options.window.end - options.window.start) / options.speed;
    const origin = context.currentTime + .06;
    const master = context.createGain(); master.gain.value = .3;
    const limiter = context.createDynamicsCompressor(); master.connect(limiter); limiter.connect(context.destination);
    const noise = context.createBuffer(1, context.sampleRate, context.sampleRate);
    const values = noise.getChannelData(0);
    for (let i = 0; i < values.length; i++) values[i] = Math.random() * 2 - 1;
    let cycle = 0, index = 0;
    const voice = (note: typeof notes[number], when: number) => {
      const drum = instrument === 'drums' ? drumPlaybackVoice(note.pitch) : null;
      if (instrument === 'drums' && !drum) return; // Never substitute an unknown GM hit with a kick.
      const length = Math.max(.01, drum ? Math.min(note.duration, drum.maxDuration) : note.duration);
      const gain = context.createGain();
      gain.gain.setValueAtTime(.0001, when);
      gain.gain.exponentialRampToValueAtTime(Math.max(.0002, Math.min(1, note.amplitude) * .2), when + Math.min(.005, length / 4));
      gain.gain.exponentialRampToValueAtTime(.0001, when + length); gain.connect(master);
      let source: OscillatorNode | AudioBufferSourceNode, filter: BiquadFilterNode | undefined;
      if (drum) ({ source, filter } = connectDrumSource(context, noise, gain, drum, when, length));
      else {
        const tone = context.createOscillator(); source = tone;
        tone.type = instrument === 'bass' ? 'sine' : 'triangle';
        tone.frequency.setValueAtTime(440 * 2 ** ((note.pitch - 69) / 12), when); tone.connect(gain);
      }
      source.onended = () => { source.disconnect(); filter?.disconnect(); gain.disconnect(); };
      source.start(when); source.stop(when + length + .01);
    };
    const pump = () => {
      if (!active) return;
      const elapsed = context.currentTime - origin;
      if (!options.loop && elapsed >= duration) { stop(); return; }
      const cutoff = context.currentTime + .12;
      while (true) {
        while (index < notes.length && origin + cycle * duration + notes[index].at <= cutoff) {
          const note = notes[index++], when = origin + cycle * duration + note.at;
          if (when >= context.currentTime - .03) voice(note, Math.max(context.currentTime, when));
        }
        if (!options.loop || origin + (cycle + 1) * duration > cutoff) break;
        cycle++; index = 0;
      }
      if (elapsed >= 0) options.onPosition(options.window.start + (options.loop ? elapsed % duration : elapsed) * options.speed);
    };
    pump(); timer = setInterval(pump, 30);
  }).catch(() => { if (active) { stop(); options.onError('음표 합성음을 재생하지 못했어요. 브라우저의 소리 재생을 허용해주세요.'); } });
  return stop;
}
