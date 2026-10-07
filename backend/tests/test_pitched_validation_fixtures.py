"""Frozen validation specification tests; no predictions or model imports."""
import hashlib
import json

import numpy as np
import pytest

from backend.pitched_validation_fixtures import (
    DURATION, FIXTURE_ID, LIMITATIONS, generate_validation_cases,
    render_validation_case, validation_cases,
)


EXPECTED = {
    "guitar_true_octaves": ("guitar", 6, 2),
    "guitar_detuned_strums": ("guitar", 12, 6),
    "guitar_high_and_repeated": ("guitar", 8, 1),
    "piano_true_octave_stacks": ("piano", 12, 4),
    "piano_transposed_inner_voices": ("piano", 12, 6),
    "piano_repeated_and_high": ("piano", 8, 1),
    "synth_true_octave_chords": ("synthesizer", 8, 4),
    "synth_amplitude_varying_sustain": ("synthesizer", 4, 4),
    "bass_low_octave_leaps": ("bass", 6, 1),
    "vocal_vibrato_and_repeat": ("vocal", 6, 1),
}


def test_frozen_specification_hash_captured_before_any_inference():
    payload = json.dumps(validation_cases(), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    assert hashlib.sha256(payload).hexdigest() == "ae9d5116e29e61ce603ef69001f8a3457e49d3f6f9a18e8e66022a02fa014501"
    assert FIXTURE_ID == "pitched-validation-v1" and DURATION == 6.
    assert any("not real instrument recordings" in limitation for limitation in LIMITATIONS)
    assert any("not a claim" in limitation for limitation in LIMITATIONS)


@pytest.mark.parametrize("case", validation_cases(), ids=lambda case: case["id"])
def test_valid_finite_notes_durations_and_expected_polyphony(case):
    instrument, count, polyphony = EXPECTED[case["id"]]
    assert case["instrument"] == instrument
    assert case["duration"] == 6. and case["sample_rate"] == 48000
    assert len(case["events"]) == count
    assert np.isfinite(np.asarray(case["events"])).all()
    assert all(0 < start < end < case["duration"] and isinstance(pitch, int) and 0 <= pitch <= 127 and 0 < amplitude < 1
               for start, end, pitch, amplitude in case["events"])
    boundaries = sorted([(start, 1) for start, *_ in case["events"]] + [(end, -1) for _, end, *_ in case["events"]])
    active = maximum = 0
    for _, delta in boundaries:
        active += delta
        maximum = max(maximum, active)
    assert active == 0 and maximum == polyphony
    assert all(abs(cents) <= 4 for cents in case["render"]["detune_cents"])
    assert len(case["render"]["harmonics"]) >= 4


@pytest.mark.parametrize("case", validation_cases(), ids=lambda case: case["id"])
def test_deterministic_audio_finite_bounded_and_with_silent_margins(case):
    first, second = render_validation_case(case), render_validation_case(case)
    assert first.shape == (288000,) and first.dtype == np.float32
    assert np.isfinite(first).all() and np.array_equal(first, second)
    assert 0 < np.max(np.abs(first)) < 1
    assert not np.any(first[:round(.3 * case["sample_rate"])])
    assert not np.any(first[-round(.3 * case["sample_rate"]):])


@pytest.mark.parametrize("case_id", ["guitar_true_octaves", "piano_true_octave_stacks", "synth_true_octave_chords"])
def test_real_octave_doubling_remains_in_ground_truth(case_id):
    case = next(case for case in validation_cases() if case["id"] == case_id)
    groups = {}
    for start, end, pitch, amplitude in case["events"]:
        groups.setdefault((start, end), set()).add(pitch)
    assert all(any(pitch + 12 in pitches for pitch in pitches) for pitches in groups.values())


@pytest.mark.parametrize("case_id,repeated_pitch,count", [
    ("guitar_high_and_repeated", 69, 5), ("piano_repeated_and_high", 67, 5),
])
def test_repeated_notes_have_true_sixty_millisecond_rests(case_id, repeated_pitch, count):
    case = next(case for case in validation_cases() if case["id"] == case_id)
    notes = [event for event in case["events"] if event[2] == repeated_pitch]
    assert len(notes) == count
    assert all(next_note[0] - note[1] == pytest.approx(.06) for note, next_note in zip(notes, notes[1:]))


def test_amplitude_variation_is_one_sustained_chord_not_repeated_labels():
    case = next(case for case in validation_cases() if case["id"] == "synth_amplitude_varying_sustain")
    assert {(start, end) for start, end, *_ in case["events"]} == {(.5, 5.4)}
    assert case["render"]["modulation_depth"] == .8
    assert 1 - case["render"]["modulation_depth"] > 0
    audio = render_validation_case(case)
    # Tremolo troughs stay audible rather than becoming actual rests.
    for start in (1., 1.4, 1.8, 2.2, 2.6, 3., 3.4, 3.8, 4.2, 4.6):
        assert np.max(np.abs(audio[round(start * 48000):round((start + .15) * 48000)])) > 0


def test_generator_contract_and_fresh_specifications():
    specifications = validation_cases()
    assert tuple(case["id"] for case in specifications) == tuple(EXPECTED)
    assert sum(len(case["events"]) for case in specifications) == 82
    generated = list(generate_validation_cases())
    assert len(generated) == 10
    for spec, case in zip(specifications, generated):
        assert {key: value for key, value in case.items() if key != "audio"} == spec
        assert np.array_equal(case["audio"], render_validation_case(spec))
    specifications[0]["events"].clear()
    generated[0]["audio"].fill(0)
    assert len(validation_cases()[0]["events"]) == 6
    assert np.any(next(generate_validation_cases())["audio"])
