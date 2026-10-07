"""Conservative pulse-grid and tempo evidence, never a meter/downbeat detector.

The existing beat tracker determines the beat sequence. A stable grid fit can
offer a separately selectable integer BPM instead of silently changing it.
Onset autocorrelation follows the tempo evidence described in librosa's tempo
documentation; its normalized correlation is not a probability of correctness.
"""
import math

import numpy as np

METHOD = "pulse-grid-review-v1"


def _correlations(envelope, max_lag):
    """Overlap-normalized onset correlation, without a preferred tempo prior."""
    x = np.asarray(envelope, dtype=float)
    # Centering prevents a steady noise floor from supporting every tempo.
    x = x - np.mean(x)
    values = np.zeros(max_lag + 1, dtype=float)
    for lag in range(1, min(max_lag + 1, len(x) - 1)):
        left, right = x[:-lag], x[lag:]
        denominator = math.sqrt(float(np.dot(left, left)) * float(np.dot(right, right)))
        if denominator > 1e-12:
            values[lag] = np.clip(np.dot(left, right) / denominator, 0, 1)
    return values


def _peaks(correlations, frame_seconds, low=40, high=240):
    peaks = []
    for lag in range(2, len(correlations) - 1):
        value = correlations[lag]
        if value <= 0 or value < correlations[lag - 1] or value <= correlations[lag + 1]:
            continue
        # A quadratic peak interpolation reduces integer-lag tempo bias, but
        # does not disambiguate musical pulse level (e.g. half/double time).
        left, right = correlations[lag - 1], correlations[lag + 1]
        denominator = left - 2 * value + right
        delta = .5 * (left - right) / denominator if denominator < -1e-12 else 0.
        period = (lag + float(np.clip(delta, -.5, .5))) * frame_seconds
        bpm = 60 / period
        if low <= bpm <= high:
            peaks.append((float(bpm), float(value)))
    return sorted(peaks, key=lambda item: (-item[1], item[0]))


def pulse_grid_fit(times, bpm):
    """Fit the observed beat index, rejecting gaps and non-constant pulse grids.

    This does not repair missing beats or infer meter. In particular, two
    regularly spaced beats may both be subdivisions rather than quarter notes.
    """
    times = np.asarray(times, dtype=float)
    if times.ndim != 1 or not np.isfinite(times).all() or len(times) < 3 or np.any(np.diff(times) <= 0):
        raise ValueError("박 위치는 유한한 오름차순 값이어야 해요.")
    if not math.isfinite(bpm) or not 40 <= bpm <= 240:
        raise ValueError("BPM 범위를 확인해주세요.")
    indices = np.arange(len(times), dtype=float)
    keep = np.ones(len(times), dtype=bool)
    # Weak boundary pulses can shift a plain least-squares slope. A few robust
    # reweighting steps protect that fit, while the acceptance residual below
    # is still measured on ALL beats (not only the retained points).
    for _ in range(5):
        selected = indices[keep]
        centered = selected - np.mean(selected)
        period = float(np.dot(centered, times[keep] - np.mean(times[keep])) / np.dot(centered, centered))
        start = float(np.mean(times[keep]) - period * np.mean(selected))
        offsets = times - (start + period * indices)
        middle = float(np.median(offsets))
        mad = float(np.median(np.abs(offsets - middle)))
        proposed = np.abs(offsets - middle) <= max(.02, 3 * 1.4826 * mad)
        if np.array_equal(keep, proposed) or np.sum(proposed) < max(3, .8 * len(times)):
            break
        keep = proposed
    residual = np.abs(times - (start + period * indices))
    deviation = float(np.percentile(residual, 95))
    intervals = np.diff(times)
    fitted_bpm = 60 / period
    interval_consistent = bool(np.all((intervals >= period * .75) & (intervals <= period * 1.25)))
    stable = (len(times) >= 8 and times[-1] - times[0] >= 4 and
              40 <= fitted_bpm <= 240 and interval_consistent and
              deviation <= max(.025, period * .06))
    candidate = round(fitted_bpm) if stable else None
    drift = float((60 / bpm - period) * (len(times) - 1))
    return {"status": "stable_fit" if stable else "review_required",
            "fitted_bpm": round(fitted_bpm, 3),
            "candidate_bpm": candidate,
            "first_pulse_seconds": round(max(0., start), 3),
            "p95_deviation_seconds": round(deviation, 4),
            "baseline_end_drift_seconds": round(drift, 4),
            "beat_count": len(times), "interval_consistent": interval_consistent,
            "downbeat_known": False, "automatically_applied": False}


