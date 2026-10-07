import numpy as np
import pytest

from backend.melody_decoding import supported_reattacks, trim_release_tails
from backend.transcription import clean_events, hz, melody_segments


def test_release_spectral_peak_cannot_use_sound_before_the_onset():
    samples = np.r_[np.full(500, .2), np.zeros(500)]
    # The tracker remains voiced beyond the physical .5 s release.
    frequency = np.full(60, hz(43))
    original = melody_segments(frequency, np.ones(60, bool), np.ones(60),
                               np.full(60, .2), .01, 1, onsets=[.5])
    assert len(original) == 2
    supported = supported_reattacks([.5], samples, 1000, silence_floor=.0016)
    corrected = melody_segments(frequency, np.ones(60, bool), np.ones(60),
                                np.full(60, .2), .01, 1, onsets=supported)
    assert len(corrected) == 1
    assert corrected[0][:3] == (0, .6, 43)  # Do not claim offset correction.


def test_genuine_repeated_note_is_kept_even_when_much_quieter():
    samples = np.r_[np.full(470, .5), np.zeros(30), np.full(500, .02)]
    assert supported_reattacks([0, .5], samples, 1000, silence_floor=.004).tolist() == [0, .5]


def test_repeated_unison_and_real_silence_are_not_joined():
    samples = np.r_[np.full(400, .2), np.zeros(200), np.full(400, .2)]
    frequency = np.full(100, hz(60))
    voiced = np.ones(100, bool)
    voiced[40:60] = False
    onsets = supported_reattacks([.2, .4, .6, .8], samples, 1000)
    events = melody_segments(frequency, voiced, np.ones(100), np.full(100, .2),
                             .01, 1, onsets=onsets)
    assert [(round(a, 2), round(b, 2), pitch) for a, b, pitch, _ in events] == [
        (0, .2, 60), (.2, .4, 60), (.6, .8, 60), (.8, 1, 60)]


@pytest.mark.parametrize("pitch", [23, 28, 40, 60, 96])
def test_support_window_keeps_low_bass_cycles_and_high_vocal(pitch):
    sr = 22050
    time = np.arange(round(sr * .07)) / sr
    samples = np.sin(2 * np.pi * hz(pitch) * time) * .1
    assert supported_reattacks([0], samples, sr).tolist() == [0]


def test_partial_file_tail_keeps_real_support_but_never_invents_a_tail():
    assert supported_reattacks([0, .07, .1, .2], np.full(100, .1), 1000).tolist() == [0]
    assert supported_reattacks([.06], np.full(100, .1), 1000).tolist() == [.06]
    assert supported_reattacks([0], np.zeros(100), 1000).tolist() == []
    assert supported_reattacks([0], np.zeros(0), 1000).tolist() == []


def test_silence_floor_matches_segmentation_and_does_not_require_energy_rise():
    samples = np.linspace(.5, .1, 1000)
    assert supported_reattacks([.2, .4], samples, 1000).tolist() == [.2, .4]
    assert supported_reattacks([0], np.full(100, 1e-6), 1000).tolist() == []
    assert supported_reattacks([0], np.full(100, .001), 1000, silence_floor=.002).tolist() == []


@pytest.mark.parametrize("samples,sr,kwargs", [
    (np.zeros((2, 2)), 1000, {}), (np.array([float('nan')]), 1000, {}),
    (np.zeros(100), 0, {}), (np.zeros(100), True, {}),
    (np.zeros(100), 1000, {"silence_floor": -1}),
    (np.zeros(100), 1000, {"minimum_note_seconds": 0}),
])
def test_rejects_invalid_waveform_or_parameters(samples, sr, kwargs):
    with pytest.raises(ValueError):
        supported_reattacks([0], samples, sr, **kwargs)


def test_input_arrays_are_immutable_and_nonfinite_onsets_are_ignored():
    samples = np.full(100, .2)
    onsets = np.array([-.1, 0, float('nan'), float('inf')])
    before = samples.copy()
    assert supported_reattacks(onsets, samples, 1000).tolist() == [0]
    np.testing.assert_array_equal(samples, before)
    assert len(onsets) == 4 and np.isnan(onsets[2])


def test_rejected_release_trims_original_note_instead_of_creating_a_repeat():
    samples = np.r_[np.full(493, .2), np.zeros(507)]
    events = [(0, .58, 40, .8)]
    assert supported_reattacks([.5], samples, 1000).tolist() == []
    assert trim_release_tails(events, [.5], samples, 1000) == [(0, .493, 40, .8)]
    assert events == [(0, .58, 40, .8)]


@pytest.mark.parametrize("pitch,release", [(23, .313), (41, .537), (76, .719)])
def test_release_boundary_uses_real_waveform_end_and_preserves_pitch(pitch, release):
    sr = 22050
    samples = np.zeros(sr)
    t = np.arange(round(release * sr)) / sr
    samples[:len(t)] = np.sin(2 * np.pi * hz(pitch) * t) * .1
    result = trim_release_tails([(0, release + .08, pitch, .7)], [release + .005], samples, sr)
    assert abs(result[0][1] - release) < .001
    assert result[0][2:] == (pitch, .7)


def test_quiet_real_repeat_or_new_pitch_with_support_is_not_trimmed():
    samples = np.r_[np.full(470, .5), np.zeros(30), np.full(500, .005)]
    events = [(0, .7, 40, .8), (.7, 1, 43, .2)]
    assert trim_release_tails(events, [.5], samples, 1000, silence_floor=.004) == events


def test_missing_eof_context_is_not_silence_evidence():
    samples = np.r_[np.full(945, .2), np.zeros(55)]
    events = [(0, 1, 60, .8)]
    assert trim_release_tails(events, [.945, .99, 1, 2], samples, 1000) == events


def test_release_without_nearby_audible_sample_is_uncertain_and_not_trimmed():
    samples = np.r_[np.full(200, .2), np.zeros(800)]
    events = [(0, .8, 40, .8)]
    assert trim_release_tails(events, [.5], samples, 1000) == events


def test_release_never_extends_or_changes_nonoverlapping_notes():
    samples = np.r_[np.full(500, .2), np.zeros(500)]
    events = [(0, .3, 40, .8), (.7, .9, 43, .5)]
    assert trim_release_tails(events, [.5], samples, 1000) == events


@pytest.mark.parametrize("start", [0.463, 1.463, 12.463, 599.463])
def test_offset_fix_does_not_drop_short_recognized_note_in_cleaner(start):
    sr = 1000
    samples = np.zeros(round((start + .2) * sr))
    samples[round(start * sr):round((start + .017) * sr)] = .2
    events = [(start, start + .09, 52, .5)]
    result = trim_release_tails(events, [start + .02], samples, sr)
    assert len(clean_events(result, len(samples) / sr)) == 1
    assert .04 <= result[0][1] - start < .041


def test_trim_ignores_nonfinite_release_candidates_and_preserves_extra_fields():
    samples = np.r_[np.full(500, .2), np.zeros(500)]
    events = [(0, .58, 40, .8, "evidence")]
    result = trim_release_tails(events, [float('nan'), float('inf'), -.2, .5], samples, 1000)
    assert result == [(0, .5, 40, .8, "evidence")]


@pytest.mark.parametrize("kwargs", [{"silence_floor": -1}, {"minimum_note_seconds": 0},
                                     {"minimum_output_seconds": 0}])
def test_trim_rejects_invalid_parameters(kwargs):
    with pytest.raises(ValueError):
        trim_release_tails([], [], np.zeros(100), 1000, **kwargs)
