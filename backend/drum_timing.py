"""Restore shifted ADT timestamps without turning padded audio into real hits."""
import math

# ADT onsets use a 10 ms token grid. Its MIDI bridge adds at most half a
# 120-BPM / 220-PPQ tick (about 1.14 ms) of rounding. Allow 11.2 ms, not an
# arbitrary overlap with the beginning of the recording.
LEADING_TOLERANCE_SECONDS = .0112


def restore_drum_timing(events, offset, duration):
    """Undo one pass's leading padding and audit clipping/rejected candidates.

    Only a slightly negative *onset*, with audio remaining after zero, may be
    restored to zero. A 100 ms synthetic MIDI duration is not evidence that a
    much earlier padded prediction was a real opening attack.
    """
    if not math.isfinite(offset) or offset < 0 or not math.isfinite(duration) or duration <= 0:
        raise ValueError("Invalid drum pass offset or audio duration")
    restored = []
    review = {"offset_seconds": offset, "leading_tolerance_seconds": LEADING_TOLERANCE_SECONDS,
              "input_count": 0, "adjusted_leading": 0, "dropped_leading": 0,
              "dropped_trailing": 0, "clipped_trailing": 0}
    for event in events:
        if len(event) != 4 or not all(math.isfinite(value) for value in event):
            raise ValueError("Invalid drum timing candidate")
        start, end, pitch, amplitude = event
        if not 0 <= start < end or pitch != int(pitch) or not 35 <= pitch <= 81 or not 0 < amplitude <= 1:
            raise ValueError("Invalid drum timing candidate")
        review["input_count"] += 1
        start, end = start - offset, end - offset
        if start < -LEADING_TOLERANCE_SECONDS - 1e-9 or end <= 0:
            review["dropped_leading"] += 1
            continue
        if start >= duration:
            review["dropped_trailing"] += 1
            continue
        if start < 0:
            start = 0.
            review["adjusted_leading"] += 1
        if end > duration:
            end = duration
            review["clipped_trailing"] += 1
        restored.append((start, end, int(pitch), amplitude))
    review["output_count"] = len(restored)
    return restored, review
