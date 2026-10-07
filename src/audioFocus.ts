import { useCallback, useEffect, useId, useRef } from 'react';
import { audioTickAtTime } from './scorePlayback';
import { measureMap } from './scoreRhythm';
import type { ScoreDocument } from './types';

export const AUDIO_FOCUS_EVENT = 'akbo:audio-focus';

function browserTarget(): EventTarget | undefined {
  return typeof window === 'undefined' ? undefined : window;
}

/** A playback group owns focus: the mixer's six stems remain one group. */
export function claimAudioFocus(owner: string, target = browserTarget()): void {
  if (!target || !owner) return;
  target.dispatchEvent(new CustomEvent(AUDIO_FOCUS_EVENT, { detail: { owner } }));
}

export function listenForAudioFocus(owner: string, onLose: () => void, target = browserTarget()): () => void {
  if (!target) return () => {};
  const listener = (event: Event) => {
    const other = (event as CustomEvent<{ owner?: unknown }>).detail?.owner;
    if (typeof other === 'string' && other && other !== owner) onLose();
  };
  target.addEventListener(AUDIO_FOCUS_EVENT, listener);
  return () => target.removeEventListener(AUDIO_FOCUS_EVENT, listener);
}

/** Claim synchronously before async play/resume; keep onLose free of focus claims. */
export function useAudioFocus(onLose: () => void): () => void {
  const owner = useId();
  const latestOnLose = useRef(onLose);
  latestOnLose.current = onLose;
  useEffect(() => {
    const unsubscribe = listenForAudioFocus(owner, () => latestOnLose.current());
    return () => { unsubscribe(); latestOnLose.current(); };
  }, [owner]);
  return useCallback(() => claimAudioFocus(owner), [owner]);
}

/** Inspection-to-editor navigation only; never edits, seeks, or starts audio. */
export function initialScoreMeasureAtAudioTime(doc: ScoreDocument, seconds?: number): number {
  const bars = measureMap(doc);
  if (!bars.length || seconds === undefined || !Number.isFinite(seconds)) return 1;
  const tick = audioTickAtTime(doc, seconds);
  if (tick === null) return seconds <= (doc.audio_offset || 0) ? 1 : bars.length;
  return bars.find(bar => tick >= bar.start && tick < bar.end)?.number || bars.length;
}
