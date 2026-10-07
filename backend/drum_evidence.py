"""Conservative waveform support check for neural kick candidates.

This is not a drum classifier: it never adds notes or changes their GM type.
Only unusually weak, non-attacking low-frequency kick candidates are rejected,
and only if at least six clear kick attacks establish a recording-local scale.
"""
import math

import numpy as np
from scipy.signal import butter, sosfiltfilt

METHOD = "conservative-kick-evidence-v1"
# Frozen using development recordings, before the validation evaluation.
MINIMUM_ANCHORS = 6
MINIMUM_ANCHOR_SEPARATION = .08
ANCHOR_ATTACK_RATIO = 2.0
MAXIMUM_RELATIVE_LOW_RMS = .2
MAXIMUM_ATTACK_RATIO = 1.2


def suppress_unsupported_kicks(events, audio, sample_rate):
    """Return retained events and an auditable record; never relabel a hi-hat.

    Gain-invariant ratios use 30–180 Hz energy. The attack measurement permits
    30 ms of model onset error before the reported timestamp. Insufficient
    evidence (silence, short recordings, recording edges, too few anchors) keeps
    the original candidate. Stereo energy is measured per channel, avoiding
    cancellation on opposite-polarity material.
    """
    if (isinstance(sample_rate, bool) or not isinstance(sample_rate, (int, float))
            or not math.isfinite(sample_rate) or not 8000 <= sample_rate <= 192000):
        raise ValueError("Sample rate must be between 8000 and 192000 Hz")
    data = np.asarray(audio)
    if (data.ndim not in (1, 2) or not data.size or data.dtype.kind not in "fiu"
            or (data.ndim == 2 and data.shape[1] not in (1, 2))
            or not np.isfinite(data).all()
            or max(abs(float(np.min(data))), abs(float(np.max(data)))) > 1e6):
        raise ValueError("Audio must be finite nonempty mono or stereo samples")
    if len(events) > 30000:
        raise ValueError("Too many drum candidates")
    original = []
    duration = len(data) / sample_rate
    for event in events:
        if (len(event) != 4 or any(isinstance(v, bool) for v in event)
                or not all(isinstance(v, (int, float, np.number)) and math.isfinite(v) for v in event)):
            raise ValueError("Invalid drum candidate")
        a, b, pitch, amp = event
        if not 0 <= a < min(b, duration) or pitch != int(pitch) or not 35 <= pitch <= 81 or not 0 < amp <= 1:
            raise ValueError("Invalid drum candidate")
        original.append(tuple(event))
    sos = butter(3, [30, 180], fs=sample_rate, btype="bandpass", output="sos")
    evidence = []
    for index, event in enumerate(original):
        if event[2] not in (35, 36):
            continue
        t = event[0]
        if t < .12 or t + .12 > duration:
            evidence.append({"index": index, "event": event, "eligible": False,
                             "reason": "recording-edge", "rejected": False})
            continue
        first, last = max(0, int((t - .35) * sample_rate)), min(len(data), int((t + .35) * sample_rate))
        # Filtering local windows bounds temporary memory even for long audio.
        segment = np.asarray(data[first:last], dtype=np.float64)
        low = sosfiltfilt(sos, segment, axis=0)
        def rms(start, end):
            window = low[int(start * sample_rate) - first:int(end * sample_rate) - first]
            return float(np.sqrt(np.mean(window * window)))
        energy, before = rms(t - .03, t + .08), rms(t - .10, t - .035)
        evidence.append({"index": index, "event": event, "eligible": True,
                         "low_rms": energy, "before_low_rms": before,
                         "attack_ratio": min(1e12, energy / max(before, energy * 1e-12, 1e-300)),
                         "rejected": False})
    # Distinct timestamps around the same attack cannot manufacture the six
    # independent anchors required by the guard. This affects evidence only:
    # the actual event list and legitimate fast repeated hits are untouched.
    qualifying = sorted((e for e in evidence if e["eligible"] and e["low_rms"] > 1e-8
                         and e["attack_ratio"] >= ANCHOR_ATTACK_RATIO),
                        key=lambda e: (e["event"][0], e["index"]))
    anchors, last_anchor = [], -math.inf
    for item in qualifying:
        if item["event"][0] - last_anchor + 1e-12 >= MINIMUM_ANCHOR_SEPARATION:
            anchors.append(item["low_rms"])
            last_anchor = item["event"][0]
    # Deduplication may raise a median. Capping by the old candidate median
    # ensures this safety amendment can only keep extra notes, never introduce
    # a rejection which the original waveform rule would not have made.
    scale = (min(float(np.median(anchors)), float(np.median([e["low_rms"] for e in qualifying])))
             if len(anchors) >= MINIMUM_ANCHORS else None)
    rejected = set()
    for item in evidence:
        if not item["eligible"]:
            continue
        ratio = item["low_rms"] / scale if scale is not None else None
        item["relative_low_rms"] = ratio
        item["rejected"] = bool(ratio is not None and ratio < MAXIMUM_RELATIVE_LOW_RMS
                                and item["attack_ratio"] < MAXIMUM_ATTACK_RATIO)
        item["reason"] = ("weak-low-band-without-attack" if item["rejected"] else
                          "insufficient-anchors" if scale is None else "retained")
        if item["rejected"]:
            rejected.add(item["index"])
    return [e for i, e in enumerate(original) if i not in rejected], {
        "method": METHOD, "anchor_count": len(anchors), "anchor_low_rms": scale,
        "rejected_count": len(rejected), "relabeled_count": 0, "added_count": 0,
        "evidence_is_confidence": False,
        "parameters": {"low_band_hz": [30, 180], "minimum_anchors": MINIMUM_ANCHORS,
                       "minimum_anchor_separation_seconds": MINIMUM_ANCHOR_SEPARATION,
                       "anchor_attack_ratio": ANCHOR_ATTACK_RATIO,
                       "maximum_relative_low_rms": MAXIMUM_RELATIVE_LOW_RMS,
                       "maximum_attack_ratio": MAXIMUM_ATTACK_RATIO},
        "candidates": evidence,
    }
