"""Adaptive route and evidence contracts; mocked inference is not accuracy data."""
import copy
import io
import json
import threading
import zipfile

import numpy as np
import pytest
import soundfile as sf

from backend import app as api, pipeline, store, transcription
from backend.pitched_review import octave_overlaps, write_pitched_review
from backend.tests.test_audio import client, finish
from backend.tests.test_note_artifacts import ready_project


def fake_transcriber(monkeypatch):
    calls = []
    monkeypatch.setattr(api, "engine_status", lambda: {"transcription_available": True})
    def transcribe(path, inst, *args, **kwargs):
        calls.append((inst, args, kwargs.get("engine", "standard")))
        events = [(.013, .7, 64, .8), (.013, .7, 76, .05)]
        if kwargs.get("engine") == "adaptive":
            description = transcription.engine_description(inst, engine="adaptive")
            description["pitched_postprocessing"] = write_pitched_review(kwargs["artifacts"], instrument=inst,
                source=path, duration=2, baseline=[events[0]], events=events,
                decoding={"evidence_is_confidence": False, "evidence": [{"pitch": 76, "onset_activation": .6}] * 500},
                description=description)
            description["pitched_review"] = True
            kwargs["details"].update(description)
        return events
    monkeypatch.setattr(pipeline, "transcribe", transcribe)
    return calls


@pytest.mark.parametrize("instrument", ["synthesizer"])
def test_adaptive_api_accepts_supported_instruments_and_preserves_review_json(client, monkeypatch, instrument):
    calls = fake_transcriber(monkeypatch)
    job = ready_project()
    endpoint = f'/api/jobs/{job["id"]}'
    response = client.post(endpoint + "/transcribe", json={"instruments": [instrument], "pitched_engine": "adaptive"})
    assert response.status_code == 202
    result = finish(client, response.json())
    stem = next(s for s in result["stems"] if s["id"] == instrument)
    assert stem["score_status"] == "ready"
    assert calls == [(instrument, ("instrument",), "adaptive")]
    info = stem["score_transcription"]
    assert info["pitched_review"] is True and info["engine"] == "basic-pitch-adaptive-v1"
    assert info["pitched_postprocessing"]["output_count"] == 2
    assert info["pitched_postprocessing"]["octave_overlap_count"] == 1
    assert "onset_activation" not in json.dumps(info) and len(json.dumps(info)) < 2500
    response = client.get(endpoint + f"/files/{instrument}.transcription.json")
    assert response.status_code == 200 and response.headers["content-type"] == "application/json"
    review = response.json()
    assert len(review["decoding"]["evidence"]) == 500
    assert review["semantics"]["activations_are_confidence"] is False
    assert review["semantics"]["octave_overlaps_are_errors"] is False
    assert review["semantics"]["reflects_manual_score_edits"] is False
    assert [e[2] for e in review["events"]] == [64, 76]  # Real weak octave is retained.
    assert str(store.DATA_DIR) not in response.text
    archive = zipfile.ZipFile(io.BytesIO(client.get(endpoint + "/archive").content))
    assert f"{instrument}.transcription.json" in archive.namelist()
    assert f"{instrument}.notes.json" in archive.namelist()


@pytest.mark.parametrize("instruments,engine", [
    (["vocal"], "adaptive"), (["bass"], "adaptive"), (["drums"], "adaptive"),
    (["guitar"], "adaptive"), (["piano"], "adaptive"), (["piano", "guitar"], "adaptive"),
    (["synthesizer", "piano"], "adaptive"), (["synthesizer", "guitar"], "adaptive"),
    (["piano", "vocal"], "adaptive"), (["guitar", "bass"], "adaptive"),
    (["synthesizer", "drums"], "adaptive"), (["piano"], "invalid"),
])
def test_invalid_adaptive_requests_do_not_reserve_or_mutate_job(client, monkeypatch, instruments, engine):
    job = ready_project()
    monkeypatch.setattr(api, "engine_status", lambda: pytest.fail("validation should precede engine access"))
    folder = store.directory(job["id"])
    before = (folder / "job.json").read_bytes()
    response = client.post(f'/api/jobs/{job["id"]}/transcribe', json={"instruments": instruments, "pitched_engine": engine})
    assert response.status_code == 422
    assert (folder / "job.json").read_bytes() == before
    assert job["id"] not in api.EVENTS


def test_switch_to_standard_hides_previous_review_without_deleting_evidence(client, monkeypatch):
    calls = fake_transcriber(monkeypatch)
    job = ready_project()
    endpoint = f'/api/jobs/{job["id"]}'
    finish(client, client.post(endpoint + "/transcribe", json={"instruments": ["synthesizer"], "pitched_engine": "adaptive"}).json())
    path = store.directory(job["id"]) / "synthesizer.transcription.json"
    before = path.read_bytes()
    result = finish(client, client.post(endpoint + "/transcribe", json={"instruments": ["synthesizer"]}).json())
    assert calls[-1] == ("synthesizer", (), "standard")  # Legacy two-argument fake stays compatible.
    info = next(s for s in result["stems"] if s["id"] == "synthesizer")["score_transcription"]
    assert info["engine"] == "basic-pitch-instrument-v2" and not info.get("pitched_review")
    assert path.read_bytes() == before
    assert client.get(endpoint + "/files/synthesizer.transcription.json").status_code == 404
    assert "synthesizer.transcription.json" not in zipfile.ZipFile(io.BytesIO(client.get(endpoint + "/archive").content)).namelist()


