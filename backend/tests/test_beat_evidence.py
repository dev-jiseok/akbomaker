"""Fixed signal regressions, not claims about real-song beat accuracy."""
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from backend.analysis import analyze_beats
from backend.beat_evidence import pulse_grid_fit, tempo_review
from backend.tests.test_audio import client, finish


@pytest.mark.parametrize("bpm", [60, 99.5, 100, 137, 220])
def test_grid_fit_recovers_spacing_without_changing_pulse_phase_or_applying_it(bpm):
    actual = .23 + np.arange(80) * 60 / bpm
    times = np.round(actual / (256 / 22050)) * (256 / 22050)
    baseline = min(240, round(bpm) + 1)
    result = pulse_grid_fit(times, baseline)
    assert result["status"] == "stable_fit"
    assert abs(result["fitted_bpm"] - bpm) < .02
    assert abs(result["first_pulse_seconds"] - .23) < .003
    assert result["candidate_bpm"] in {round(bpm), int(bpm), int(bpm) + 1}
    assert result["downbeat_known"] is False
    assert result["automatically_applied"] is False
    assert abs(result["baseline_end_drift_seconds"]) > .09


@pytest.mark.parametrize("times", [np.array([.2, .8, 1.4]), np.delete(.2 + np.arange(40) * .6, 20),
                                  np.r_[.2 + np.arange(20) * .6, 12.2 + np.arange(20) * .5]])
def test_grid_does_not_offer_false_precision_for_short_missing_or_changing_beats(times):
    result = pulse_grid_fit(times, 100)
    assert result["candidate_bpm"] is None
    assert result["status"] == "review_required"


@pytest.mark.parametrize("times", [[0., 1., np.nan], [0., 1., np.inf], [1., .5, 2.], [0., 0., 1.], [], [[1, 2, 3]]])
def test_grid_rejects_invalid_times(times):
    with pytest.raises(ValueError):
        pulse_grid_fit(times, 100)


def onset_train(bpm, seconds=32, frame_seconds=.01):
    onset = np.zeros(round(seconds / frame_seconds))
    for time in np.arange(.2, seconds - .1, 60 / bpm):
        position = round(time / frame_seconds)
        onset[position] = 1.
        onset[position + 1] = .2
    return onset


def test_periodicity_preserves_half_double_ambiguity_instead_of_claiming_probability():
    result = tempo_review(onset_train(120), sr=100, hop=1, times=.2 + np.arange(60) * .5, bpm=120)
    assert result["metrics_are_confidence"] is False
    assert result["downbeat_known"] is False
    assert result["tempo_ambiguous"]
    assert any(abs(candidate["bpm"] - 120) < .1 for candidate in result["tempo_candidates"])
    assert any(abs(candidate["bpm"] - 60) < .1 for candidate in result["tempo_candidates"])
    assert result["local_tempo"]["status"] == "no_large_variation_detected"
    assert max(abs(item["bpm"] - 120) for item in result["local_tempo"]["segments"] if item["bpm"]) < .1


def test_local_onset_evidence_flags_tempo_change_even_when_tracker_returns_regular_grid():
    onset = np.r_[onset_train(90, 16), onset_train(120, 16)]
    result = tempo_review(onset, sr=100, hop=1, times=.2 + np.arange(50) * .6, bpm=100)
    assert result["local_tempo"]["status"] == "variation_requires_review"
    assert result["local_tempo"]["range_bpm"][0] == pytest.approx(90, abs=.3)
    assert result["local_tempo"]["range_bpm"][1] == pytest.approx(120, abs=.3)
    assert result["grid_fit"]["candidate_bpm"] is None
    assert any("패턴 차이" in warning for warning in result["warnings"])


@pytest.mark.parametrize("level", [0., .5])
def test_flat_envelope_cannot_create_periodicity_or_tempo_variation(level):
    result = tempo_review(np.full(3200, level), sr=100, hop=1, times=.2 + np.arange(50) * .6, bpm=100)
    assert result["tempo_candidates"] == []
    assert not result["tempo_ambiguous"]
    assert result["local_tempo"]["status"] == "insufficient_evidence"
    assert result["local_tempo"]["range_bpm"] is None
    assert result["grid_fit"]["candidate_bpm"] is None


