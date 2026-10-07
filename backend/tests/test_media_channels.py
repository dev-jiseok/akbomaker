"""Actual FFmpeg input preservation, not transcription-model accuracy tests."""
import hashlib
import json
import shutil
import subprocess
import threading
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from backend import media


pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                                reason="FFmpeg and ffprobe are required for media input tests")


def tone(seconds=.7, sr=44100, hz=220):
    t = np.arange(round(seconds * sr)) / sr
    return (.2 * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def old_normalize(source, target, *, selected=None):
    command = ["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe",
               "-i", str(source), "-map", "0:a:0", "-vn"]
    if selected is not None:
        command += ["-af", f"pan=mono|c0=c{selected}"]
    command += ["-ac", "1", "-ar", str(media.SAMPLE_RATE), "-t", str(media.MAX_AUDIO_SECONDS + 1),
                "-c:a", "pcm_f32le", "-y", str(target)]
    subprocess.run(command, capture_output=True, check=True, timeout=10)


@pytest.mark.parametrize("kind", ["mono", "duplicate", "one_sided", "unrelated", "partly_opposed", "silence"])
def test_normal_inputs_keep_exact_previous_ffmpeg_output(tmp_path, kind):
    signal = tone()
    audio = {"mono": signal, "duplicate": np.c_[signal, signal],
             "one_sided": np.c_[signal, np.zeros_like(signal)],
             "unrelated": np.c_[signal, tone(hz=330)],
             "partly_opposed": np.c_[signal, -.7 * signal],
             "silence": np.zeros((len(signal), 2), np.float32)}[kind]
    source, target, old = (tmp_path / name for name in ("source.wav", "normal.wav", "old.wav"))
    sf.write(source, audio, 44100, subtype="FLOAT")
    original = source.read_bytes()
    old_normalize(source, old)
    audit = {}
    assert media.normalize(source, target, details=audit) == pytest.approx(.7)
    assert target.read_bytes() == old.read_bytes()
    assert source.read_bytes() == original
    assert not audit["used_channel_fallback"]
    assert audit["diagnostic_status"] == ("mono" if kind == "mono" else "checked")
    assert not list(tmp_path.glob(".channel-check-*"))
    json.dumps(audit, allow_nan=False)


@pytest.mark.parametrize("left_gain,right_gain,selected", [(1., -1., 0), (1., -.9, 0), (.9, -1., 1)])
def test_antiphase_upload_selects_original_channel_without_gain_or_source_mutation(tmp_path, left_gain, right_gain, selected):
    signal = tone()
    source, target, expected = (tmp_path / name for name in ("source.wav", "normal.wav", "selected.wav"))
    sf.write(source, np.c_[signal * left_gain, signal * right_gain], 44100, subtype="FLOAT")
    original = source.read_bytes()
    old_normalize(source, expected, selected=selected)
    audit = {}
    assert media.normalize(source, target, details=audit) == pytest.approx(.7)
    assert target.read_bytes() == expected.read_bytes()
    assert source.read_bytes() == original
    assert audit["used_channel_fallback"] and audit["selected_channel"] == selected
    assert audit["cancellation_power_ratio"] <= .01
    assert sf.read(target)[0].std() > .1
    assert not list(tmp_path.glob(".channel-check-*"))
    json.dumps(audit, allow_nan=False)


def test_inaudible_antiphase_noise_is_not_promoted(tmp_path):
    signal = tone() * 1e-6
    source, target = tmp_path / "source.wav", tmp_path / "normal.wav"
    sf.write(source, np.c_[signal, -signal], 44100, subtype="FLOAT")
    audit = {}
    media.normalize(source, target, details=audit)
    assert not audit["used_channel_fallback"] and audit["strongest_channel_rms"] < 1e-5
    assert np.max(np.abs(sf.read(target)[0])) < 1e-10


@pytest.mark.parametrize("channels", [1, 4])
def test_nonstereo_inputs_keep_legacy_command_without_extra_decoder(tmp_path, monkeypatch, channels):
    source, target, old = tmp_path / "source.wav", tmp_path / "normal.wav", tmp_path / "old.wav"
    signal = tone()
    sf.write(source, signal if channels == 1 else np.tile(signal[:, None], (1, channels)), 44100, subtype="FLOAT")
    old_normalize(source, old)
    actual_run, calls = subprocess.run, []
    def record(command, **kwargs):
        calls.append(command)
        return actual_run(command, **kwargs)
    monkeypatch.setattr(media.subprocess, "run", record)
    audit = {}
    media.normalize(source, target, details=audit)
    assert [c[0] for c in calls] == ["ffprobe", "ffmpeg"]
    assert target.read_bytes() == old.read_bytes()
    assert audit["diagnostic_status"] == ("mono" if channels == 1 else "unsupported-channel-count")


def test_stereo_evidence_reads_bounded_blocks_not_a_whole_song(tmp_path, monkeypatch):
    signal = tone(seconds=3, sr=media.SAMPLE_RATE)
    source, target = tmp_path / "source.wav", tmp_path / "normal.wav"
    sf.write(source, np.c_[signal, -signal], media.SAMPLE_RATE, subtype="FLOAT")
    actual_blocks, block_lengths = sf.SoundFile.blocks, []
    def record_blocks(self, *args, **kwargs):
        assert kwargs["blocksize"] == 65536 and kwargs["dtype"] == "float64"
        for block in actual_blocks(self, *args, **kwargs):
            block_lengths.append(len(block))
            yield block
    monkeypatch.setattr(sf.SoundFile, "blocks", record_blocks)
    monkeypatch.setattr(sf, "read", lambda *a, **k: pytest.fail("loaded a whole diagnostic file"))
    audit = {}
    media.normalize(source, target, details=audit)
    assert len(block_lengths) == 3 and max(block_lengths) <= 65536
    assert sum(block_lengths) == len(signal) and audit["used_channel_fallback"]


def make_video(path, audio=None):
    command = ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=32x32:d=0.7"]
    if audio is not None:
        command += ["-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", "-c:a", "pcm_f32le", "-shortest"]
    command += ["-c:v", "ffv1", "-y", str(path)]
    subprocess.run(command, capture_output=True, check=True, timeout=10)


def test_video_first_audio_track_preserves_antiphase_signal(tmp_path):
    signal = tone()
    audio, source, target = tmp_path / "audio.wav", tmp_path / "source.mkv", tmp_path / "normal.wav"
    sf.write(audio, np.c_[signal, -signal], 44100, subtype="FLOAT")
    make_video(source, audio)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    audit = {}
    assert media.normalize(source, target, details=audit) >= .7
    assert audit["input_channels"] == 2 and audit["used_channel_fallback"]
    assert sf.read(target)[0].std() > .1
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_silent_video_without_audio_fails_before_creating_an_output(tmp_path):
    source, target = tmp_path / "source.mkv", tmp_path / "normal.wav"
    make_video(source)
    audit = {}
    with pytest.raises(ValueError, match="오디오 트랙"):
        media.normalize(source, target, details=audit)
    assert not target.exists() and audit == {}
    assert not list(tmp_path.glob(".channel-check-*"))


def test_failed_diagnostic_is_cleaned_and_does_not_touch_existing_output(tmp_path, monkeypatch):
    signal = tone()
    source, target = tmp_path / "source.wav", tmp_path / "normal.wav"
    sf.write(source, np.c_[signal, -signal], 44100, subtype="FLOAT")
    target.write_bytes(b"previous valid output")
    def fail(*args):
        raise ValueError("diagnostic failure")
    monkeypatch.setattr(media, "_stereo_cancellation_evidence", fail)
    audit = {}
    with pytest.raises(ValueError, match="diagnostic failure"):
        media.normalize(source, target, details=audit)
    assert target.read_bytes() == b"previous valid output" and audit == {}
    assert not list(tmp_path.glob(".channel-check-*"))


@pytest.mark.parametrize("analysis_only", [False, True])
def test_upload_pipeline_persists_input_audit_before_any_instrument_analysis(tmp_path, monkeypatch, analysis_only):
    from backend import pipeline, store
    monkeypatch.setattr(store, "DATA_DIR", tmp_path / "projects")
    signal = tone()
    source = tmp_path / "source.wav"
    sf.write(source, np.c_[signal, -signal], 44100, subtype="FLOAT")
    before = source.read_bytes()
    analyzed = []
    def extract(audio, instrument, *args):
        # SAM is mocked: this verifies preserved input reaches every instrument,
        # not GPU separation or real-song model quality.
        assert audio.ndim == 1 and np.std(audio) > .03
        analyzed.append(instrument)
        return audio * .2
    monkeypatch.setattr(pipeline, "ENGINE", SimpleNamespace(extract=extract, offload=lambda: None))
    job = store.create("Stereo upload input preservation", "upload")
    pipeline.run_separation(job["id"], threading.Event(), source, analysis_only=analysis_only)
    result = store.get(job["id"])
    assert result["status"] == "separated", result
    assert result["audio_preprocessing"]["used_channel_fallback"]
    assert result["audio_preprocessing"]["selected_channel"] == 0
    assert result["audio_preprocessing"]["method"] == "preserve-upload-stereo-cancellation-v1"
    assert result["duration"] == pytest.approx(.7)
    assert len(analyzed) == (0 if analysis_only else 6)
    assert source.read_bytes() == before
