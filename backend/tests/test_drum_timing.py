import pytest

from backend.drum_consensus import consensus_events
from backend.drum_timing import LEADING_TOLERANCE_SECONDS, restore_drum_timing


def hit(start, end=None, pitch=36):
    return (start, start + .1 if end is None else end, pitch, .8)


def test_opening_attack_survives_three_offsets_despite_token_rounding():
    offsets = [0., 2.56 / 3, 2 * 2.56 / 3]
    token_onsets = [0., .85, 1.70]
    passes, adjusted = [], []
    for offset, onset in zip(offsets, token_onsets):
        restored, review = restore_drum_timing([hit(onset)], offset, 3.)
        passes.append(restored)
        adjusted.append(review["adjusted_leading"])
    result, review = consensus_events(passes)
    assert len(result) == 1 and result[0][0] == 0
    assert review["candidates"][0]["votes"] == 3
    assert adjusted == [0, 1, 1]


def test_tolerance_is_bounded_and_padding_overlap_does_not_invent_an_attack():
    offset = 1.
    restored, review = restore_drum_timing([
        hit(offset - LEADING_TOLERANCE_SECONDS),
        hit(offset - LEADING_TOLERANCE_SECONDS - .0001),
        hit(.92, 1.02),  # Artificial note duration crosses zero, onset far before it.
        hit(.5),
    ], offset, 2.)
    assert len(restored) == 1 and restored[0][0] == 0
    assert review["adjusted_leading"] == 1
    assert review["dropped_leading"] == 3


def test_a_wholly_padded_short_event_is_rejected_even_with_near_zero_onset():
    result, review = restore_drum_timing([hit(.995, .999)], 1., 2.)
    assert result == []
    assert review["dropped_leading"] == 1
    assert review["adjusted_leading"] == 0


def test_tail_clipping_is_audited_but_outside_onsets_are_never_moved_inside():
    result, review = restore_drum_timing([hit(1.95), hit(2.), hit(2.1)], 0., 2.)
    assert result == [(1.95, 2., 36, .8)]
    assert review["clipped_trailing"] == 1
    assert review["dropped_trailing"] == 2
    assert review["input_count"] == 3 and review["output_count"] == 1


def test_unshifted_in_range_events_and_nonkit_gm_are_preserved():
    events = [hit(0.), hit(.5, pitch=56), hit(.5, pitch=42)]
    result, review = restore_drum_timing(events, 0., 1.)
    assert result == events
    assert all(review[k] == 0 for k in ("adjusted_leading", "dropped_leading", "dropped_trailing", "clipped_trailing"))


def test_shift_is_applied_once_to_both_ends_without_changing_pitch_or_velocity():
    result, review = restore_drum_timing([hit(1.5, 1.6, 44)], .85, 3.)
    assert result[0][:2] == pytest.approx((.65, .75))
    assert result[0][2:] == (44, .8)
    assert review["offset_seconds"] == .85


def test_silence_produces_empty_passes_and_zero_counts():
    result, review = restore_drum_timing([], .85, 3.)
    assert result == []
    assert review["input_count"] == review["output_count"] == 0
    assert all(review[k] == 0 for k in ("adjusted_leading", "dropped_leading", "dropped_trailing", "clipped_trailing"))


@pytest.mark.parametrize("offset,duration", [(-1, 2), (float("nan"), 2), (0, 0), (0, float("inf"))])
def test_rejects_invalid_pass_bounds(offset, duration):
    with pytest.raises(ValueError):
        restore_drum_timing([], offset, duration)


@pytest.mark.parametrize("event", [(-.1, .1, 36, .8), (0, 0, 36, .8), (0, .1, 36, 0),
                                   (0, .1, 36.5, .8), (0, .1, 34, .8), (0, float("nan"), 36, .8)])
def test_rejects_invalid_candidates(event):
    with pytest.raises(ValueError):
        restore_drum_timing([event], 0, 2)
