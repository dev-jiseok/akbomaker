"""Default route integration; mocked models are not accuracy measurements."""
import json

import numpy as np
import pytest
import soundfile as sf

from backend import drum_worker, transcription
from backend.note_artifacts import write_note_artifact
from backend.tests.test_drum_evidence import SR, fixture_audio, add_hat


@pytest.mark.parametrize("engine", ["auto", "neural", "hybrid", "consensus"])
def test_default_neural_postprocess_removes_only_unsupported_kick_and_preserves_raw(tmp_path, monkeypatch, engine):
    audio, kicks = fixture_audio()
    audio += .001 * np.sin(2 * np.pi * 65 * np.arange(len(audio)) / SR)
    add_hat(audio, 3.9)
    source = tmp_path / "drums.wav"
    sf.write(source, audio, SR, subtype="FLOAT")
    source_bytes = source.read_bytes()
    false_kick, hat = (3.9, 4., 36, .2), (3.9, 4., 42, .4)
    original = [*kicks, false_kick, hat]
    monkeypatch.setenv("AKBO_DRUM_WORKER", "explicit-test-worker")
    monkeypatch.setattr(drum_worker, "worker_status", lambda: {"paths_ready": True})
    monkeypatch.setattr(transcription, "multiband_drums", lambda path: [])

    def infer(path, duration, *, artifacts, details, **options):
        details.update({"review": {"unsupported_count": 0, "simplified_counts": {}},
                        "context_passes": options.get("context_passes", 1)})
        (artifacts / "drums.raw.mid").write_bytes(b"original-model-midi")
        (artifacts / "drums.transcription.json").write_text(json.dumps({"events": original}))
        return list(original)

    monkeypatch.setattr(drum_worker, "transcribe_external", infer)
    events, details = transcription.transcribe_drums(source, 8, engine=engine, artifacts=tmp_path)
    assert events == sorted([*kicks, hat]) if engine == "hybrid" else events == [*kicks, hat]
    assert details["kick_evidence"]["rejected_count"] == 1
    assert "candidates" not in details["kick_evidence"]
    assert details["postprocessing"] == "conservative-kick-evidence-v1"
    assert "킥 후보 1개" in details["warning"]
    assert source.read_bytes() == source_bytes
    assert (tmp_path / "drums.raw.mid").read_bytes() == b"original-model-midi"
    raw = json.loads((tmp_path / "drums.transcription.json").read_text())
    assert raw["events"] == [list(e) for e in original]
    assert raw["score_events"] == [list(e) for e in events]
    assert raw["kick_evidence"]["relabeled_count"] == raw["kick_evidence"]["added_count"] == 0
    write_note_artifact(tmp_path, events, instrument="drums", duration=8, source=source, description=details)
    notes = json.loads((tmp_path / "drums.notes.json").read_text())
    assert notes["provenance"]["postprocessing"] == details["postprocessing"]
    assert notes["events"] == raw["score_events"]


def test_drum_direct_route_does_not_bypass_postprocessing(tmp_path, monkeypatch):
    source = tmp_path / "drums.wav"
    sf.write(source, np.full(16000, .1), 16000)
    calls = []

    def run(path, duration, *, artifacts):
        calls.append((path, duration, artifacts))
        return [(0., .1, 42, .4)], {"postprocessing": "conservative-kick-evidence-v1"}

    monkeypatch.setattr(transcription, "transcribe_drums", run)
    details = {}
    assert transcription.transcribe_instrument(source, "drums", lambda: pytest.fail("no pitched model"),
                                               details=details, artifacts=tmp_path) == [(0., .1, 42, .4)]
    assert calls == [(source, 1., tmp_path)]
    assert details["postprocessing"] == "conservative-kick-evidence-v1"


@pytest.mark.parametrize("recovery,warning", [
    ({"replaced_chunks": 1, "unresolved_chunks": 0}, "정확도 보증이 아니며"),
    ({"replaced_chunks": 0, "unresolved_chunks": 1}, "불완전한 구간이 남아"),
])
def test_experimental_retry_state_is_visible_not_reported_as_accuracy(tmp_path, monkeypatch, recovery, warning):
    source = tmp_path / "drums.wav"
    sf.write(source, np.zeros(16000), 16000)
    monkeypatch.setattr(drum_worker, "worker_status", lambda: {"paths_ready": True})
    def infer(path, duration, *, details, **kwargs):
        details.update(review={"unsupported_count": 0, "simplified_counts": {}}, recovery_review=recovery)
        return []
    monkeypatch.setattr(drum_worker, "transcribe_external", infer)
    _, details = transcription.transcribe_drums(source, 1., engine="neural")
    assert warning in details["warning"]


@pytest.mark.parametrize("instrument", ["bass", "vocal"])
def test_default_melody_route_filters_release_peak_before_segmentation(tmp_path, monkeypatch, instrument):
    import librosa
    source = tmp_path / f"{instrument}.wav"
    audio = np.r_[np.full(11025, .2), np.zeros(11025)]
    sf.write(source, audio, 22050, subtype="FLOAT")
    monkeypatch.setattr(librosa, "pyin", lambda *a, **k: (np.full(60, transcription.hz(43)), np.ones(60, bool), np.ones(60)))
    monkeypatch.setattr(librosa.feature, "rms", lambda *a, **k: np.full((1, 60), .2))
    monkeypatch.setattr(librosa.onset, "onset_detect", lambda *a, **k: np.array([.5]))
    events = transcription.melody_events(source, instrument)
    assert len(events) == 1
    assert events[0][2] == 43
    assert events[0][1] == pytest.approx(.5, abs=1 / 22050)
    details = transcription.engine_description(instrument)
    assert details["postprocessing"] == "waveform-supported-boundaries-v1"
