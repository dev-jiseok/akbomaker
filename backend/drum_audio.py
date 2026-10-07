"""Deterministic whole-recording ADT input conditioning (no reference data)."""
import numpy as np


def prepare_audio(audio, *, normalization="peak"):
    """Downmix and match the upstream evaluation's peak scaling, with a gain cap.

    One gain for the entire recording preserves weak/loud hit relationships.
    Never normalize silence, and cap amplification at 20 dB to limit noise boost.
    """
    if normalization not in {"none", "peak"}:
        raise ValueError("Unknown drum input normalization")
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim not in {1, 2} or not audio.size or not np.isfinite(audio).all():
        raise ValueError("Audio must be nonempty and finite")
    mono = audio.mean(axis=1, dtype=np.float64).astype(np.float32) if audio.ndim == 2 else audio.copy()
    rms = float(np.sqrt(np.mean(mono.astype(np.float64) ** 2)))
    peak = float(np.max(np.abs(mono)))
    silent = rms < 1e-5
    gain = min(10., 1. / peak) if normalization == "peak" and not silent else 1.
    return mono * gain, {"normalization": normalization, "input_peak": peak,
                         "input_rms": rms, "gain": gain, "silent_input": silent}
