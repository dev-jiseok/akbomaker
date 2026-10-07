import hashlib
import io
import json
import threading
import zipfile

import numpy as np
import pytest

from backend import app as api, pipeline, store
from backend.config import INSTRUMENTS
from backend.note_artifacts import safe_provenance, write_note_artifact
from backend.tests.test_audio import client, finish


def ready_project():
    job = store.create("Traceable notes", "upload")
    folder = store.directory(job["id"])
    for inst in (*INSTRUMENTS, "original"):
        # All inference in these integration tests is mocked, not audio quality.
        (folder / f"{inst}.wav").write_bytes(f"test source for {inst}".encode())
    return store.update(job["id"], duration=2, status="separated",
                        stems=[{**stem, "status": "ready"} for stem in job["stems"]])


def fake_inference(monkeypatch):
    pitches = {"vocal": 69, "bass": 40, "guitar": 64, "piano": 60, "synthesizer": 67}
    monkeypatch.setattr(api, "engine_status", lambda: {"transcription_available": True})
    monkeypatch.setattr(pipeline, "transcribe", lambda path, inst, *args, **kwargs: [(.013, .437, pitches[inst], .8)])
    monkeypatch.setattr(pipeline, "transcribe_drums", lambda *args, **kwargs: (
        [(.013, .113, 42, .7)], {"engine": "multiband-onsets-v2", "profile": "instrument", "warning": "test"}))


@pytest.mark.parametrize("inst", INSTRUMENTS)
def test_each_instrument_downloads_pre_quantization_source_linked_events(client, monkeypatch, inst):
    fake_inference(monkeypatch)
    job = ready_project()
    endpoint = f'/api/jobs/{job["id"]}'
    response = client.post(endpoint + "/transcribe", json={"instruments": [inst], "bpm": 120, "drum_engine": "spectral"})
    assert response.status_code == 202
    result = finish(client, response.json())
    stem = next(s for s in result["stems"] if s["id"] == inst)
    assert stem["score_status"] == "ready" and stem["score_transcription"]["raw_events"] is True
    response = client.get(endpoint + f"/files/{inst}.notes.json")
    assert response.status_code == 200 and response.headers["content-type"] == "application/json"
    artifact = response.json()
    assert artifact["schema_version"] == 1 and artifact["instrument"] == inst
    assert artifact["events"][0][0] == .013  # Not quantized to tick zero.
    assert artifact["source"]["sha256"] == hashlib.sha256(f"test source for {inst}".encode()).hexdigest()
    assert artifact["source"]["file"] == f"{inst}.wav" and artifact["source"]["kind"] == "stem"
    assert artifact["semantics"]["confidence_available"] is False
    assert "not a calibrated probability" in artifact["semantics"]["amplitude"]
    assert not artifact["semantics"]["reflects_manual_score_edits"]
    assert str(store.DATA_DIR) not in response.text
    assert artifact["provenance"]["engine"] == stem["score_transcription"]["engine"]
    document = client.get(endpoint + f"/scores/{inst}").json()
    assert document["notes"][0]["start"] == 0
    bundle = zipfile.ZipFile(io.BytesIO(client.get(endpoint + "/archive").content))
    assert f"{inst}.notes.json" in bundle.namelist()
    assert b"NOT confidence" in bundle.read("README.txt")


def test_original_drum_events_use_original_hash_before_score_offset(client, monkeypatch):
    fake_inference(monkeypatch)
    job = ready_project()
    endpoint = f'/api/jobs/{job["id"]}'
    result = client.post(endpoint + "/transcribe", json={"instruments": ["drums"], "drum_engine": "spectral",
                                                        "drum_source": "original", "audio_offset": .25})
    assert result.status_code == 202
    finish(client, result.json())
    artifact = client.get(endpoint + "/files/drums.notes.json").json()
    assert artifact["source"] == {"kind": "original", "file": "original.wav", "instrument": None,
                                   "sha256": hashlib.sha256(b"test source for original").hexdigest()}
    assert artifact["event_count"] == 1 and artifact["events"][0][0] == .013
    assert client.get(endpoint + "/scores/drums").json()["notes"] == []