def tempo_review(envelope, *, sr, hop, times, bpm):
    """Add bounded, auditable evidence to the live CPU analysis response."""
    envelope = np.asarray(envelope, dtype=float)
    if envelope.ndim != 1 or not len(envelope) or not np.isfinite(envelope).all():
        raise ValueError("리듬 분석 입력에 유효하지 않은 값이 있어요.")
    if not math.isfinite(sr) or sr <= 0 or not math.isfinite(hop) or hop <= 0:
        raise ValueError("리듬 분석 샘플 간격을 확인해주세요.")
    frame_seconds = hop / sr
    max_lag = math.ceil(60 / 40 / frame_seconds) + 2
    correlations = _correlations(envelope, max_lag)
    peaks = _peaks(correlations, frame_seconds)
    candidates = []
    for candidate, strength in peaks:
        if strength < .1:
            continue
        if any(abs(candidate / previous["bpm"] - 1) < .04 for previous in candidates):
            continue
        candidates.append({"bpm": round(candidate, 2), "periodicity": round(strength, 3)})
        if len(candidates) == 5:
            break
    ambiguous = bool(len(candidates) > 1 and candidates[1]["periodicity"] >= candidates[0]["periodicity"] * .85)

    # Eight-second local windows, at most 45 on the application's 180 s input.
    # Restrict the local comparison to the tracker-selected pulse family so a
    # simple half/double choice is not mislabeled as a physical tempo change.
    window = max(4, round(8 / frame_seconds))
    stride = max(1, round(4 / frame_seconds))
    segments = []
    for first in range(0, len(envelope), stride):
        last = min(first + window, len(envelope))
        if (last - first) * frame_seconds < 4:
            break
        part = envelope[first:last]
        local = _peaks(_correlations(part, max_lag), frame_seconds,
                       max(40, bpm * .75), min(240, bpm * 1.33))
        match = local[0] if local and local[0][1] >= .15 else None
        segments.append({"start_seconds": round(first * frame_seconds, 3),
                         "end_seconds": round(last * frame_seconds, 3),
                         "bpm": round(match[0], 2) if match else None,
                         "periodicity": round(match[1], 3) if match else 0.})
        if last == len(envelope) or len(segments) == 45:
            break
    supported = [item["bpm"] for item in segments if item["bpm"] is not None]
    enough = len(supported) >= 3
    low, high = (np.percentile(supported, [10, 90]) if supported else (None, None))
    variation = bool(enough and (high - low) / np.median(supported) > .08)
    fit = pulse_grid_fit(times, bpm)
    # A DP tracker can impose regular spacing despite changes in the audio.
    # Do not offer a precise constant BPM merely because its own grid is tidy.
    if variation or not candidates:
        fit["status"] = "review_required"
        fit["candidate_bpm"] = None
    warnings = []
    if ambiguous:
        warnings.append("여러 빠르기의 반복 근거가 비슷해요. 반속·배속과 실제 박 단위를 들어보고 선택해주세요.")
    if variation:
        warnings.append("구간별 반복 주기가 달라 고정 BPM으로 끝까지 맞지 않을 수 있어요. 실제 템포 변화인지 연주 패턴 차이인지 확인해주세요.")
    if fit["status"] != "stable_fit":
        warnings.append("일정한 박 격자의 근거가 충분하지 않아 정밀 BPM 후보를 제안하지 않았어요.")
    elif abs(fit["baseline_end_drift_seconds"]) > .08:
        warnings.append("기존 정수 BPM 격자와 검출 박 사이에 누적 밀림이 있어요. 박열 적합 후보도 들어보고 비교해주세요.")
    return {"method": METHOD, "metrics_are_confidence": False, "downbeat_known": False,
            "grid_fit": fit, "tempo_candidates": candidates, "tempo_ambiguous": ambiguous,
            "local_tempo": {"status": "variation_requires_review" if variation else "no_large_variation_detected" if enough else "insufficient_evidence",
                            "range_bpm": [round(float(low), 2), round(float(high), 2)] if supported else None,
                            "segments": segments}, "warnings": warnings}
