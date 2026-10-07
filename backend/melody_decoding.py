"""Waveform evidence for monophonic note segmentation, not pitch correction.

Positive spectral flux is not sufficient evidence for a repeated note: an
abrupt release redistributes FFT energy and can itself produce an onset peak.
pYIN's long, centered analysis window may remain voiced after that release.
Requiring audible waveform support in the latter half of the existing minimum
note duration prevents that combination from manufacturing a second note.

This deliberately does not infer a missing pitch, join rests, fold octaves, or
require a volume increase (a real repeated note can be quieter than its
predecessor). Residual separation noise can still support a false onset.
"""
from __future__ import annotations

import math

import numpy as np


def supported_reattacks(onsets, samples, sample_rate, *, silence_floor=1e-5,
                       minimum_note_seconds=.07):
    """Keep onset candidates with physical support for a minimum-length note.

    ``silence_floor`` is an RMS amplitude, normally the same floor used by the
    pitch segmentation stage. Inspect the latter half of the minimum duration,
    not centered pitch-analysis RMS, which includes sound *before* the attack.
    Partial support at EOF is allowed, but a candidate outside the waveform or
    with no latter-half samples cannot establish a valid repeated note.

    Returns onset times unchanged; only repeated-note splitting uses these
    candidates. Pitch transitions and voiced/unvoiced boundaries remain the
    responsibility of the fundamental tracker.
    """
    audio = np.asarray(samples)
    if audio.ndim != 1 or not np.isfinite(audio).all():
        raise ValueError("Repeated-note support requires finite mono samples")
    if (isinstance(sample_rate, bool) or not math.isfinite(sample_rate)
            or sample_rate <= 0 or not math.isfinite(silence_floor)
            or silence_floor < 0 or not math.isfinite(minimum_note_seconds)
            or minimum_note_seconds <= 0):
        raise ValueError("Invalid repeated-note support parameters")
    result = []
    for value in onsets:
        onset = float(value)
        if not math.isfinite(onset) or onset < 0:
            continue
        a = round((onset + minimum_note_seconds / 2) * sample_rate)
        b = min(len(audio), round((onset + minimum_note_seconds) * sample_rate))
        if a >= b:
            continue
        support = audio[a:b].astype(np.float64, copy=False)
        if float(np.sqrt(np.mean(support * support))) > silence_floor:
            result.append(onset)
    return np.asarray(result, dtype=float)


def trim_release_tails(events, rejected_onsets, samples, sample_rate, *, silence_floor=1e-5,
                       minimum_note_seconds=.07, minimum_output_seconds=.04):
    """Use confirmed release peaks to trim voiced-window overhang, not reattack.

    A removed spectral onset can mark a real release. Without retaining this
    evidence, suppressing the false repeat would merge its silent tail into the
    previous note. Require the *complete* latter-half support window to be
    silent, then locate the last audible waveform sample near that peak. Never
    infer a release from missing EOF context, extend an event, or touch a
    non-overlapping note. Keep the downstream cleaner's minimum event length
    so fixing an offset does not discard an otherwise recognized short note.
    """
    audio = np.asarray(samples, dtype=np.float64)
    if (audio.ndim != 1 or not np.isfinite(audio).all()
            or isinstance(sample_rate, bool) or not math.isfinite(sample_rate) or sample_rate <= 0
            or not math.isfinite(silence_floor) or silence_floor < 0
            or not math.isfinite(minimum_note_seconds) or minimum_note_seconds <= 0
            or not math.isfinite(minimum_output_seconds) or minimum_output_seconds <= 0):
        raise ValueError("Invalid release-tail evidence")
    releases = []
    radius = minimum_note_seconds / 2
    for value in rejected_onsets:
        onset = float(value)
        if not math.isfinite(onset) or onset < 0:
            continue
        a = round((onset + radius) * sample_rate)
        b = round((onset + minimum_note_seconds) * sample_rate)
        # An absent file tail is not evidence of physical silence.
        if a >= b or b > len(audio):
            continue
        if float(np.sqrt(np.mean(audio[a:b] ** 2))) > silence_floor:
            continue
        left = max(0, round((onset - radius) * sample_rate))
        audible = np.flatnonzero(np.abs(audio[left:a]) > silence_floor)
        if len(audible):
            releases.append((onset, (left + int(audible[-1]) + 1) / sample_rate))
    result = []
    for event in events:
        start, end, *rest = event
        for onset, release in releases:
            if start < onset < end and start < release < end:
                # Keep the exact downstream >= minimum test across floating
                # subtraction at nonzero timestamps.
                minimum_end = math.nextafter(start + minimum_output_seconds, math.inf)
                end = min(end, max(minimum_end, release))
        result.append((start, end, *rest))
    return result
