import io
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
import zipfile

import mido
import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from backend import app as api, pipeline, store
from backend.config import INSTRUMENTS, SAMPLE_RATE
from backend.media import normalize, youtube_url
from backend.score import export_score
from backend.separator import Cancelled, chunk_starts, separate_sequential


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    with TestClient(api.app) as client:
        yield client
        deadline = time.monotonic() + 10
        while api.EVENTS and time.monotonic() < deadline:
            time.sleep(0.02)


def finish(client, job):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = client.get(f'/api/jobs/{job["id"]}').json()
        if job["status"] not in {"queued", "running", "transcribing", "analyzing"}:
            return job
        time.sleep(0.02)
    pytest.fail("Worker did not finish")


def test_sequential_extraction_conserves_audio_and_calls_once():
    original = np.arange(100, dtype=np.float32) / 100
    calls, stems = [], []

    def extract(residual, inst, event, progress):
        calls.append((inst, residual.copy()))
        return residual * 0.2

    def emit(inst, target, index, fraction):
        stems.append(target)

    residual = separate_sequential(original, extract, emit, threading.Event())
    assert [inst for inst, _ in calls] == list(INSTRUMENTS)
    assert len(calls) == 6
    for index in range(1, len(calls)):
        np.testing.assert_allclose(calls[index][1], calls[index - 1][1] - stems[index - 1])
    np.testing.assert_allclose(sum(stems) + residual, original, atol=2e-7)


def test_cancellation_stops_before_next_inference():
    event = threading.Event()
    calls = []

    def extract(audio, inst, cancel, progress):
        calls.append(inst)
        cancel.set()
        return np.zeros_like(audio)

    with pytest.raises(Cancelled):
        separate_sequential(np.ones(100), extract, lambda *args: None, event)
    assert calls == ["vocal"]


def test_overlapping_chunks_cover_tail_without_gaps():
    for length in [1, 100, 120, 135, 201, 301]:
        starts = chunk_starts(length, 100, 20)
        coverage = np.zeros(length)
        for start in starts:
            coverage[start:start + 100] += 1
        assert coverage.min() >= 1
        assert starts == sorted(set(starts))


@pytest.mark.parametrize("url", ["http://youtu.be/abcdefghijk", "https://youtube.com.evil.test/watch?v=abcdefghijk", "https://127.0.0.1/watch?v=abcdefghijk", "https://user:password@youtube.com/watch?v=abcdefghijk", "https://youtube.com/playlist?list=x", "https://youtu.be/bad", "https://youtube.com:444/watch?v=abcdefghijk"])
def test_youtube_rejects_non_video_and_non_youtube_urls(url):
    with pytest.raises(ValueError):
        youtube_url(url)


def test_youtube_normalizes_video_and_discards_untrusted_query():
    assert youtube_url("https://youtu.be/abcdefghijk?t=30&list=anything") == "https://www.youtube.com/watch?v=abcdefghijk"


def test_musicxml_conserves_measure_rhythm_and_ties(tmp_path):
    events = [(0, 3, 60, 0.8), (0.5, 1, 64, 0.7), (1, 1.5, 67, 0.7)]
    assert export_score(events, "piano", "A & B", 120, 4, tmp_path) == 3
    tree = ET.parse(tmp_path / "piano.musicxml")
    assert tree.findtext("work/work-title") == "A & B"
    assert len(tree.findall("part/measure")) == 2
    for measure in tree.findall("part/measure"):
        assert sum(int(note.findtext("duration")) for note in measure.findall("note") if note.find("chord") is None) == 16
    assert tree.findall(".//tie[@type='start']")
    assert tree.findall(".//tie[@type='stop']")
    midi = mido.MidiFile(tmp_path / "piano.mid")
    assert sum(message.type == "note_on" for message in midi.tracks[0]) == 3


def test_drums_use_percussion_clef_and_channel(tmp_path):
    export_score([(0, 0.1, 36, 0.8), (0.5, 0.6, 42, 0.6)], "drums", "Drums", 120, 2, tmp_path)
    tree = ET.parse(tmp_path / "drums.musicxml")
    assert tree.findtext(".//clef/sign") == "percussion"
    assert tree.findtext(".//midi-channel") == "10"
    assert len(tree.findall(".//unpitched")) == 2
    assert tree.findtext(".//notehead") == "x"
    midi = mido.MidiFile(tmp_path / "drums.mid")
    assert all(message.channel == 9 for message in midi.tracks[0] if message.type == "note_on")


