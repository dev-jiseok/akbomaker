"""Opt-in real CPU/GPU ADT inference, with no network downloads or SAM changes."""
import json
import os

import numpy as np
import pytest
import soundfile as sf

from backend import demo
from backend.config import SAMPLE_RATE
from backend.drum_mapping import DRUM_PITCHES
from backend.drum_worker import read_drum_midi, worker_status
from backend.transcription import transcribe_drums

pytestmark = pytest.mark.skipif(os.getenv("RUN_ADT_MODEL_TESTS") != "1", reason="Explicit local ADT checkpoint/environment required")


@pytest.mark.parametrize("silent", [False, True])
@pytest.mark.parametrize("engine", ["neural", "hybrid", "consensus"])
def test_real_drum_inference_has_valid_mapping_raw_midi_and_provenance(tmp_path, silent, engine):
    assert worker_status()["paths_ready"], "Configure AKBO_DRUM_* before this opt-in test"
    path = tmp_path / "test.wav"
    audio = np.zeros(3 * SAMPLE_RATE) if silent else demo.generate()[0]["drums"][:3 * SAMPLE_RATE]
    sf.write(path, audio, SAMPLE_RATE)
    events, details = transcribe_drums(path, 3., engine=engine, artifacts=tmp_path)
    assert (not events) if silent else events
    assert all(np.isfinite(e).all() and 0 <= e[0] < e[1] <= 3 and e[2] in DRUM_PITCHES for e in events)
    assert details["mapping"] == "adt-custom-26-to-gm-v1"
    assert details["decoder"] == "strict-triples-v1"
    assert details["model"]["revision"] == "a33c5c6b191a4ca1e0f6dc22140947485eb36ce8"
    assert read_drum_midi(tmp_path / "drums.raw.mid", 3, raw=True) or silent
    report = json.loads((tmp_path / "drums.transcription.json").read_text())
    assert report["runtime"]["silent_input"] == silent
    if engine == "hybrid":
        assert report["runtime"]["conditioning"]["normalization"] == "peak"
        assert report["score_events"] == [list(e) for e in events]
        if silent:
            assert report["recovery"]["added_count"] == 0
    if engine == "consensus":
        assert details["engine"] == "adt-str-consensus-v1"
        assert report["runtime"]["context_passes"] == 3
        assert len(report["runtime"]["consensus"]["pass_events"]) == 3
        assert report["runtime"]["consensus"]["agreement_is_confidence"] is False
        assert all(c["votes"] >= 2 for c in report["runtime"]["consensus"]["candidates"] if c["accepted"])
        assert "recovery" not in report  # No heuristic hi-hat additions.
    # Execution contract only: no assertion that a synthetic snare was recognized.
