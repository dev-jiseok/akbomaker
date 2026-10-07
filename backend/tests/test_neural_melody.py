"""Fast contract tests use a fake predictor, not claims about neural quality."""
import numpy as np
import pytest
import soundfile as sf

from backend import neural_melody


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setattr(neural_melody, "checkpoint_provenance", lambda capacity: {"capacity": capacity, "checkpoint_sha256": "a" * 64})
    calls = []
    def predict(samples, **kwargs):
        calls.append(kwargs)
        size = 1 + len(samples) // neural_melody.HOP
        return np.full(size, 220.), np.full(size, .9)
    monkeypatch.setattr(neural_melody, "_predict", predict)
    return calls


def write_audio(tmp_path, duration=1., rate=16000, stereo=False, silent=False):
    t = np.arange(round(duration * rate)) / rate
    audio = np.zeros(len(t)) if silent else .1 * np.sin(2 * np.pi * 220 * t)
    if stereo:
        audio = np.stack((audio, -audio), axis=1)
    path = tmp_path / "audio.wav"
    sf.write(path, audio, rate, subtype="FLOAT")
    return path


@pytest.mark.parametrize("instrument", ["guitar", "piano", "synthesizer", "drums", "unknown"])
def test_rejects_polyphony_before_loading_audio(instrument):
    with pytest.raises(ValueError, match="monophonic"):
        neural_melody.transcribe_crepe("nonexistent.wav", instrument)


def test_silence_never_becomes_a_crepe_note(tmp_path, model):
    details = {}
    events = neural_melody.transcribe_crepe(write_audio(tmp_path, silent=True), "vocal", details=details)
    assert events == [] and model == []
    assert details["chunks"][0]["status"] == "silent"
    assert details["experimental"] and not details["periodicity_is_accuracy"]


@pytest.mark.parametrize("instrument,pitch_range", [("vocal", [36, 95]), ("bass", [24, 79])])
def test_ranges_and_original_source_provenance(tmp_path, model, instrument, pitch_range):
    path = write_audio(tmp_path, stereo=True)
    original_hash = neural_melody.sha256(path)
    details = {}
    notes = neural_melody.transcribe_crepe(path, instrument, details=details)
    assert notes and all(note[2] == 57 for note in notes)
    assert details["source_sha256"] == original_hash == neural_melody.sha256(path)
    assert details["channel_preprocessing"]["used_channel_fallback"]
    assert details["pitch_range"] == pitch_range
    assert model == [{"low": pitch_range[0], "high": pitch_range[1], "capacity": "full"}]


@pytest.mark.parametrize("bad", ["shape", "nonfinite", "negative_probability", "too_large_probability"])
def test_malformed_model_output_rejected(tmp_path, model, monkeypatch, bad):
    def predict(samples, **kwargs):
        size = 1 + len(samples) // neural_melody.HOP
        pitch, probability = np.full(size, 220.), np.ones(size)
        if bad == "shape":
            pitch = pitch[:-1]
        elif bad == "nonfinite":
            pitch[0] = np.nan
        elif bad == "negative_probability":
            probability[0] = -.1
        else:
            probability[0] = 1.1
        return pitch, probability
    monkeypatch.setattr(neural_melody, "_predict", predict)
    with pytest.raises(ValueError, match="Malformed"):
        neural_melody.transcribe_crepe(write_audio(tmp_path), "vocal")


def test_native_hop_and_chunk_boundary_do_not_duplicate_sustain(tmp_path, model, monkeypatch):
    import librosa
    monkeypatch.setattr(librosa.onset, "onset_detect", lambda **kwargs: np.array([]))
    details = {}
    events = neural_melody.transcribe_crepe(write_audio(tmp_path, duration=13.13, rate=22050), "vocal", details=details)
    assert len(model) == 2 and len(events) == 1
    assert events[0][0] == 0 and abs(events[0][1] - 13.13) < .001
    assert details["hop_seconds"] == .01
    assert details["chunks"][1]["context_start_seconds"] == 11.64


def test_low_periodicity_is_not_counted_as_a_note(tmp_path, model, monkeypatch):
    def predict(samples, **kwargs):
        size = 1 + len(samples) // neural_melody.HOP
        return np.full(size, 220.), np.full(size, .20)
    monkeypatch.setattr(neural_melody, "_predict", predict)
    assert neural_melody.transcribe_crepe(write_audio(tmp_path), "vocal") == []


def test_input_replacement_during_inference_refuses_result(tmp_path, model, monkeypatch):
    path = write_audio(tmp_path)
    original_predict = neural_melody._predict
    def replace_audio(samples, **kwargs):
        sf.write(path, np.zeros(16000), 16000, subtype="FLOAT")
        return original_predict(samples, **kwargs)
    monkeypatch.setattr(neural_melody, "_predict", replace_audio)
    details = {}
    with pytest.raises(RuntimeError, match="changed"):
        neural_melody.transcribe_crepe(path, "vocal", details=details)
    assert details == {}
