"""CPU-only pulse analysis. Beat phase is a suggestion, never a downbeat claim."""
import math
from pathlib import Path

import numpy as np


def analyze_beats(path: Path):
    import librosa
    from .beat_evidence import tempo_review
    samples, sr = librosa.load(path, sr=22050, mono=True, duration=180)
    if not np.isfinite(samples).all():
        raise ValueError("음원에 유효하지 않은 샘플이 포함되어 있어요.")
    if len(samples) < sr * 3 or np.sqrt(np.mean(samples.astype(float) ** 2)) < 1e-5:
        raise ValueError("3초 이상의 리듬이 있는 음원이 필요해요.")
    hop = 256
    envelope = librosa.onset.onset_strength(y=samples, sr=sr, hop_length=hop)
    tempo, frames = librosa.beat.beat_track(onset_envelope=envelope, sr=sr, hop_length=hop, trim=True)
    bpm = float(np.asarray(tempo).reshape(-1)[0])
    times = librosa.frames_to_time(frames, sr=sr, hop_length=hop)
    if not math.isfinite(bpm) or not 40 <= bpm <= 240 or len(times) < 3:
        raise ValueError("일정한 박을 찾지 못했어요. BPM과 첫 박을 직접 설정해주세요.")
    intervals = np.diff(times)
    rounded_bpm = round(bpm)
    regularity = max(0, min(1, 1 - float(np.std(intervals) / max(np.mean(intervals), 1e-6))))
    evidence = tempo_review(envelope, sr=sr, hop=hop, times=times, bpm=rounded_bpm)
    return {"bpm": rounded_bpm, "first_beat_seconds": round(float(times[0]), 3),
            "beat_times": [round(float(t), 3) for t in times[:2048]],
            "regularity": round(regularity, 3), "analyzed_seconds": round(len(samples) / sr, 2),
            "alternatives": sorted({round(t) for t in (rounded_bpm / 2, rounded_bpm, rounded_bpm * 2) if 40 <= t <= 240}),
            "evidence": evidence,
            "warning": "BPM·박 위치 추정입니다. 마디의 첫 박과 박자표를 알아낸 것은 아니에요. 반속/배속과 무박 인트로를 직접 확인해주세요."}
