"""Input-path correctness tests; synthetic signals are not song accuracy data."""
import json
import hashlib
import math
import os
import threading

import numpy as np
import pytest
import soundfile as sf

from backend.audio_channels import mono_with_cancellation_guard


def wave(pitch=60, seconds=1.3, sr=22050):
    t = np.arange(round(seconds * sr)) / sr
    envelope = np.minimum(t / .015, 1) * np.minimum((seconds - t) / .04, 1)
    phase = 2 * np.pi * 440 * 2 ** ((pitch - 69) / 12) * t
    return ((np.sin(phase) + .17 * np.sin(2 * phase)) * envelope * .2).astype(np.float32)


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
@pytest.mark.parametrize("kind", ["mono", "one_channel", "duplicate", "one_sided", "unrelated"])
def test_ordinary_audio_is_bit_identical_to_existing_average(dtype, kind):
    source = wave().astype(dtype)
    inputs = {"mono": source, "one_channel": source[:, None], "duplicate": np.c_[source, source],
              "one_sided": np.c_[source, np.zeros_like(source)], "unrelated": np.c_[source, wave(67)]}
    audio = inputs[kind]
    before = audio.copy()
    mono, evidence = mono_with_cancellation_guard(audio)
    expected = audio if audio.ndim == 1 else np.mean(audio, axis=1)
    np.testing.assert_array_equal(mono, expected)
    np.testing.assert_array_equal(audio, before)
    assert mono.dtype == dtype
    assert not evidence["used_channel_fallback"] and evidence["selected_channel"] is None
    json.dumps(evidence, allow_nan=False)


@pytest.mark.parametrize("gain", [1., .99, .9])
def test_cancelled_stereo_preserves_one_original_channel_without_changing_gain(gain):
    source = wave()
    audio = np.c_[source, -gain * source]
    before = audio.copy()
    mono, evidence = mono_with_cancellation_guard(audio)
    np.testing.assert_array_equal(mono, source)
    np.testing.assert_array_equal(audio, before)
    assert evidence["used_channel_fallback"] and evidence["selected_channel"] == 0
    assert evidence["cancellation_power_ratio"] <= .01
    assert evidence["input_channels"] == 2
    json.dumps(evidence, allow_nan=False)


def test_stronger_channel_selection_has_no_phase_flip_or_normalization():
    source = wave()
    mono, evidence = mono_with_cancellation_guard(np.c_[.9 * source, -source])
    np.testing.assert_array_equal(mono, -source)
    assert evidence["selected_channel"] == 1


def test_real_stereo_difference_is_not_automatically_a_reason_to_select_a_channel():
    source = wave()
    audio = np.c_[source, -.7 * source]  # >1% remaining power: retain average.
    mono, evidence = mono_with_cancellation_guard(audio)
    np.testing.assert_array_equal(mono, np.mean(audio, axis=1))
    assert not evidence["used_channel_fallback"]


