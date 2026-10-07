import pytest
import json
import numpy as np
import soundfile as sf

from backend.drum_fusion import CYMBALS, recover_hihats


@pytest.mark.parametrize("pitch", sorted(CYMBALS))
def test_nearby_cymbal_blocks_added_closed_hat(pitch):
    neural = [(1, 1.1, pitch, .7)]
    result, review = recover_hihats(neural, [(1.05, 1.15, 42, .4)])
    assert result == neural and review["added_count"] == 0


def test_preserves_all_neural_notes_and_adds_only_hats_without_grid_snap():
    neural = [(0, .1, 36, .7), (.301, .4, 56, .1)]
    spectral = [(0, .1, 42, .4), (.18, .28, 38, .6), (.301, .401, 42, .6)]
    result, review = recover_hihats(neural, spectral)
    assert all(e in result for e in neural)
    assert len(result) == 4
    assert [e[0] for e in review["added_events"]] == [0, .301]
    assert review["candidate_count"] == review["added_count"] == 2


def test_candidate_duplicates_do_not_inflate_hit_count_or_mutate_inputs():
    candidate = (1., 1.1, 42, .5)
    spectral = [candidate, candidate, (1.04, 1.14, 42, .5), (1.1, 1.2, 42, .5)]
    result, review = recover_hihats([], spectral)
    assert len(spectral) == 4
    assert len(result) == review["added_count"] == 2
    assert recover_hihats([], [])[0] == []


@pytest.mark.parametrize("silent", [False, True])
def test_hybrid_records_additions_separately_from_original_model(tmp_path, monkeypatch, silent):
    sf.write(tmp_path / "audio.wav", np.zeros(48000), 48000)
    from backend import transcription, drum_worker
    model_events = [] if silent else [(0, .1, 36, .8)]
    options = []
    monkeypatch.setattr(drum_worker, "worker_status", lambda: {"paths_ready": True})
    def worker(path, duration, *, artifacts, details, **kwargs):
        options.append(kwargs)
        details.update({"review": {"raw_note_count": len(model_events), "unsupported_count": 0, "simplified_counts": {}},
                        "conditioning": {"silent_input": silent},
                        "sampling_review": {"budget_limited_chunks": 1}})
        (artifacts / "drums.transcription.json").write_text(json.dumps({"events": model_events}))
        (artifacts / "drums.raw.mid").write_bytes(b"unchanged-model")
        return model_events
    monkeypatch.setattr(drum_worker, "transcribe_external", worker)
    monkeypatch.setattr(transcription, "multiband_drums", lambda p: [(0, .1, 42, .5)])
    events, details = transcription.transcribe_drums(tmp_path / "audio.wav", 1, engine="hybrid", artifacts=tmp_path)
    assert options == [{"normalization": "peak", "decoding": "greedy", "context_passes": 1}]
    assert len(events) == (0 if silent else 2) and details["engine"] == "adt-str-hybrid-v1"
    assert details["recovery"]["added_count"] == (0 if silent else 1) and "출력 한도" in details["warning"]
    report = json.loads((tmp_path / "drums.transcription.json").read_text())
    assert len(report["events"]) == len(model_events) and len(report["score_events"]) == len(events)
    if not silent:
        assert report["recovery"]["added_events"][0][2] == 42
    assert (tmp_path / "drums.raw.mid").read_bytes() == b"unchanged-model"
