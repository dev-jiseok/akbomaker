"""Deterministic whole-recording ADT input conditioning (no reference data)."""
import numpy as np

from .audio_channels import mono_with_cancellation_guard


def prepare_audio(audio, *, normalization="peak"):
    """Downmix and match the upstream evaluation's peak scaling, with a gain cap.

    One gain for the entire recording preserves weak/loud hit relationships.
    Never normalize silence, and cap amplification at 20 dB to limit noise boost.
    Near-total stereo cancellation keeps the strongest original channel, with
    evidence; ordinary downmix and all drum classification remain unchanged.
    """
    if normalization not in {"none", "peak"}:
        raise ValueError("Unknown drum input normalization")
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim not in {1, 2} or not audio.size or not np.isfinite(audio).all():
        raise ValueError("Audio must be nonempty and finite")
    protected, channel_evidence = mono_with_cancellation_guard(audio)
    # Keep the original arithmetic (including float64 accumulation) bit-for-bit
    # for ordinary recordings. Only proven near-total stereo cancellation uses
    # the strongest original channel; no hit or drum class is invented here.
    mono = (protected.copy() if channel_evidence["used_channel_fallback"] else
            audio.mean(axis=1, dtype=np.float64).astype(np.float32) if audio.ndim == 2 else audio.copy())
    rms = float(np.sqrt(np.mean(mono.astype(np.float64) ** 2)))
    peak = float(np.max(np.abs(mono)))
    silent = rms < 1e-5
    gain = min(10., 1. / peak) if normalization == "peak" and not silent else 1.
    return mono * gain, {"normalization": normalization, "input_peak": peak,
                         "input_rms": rms, "gain": gain, "silent_input": silent,
                         "channel_preprocessing": channel_evidence}