@pytest.mark.parametrize("instrument", ["guitar", "piano", "synthesizer"])
def test_previously_saved_adaptive_review_still_downloadable_for_all_three(client, monkeypatch, instrument):
    fake_transcriber(monkeypatch)
    job = ready_project()
    # Low-level research route stays available; public API is synth-only.
    pipeline.run_transcription(job["id"], threading.Event(), [instrument], 120, pitched_engine="adaptive")
    endpoint = f'/api/jobs/{job["id"]}'
    assert client.get(endpoint + f"/files/{instrument}.transcription.json").status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(client.get(endpoint + "/archive").content))
    assert f"{instrument}.transcription.json" in archive.namelist()


@pytest.mark.parametrize("availability", [None, False, 1, "true"])
def test_review_download_and_zip_require_strict_boolean_flag(client, availability):
    job = ready_project()
    (store.directory(job["id"]) / "piano.transcription.json").write_text('{"old": true}')
    pipeline.stem_update(job["id"], "piano", score_transcription={"pitched_review": availability})
    endpoint = f'/api/jobs/{job["id"]}'
    assert client.get(endpoint + "/files/piano.transcription.json").status_code == 404
    assert "piano.transcription.json" not in zipfile.ZipFile(io.BytesIO(client.get(endpoint + "/archive").content)).namelist()


@pytest.mark.parametrize("stage", ["inference", "notation"])
def test_failed_adaptive_run_preserves_prior_score_and_review(client, monkeypatch, stage):
    fake_transcriber(monkeypatch)
    job = ready_project()
    endpoint = f'/api/jobs/{job["id"]}'
    pipeline.run_transcription(job["id"], threading.Event(), ["piano"], 120, pitched_engine="adaptive")
    folder = store.directory(job["id"])
    before = {p.name: p.read_bytes() for p in folder.glob("piano.*")}
    if stage == "inference":
        def fail(*args, **kwargs):
            (kwargs["artifacts"] / "piano.transcription.json").write_text('{"partial": true}')
            raise ValueError("mock inference failure")
        monkeypatch.setattr(pipeline, "transcribe", fail)
    else:
        original = pipeline.generate_score
        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise ValueError("mock staged notation failure")
        monkeypatch.setattr(pipeline, "generate_score", fail)
    pipeline.run_transcription(job["id"], threading.Event(), ["piano"], 120, pitched_engine="adaptive")
    assert before == {p.name: p.read_bytes() for p in folder.glob("piano.*")}
    finished = store.get(job["id"])
    assert "피아노 채보에 실패했어요" in finished["message"]
    assert "기존 악보가 있다면 보존" in finished["message"]
    assert "채보가 끝났어요" not in finished["message"]
    assert client.get(endpoint + "/files/piano.transcription.json").status_code == 200
    assert not list(folder.glob("transcription-*"))


def test_standard_engine_descriptions_and_monophonic_routing_stay_unchanged(tmp_path, monkeypatch):
    for inst in ("guitar", "piano", "synthesizer"):
        assert transcription.engine_description(inst)["engine"] == "basic-pitch-instrument-v2"
    for inst in ("vocal", "bass"):
        assert transcription.engine_description(inst)["engine"] == "pyin-monophonic-v1"
        path = tmp_path / f"{inst}.wav"
        sf.write(path, np.full(22050, .02), 22050)
        expected = [(0, .5, 64, .5)]
        monkeypatch.setattr(transcription, "melody_events", lambda p, i: expected)
        assert transcription.transcribe_instrument(path, inst, lambda: pytest.fail("mono route loaded polyphonic model")) == expected


def test_adaptive_runtime_uses_one_model_output_isolated_baseline_and_compact_summary(tmp_path, monkeypatch):
    import basic_pitch.inference as inference
    import basic_pitch.note_creation as notes
    import backend.pitched_decoder as decoder
    path = tmp_path / "guitar.wav"
    sf.write(path, np.full(22050, .02), 22050)
    output = {"note": np.full((88, 88), .5), "onset": np.full((88, 88), .7), "contour": np.zeros((88, 264))}
    model, calls = object(), []
    def infer(source, actual_model):
        calls.append(source)
        assert actual_model is model
        return output
    def baseline(copied, **kwargs):
        copied["note"][:] = 0  # Upstream decoder is allowed to mutate its copy.
        return None, [(0, .5, 64, .7, None)]
    def adaptive(raw, **kwargs):
        assert raw is output and np.all(raw["note"] == .5)
        assert kwargs["min_pitch"] == 28 and kwargs["max_pitch"] == 103
        return [(0, .5, 64, .7), (0, .5, 76, .05)], {"evidence": [{"pitch": 76, "onset_activation": .6}] * 500}
    monkeypatch.setattr(inference, "run_inference", infer)
    monkeypatch.setattr(notes, "model_output_to_notes", baseline)
    monkeypatch.setattr(decoder, "decode_pitched", adaptive)
    details = {}
    events = transcription.transcribe_instrument(path, "guitar", lambda: model, engine="adaptive", details=details, artifacts=tmp_path)
    assert len(calls) == 1 and [e[2] for e in events] == [64, 76]
    assert details["pitched_review"] and details["pitched_postprocessing"]["baseline_count"] == 1
    assert "evidence" not in json.dumps(details) and len(json.dumps(details)) < 2500
    assert len(json.loads((tmp_path / "guitar.transcription.json").read_text())["decoding"]["evidence"]) == 500


