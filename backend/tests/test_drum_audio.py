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


def test_silence_and_stereo_cancellation_do_not_amplify_noise():
    for samples in [np.zeros(20), np.full(20, 1e-7), np.tile([.5, -.5], (20, 1))]:
        result, info = prepare_audio(samples)
        assert info["silent_input"] and info["gain"] == 1
        assert np.max(np.abs(result)) < 1e-5


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