@pytest.mark.parametrize("channels", [4, 6])
def test_surround_cancellation_keeps_existing_downmix_without_selecting_one_channel(channels):
    source = wave()
    audio = np.tile(np.c_[source, -source], (1, channels // 2))
    mono, evidence = mono_with_cancellation_guard(audio)
    np.testing.assert_array_equal(mono, np.mean(audio, axis=1))
    assert not evidence["used_channel_fallback"] and evidence["selected_channel"] is None


@pytest.mark.parametrize("shape", [(0,), (0, 1), (0, 2), (100,), (100, 2)])
def test_silent_or_empty_inputs_never_invent_sound(shape):
    mono, evidence = mono_with_cancellation_guard(np.zeros(shape, np.float32))
    assert mono.ndim == 1 and len(mono) == shape[0] and not np.any(mono)
    assert not evidence["used_channel_fallback"]
    json.dumps(evidence, allow_nan=False)


def test_inaudible_channel_noise_does_not_trigger_the_guard():
    source = wave() * 1e-6
    mono, evidence = mono_with_cancellation_guard(np.c_[source, -source])
    assert not np.any(mono) and not evidence["used_channel_fallback"]


@pytest.mark.parametrize("audio", [np.array([np.nan]), np.array([np.inf]), np.zeros((2, 0)),
                                  np.zeros((2, 2, 2)), np.zeros(2, int), np.zeros(2, complex)])
def test_invalid_samples_fail_explicitly(audio):
    with pytest.raises(ValueError):
        mono_with_cancellation_guard(audio)


@pytest.mark.parametrize("kwargs", [{"minimum_rms": -1}, {"minimum_rms": math.nan},
    {"minimum_rms": True}, {"cancellation_power_ratio": .1}, {"cancellation_power_ratio": -1},
    {"cancellation_power_ratio": math.inf}, {"cancellation_power_ratio": True}])
def test_guard_cannot_be_relaxed_into_a_general_stereo_channel_picker(kwargs):
    with pytest.raises(ValueError):
        mono_with_cancellation_guard(np.zeros(100, np.float32), **kwargs)


def test_extreme_finite_audio_produces_finite_evidence():
    audio = np.full((10, 2), np.finfo(np.float64).max)
    with np.errstate(over="ignore"):
        mono, evidence = mono_with_cancellation_guard(audio)
    assert np.isfinite(mono).all()
    json.dumps(evidence, allow_nan=False)


def test_long_audio_evidence_uses_bounded_blocks_and_one_global_channel(monkeypatch):
    from backend.audio_channels import EVIDENCE_BLOCK_FRAMES
    frames = EVIDENCE_BLOCK_FRAMES
    # A per-block chooser would switch channels halfway through this input.
    # Globally both channels have equal power, so deterministic channel zero
    # must be selected for the entire recording.
    audio = np.r_[np.tile(np.array([[1., -.95]], np.float32), (frames, 1)),
                  np.tile(np.array([[.95, -1.]], np.float32), (frames, 1))]
    absolute, finite, calls = np.abs, np.isfinite, []
    def bounded_absolute(values, *args, **kwargs):
        assert len(values) <= frames
        calls.append(len(values))
        return absolute(values, *args, **kwargs)
    def bounded_finite(values, *args, **kwargs):
        assert len(values) <= frames
        return finite(values, *args, **kwargs)
    with monkeypatch.context() as context:
        context.setattr(np, "abs", bounded_absolute)
        context.setattr(np, "isfinite", bounded_finite)
        mono, evidence = mono_with_cancellation_guard(audio)
    np.testing.assert_array_equal(mono, audio[:, 0])
    assert calls == [frames, frames]
    assert evidence["used_channel_fallback"] and evidence["selected_channel"] == 0
    json.dumps(evidence, allow_nan=False)


def test_extreme_finite_output_repair_is_safe_across_multiple_blocks():
    from backend.audio_channels import EVIDENCE_BLOCK_FRAMES
    audio = np.full((EVIDENCE_BLOCK_FRAMES + 7, 2), np.finfo(np.float64).max)
    mono, evidence = mono_with_cancellation_guard(audio)
    np.testing.assert_array_equal(mono, audio[:, 0])
    json.dumps(evidence, allow_nan=False)


@pytest.mark.parametrize("source_rate", [22050, 44100, 48000])
@pytest.mark.parametrize("kind", ["mono", "duplicate", "one_sided", "unrelated"])
def test_melody_route_preserves_old_downmix_then_resample_input(tmp_path, monkeypatch, source_rate, kind):
    import librosa
    from backend.transcription import melody_events
    source = wave(sr=source_rate)
    audio = {"mono": source, "duplicate": np.c_[source, source],
             "one_sided": np.c_[source, np.zeros_like(source)],
             "unrelated": np.c_[source, wave(67, sr=source_rate)]}[kind]
    path = tmp_path / "vocal.wav"
    sf.write(path, audio, source_rate, subtype="FLOAT")
    expected, old_sr = librosa.load(path, sr=22050, mono=True)
    calls = []
    def inspect(samples, *, sr, **kwargs):
        calls.append(True)
        assert sr == old_sr == 22050
        np.testing.assert_array_equal(samples, expected)
        frames = len(samples) // kwargs["hop_length"] + 1
        return np.full(frames, np.nan), np.zeros(frames, bool), np.zeros(frames)
    monkeypatch.setattr(librosa, "pyin", inspect)
    details = {}
    assert melody_events(path, "vocal", details=details) == []
    assert calls == [True]
    assert not details["channel_preprocessing"]["used_channel_fallback"]


@pytest.mark.skipif(os.getenv("RUN_MELODY_MODEL_TESTS") != "1", reason="Explicit real CPU pYIN comparison")
@pytest.mark.parametrize("instrument,pitch", [("vocal", 60), ("bass", 40)])
def test_real_pyin_antiphase_reproduction_and_original_mono_parity(tmp_path, instrument, pitch):
    from backend.transcription import melody_events
    source = wave(pitch)
    mono_path, stereo_path, legacy_path, guarded_path = (tmp_path / name for name in
        ("mono.wav", "stereo.wav", "legacy-downmix.wav", "guarded.wav"))
    sf.write(mono_path, source, 22050, subtype="FLOAT")
    stereo = np.c_[source, -source]
    sf.write(stereo_path, stereo, 22050, subtype="FLOAT")
    # Freeze the original arithmetic downmix explicitly so this regression
    # remains useful after the production input path adopts the guard.
    sf.write(legacy_path, np.mean(stereo, axis=1), 22050, subtype="FLOAT")
    restored, evidence = mono_with_cancellation_guard(stereo)
    sf.write(guarded_path, restored, 22050, subtype="FLOAT")
    baseline = melody_events(mono_path, instrument)
    assert baseline and any(note[2] == pitch for note in baseline)
    assert melody_events(legacy_path, instrument) == []
    assert melody_events(guarded_path, instrument) == baseline
    assert evidence["used_channel_fallback"]
    details = {}
    assert melody_events(stereo_path, instrument, details=details) == baseline
    assert details["channel_preprocessing"]["used_channel_fallback"]
    assert "상쇄" in details["warning"]


@pytest.mark.skipif(os.getenv("RUN_MELODY_MODEL_TESTS") != "1", reason="Explicit real CPU pYIN pipeline comparison")
@pytest.mark.parametrize("instrument,pitch,source_rate", [("vocal", 60, 44100), ("bass", 40, 48000)])
def test_actual_pipeline_keeps_original_stereo_hash_and_channel_audit(tmp_path, monkeypatch, instrument, pitch, source_rate):
    from backend import pipeline, store
    monkeypatch.setattr(store, "DATA_DIR", tmp_path / "projects")
    job = store.create("Synthetic stereo cancellation regression", "upload")
    folder = store.directory(job["id"])
    source = folder / f"{instrument}.wav"
    audio = wave(pitch, sr=source_rate)
    sf.write(source, np.c_[audio, -audio], source_rate, subtype="FLOAT")
    original_bytes = source.read_bytes()
    store.update(job["id"], duration=len(audio) / source_rate, status="separated",
                 stems=[{**stem, "status": "ready" if stem["id"] == instrument else "pending"}
                        for stem in job["stems"]])
    pipeline.run_transcription(job["id"], threading.Event(), [instrument], 120)
    completed = store.get(job["id"])
    stem = next(s for s in completed["stems"] if s["id"] == instrument)
    assert stem["score_status"] == "ready", stem
    details = stem["score_transcription"]
    assert details["channel_preprocessing"]["used_channel_fallback"]
    assert "상쇄" in details["warning"]
    artifact = json.loads((folder / f"{instrument}.notes.json").read_text())
    assert artifact["events"] and any(note[2] == pitch for note in artifact["events"])
    assert artifact["source"]["sha256"] == hashlib.sha256(original_bytes).hexdigest()
    assert artifact["provenance"]["preprocessing"] == "preserve-stereo-cancellation-v1"
    assert artifact["provenance"]["channel_preprocessing"] == {
        "method": "preserve-stereo-cancellation-v1", "input_channels": 2,
        "used_channel_fallback": True, "selected_channel": 0}
    assert not artifact["semantics"]["ground_truth"]
    assert source.read_bytes() == original_bytes
    assert (folder / f"{instrument}.musicxml").is_file()