def test_adaptive_silence_does_not_load_model_and_records_empty_review(tmp_path):
    path = tmp_path / "piano.wav"
    sf.write(path, np.zeros(22050), 22050)
    details = {}
    assert transcription.transcribe_instrument(path, "piano", lambda: pytest.fail("silence loaded model"),
                                               engine="adaptive", details=details, artifacts=tmp_path) == []
    review = json.loads((tmp_path / "piano.transcription.json").read_text())
    assert review["decoding"]["silent_input"] and review["events"] == review["baseline_events"] == []
    assert details["pitched_review"] is True


@pytest.mark.parametrize("engine", ["standard", "adaptive"])
@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_audio_fails_before_model_instead_of_returning_blank_score(tmp_path, monkeypatch, engine, invalid):
    # Invalid input must not take the RMS silence path and look successful.
    monkeypatch.setattr(transcription.sf, "read", lambda *args, **kwargs: (np.array([.1, invalid, .1], dtype=np.float32), 22050))
    details = {}
    with pytest.raises(ValueError, match="유효하지 않은 샘플"):
        transcription.transcribe_instrument(tmp_path / "synthesizer.wav", "synthesizer",
            lambda: pytest.fail("nonfinite audio loaded model"), engine=engine, details=details, artifacts=tmp_path)
    assert details == {}
    assert not (tmp_path / "synthesizer.transcription.json").exists()


def test_octave_review_preserves_real_weak_chords_and_original_unsorted_indices():
    events = [(1.04, 1.7, 76, .01), (3., 3.8, 64, .6), (1., 1.8, 64, .6), (1.02, 1.9, 52, .5), (1.03, 1.9, 88, .4)]
    before = copy.deepcopy(events)
    pairs, truncated = octave_overlaps(events)
    assert not truncated and events == before
    assert {tuple((p["lower_event_index"], p["upper_event_index"])) for p in pairs} == {(2, 0), (3, 0), (3, 2), (0, 4), (2, 4)}
    for pair in pairs:
        assert events[pair["lower_event_index"]][2] == pair["lower_pitch"]
        assert events[pair["upper_event_index"]][2] == pair["upper_pitch"]
    clipped, truncated = octave_overlaps(events, maximum=1)
    assert len(clipped) == 1 and truncated and clipped == pairs[:1]
    assert events == before
    assert octave_overlaps([(0, .1, 64, .6), (.081, .5, 76, .3)])[0] == []


@pytest.mark.parametrize("bad_location", ["events", "baseline", "decoding", "duration"])
def test_review_rejects_nonfinite_json_without_overwriting_previous_file(tmp_path, bad_location):
    source = tmp_path / "guitar.wav"
    source.write_bytes(b"audio")
    path = tmp_path / "guitar.transcription.json"
    path.write_text('{"previous": true}')
    args = dict(instrument="guitar", source=source, duration=1, baseline=[(0, .5, 64, .6)],
                events=[(0, .5, 64, .6)], decoding={}, description={})
    if bad_location in {"events", "baseline"}:
        args[bad_location] = [(0, .5, 64, float("nan"))]
    elif bad_location == "decoding":
        args["decoding"] = {"activation": float("inf")}
    else:
        args["duration"] = float("nan")
    with pytest.raises(ValueError):
        write_pitched_review(tmp_path, **args)
    assert path.read_text() == '{"previous": true}'


def test_review_size_and_note_count_bounds_preserve_old_file(tmp_path, monkeypatch):
    from backend import pitched_review
    source = tmp_path / "guitar.wav"
    source.write_bytes(b"audio")
    target = tmp_path / "guitar.transcription.json"
    target.write_text('{"previous": true}')
    args = dict(instrument="guitar", source=source, duration=1, baseline=[], events=[], decoding={}, description={})
    monkeypatch.setattr(pitched_review, "MAX_REVIEW_BYTES", 1)
    with pytest.raises(ValueError, match="크기"):
        write_pitched_review(tmp_path, **args)
    assert target.read_text() == '{"previous": true}'
    args["events"] = [(0, .5, 64, .6)] * 30_001
    with pytest.raises(ValueError, match="음표 수"):
        write_pitched_review(tmp_path, **args)
    assert target.read_text() == '{"previous": true}'
