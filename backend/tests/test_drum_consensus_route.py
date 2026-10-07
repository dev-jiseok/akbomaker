"""Route/worker contract tests; mocked inference is not an accuracy benchmark."""
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
import numpy as np
import soundfile as sf

from backend import drum_evidence, drum_worker, transcription, store
from backend.tests.test_audio import client, finish
from backend.tests.test_drum_transcription import midi_file


@pytest.fixture
def worker_environment(tmp_path, monkeypatch):
    sf.write(tmp_path / "drums.wav", np.zeros(48000), 48000)
    source, model = tmp_path / "source", tmp_path / "model"
    source.mkdir()
    model.mkdir()
    (source / "adt_transcriber.py").write_text("# test worker source")
    for name in ("adt_config.yaml", "config.json", "model.safetensors"):
        (model / name).write_text("test")
    for key, value in {"AKBO_DRUM_WORKER": sys.executable, "AKBO_DRUM_SOURCE_DIR": str(source),
                       "AKBO_DRUM_MODEL_DIR": str(model)}.items():
        monkeypatch.setenv(key, value)
    for key in ("AKBO_DRUM_NORMALIZATION", "AKBO_DRUM_DECODING", "AKBO_DRUM_CONTEXT_PASSES"):
        monkeypatch.delenv(key, raising=False)
    assert drum_worker.worker_status()["paths_ready"]


def mock_worker(monkeypatch):
    calls = []
    candidates = [
        {"event": [0, .1, 42, .7], "accepted": True, "votes": 3, "pass_ids": [0, 1, 2]},
        {"event": [.5, .6, 36, .6], "accepted": False, "votes": 1, "pass_ids": [0]},
    ]
    pass_events = [[[0, .1, 42, .7], [.5, .6, 36, .6]], [[0, .1, 42, .7]], [[0, .1, 42, .7]]]

    def run(command, **kwargs):
        options = {flag: command[command.index(flag) + 1]
                   for flag in ("--context-passes", "--normalization", "--decoding")}
        calls.append({**options, **kwargs})
        output = Path(command[command.index("--output") + 1])
        # The accepted output is a hi-hat, never the rejected kick candidate.
        midi_file(output, [42])
        count = int(options["--context-passes"])
        metadata = {"context_passes": count, "decoder": options["--decoding"],
                    "conditioning": {"normalization": options["--normalization"], "silent_input": False}}
        if count == 3:
            metadata["consensus"] = {"method": "shifted-context-consensus-v1", "passes": 3,
                                      "minimum_votes": 2, "tolerance_seconds": .05,
                                      "accepted_count": 1, "rejected_count": 1,
                                      "agreement_is_confidence": False,
                                      "candidates": candidates, "pass_events": pass_events}
        output.with_suffix(".json").write_text(json.dumps(metadata))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(drum_worker.subprocess, "run", run)
    return calls, candidates, pass_events


def no_multiband(*args, **kwargs):
    pytest.fail("Consensus/neural must not add spectral candidates or relabel output")


def test_consensus_route_pins_three_peak_greedy_passes_and_keeps_rejected_evidence(worker_environment, tmp_path, monkeypatch):
    monkeypatch.setenv("AKBO_DRUM_NORMALIZATION", "none")
    monkeypatch.setenv("AKBO_DRUM_DECODING", "constrained")
    monkeypatch.setenv("AKBO_DRUM_CONTEXT_PASSES", "1")
    monkeypatch.setattr(transcription, "multiband_drums", no_multiband)
    calls, candidates, pass_events = mock_worker(monkeypatch)
    artifacts = tmp_path / "artifacts"
    events, info = transcription.transcribe_drums(tmp_path / "drums.wav", 1,
                                                  engine="consensus", artifacts=artifacts)
    assert len(calls) == 1
    assert calls[0]["--normalization"] == "peak"
    assert calls[0]["--decoding"] == "greedy"
    assert calls[0]["--context-passes"] == "3" and calls[0]["timeout"] == 1800
    assert [event[2] for event in events] == [42]  # No conversion into kick.
    assert len(events) == 1  # No manufactured beat-pattern additions.
    assert info["engine"] == "adt-str-consensus-v1"
    assert "약한 타격도 빠질 수" in info["warning"] and "정확도 보증이 아닙니다" in info["warning"]
    assert info["consensus"]["accepted_count"] == info["consensus"]["rejected_count"] == 1
    assert info["consensus"]["agreement_is_confidence"] is False
    assert "candidates" not in info["consensus"] and "pass_events" not in info["consensus"]
    raw = json.loads((artifacts / "drums.transcription.json").read_text())
    assert raw["runtime"]["consensus"]["candidates"] == candidates
    assert raw["runtime"]["consensus"]["pass_events"] == pass_events
    assert raw["kick_evidence"]["method"] == drum_evidence.METHOD
    assert raw["score_events"] == [list(e) for e in events]
    assert [event[2] for event in raw["events"]] == [42]
    assert [event[2] for event in drum_worker.read_drum_midi(artifacts / "drums.raw.mid", 1, raw=True)] == [42]


@pytest.mark.parametrize("engine", ["auto", "neural"])
def test_existing_neural_routes_remain_one_pass_without_environment_override(worker_environment, tmp_path, monkeypatch, engine):
    calls, _, _ = mock_worker(monkeypatch)
    monkeypatch.setattr(transcription, "multiband_drums", no_multiband)
    events, info = transcription.transcribe_drums(tmp_path / "drums.wav", 1, engine=engine)
    assert calls[0]["--context-passes"] == "1" and calls[0]["timeout"] == 600
    assert calls[0]["--normalization"] == "none" and calls[0]["--decoding"] == "greedy"
    assert info["engine"] == "adt-str" and info["context_passes"] == 1
    assert [event[2] for event in events] == [42]


