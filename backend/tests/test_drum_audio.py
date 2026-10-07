import numpy as np
import pytest

from backend.drum_audio import prepare_audio


def test_peak_scaling_preserves_relative_weak_hits_and_entire_timeline():
    audio = np.array([0, .1, -.2, .05], dtype=np.float32)
    result, info = prepare_audio(audio)
    np.testing.assert_allclose(result, audio * 5)
    assert info["gain"] == pytest.approx(5)
    assert not info["silent_input"]
    unscaled, info = prepare_audio(audio, normalization="none")
    np.testing.assert_array_equal(unscaled, audio)
    assert info["gain"] == 1


def test_silence_and_inaudible_stereo_do_not_amplify_noise():
    for samples in [np.zeros(20), np.full(20, 1e-7), np.tile([1e-7, -1e-7], (20, 1))]:
        result, info = prepare_audio(samples)
        assert info["silent_input"] and info["gain"] == 1
        assert np.max(np.abs(result)) < 1e-5


@pytest.mark.parametrize("normalization", ["none", "peak"])
@pytest.mark.parametrize("right_gain", [-1., -.95])
def test_opposite_polarity_preserves_real_audio_instead_of_returning_silence(normalization, right_gain):
    mono = (.2 * np.sin(np.arange(4000) / 5)).astype(np.float32)
    expected, _ = prepare_audio(mono, normalization=normalization)
    actual, info = prepare_audio(np.column_stack([mono, right_gain * mono]), normalization=normalization)
    np.testing.assert_array_equal(actual, expected)
    assert not info["silent_input"]
    assert info["channel_preprocessing"]["used_channel_fallback"]
    assert info["channel_preprocessing"]["selected_channel"] == 0
    assert info["channel_preprocessing"]["input_channels"] == 2


@pytest.mark.parametrize("normalization", ["none", "peak"])
@pytest.mark.parametrize("channels", [1, 2, 6])
def test_ordinary_audio_retains_original_float64_downmix_bit_for_bit(normalization, channels):
    audio = np.random.default_rng(17).uniform(-.3, .3, (5000, channels)).astype(np.float32)
    mono = audio.mean(axis=1, dtype=np.float64).astype(np.float32)
    expected, _ = prepare_audio(mono, normalization=normalization)
    actual, info = prepare_audio(audio, normalization=normalization)
    np.testing.assert_array_equal(actual, expected)
    assert not info["channel_preprocessing"]["used_channel_fallback"]


def test_cancellation_uses_louder_original_channel_without_per_block_switching():
    mono = (.3 * np.sin(np.arange(3000) / 6)).astype(np.float32)
    result, info = prepare_audio(np.column_stack([-.95 * mono, mono]), normalization="none")
    np.testing.assert_array_equal(result, mono)
    assert info["channel_preprocessing"]["selected_channel"] == 1


def test_gain_is_bounded_and_overrange_is_attenuated():
    assert prepare_audio(np.full(20, 1e-4))[1]["gain"] == 10
    assert prepare_audio(np.array([-2, 1]))[1]["gain"] == .5


@pytest.mark.parametrize("samples", [[], [float("nan")], [float("inf")], np.zeros((2, 2, 2))])
def test_invalid_audio_rejected(samples):
    with pytest.raises(ValueError, match="nonempty and finite"):
        prepare_audio(samples)


def test_unknown_normalization_rejected():
    with pytest.raises(ValueError, match="normalization"):
        prepare_audio([1], normalization="magic")


@pytest.mark.parametrize("sample_rate", [22050, 44100])
def test_spectral_drum_route_preserves_opposite_polarity_hits_and_reports_it(tmp_path, sample_rate):
    import soundfile as sf
    from backend.transcription import multiband_drums, transcribe_drums

    audio = np.zeros(sample_rate * 2, dtype=np.float32)
    for start in (.1, .4, .7, 1., 1.3, 1.6):
        time = np.arange(int(.07 * sample_rate)) / sample_rate
        # Labeled synthetic attacks test signal preservation, not the quality
        # of real-world kick/snare/hat classification.
        hit = .4 * np.sin(2 * np.pi * 70 * time) * np.exp(-time * 60)
        index = round(start * sample_rate)
        audio[index:index + len(hit)] += hit.astype(np.float32)
    reference, opposite = tmp_path / "mono.wav", tmp_path / "opposite.wav"
    sf.write(reference, audio, sample_rate, subtype="FLOAT")
    sf.write(opposite, np.column_stack([audio, -audio]), sample_rate, subtype="FLOAT")
    expected = multiband_drums(reference)
    actual, info = transcribe_drums(opposite, 2, engine="spectral")
    assert expected and actual == expected
    assert info["channel_preprocessing"]["used_channel_fallback"]
    assert "상쇄" in info["warning"]


@pytest.mark.parametrize("sample_rate", [22050, 44100])
def test_spectral_ordinary_stereo_matches_previous_librosa_downmix_exactly(tmp_path, sample_rate):
    import librosa
    import soundfile as sf
    from backend.transcription import multiband_drums

    audio = np.random.default_rng(29).normal(0, .01, (sample_rate * 2, 2)).astype(np.float32)
    audio[::sample_rate // 4] += .4
    original, previous = tmp_path / "stereo.wav", tmp_path / "old-mono.wav"
    sf.write(original, audio, sample_rate, subtype="FLOAT")
    old_mono, sr = librosa.load(original, sr=22050, mono=True)
    sf.write(previous, old_mono, sr, subtype="FLOAT")
    details = {}
    assert multiband_drums(original, details=details) == multiband_drums(previous)
    assert not details["channel_preprocessing"]["used_channel_fallback"]
