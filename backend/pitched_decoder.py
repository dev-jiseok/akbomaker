"""Experimental onset-gated polyphonic decoding; activations are not confidence.

The onset/energy decoding concept builds on Spotify Basic Pitch note_creation.py
(Copyright 2024 Spotify AB, Apache-2.0, https://www.apache.org/licenses/LICENSE-2.0).
This decoder uses independent pitches and onset-relative sustain thresholds;
it never creates new notes solely from a frame-energy peak or removes octaves.
"""
import math

import numpy as np
from scipy.signal import find_peaks

SAMPLE_RATE = 22050
HOP = 256
MIDI_LOW, MIDI_HIGH = 21, 108
MAX_SECONDS = 600
MAX_FRAMES = math.ceil(MAX_SECONDS * SAMPLE_RATE / HOP) + 172
MAX_EVENTS = 30_000
SUSTAIN_RATIO = .5
FRAME_FLOOR = .05
ENERGY_TOLERANCE = 11


def _frame_times(count):
    # Use the installed model's own window-offset correction, not uniform
    # ticks or a hand-tuned audio shift. This loads no model or checkpoint.
    from basic_pitch.note_creation import model_frames_to_time
    return model_frames_to_time(count)


def _sustain_end(column, start, limit, threshold):
    """First sustained low-energy gap, otherwise the last supported frame end."""
    values = column[start + 1:limit]
    if not len(values):
        return start + 1
    below = values < threshold
    edges = np.flatnonzero(np.diff(np.r_[False, below, False]))
    for a, b in zip(edges[::2], edges[1::2]):
        if b - a >= ENERGY_TOLERANCE:
            return start + 1 + int(a)
    supported = np.flatnonzero(~below)
    return start + 2 + int(supported[-1]) if len(supported) else start + 1


def decode_pitched(output, onset_threshold, frame_threshold, minimum_ms, min_pitch, max_pitch, duration):
    """Return raw-second notes and inspectable, uncalibrated activation evidence.

    Confirmed onset peaks start notes. Their first minimum-note-length window
    sets a sustain threshold min(global, max(.05, .5 * early frame peak)).
    Short frame dips may be crossed, but a confirmed same-pitch reattack ends
    the earlier note. Weak/onsetless tones are deliberately not fabricated.
    """
    scalars = (onset_threshold, frame_threshold, minimum_ms, duration)
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in scalars):
        raise ValueError("Pitched decoder settings must be finite numbers")
    if not 0 < onset_threshold <= 1 or not 0 < frame_threshold <= 1 or not 0 < minimum_ms <= MAX_SECONDS * 1000 or not 0 < duration <= MAX_SECONDS:
        raise ValueError("Invalid pitched decoder thresholds or duration")
    if type(min_pitch) is not int or type(max_pitch) is not int or not MIDI_LOW <= min_pitch <= max_pitch <= MIDI_HIGH:
        raise ValueError("Pitched decoder range must be MIDI 21–108, inclusive")
    try:
        frames, onsets = np.asarray(output["note"]), np.asarray(output["onset"])
    except (KeyError, TypeError) as error:
        raise ValueError("Pitched decoder needs note and onset matrices") from error
    if frames.ndim != 2 or frames.shape != onsets.shape or frames.shape[1] != 88 or len(frames) > MAX_FRAMES:
        raise ValueError("Invalid or oversized pitched activation matrices")
    for matrix in (frames, onsets):
        if not np.issubdtype(matrix.dtype, np.number) or np.iscomplexobj(matrix) or not np.isfinite(matrix).all() or np.any(matrix < 0) or np.any(matrix > 1):
            raise ValueError("Pitched activations must be finite values between zero and one")
    minimum_frames = max(1, round(minimum_ms / 1000 * SAMPLE_RATE / HOP))
    review = {"method": "onset-relative-sustain-v1", "evidence_is_confidence": False,
              "uses_inferred_onsets": False, "creates_frame_only_notes": False,
              "onset_threshold": onset_threshold, "frame_threshold": frame_threshold,
              "sustain_ratio": SUSTAIN_RATIO, "frame_floor": FRAME_FLOOR,
              "energy_tolerance_frames": ENERGY_TOLERANCE,
              "minimum_ms": minimum_ms, "minimum_frames": minimum_frames,
              "pitch_range": [min_pitch, max_pitch], "onset_candidates": 0,
              "rejected_short_or_unsupported": 0, "out_of_audio_candidates": 0,
              "offsets_without_energy_release": 0, "event_count": 0, "evidence": []}
    if not len(frames) or minimum_ms / 1000 > duration:
        return [], review
    times = np.asarray(_frame_times(len(frames) + 1), dtype=float)
    if times.shape != (len(frames) + 1,) or not np.isfinite(times).all() or times[0] < 0 or np.any(np.diff(times) <= 0):
        raise ValueError("Invalid model frame time mapping")
    events, evidence = [], []
    for pitch in range(min_pitch, max_pitch + 1):
        index = pitch - MIDI_LOW
        column = frames[:, index]
        # Padding makes an onset at frame zero eligible. The left edge of a
        # plateau is its one onset, not a string of fabricated repeat attacks.
        _, properties = find_peaks(np.r_[-np.inf, onsets[:, index], -np.inf],
                                   height=onset_threshold, plateau_size=(1, None))
        candidates = properties["left_edges"] - 1
        review["onset_candidates"] += len(candidates)
        boundary = len(frames)
        for start in candidates[::-1]:
            start = int(start)
            if times[start] >= duration:
                review["out_of_audio_candidates"] += 1
                continue
            early_peak = float(np.max(column[start:min(len(column), start + max(3, minimum_frames))]))
            threshold = min(frame_threshold, max(FRAME_FLOOR, SUSTAIN_RATIO * early_peak))
            end = _sustain_end(column, start, boundary, threshold)
            start_seconds, end_seconds = float(times[start]), min(duration, float(times[end]))
            if end - start <= minimum_frames or end_seconds - start_seconds + 1e-9 < minimum_ms / 1000:
                review["rejected_short_or_unsupported"] += 1
                continue
            amplitude = float(np.mean(column[start:end], dtype=np.float64))
            if amplitude <= 0:
                review["rejected_short_or_unsupported"] += 1
                continue
            offset_reason = ("energy_release" if end < boundary else
                             "reattack_boundary" if boundary < len(frames) else "model_end")
            if times[end] > duration:
                offset_reason = "audio_end"
            if offset_reason != "energy_release":
                review["offsets_without_energy_release"] += 1
            before = column[max(0, start - max(3, minimum_frames)):start]
            background = float(np.median(before)) if len(before) else None
            boundary = start
            event = (start_seconds, end_seconds, pitch, min(1., amplitude))
            events.append(event)
            evidence.append({"start": start_seconds, "end": end_seconds, "pitch": pitch,
                             "onset_activation": float(onsets[start, index]),
                             "early_frame_peak": early_peak, "sustain_threshold": threshold,
                             "pre_onset_frame_median": background, "offset_reason": offset_reason,
                             "mean_frame_activation": amplitude})
            if len(events) > MAX_EVENTS:
                raise ValueError("Too many pitched note events")
    review["event_count"] = len(events)
    review["evidence"] = sorted(evidence, key=lambda item: (item["start"], item["pitch"]))
    return sorted(events), review
