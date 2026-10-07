import numpy as np
import pytest

from backend import pitched_decoder as decoder


@pytest.fixture(autouse=True)
def fixed_timebase(monkeypatch):
    monkeypatch.setattr(decoder, "_frame_times", lambda count: np.arange(count) * .012)


def output(length=200):
    return {"note": np.zeros((length, 88)), "onset": np.zeros((length, 88))}


def tone(raw, start, end, pitch, frame=.6, onset=.8):
    raw["note"][start:end, pitch - 21] = frame
    raw["onset"][start, pitch - 21] = onset


def decode(raw, **changes):
    settings = dict(onset_threshold=.6, frame_threshold=.4, minimum_ms=80,
                    min_pitch=21, max_pitch=108, duration=len(raw["note"]) * .012 or 1.)
    settings.update(changes)
    return decoder.decode_pitched(raw, **settings)


def test_keeps_simultaneous_octaves_adjacent_semitones_and_highest_piano_note():
    raw = output()
    for pitch in (48, 60, 61, 72, 108):
        tone(raw, 10, 80, pitch)
    notes, review = decode(raw)
    assert {note[2] for note in notes} == {48, 60, 61, 72, 108}
    assert len(notes) == 5
    assert review["event_count"] == 5 and not review["evidence_is_confidence"]


def test_quiet_frame_with_strong_onset_uses_relative_sustain_threshold():
    raw = output()
    tone(raw, 10, 80, 55, frame=.1)
    notes, review = decode(raw)
    assert len(notes) == 1
    assert notes[0][:3] == (.12, .96, 55)
    assert review["evidence"][0]["onset_activation"] == .8
    assert review["evidence"][0]["sustain_threshold"] == .05


def test_wavering_sustain_is_one_note_without_frame_only_retriggers():
    raw = output()
    tone(raw, 10, 170, 55, frame=.39)
    raw["note"][10:17, 34] = .6
    raw["note"][40:46, 34] = .2  # Shorter than 11-frame gap tolerance.
    raw["note"][80:150:2, 34] = .41
    notes, _ = decode(raw)
    assert len(notes) == 1 and notes[0][:3] == (.12, 2.04, 55)


def test_silence_gap_is_preserved_and_repeated_onsets_remain_distinct():
    raw = output()
    tone(raw, 10, 50, 60)
    tone(raw, 90, 140, 60)
    notes, _ = decode(raw)
    assert len(notes) == 2
    assert notes[0][1] == .6 and notes[1][0] == 1.08


def test_same_pitch_reattack_splits_a_continuously_sounding_note():
    raw = output()
    tone(raw, 10, 170, 60)
    raw["onset"][90, 39] = .9
    notes, _ = decode(raw)
    assert [(n[0], n[1]) for n in notes] == [(.12, 1.08), (1.08, 2.04)]


def test_quieter_same_pitch_reattack_is_not_gated_by_the_previous_loud_sustain():
    raw = output()
    tone(raw, 10, 90, 60, frame=.6)
    tone(raw, 90, 170, 60, frame=.15)
    notes, _ = decode(raw)
    assert [(n[0], n[1]) for n in notes] == [(.12, 1.08), (1.08, 2.04)]


def test_short_false_onset_does_not_erase_an_earlier_accepted_note():
    raw = output()
    tone(raw, 10, 100, 60)
    raw["onset"][97, 39] = .9
    notes, review = decode(raw)
    assert len(notes) == 1 and notes[0][:2] == (.12, 1.2)
    assert review["rejected_short_or_unsupported"] == 1


def test_onsetless_or_weak_onset_energy_is_deliberately_deferred():
    raw = output()
    tone(raw, 10, 150, 60, onset=.59)
    tone(raw, 10, 150, 72, onset=0.)
    notes, review = decode(raw)
    assert notes == []
    assert not review["creates_frame_only_notes"] and not review["uses_inferred_onsets"]


