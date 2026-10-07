"""Guard against destroying an audible stem while converting stereo to mono.

An arithmetic downmix remains the default. Only near-total cancellation takes
the strongest original channel instead; this is not source separation or a
general solution for multiple independently panned musical parts.
"""
from __future__ import annotations

import math

import numpy as np

EVIDENCE_BLOCK_FRAMES = 65536


def mono_with_cancellation_guard(samples, *, minimum_rms=1e-5, cancellation_power_ratio=.01):
    """Return mono samples and compact, JSON-safe preprocessing evidence.

    Input layout is samples-by-channels, as returned by ``soundfile.read``.
    Preserve ordinary averaging, mono audio, sample count, and channel gain.
    If the average contains at most 1% of the strongest channel's power
    (-20 dB), despite an audible original channel, select that original channel.
    No phase inversion, amplification, alignment, or model inference is done.
    """
    audio = np.asarray(samples)
    if (audio.ndim not in (1, 2) or not np.issubdtype(audio.dtype, np.floating)
            or (audio.ndim == 2 and audio.shape[1] == 0)):
        raise ValueError("Mono preparation requires finite floating-point audio")
    if (isinstance(minimum_rms, bool) or not math.isfinite(minimum_rms) or minimum_rms < 0
            or isinstance(cancellation_power_ratio, bool) or not math.isfinite(cancellation_power_ratio)
            or not 0 <= cancellation_power_ratio <= .01):
        raise ValueError("Invalid conservative downmix parameters")
    count = 1 if audio.ndim == 1 else audio.shape[1]
    audit = {"method": "preserve-stereo-cancellation-v1", "input_channels": count,
             "used_channel_fallback": False, "selected_channel": None,
             "cancellation_power_ratio": None,
             "maximum_cancellation_power_ratio": float(cancellation_power_ratio),
             "strongest_channel_rms": 0., "downmix_rms": 0.}
    if not len(audio):
        return audio if audio.ndim == 1 else audio[:, 0], audit
    # Bound temporary evidence arrays even for a ten-minute stereo upload.
    # First establish one global scale, then accumulate whole-file power; never
    # choose a different channel per block or normalize the returned waveform.
    maximum = 0.
    for start in range(0, len(audio), EVIDENCE_BLOCK_FRAMES):
        block = audio[start:start + EVIDENCE_BLOCK_FRAMES]
        if not np.isfinite(block).all():
            raise ValueError("Mono preparation requires finite floating-point audio")
        maximum = max(maximum, float(np.max(np.abs(block))))
    if not maximum:
        return audio if audio.ndim == 1 else np.mean(audio, axis=1), audit
    channel_power = np.zeros(count, dtype=np.float64)
    downmix_power = 0.
    for start in range(0, len(audio), EVIDENCE_BLOCK_FRAMES):
        scaled = audio[start:start + EVIDENCE_BLOCK_FRAMES].astype(np.float64, copy=False) / maximum
        channel_power += np.atleast_1d(np.sum(scaled * scaled, axis=0))
        downmix_scaled = scaled if audio.ndim == 1 else np.mean(scaled, axis=1)
        downmix_power += float(np.sum(downmix_scaled * downmix_scaled))
    channel_power /= len(audio)
    downmix_power /= len(audio)
    selected = int(np.argmax(channel_power))
    strongest_power = float(channel_power[selected])
    ratio = downmix_power / strongest_power
    # Each scaled sample is <=1; cap a possible summation roundoff at that
    # mathematical bound so extreme finite amplitudes still produce finite RMS.
    strongest_rms = maximum * math.sqrt(min(1., strongest_power))
    audit.update(cancellation_power_ratio=ratio, strongest_channel_rms=strongest_rms,
                 downmix_rms=maximum * math.sqrt(min(1., downmix_power)))
    # Surround layouts have different intended downmix semantics; do not select
    # an arbitrary single channel from those recordings.
    if count == 2 and strongest_rms >= minimum_rms and ratio <= cancellation_power_ratio:
        audit.update(used_channel_fallback=True, selected_channel=selected)
        return audio[:, selected], audit
    if audio.ndim == 1:
        return audio, audit
    # Match librosa.to_mono exactly for ordinary audio. Its channel-major
    # layout does not change the samples-by-channels mean used here.
    with np.errstate(over="ignore"):
        mixed = np.mean(audio, axis=1)
    for start in range(0, len(audio), EVIDENCE_BLOCK_FRAMES):
        stop = start + EVIDENCE_BLOCK_FRAMES
        if not np.isfinite(mixed[start:stop]).all():
            # Finite but extreme floating values can overflow an unscaled sum.
            # Repair only those blocks; ordinary averaging stays bit-identical.
            scaled = audio[start:stop].astype(np.float64, copy=False) / maximum
            mixed[start:stop] = (np.mean(scaled, axis=1) * maximum).astype(audio.dtype)
    return mixed, audit