@pytest.mark.parametrize("stage", ["inference", "invalid_events", "notation", "cancel"])
def test_failed_retranscription_never_replaces_previous_score_or_note_evidence(client, monkeypatch, stage):
    fake_inference(monkeypatch)
    job = ready_project()
    endpoint = f'/api/jobs/{job["id"]}'
    finish(client, client.post(endpoint + "/transcribe", json={"instruments": ["bass"]}).json())
    folder = store.directory(job["id"])
    before = {p.name: p.read_bytes() for p in folder.glob("bass.*")}
    event = threading.Event()
    def fail(*args, **kwargs):
        raise ValueError("test failure")
    if stage == "inference":
        monkeypatch.setattr(pipeline, "transcribe", fail)
    elif stage == "invalid_events":
        monkeypatch.setattr(pipeline, "transcribe", lambda *args, **kwargs: [(float("nan"), .1, 40, .8)])
    else:
        generate = pipeline.generate_score
        def changed(*args, **kwargs):
            document = generate(*args, **kwargs)
            if stage == "notation":
                raise ValueError("test failure after staged score creation")
            event.set()
            return document
        monkeypatch.setattr(pipeline, "generate_score", changed)
    pipeline.run_transcription(job["id"], event, ["bass"], 120)
    assert before == {p.name: p.read_bytes() for p in folder.glob("bass.*")}
    assert client.get(endpoint + "/files/bass.notes.json").status_code == 200
    assert not list(folder.glob("transcription-*"))


@pytest.mark.parametrize("metadata", [None, {}, {"engine": "imported"}, {"raw_events": False}, {"raw_events": "true"}])
def test_old_evidence_is_never_served_without_current_availability_flag(client, metadata):
    job = ready_project()
    folder = store.directory(job["id"])
    (folder / "bass.notes.json").write_text('{"old": true}')
    pipeline.stem_update(job["id"], "bass", score_transcription=metadata)
    endpoint = f'/api/jobs/{job["id"]}'
    assert client.get(endpoint + "/files/bass.notes.json").status_code == 404
    assert "bass.notes.json" not in zipfile.ZipFile(io.BytesIO(client.get(endpoint + "/archive").content)).namelist()


def test_demo_is_known_composition_not_inference_or_raw_audit(client):
    job = finish(client, client.post("/api/demo").json())
    for stem in job["stems"]:
        assert stem["score_transcription"]["engine"] == "known-composition"
        assert stem["score_transcription"]["raw_events"] is False
        assert client.get(f'/api/jobs/{job["id"]}/files/{stem["id"]}.notes.json').status_code == 404


@pytest.mark.parametrize("event", [
    (0, .1, 60, float("nan")), (0, float("inf"), 60, .5), (0, .1, 60.5, .5),
    (-.1, .1, 60, .5), (0, 2.1, 60, .5), (0, .1, 128, .5), (0, .1, -1, .5),
    (0, .1, 60, 1.1), (0, .1, 60, 0), (0, 0, 60, .5), (False, .1, 60, .5),
    (0, .1, 60), (0, .1, 60, ".5"),
])
def test_invalid_events_are_rejected_not_silently_dropped(tmp_path, event):
    source = tmp_path / "bass.wav"
    source.write_bytes(b"audio")
    with pytest.raises(ValueError):
        write_note_artifact(tmp_path, [event], instrument="bass", duration=2, source=source, description={})
    assert not (tmp_path / "bass.notes.json").exists()


def test_artifact_limits_events_and_size_and_keeps_numpy_scalars_json_safe(tmp_path, monkeypatch):
    from backend import note_artifacts
    source = tmp_path / "bass.wav"
    source.write_bytes(b"audio")
    events = [(np.float64(0), np.float32(.5), np.int64(40), np.float32(.5))]
    cleaned = write_note_artifact(tmp_path, events, instrument="bass", duration=2, source=source, description={})
    assert cleaned == [[0., .5, 40, .5]]
    before = (tmp_path / "bass.notes.json").read_bytes()
    monkeypatch.setattr(note_artifacts, "MAX_EVENTS", 1)
    with pytest.raises(ValueError, match="제한"):
        write_note_artifact(tmp_path, iter(events * 2), instrument="bass", duration=2, source=source, description={})
    assert (tmp_path / "bass.notes.json").read_bytes() == before
    monkeypatch.setattr(note_artifacts, "MAX_ARTIFACT_BYTES", 1)
    with pytest.raises(ValueError, match="크기"):
        write_note_artifact(tmp_path, events, instrument="bass", duration=2, source=source, description={})
    assert (tmp_path / "bass.notes.json").read_bytes() == before


def test_provenance_excludes_paths_unknown_metadata_and_unbounded_strings():
    info = safe_provenance({"engine": "adt-str", "profile": "instrument", "device": "/private/token",
                            "secret": "do not publish", "decoder": "x" * 101,
                            "model": {"repo": "owner/model", "revision": "a" * 40, "directory": "/private/model",
                                      "source_repo": "/private/source", "sha256": {"model.safetensors": "b" * 64,
                                                                                 "/private/key": "c" * 64}}})
    assert info["engine"] == "adt-str" and info["model"]["repo"] == "owner/model"
    assert info["model"]["sha256"] == {"model.safetensors": "b" * 64}
    text = json.dumps(info)
    assert "/private" not in text and "secret" not in text and "decoder" not in info