def test_demo_generates_real_playable_and_downloadable_artifacts(client):
    response = client.post("/api/demo")
    assert response.status_code == 202
    job = finish(client, response.json())
    assert job["status"] == "completed"
    assert job["demo"] is True
    reconstructed = None
    for stem in job["stems"]:
        assert stem["status"] == stem["score_status"] == "ready"
        audio_response = client.get(stem["audio_url"])
        audio, sr = sf.read(io.BytesIO(audio_response.content))
        assert sr == SAMPLE_RATE
        assert np.max(np.abs(audio)) > 0.01
        reconstructed = audio if reconstructed is None else reconstructed + audio
        assert client.get(stem["audio_url"], headers={"Range": "bytes=0-127"}).status_code == 206
        assert client.get(stem["score_url"]).content.startswith(b"<?xml")
        assert client.get(stem["midi_url"]).content.startswith(b"MThd")
    original, _ = sf.read(io.BytesIO(client.get(job["original_url"]).content))
    np.testing.assert_allclose(reconstructed, original, atol=1e-7)
    archive = zipfile.ZipFile(io.BytesIO(client.get(f'/api/jobs/{job["id"]}/archive').content))
    assert "piano.musicxml" in archive.namelist()
    assert "job.json" not in archive.namelist()
    assert b"Not SAM Audio inference" in archive.read("README.txt")
    assert client.get(f'/api/jobs/{job["id"]}/files/job.json').status_code == 404
    assert client.get("/api/jobs/invalid").status_code == 404


def test_actual_upload_pipeline_and_transcription_contract(client, monkeypatch):
    monkeypatch.setattr(api, "engine_status", lambda: {"available": True, "transcription_available": True})

    class TestSeparator:
        def extract(self, audio, inst, event, progress):
            progress(0.5)
            return audio * 0.15

    monkeypatch.setattr(pipeline, "ENGINE", TestSeparator())
    samples = (0.2 * np.sin(2 * np.pi * 440 * np.arange(SAMPLE_RATE) / SAMPLE_RATE)).astype(np.float32)
    source = io.BytesIO()
    sf.write(source, samples, SAMPLE_RATE, format="WAV", subtype="FLOAT")
    response = client.post("/api/jobs", files={"file": ("test-tone.wav", source.getvalue(), "audio/wav")})
    assert response.status_code == 202
    job = finish(client, response.json())
    assert job["status"] == "separated", job
    assert job["demo"] is False
    all_audio = [sf.read(io.BytesIO(client.get(s["audio_url"]).content))[0] for s in job["stems"]]
    residual, _ = sf.read(io.BytesIO(client.get(job["residual_url"]).content))
    np.testing.assert_allclose(sum(all_audio) + residual, samples, atol=1e-7)
    monkeypatch.setattr(pipeline, "transcribe", lambda path, inst: [(0, 0.5, 69, 0.8)])
    response = client.post(f'/api/jobs/{job["id"]}/transcribe', json={"instruments": ["piano"], "bpm": 120})
    assert response.status_code == 202
    job = finish(client, response.json())
    piano = next(stem for stem in job["stems"] if stem["id"] == "piano")
    assert piano["score_status"] == "ready"
    assert piano["note_count"] == 1
    assert client.get(piano["score_url"]).status_code == 200


def test_unconfigured_engine_refuses_real_inference_and_invalid_inputs(client, monkeypatch):
    monkeypatch.setattr(api, "engine_status", lambda: {"available": False, "issues": ["GPU not configured"]})
    assert client.post("/api/jobs", files={"file": ("test.mp3", b"audio")}).status_code == 503
    assert client.post("/api/jobs", files={"file": ("test.exe", b"not audio")}).status_code == 415
    assert client.post("/api/jobs", data={"url": "https://localhost/secret"}).status_code == 422
    assert client.post("/api/jobs").status_code == 422


def test_upload_body_limit_applies_before_form_parsing(client):
    response = client.post("/api/jobs", content=b"", headers={"Content-Length": "999999999"})
    assert response.status_code == 413


@pytest.mark.parametrize("with_audio", [True, False])
def test_mp4_audio_extraction_and_silent_video_rejection(tmp_path, with_audio):
    source, output = tmp_path / "input.mp4", tmp_path / "decoded.wav"
    command = ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=32x32:d=1"]
    if with_audio:
        command += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-shortest", "-c:a", "aac"]
    command += ["-c:v", "mpeg4", str(source)]
    subprocess.run(command, check=True, capture_output=True, timeout=20)
    if with_audio:
        duration = normalize(source, output)
        assert 0.9 < duration < 1.2
        samples, sr = sf.read(output)
        assert samples.ndim == 1 and sr == SAMPLE_RATE
        assert np.max(np.abs(samples)) > 0.01
    else:
        with pytest.raises(ValueError, match="오디오 트랙"):
            normalize(source, output)
