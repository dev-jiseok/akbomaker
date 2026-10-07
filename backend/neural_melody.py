"""Opt-in CREPE challenger for isolated monophonic vocal/bass recordings.

This is not the application's default transcriber and cannot resolve chords.
The bundled torchcrepe model is independent of pYIN. Its periodicity is model
evidence, NOT calibrated note correctness. Inference performs no downloads.
"""
from __future__ import annotations

import hashlib
from importlib import metadata
from pathlib import Path

import numpy as np
import soundfile as sf

METHOD = "crepe-monophonic-challenger-v1"
SAMPLE_RATE = 16000
HOP = 160
WINDOW = 1024
PERIODICITY_THRESHOLD = .21
MAX_SECONDS = 600
RANGES = {"vocal": (36, 95), "bass": (24, 79)}
CHECKPOINT_SHA256 = {
    "full": "133225604dedd2e4005f8bbd1bd0a2ec073ba8b7a6cd31ff6d5edbbfa3539986",
    "tiny": "d4993eea36ed1a0ad9ac549c740dae5265b049ce72004f00c2f59e01c0be8432",
}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def package_versions():
    result = {}
    for name in ("torchcrepe", "torch", "torchaudio", "numpy", "scipy", "librosa", "resampy", "soundfile"):
        try:
            result[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            result[name] = None
    return result


def checkpoint_provenance(capacity):
    """Verify the installed package contains weights before any inference."""
    if capacity not in {"tiny", "full"}:
        raise ValueError("CREPE capacity must be tiny or full")
    import torchcrepe
    if metadata.version("torchcrepe") != "0.0.24":
        raise ValueError("Only reviewed torchcrepe 0.0.24 is supported")
    checkpoint = Path(torchcrepe.__file__).parent / "assets" / f"{capacity}.pth"
    if not checkpoint.is_file():
        raise FileNotFoundError("Install torchcrepe with its bundled model weights; no automatic downloads")
    digest = sha256(checkpoint)
    if digest != CHECKPOINT_SHA256[capacity]:
        raise ValueError("CREPE checkpoint does not match the reviewed package weights")
    return {"package": "torchcrepe", "version": metadata.version("torchcrepe"),
            "capacity": capacity, "checkpoint_sha256": digest, "checkpoint_verified": True,
            "checkpoint_bytes": checkpoint.stat().st_size,
            "source": "https://github.com/maxrmorrison/torchcrepe",
            "paper": "https://arxiv.org/abs/1802.06182", "license": "MIT"}


def _predict(samples, *, low, high, capacity):
    import torch
    import torchcrepe
    from .transcription import hz
    # Upstream bins_to_frequency adds SciPy/NumPy random triangular dither.
    # Freeze it in this isolated worker, restoring caller state even on error.
    previous_rng = np.random.get_state()
    try:
        np.random.seed(0)
        with torch.inference_mode():
            pitch, periodicity = torchcrepe.predict(
                torch.from_numpy(samples.copy()).unsqueeze(0), SAMPLE_RATE, HOP,
                hz(low), hz(high), capacity, decoder=torchcrepe.decode.viterbi,
                batch_size=256, device="cpu", return_periodicity=True, pad=True)
    finally:
        np.random.set_state(previous_rng)
    return pitch[0].cpu().numpy(), periodicity[0].cpu().numpy()


def transcribe_crepe(path, instrument, *, capacity="full", details=None):
    """Return raw-second notes; retain unaltered audio and all model audit data.

    Chunking bounds Viterbi/activation memory. Centered 10 ms frames use the
    model's native 16 kHz rate (no implicit rounded 22.05 kHz hop). Silence is
    gated from waveform energy because CREPE was not trained on silent audio.
    Existing note segmentation/reattack checks are shared for a fair challenger.
    """
    if instrument not in RANGES:
        raise ValueError("CREPE challenger supports only monophonic vocal or bass, not chords")
    if capacity not in {"tiny", "full"}:
        raise ValueError("CREPE capacity must be tiny or full")
    info = sf.info(path)
    if not 0 < info.duration <= MAX_SECONDS or not 1 <= info.channels <= 8 or not 8000 <= info.samplerate <= 192000:
        raise ValueError("Unsupported or oversized CREPE audio")
    source_hash = sha256(path)
    from .audio_channels import mono_with_cancellation_guard
    from .melody_decoding import supported_reattacks, trim_release_tails
    from .transcription import clean_events, melody_segments
    import librosa
    audio, original_rate = sf.read(path, dtype="float32", always_2d=True)
    audio, channel_audit = mono_with_cancellation_guard(audio)
    samples = librosa.resample(audio, orig_sr=original_rate, target_sr=SAMPLE_RATE)
    duration = len(samples) / SAMPLE_RATE
    model = checkpoint_provenance(capacity)
    low, high = RANGES[instrument]
    audit = {"engine": METHOD, "experimental": True, "model": model,
             "packages": package_versions(),
             "device": "cpu", "decoder": "viterbi", "sample_rate": SAMPLE_RATE,
             "dither_seed": 0, "requires_isolated_worker": True,
             "hop_seconds": HOP / SAMPLE_RATE, "periodicity_threshold": PERIODICITY_THRESHOLD,
             "periodicity_is_accuracy": False, "pitch_range": [low, high],
             "channel_preprocessing": channel_audit, "source_sha256": source_hash,
             "chunks": [], "limitations": [
                 "Independent monophonic pitch model, not a chord or instrument classifier.",
                 "No real-song accuracy improvement has been established; not enabled by default.",
                 "Bass MIDI 23 and vocal MIDI 96 exceed this challenger's supported frequency range.",
                 "Quantization, score styling, lyrics and source separation are outside this model."]}
    events = []
    chunk, margin = 12 * SAMPLE_RATE, 36 * HOP
    for offset in range(0, len(samples), chunk):
        end = min(len(samples), offset + chunk)
        left, right = max(0, offset - margin), min(len(samples), end + margin)
        part = samples[left:right]
        chunk_audit = {"start_seconds": offset / SAMPLE_RATE, "end_seconds": end / SAMPLE_RATE,
                       "context_start_seconds": left / SAMPLE_RATE, "status": "silent"}
        if float(np.sqrt(np.mean(part.astype(np.float64) ** 2))) < 1e-5:
            audit["chunks"].append(chunk_audit)
            continue
        frequency, periodicity = _predict(part, low=low, high=high, capacity=capacity)
        expected = 1 + len(part) // HOP
        frequency, periodicity = np.asarray(frequency), np.asarray(periodicity)
        if (frequency.shape != (expected,) or periodicity.shape != (expected,)
                or not np.isfinite(frequency).all() or not np.isfinite(periodicity).all()
                or np.any(frequency <= 0) or np.any(periodicity < 0) or np.any(periodicity > 1)):
            raise ValueError("Malformed CREPE model output")
        rms = librosa.feature.rms(y=part, frame_length=WINDOW, hop_length=HOP)[0]
        floor = max(1e-5, float(np.max(rms, initial=0)) * .008)
        voiced = (periodicity >= PERIODICITY_THRESHOLD) & (rms >= floor)
        raw_attacks = librosa.onset.onset_detect(y=part, sr=SAMPLE_RATE, hop_length=HOP,
                                               units="time", backtrack=False)
        attacks = supported_reattacks(raw_attacks, part, SAMPLE_RATE, silence_floor=floor)
        current = melody_segments(frequency, voiced, periodicity, rms, HOP / SAMPLE_RATE,
                                  len(part) / SAMPLE_RATE, onsets=attacks)
        current = trim_release_tails(current, np.setdiff1d(raw_attacks, attacks), part,
                                     SAMPLE_RATE, silence_floor=floor)
        for start, stop, pitch, amplitude in current:
            start, stop = max(offset / SAMPLE_RATE, start + left / SAMPLE_RATE), min(end / SAMPLE_RATE, stop + left / SAMPLE_RATE)
            if stop <= start:
                continue
            if events and start == offset / SAMPLE_RATE and events[-1][2] == pitch and abs(events[-1][1] - start) < 1e-5:
                previous = events.pop()
                events.append((previous[0], stop, pitch, max(previous[3], amplitude)))
            else:
                events.append((start, stop, pitch, amplitude))
        chunk_audit.update(status="complete", frame_count=expected,
                           voiced_frames=int(np.sum(voiced)),
                           median_periodicity=float(np.median(periodicity)),
                           raw_onset_count=len(raw_attacks), supported_onset_count=len(attacks))
        audit["chunks"].append(chunk_audit)
    events = clean_events(events, duration)
    if sha256(path) != source_hash:
        raise RuntimeError("Input audio changed during CREPE inference; refusing stale output")
    audit["event_count"] = len(events)
    if details is not None:
        details.update(audit)
    return events