def test_hybrid_route_keeps_one_pass_even_when_global_consensus_is_configured(worker_environment, tmp_path, monkeypatch):
    monkeypatch.setenv("AKBO_DRUM_CONTEXT_PASSES", "3")
    calls, _, _ = mock_worker(monkeypatch)
    monkeypatch.setattr(transcription, "multiband_drums", lambda path: [])
    events, info = transcription.transcribe_drums(tmp_path / "drums.wav", 1, engine="hybrid")
    assert calls[0]["--context-passes"] == "1" and calls[0]["timeout"] == 600
    assert info["engine"] == "adt-str-hybrid-v1"
    assert [event[2] for event in events] == [42]


def test_guarded_retry_is_explicit_and_consensus_keeps_existing_default(worker_environment, tmp_path, monkeypatch):
    monkeypatch.setenv("AKBO_DRUM_DECODING", "guarded")
    calls, _, _ = mock_worker(monkeypatch)
    transcription.transcribe_drums(tmp_path / "drums.wav", 1, engine="neural")
    transcription.transcribe_drums(tmp_path / "drums.wav", 1, engine="consensus")
    assert [call["--decoding"] for call in calls] == ["guarded", "greedy"]


@pytest.mark.parametrize("passes", [0, "0", 2, -1, True, 1.0, "3.0", "", "../../3"])
def test_worker_rejects_invalid_context_passes_before_subprocess(worker_environment, tmp_path, monkeypatch, passes):
    # Explicit zero must not fall through to a valid environment default.
    monkeypatch.setenv("AKBO_DRUM_CONTEXT_PASSES", "3")
    monkeypatch.setattr(drum_worker.subprocess, "run", lambda *a, **k: pytest.fail("invalid setting ran a process"))
    with pytest.raises(ValueError, match="설정"):
        drum_worker.transcribe_external(tmp_path / "drums.wav", 1, context_passes=passes)


def test_worker_rejects_invalid_environment_context_default(worker_environment, tmp_path, monkeypatch):
    monkeypatch.setenv("AKBO_DRUM_CONTEXT_PASSES", "0")
    monkeypatch.setattr(drum_worker.subprocess, "run", lambda *a, **k: pytest.fail("invalid default ran a process"))
    with pytest.raises(ValueError, match="설정"):
        drum_worker.transcribe_external(tmp_path / "drums.wav", 1)


def test_large_candidate_arrays_stay_in_download_not_project_summary(worker_environment, tmp_path, monkeypatch):
    _, candidates, pass_events = mock_worker(monkeypatch)
    candidates.extend({"event": [i / 10, i / 10 + .1, 36, .5], "accepted": False,
                       "votes": 1, "pass_ids": [0]} for i in range(1, 5001))
    pass_events[0].extend(candidate["event"] for candidate in candidates[2:])
    info = {}
    artifacts = tmp_path / "artifacts"
    drum_worker.transcribe_external(tmp_path / "drums.wav", 600, context_passes=3, artifacts=artifacts, details=info)
    assert len(json.dumps(info)) < 2000
    assert "candidates" not in info["consensus"] and "pass_events" not in info["consensus"]
    raw = json.loads((artifacts / "drums.transcription.json").read_text())
    assert len(raw["runtime"]["consensus"]["candidates"]) == 5002
    assert len(raw["runtime"]["consensus"]["pass_events"][0]) == 5002


@pytest.mark.parametrize("passes,timeout,minutes", [(None, 600, "10분"), (3, 1800, "30분")])
def test_worker_timeout_tracks_pass_budget_without_leaking_subprocess_details(worker_environment, tmp_path, monkeypatch, passes, timeout, minutes):
    def run(command, **kwargs):
        assert kwargs["timeout"] == timeout
        raise subprocess.TimeoutExpired("/private/secret/worker", timeout, stderr="private token")
    monkeypatch.setattr(drum_worker.subprocess, "run", run)
    with pytest.raises(ValueError, match=minutes) as error:
        drum_worker.transcribe_external(tmp_path / "drums.wav", 1, context_passes=passes)
    assert "private" not in str(error.value) and "token" not in str(error.value)


def test_consensus_api_accepts_request_persists_safe_summary_and_downloadable_candidates(client, worker_environment, monkeypatch):
    calls, candidates, pass_events = mock_worker(monkeypatch)
    monkeypatch.setattr(transcription, "multiband_drums", no_multiband)
    job = finish(client, client.post("/api/demo").json())
    store.update(job["id"], demo=False)
    endpoint = f'/api/jobs/{job["id"]}'
    response = client.post(endpoint + "/transcribe", json={"instruments": ["drums"], "drum_engine": "consensus"})
    assert response.status_code == 202
    result = finish(client, response.json())
    stem = next(s for s in result["stems"] if s["id"] == "drums")
    assert stem["score_status"] == "ready"
    assert calls[0]["--context-passes"] == "3"
    info = stem["score_transcription"]
    assert info["engine"] == "adt-str-consensus-v1" and info["raw_midi"] and info["raw_events"]
    assert "candidates" not in info["consensus"] and "pass_events" not in info["consensus"]
    raw = client.get(endpoint + "/files/drums.transcription.json").json()
    assert raw["runtime"]["consensus"]["candidates"] == candidates
    assert raw["runtime"]["consensus"]["pass_events"] == pass_events
    artifact = client.get(endpoint + "/files/drums.notes.json").json()
    assert [event[2] for event in artifact["events"]] == [42]
    assert artifact["provenance"]["engine"] == "adt-str-consensus-v1"