def test_background_sustain_without_release_is_explicitly_auditable_not_confidence():
    raw = output()
    raw["note"][:, 34] = .08
    raw["note"][10:80, 34] = .1
    raw["onset"][10, 34] = .8
    notes, review = decode(raw)
    assert len(notes) == 1
    assert review["offsets_without_energy_release"] == 1
    assert review["evidence"][0]["offset_reason"] == "model_end"
    assert review["evidence"][0]["pre_onset_frame_median"] == .08
    assert not review["evidence_is_confidence"]


def test_strong_onset_without_sustain_is_not_a_note():
    raw = output()
    tone(raw, 10, 11, 60, frame=.01)
    notes, _ = decode(raw)
    assert notes == []


def test_onset_zero_and_plateau_are_kept_as_a_single_attack():
    raw = output()
    tone(raw, 0, 80, 60)
    raw["onset"][:4, 39] = .8
    notes, _ = decode(raw)
    assert len(notes) == 1 and notes[0][:2] == (0., .96)


def test_tail_end_is_clipped_to_duration_without_losing_last_supported_frame():
    raw = output(100)
    tone(raw, 80, 100, 60)
    notes, _ = decode(raw, duration=1.19)
    assert len(notes) == 1 and notes[0][:2] == (.96, 1.19)
    notes, _ = decode(raw, duration=.97)
    assert notes == []  # Physical audio overlap is below minimum length.


def test_candidates_outside_audio_are_not_clamped_back_onto_the_tail():
    raw = output(100)
    tone(raw, 80, 100, 60)
    notes, review = decode(raw, duration=.5)
    assert notes == [] and review["out_of_audio_candidates"] == 1


def test_inclusive_pitch_bounds_do_not_mutate_cached_or_readonly_outputs():
    raw = output()
    for pitch in (59, 60, 61):
        tone(raw, 10, 80, pitch)
    copies = {key: value.copy() for key, value in raw.items()}
    for matrix in raw.values():
        matrix.flags.writeable = False
    notes, _ = decode(raw, min_pitch=60, max_pitch=61)
    assert {n[2] for n in notes} == {60, 61}
    for key in raw:
        np.testing.assert_array_equal(raw[key], copies[key])


def test_empty_and_silent_outputs_make_no_notes():
    assert decode(output(0))[0] == []
    assert decode(output())[0] == []


@pytest.mark.parametrize("field,value", [("onset_threshold", 0), ("frame_threshold", float("nan")),
                                        ("minimum_ms", -1), ("duration", 0), ("duration", 601),
                                        ("min_pitch", 20), ("max_pitch", 109), ("min_pitch", 21.),
                                        ("max_pitch", True), ("onset_threshold", True)])
def test_rejects_invalid_settings(field, value):
    with pytest.raises(ValueError):
        decode(output(), **{field: value})


@pytest.mark.parametrize("invalid", [np.zeros((12, 87)), np.zeros((12, 88, 1)),
                                    np.full((12, 88), np.nan), np.full((12, 88), -.01),
                                    np.full((12, 88), 1.01), np.zeros((12, 88), dtype=complex)])
def test_rejects_invalid_matrices(invalid):
    with pytest.raises(ValueError):
        decode({"note": invalid, "onset": invalid.copy()})


def test_rejects_mismatched_oversized_or_missing_matrices():
    with pytest.raises(ValueError):
        decode({"note": np.zeros((12, 88)), "onset": np.zeros((11, 88))})
    with pytest.raises(ValueError):
        decoder.decode_pitched({}, .6, .4, 80, 21, 108, 1.)
    oversized = np.broadcast_to(np.float32(0), (decoder.MAX_FRAMES + 1, 88))
    with pytest.raises(ValueError):
        decode({"note": oversized, "onset": oversized})


def test_rejects_more_than_event_limit_without_retaining_unbounded_evidence(monkeypatch):
    monkeypatch.setattr(decoder, "MAX_EVENTS", 2)
    raw = output()
    for pitch in (60, 64, 67):
        tone(raw, 10, 80, pitch)
    with pytest.raises(ValueError, match="Too many"):
        decode(raw)


def test_rejects_invalid_timebase(monkeypatch):
    monkeypatch.setattr(decoder, "_frame_times", lambda count: np.zeros(count))
    with pytest.raises(ValueError, match="time mapping"):
        decode(output())
