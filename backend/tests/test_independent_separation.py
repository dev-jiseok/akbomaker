"""Production routing tests; injected model outputs are not SAM quality evidence."""
import io
import threading
import zipfile
from unittest.mock import Mock

import numpy as np
import pytest
import soundfile as sf

from backend import app as api, pipeline, store
from backend.config import INSTRUMENTS
from backend.separator import Cancelled, separate_independent
from backend.tests.test_audio import client, finish


def audio_upload():
    stream = io.BytesIO()
    sf.write(stream, np.ones(48000 * 3, dtype=np.float32) * .1, 48000, format="WAV", subtype="FLOAT")
    return {"file": ("source.wav", stream.getvalue(), "audio/wav")}


def test_each_independent_request_is_original_and_emitter_cannot_change_future_inputs():
    original = np.linspace(-.4, .4, 100, dtype=np.float32)
    before = original.copy()
    calls, outputs, progress = [], [], []

    def extract(request, inst, event, report):
        assert np.array_equal(request, before)
        assert not np.shares_memory(request, original)
        calls.append(inst)
        report(.5)
        return request  # Even an output alias must not escape into later requests.

    def emit(inst, target, index, fraction):
        progress.append((inst, index, fraction))
        if target is not None:
            outputs.append(target.copy())
            target[:] = 100

    assert separate_independent(original, extract, emit, threading.Event()) is None
    assert calls == list(INSTRUMENTS)
    assert len(outputs) == 6 and all(np.array_equal(output, before) for output in outputs)
    assert np.array_equal(original, before)
    assert sum(p[2] == 1 for p in progress) == 6


@pytest.mark.parametrize("result", [None, np.zeros(99), np.full(100, np.nan)])
def test_invalid_independent_output_rejected_without_emit(result):
    with pytest.raises(ValueError):
        separate_independent(np.ones(100), lambda *_: result,
                             lambda *_: pytest.fail("Invalid output must not be emitted"), threading.Event())


def test_independent_input_mutation_rejected_and_original_preserved():
    original = np.ones(100)
    def extract(request, *_):
        request[:] = 0
        return request
    with pytest.raises(ValueError, match="입력"):
        separate_independent(original, extract, lambda *_: None, threading.Event())
    assert np.all(original == 1)


def test_cancelled_independent_does_not_publish_incomplete_output():
    event = threading.Event()
    def extract(request, *_):
        event.set()
        return request
    with pytest.raises(Cancelled):
        separate_independent(np.ones(100), extract,
                             lambda *_: pytest.fail("Cancelled output must not be emitted"), event)


@pytest.mark.parametrize("strategy", ["sequential", "independent"])
def test_upload_strategy_propagates_to_all_six_parts_and_archive(client, monkeypatch, strategy):
    monkeypatch.setattr(api, "engine_status", lambda: {"available": True, "issues": []})
    engine = Mock()
    inputs = []
    def extract(request, *_):
        inputs.append(request.copy())
        return request * .5
    engine.extract.side_effect = extract
    monkeypatch.setattr(pipeline, "ENGINE", engine)
    response = client.post("/api/jobs", data={"separation_strategy": strategy}, files=audio_upload())
    assert response.status_code == 202, response.text
    job = finish(client, response.json())
    assert job["status"] == "separated", job
    assert job["separation_strategy"] == strategy
    assert job["separation"]["accuracy_evaluated"] is False
    assert len(inputs) == 6 and len({stem["audio_url"] for stem in job["stems"]}) == 6
    independent = strategy == "independent"
    for index, samples in enumerate(inputs):
        np.testing.assert_array_equal(samples, inputs[0] if independent else inputs[0] * .5 ** index)
    assert job["separation"]["stems_may_overlap"] is independent
    assert job["separation"]["additive_residual"] is not independent
    assert bool(job["residual_url"]) is not independent
    assert (store.directory(job["id"]) / "residual.wav").exists() is not independent
    engine.offload.assert_called_once()
    response = client.get(f'/api/jobs/{job["id"]}/archive')
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
        note = bundle.read("README.txt").decode()
        assert ("independent source separation" in note) is independent
        assert ("residual.wav" in bundle.namelist()) is not independent


def test_invalid_separation_selection_creates_no_project(client):
    before = set(store.DATA_DIR.iterdir())
    for data in ({"separation_strategy": "best"}, {"separation_strategy": "independent", "analysis_only": "true"}):
        result = client.post("/api/jobs", data=data, files=audio_upload())
        assert result.status_code == 422
    assert set(store.DATA_DIR.iterdir()) == before


def test_analysis_only_and_legacy_upload_keep_sequential_default(client, monkeypatch):
    monkeypatch.setattr(api, "engine_status", lambda: {"available": False, "issues": []})
    monkeypatch.setattr(pipeline.ENGINE, "extract", lambda *_: pytest.fail("Analysis-only must not separate"))
    response = client.post("/api/jobs", data={"analysis_only": "true"}, files=audio_upload())
    job = finish(client, response.json())
    assert job["separation_strategy"] == "sequential" and "separation" not in job
    assert not job["residual_url"]


@pytest.mark.parametrize("cancel", [True, False])
def test_independent_failure_retains_finished_stem_and_offloads(client, monkeypatch, cancel):
    monkeypatch.setattr(api, "engine_status", lambda: {"available": True, "issues": []})
    engine = Mock()
    def extract(request, inst, event, progress):
        if inst == "bass":
            if cancel:
                event.set()
                return request
            raise RuntimeError("Test model failure")
        return request * .1
    engine.extract.side_effect = extract
    monkeypatch.setattr(pipeline, "ENGINE", engine)
    response = client.post("/api/jobs", data={"separation_strategy": "independent"}, files=audio_upload())
    job = finish(client, response.json())
    assert job["status"] == ("cancelled" if cancel else "error")
    assert job["stems"][0]["status"] == "ready"
    assert client.get(job["stems"][0]["audio_url"]).status_code == 200
    assert all(not stem.get("audio_url") for stem in job["stems"][1:])
    assert engine.extract.call_count == 2
    assert not job["residual_url"]
    engine.offload.assert_called_once()
