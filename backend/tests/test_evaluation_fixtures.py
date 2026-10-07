"""Synthetic diagnostic contracts, without loading any inference model."""
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from backend.config import SAMPLE_RATE
from backend.evaluation_fixtures import DURATION, INSTRUMENTS, challenge_events, generate_challenges, render_challenge

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def cli():
    spec = importlib.util.spec_from_file_location("evaluate_transcription_fixture_tests", ROOT / "scripts/evaluate-transcription.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REFERENCE_HASHES = {
    # Captured from the original 2026-10-06 diagnostic, before promotion to
    # this module. Changes require a new fixture version, never adjusted truth.
    "vocal": "0e34938351ad6e6eff1882e69da48a7827da3dc11a47966a77b5ff9512afb302",
    "bass": "8e4762dc090ae1184cc7dda02300ccefebfe250fa696c9d932264eea08753e3b",
    "guitar": "54f7b6fbca01f0823ea1a6214c1f1fcd89f3f6ed0eb1d5894e33ee170c7e21d5",
    "piano": "e4bebe66d622e15f763b95a7b033d6a26146dd0d023efc6b13d011f2be38a013",
    "synthesizer": "10a3fb408f1ec1b55c0c4df98cdbbd786055248b268c339a2634cc424f5cfeca",
}