@pytest.mark.parametrize("bad", [[], [0, np.nan, 0], [0, np.inf, 0], [[0, 1]]])
def test_invalid_envelope_is_rejected(bad):
    with pytest.raises(ValueError):
        tempo_review(bad, sr=100, hop=1, times=[0., .6, 1.2], bpm=100)


@pytest.mark.parametrize("sr,hop", [(0, 256), (22050, 0), (np.inf, 256), (22050, np.nan)])
def test_invalid_sample_spacing_is_rejected(sr, hop):
    with pytest.raises(ValueError):
        tempo_review(np.zeros(100), sr=sr, hop=hop, times=[0., .6, 1.2], bpm=100)


def test_evidence_is_bounded_on_three_minutes():
    result = tempo_review(onset_train(100, 180), sr=100, hop=1, times=.2 + np.arange(299) * .6, bpm=100)
    assert len(result["local_tempo"]["segments"]) <= 45
    assert len(result["tempo_candidates"]) <= 5


def write_clicks(path, bpm, seconds=24):
    sr = 22050
    samples = np.zeros(round(sr * seconds), dtype=np.float32)
    count = round(sr * .025)
    pulse = np.random.default_rng(40).normal(0, .4, count) * np.hanning(count)
    for time in np.arange(.2, seconds - .1, 60 / bpm):
        first = round(time * sr)
        samples[first:first + count] += pulse[:len(samples) - first]
    sf.write(path, samples, sr)
    return samples, sr


@pytest.mark.parametrize("bpm", [100, 137])
def test_actual_audio_analysis_preserves_legacy_output_and_provides_better_grid_candidate(tmp_path, bpm):
    import librosa
    path = tmp_path / "clicks.wav"
    write_clicks(path, bpm)
    samples, sr = librosa.load(path, sr=22050, mono=True, duration=180)
    envelope = librosa.onset.onset_strength(y=samples, sr=sr, hop_length=256)
    tempo, frames = librosa.beat.beat_track(onset_envelope=envelope, sr=sr, hop_length=256, trim=True)
    times = librosa.frames_to_time(frames, sr=sr, hop_length=256)
    result = analyze_beats(path)
    assert result["bpm"] == round(float(np.asarray(tempo).reshape(-1)[0]))
    assert result["first_beat_seconds"] == round(float(times[0]), 3)
    assert result["beat_times"] == [round(float(time), 3) for time in times]
    fit = result["evidence"]["grid_fit"]
    assert fit["candidate_bpm"] == bpm
    assert abs(fit["fitted_bpm"] - bpm) < .04
    assert abs(fit["fitted_bpm"] - bpm) < abs(result["bpm"] - bpm)


def test_analysis_rejects_nonfinite_audio_before_librosa_onsets(monkeypatch):
    import librosa
    monkeypatch.setattr(librosa, "load", lambda *args, **kwargs: (np.full(22050 * 4, np.nan), 22050))
    monkeypatch.setattr(librosa.onset, "onset_strength", lambda **kwargs: pytest.fail("Invalid audio reached the model"))
    with pytest.raises(ValueError, match="유효하지"):
        analyze_beats(Path("unused.wav"))


def test_live_analysis_route_persists_evidence_but_never_applies_tempo_or_edits_existing_scores(client, monkeypatch):
    from backend import analysis, store
    job = finish(client, client.post("/api/demo").json())
    folder = store.directory(job["id"])
    preserved = {path.name: path.read_bytes() for path in folder.iterdir() if path.name != "job.json"}
    result = {"bpm": 99, "first_beat_seconds": .12, "beat_times": [.12, .72, 1.32],
              "regularity": 1., "analyzed_seconds": 4., "alternatives": [50, 99, 198], "warning": "검토 필요",
              "evidence": tempo_review(onset_train(100), sr=100, hop=1,
                                       times=.2 + np.arange(50) * .6, bpm=99)}
    assert result["evidence"]["grid_fit"]["candidate_bpm"] == 100
    monkeypatch.setattr(analysis, "analyze_beats", lambda path: result)
    response = client.post(f'/api/jobs/{job["id"]}/analyze-beats')
    assert response.status_code == 202
    completed = finish(client, response.json())
    assert completed["rhythm_analysis"] == result
    assert completed["stems"] == job["stems"]
    assert preserved == {path.name: path.read_bytes() for path in folder.iterdir() if path.name != "job.json"}