@pytest.mark.parametrize("instrument,count,polyphony", [
    ("vocal", 8, 1), ("bass", 8, 1), ("guitar", 24, 6), ("piano", 24, 6), ("synthesizer", 12, 4),
])
def test_exact_finite_references_bounds_and_polyphony(instrument, count, polyphony):
    events = challenge_events()[instrument]
    assert DURATION == 8.
    assert len(events) == count
    assert np.isfinite(np.asarray(events)).all()
    assert all(0 <= start < end <= DURATION and isinstance(pitch, int) and 0 <= pitch <= 127 and 0 < amp <= 1
               for start, end, pitch, amp in events)
    digest = hashlib.sha256(json.dumps(events, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    assert digest == REFERENCE_HASHES[instrument]
    boundaries = sorted([(start, 1) for start, *_ in events] + [(end, -1) for _, end, *_ in events])
    active = maximum = 0
    for _, delta in boundaries:
        active += delta
        maximum = max(maximum, active)
    assert active == 0 and maximum == polyphony


@pytest.mark.parametrize("instrument", INSTRUMENTS)
def test_audio_is_deterministic_finite_unclipped_and_exact_duration(instrument):
    events = challenge_events()[instrument]
    first = render_challenge(events, instrument)
    second = render_challenge(events, instrument)
    assert first.dtype == np.float32
    assert first.shape == (round(8 * SAMPLE_RATE),)
    assert np.isfinite(first).all()
    assert np.array_equal(first, second)
    assert 0 < np.max(np.abs(first)) < 1
    assert not np.any(first[:round(.5 * SAMPLE_RATE)])
    assert not np.any(first[-round(.5 * SAMPLE_RATE):])


def test_generate_returns_fresh_independent_notes_and_audio():
    first, events = generate_challenges()
    second, other_events = generate_challenges()
    assert tuple(first) == tuple(events) == INSTRUMENTS
    assert "drums" not in first
    for inst in INSTRUMENTS:
        assert np.array_equal(first[inst], second[inst])
        assert events[inst] == other_events[inst]
    events["vocal"].clear()
    first["vocal"].fill(1)
    assert len(other_events["vocal"]) == 8
    assert not np.array_equal(first["vocal"], second["vocal"])


def test_unknown_fixture_instrument_is_rejected():
    with pytest.raises(ValueError, match="Unsupported"):
        render_challenge([], "drums")


@pytest.mark.parametrize("flags", [
    [], ["--demo-seconds", "2"], ["--demo-seconds", "nan"],
    ["--synthetic-challenges", "--demo-seconds", "8"],
    ["--synthetic-challenges", "--audio", "unused.wav"],
    ["--synthetic-challenges", "--reference", "unused.json"],
    ["--synthetic-challenges", "--instrument", "piano"],
    ["--demo-seconds", "8", "--audio", "unused.wav"],
    ["--audio", "unused.wav", "--reference", "unused.json"],
    ["--instrument", "piano"],
])
def test_invalid_cli_combinations_fail_before_models_or_output(cli, flags, tmp_path, monkeypatch):
    def no_model(*args, **kwargs):
        pytest.fail("Invalid CLI arguments must not invoke a model")
    monkeypatch.setattr(cli, "transcribe", no_model)
    output = tmp_path / "not-created"
    with pytest.raises(SystemExit) as error:
        cli.main([*flags, "--output", str(output)])
    assert error.value.code == 2
    assert not output.exists()


@pytest.mark.parametrize("flags", [
    ["--synthetic-challenges"], ["--demo-seconds", "8"],
    ["--audio", "input.wav", "--reference", "truth.json", "--instrument", "bass"],
])
def test_valid_existing_and_new_cli_modes_parse_without_files(cli, flags, tmp_path):
    args = cli.parse_args([*flags, "--output", str(tmp_path / "output")])
    assert args.output == tmp_path / "output"


def test_challenge_cli_preserves_raw_contract_and_writes_provenance_without_models(cli, tmp_path, monkeypatch):
    reference = challenge_events()
    calls = []
    def fake_transcribe(path, instrument):
        calls.append(instrument)
        assert sf.info(path).duration == 8.
        # This is a wiring test using explicit truth, NOT an accuracy result.
        return reference[instrument]
    monkeypatch.setattr(cli, "transcribe", fake_transcribe)
    cli.main(["--synthetic-challenges", "--output", str(tmp_path)])
    report = json.loads((tmp_path / "evaluation.json").read_text())
    assert tuple(calls) == INSTRUMENTS
    assert report["synthetic"] is True
    assert report["fixture_id"] == "pitched-challenges-v1"
    assert report["duration"] == 8.
    assert "not real-song accuracy" in report["dataset"]
    assert any("not real-song accuracy" in text for text in report["limitations"])
    assert "backend/evaluation_fixtures.py" in report["provenance"]["source_sha256"]
    for relative, digest in report["provenance"]["source_sha256"].items():
        assert digest == hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
    for instrument, result in report["instruments"].items():
        assert result["current"]["f1"] == 1.
        assert result["current"]["possible_confusions"] == []
        assert result["current"]["confusion_diagnostics"]["heuristic_only"] is True
        assert set(result["diagnostics_by_tolerance_ms"]) == {"100", "200"}
        assert result["reference_events_sha256"] == REFERENCE_HASHES[instrument]
        assert result["input_audio_sha256"] == hashlib.sha256((tmp_path / f"input-{instrument}.wav").read_bytes()).hexdigest()
        decoded, _ = sf.read(tmp_path / f"input-{instrument}.wav", dtype="float32", always_2d=True)
        assert result["input_pcm_sha256"] == hashlib.sha256(decoded.astype("<f4", copy=False).tobytes()).hexdigest()
        assert result["input_sample_rate"] == SAMPLE_RATE
        assert result["input_channels"] == 1
        assert set(result["artifacts_sha256"]) == {f"{instrument}.{suffix}" for suffix in
                                                   ("events.json", "score.json", "auto.json", "musicxml", "mid")}
        for name, digest in result["artifacts_sha256"].items():
            assert digest == hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
        raw = json.loads((tmp_path / f"{instrument}.events.json").read_text())
        assert raw == {"reference": [list(event) for event in reference[instrument]],
                       "estimated": [list(event) for event in reference[instrument]]}


def test_external_mode_still_works_and_hashes_reference_file(cli, tmp_path, monkeypatch):
    audio, reference_path, output = tmp_path / "input.wav", tmp_path / "truth.json", tmp_path / "out"
    sf.write(audio, np.zeros(SAMPLE_RATE, dtype=np.float32), SAMPLE_RATE)
    reference_path.write_text(json.dumps({"bpm": 120, "events": []}))
    monkeypatch.setattr(cli, "transcribe", lambda *args: [])
    cli.main(["--audio", str(audio), "--reference", str(reference_path), "--instrument", "vocal", "--output", str(output)])
    report = json.loads((output / "evaluation.json").read_text())
    assert report["synthetic"] is False
    assert report["dataset"] == str(reference_path)
    assert report["duration"] == 1
    assert set(report["instruments"]) == {"vocal"}
    assert report["provenance"]["reference_file_sha256"] == hashlib.sha256(reference_path.read_bytes()).hexdigest()


def test_existing_demo_and_legacy_comparison_still_cover_all_six_routes(cli, tmp_path, monkeypatch):
    calls, legacy_calls = [], []
    def no_notes(path, instrument):
        calls.append(instrument)
        return []
    def no_legacy_notes(path, instrument):
        legacy_calls.append(instrument)
        return []
    monkeypatch.setattr(cli, "transcribe", no_notes)
    monkeypatch.setattr(cli, "legacy", no_legacy_notes)
    cli.main(["--demo-seconds", "3", "--compare-legacy", "--output", str(tmp_path)])
    report = json.loads((tmp_path / "evaluation.json").read_text())
    assert calls == legacy_calls == list(cli.INSTRUMENTS)
    assert report["dataset"] == "self-authored-synthetic; not real-song accuracy"
    assert report["duration"] == 3 and report["bpm"] == cli.demo.BPM
    assert report["synthetic"] is True
    assert "fixture_id" not in report
    for inst, result in report["instruments"].items():
        assert result["legacy"]["f1"] == result["current"]["f1"] == 0
        assert sf.info(tmp_path / f"input-{inst}.wav").duration == 3
