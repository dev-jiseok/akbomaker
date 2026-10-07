"""Safety checks independent of the development/validation recordings."""
import json

import numpy as np
import pytest

from backend.drum_evidence import suppress_unsupported_kicks


SR = 16000


def fixture_audio():
    audio = np.zeros(SR * 8)
    kicks = []
    for t in np.arange(.5, 7, 1):
        add_kick(audio, t)
        kicks.append((float(t), float(t + .1), 36, .8))
    return audio, kicks


def add_kick(audio, start, gain=1):
    n = min(int(SR * .25), len(audio) - int(start * SR))
    t = np.arange(n) / SR
    audio[int(start * SR):int(start * SR) + n] += gain * np.sin(2 * np.pi * 65 * t) * np.exp(-t * 24)


def add_hat(audio, start):
    n = min(int(SR * .06), len(audio) - int(start * SR))
    t = np.arange(n) / SR
    audio[int(start * SR):int(start * SR) + n] += .2 * np.sin(2 * np.pi * 5000 * t) * np.exp(-t * 100)


def test_removes_unsupported_kick_but_never_adds_hat_or_relabels():
    audio, kicks = fixture_audio()
    t = 3.9
    # A decayed low-band background is not a fresh kick attack. An isolated
    # high-frequency transient on digital silence can ring the low filter and
    # is deliberately left ambiguous by the conservative rule.
    audio += .001 * np.sin(2 * np.pi * 65 * np.arange(len(audio)) / SR)
    add_hat(audio, t)
    false_kick = (t, t + .1, 36, .2)
    events = [*kicks, false_kick, (t, t + .1, 42, .4), (t, t + .1, 75, .3)]
    kept, audit = suppress_unsupported_kicks(events, audio, SR)
    assert false_kick not in kept
    assert kept == [*kicks, (t, t + .1, 42, .4), (t, t + .1, 75, .3)]
    assert audit['rejected_count'] == 1
    assert audit['relabeled_count'] == audit['added_count'] == 0
    assert audit['evidence_is_confidence'] is False
    json.dumps(audit, allow_nan=False)


def test_preserves_simultaneous_kick_and_hat():
    audio, kicks = fixture_audio()
    add_hat(audio, 3.5)
    events = [*kicks, (3.5, 3.6, 42, .8)]
    assert suppress_unsupported_kicks(events, audio, SR)[0] == events


@pytest.mark.parametrize('gain', [.02, .1, 1])
def test_preserves_weak_kick_when_it_has_a_real_low_band_attack(gain):
    audio, kicks = fixture_audio()
    add_kick(audio, 3.9, gain=gain)
    add_hat(audio, 3.9)
    events = [*kicks, (3.9, 4., 36, .05), (3.9, 4., 42, .7)]
    assert suppress_unsupported_kicks(events, audio, SR)[0] == events


def test_stereo_opposite_polarity_cannot_cancel_real_kicks():
    audio, events = fixture_audio()
    stereo = np.column_stack([audio, -audio])
    kept, audit = suppress_unsupported_kicks(events, stereo, SR)
    assert kept == events
    assert audit['anchor_count'] >= 6


@pytest.mark.parametrize('gain', [.01, .1, 1, 10])
def test_gain_does_not_change_rejection(gain):
    audio, kicks = fixture_audio()
    events = [*kicks, (3.9, 4., 36, .2)]
    assert suppress_unsupported_kicks(events, audio * gain, SR)[0] == kicks


def test_silence_without_anchors_abstains():
    _, kicks = fixture_audio()
    kept, audit = suppress_unsupported_kicks(kicks, np.zeros(SR * 8), SR)
    assert kept == kicks
    assert audit['anchor_low_rms'] is None
    assert audit['rejected_count'] == 0
    json.dumps(audit, allow_nan=False)


def test_fewer_than_six_anchors_abstains():
    audio, kicks = fixture_audio()
    events = [*kicks[:5], (3.9, 4., 36, .2)]
    kept, audit = suppress_unsupported_kicks(events, audio, SR)
    assert kept == events
    assert audit['anchor_count'] == 5
    assert audit['anchor_low_rms'] is None


@pytest.mark.parametrize('timestamps', [[1.] * 6, [.98, .99, 1., 1.01, 1.02, 1.03]])
def test_same_attack_duplicates_cannot_manufacture_six_anchors(timestamps):
    audio = np.zeros(SR * 3)
    add_kick(audio, 1.)
    # Include both GM kick aliases and reverse input order to exercise grouping.
    events = [(t, t + .1, 35 + i % 2, .8) for i, t in enumerate(timestamps)][::-1]
    events.append((2., 2.1, 36, .2))
    kept, audit = suppress_unsupported_kicks(events, audio, SR)
    assert kept == events
    assert audit['anchor_count'] == 1
    assert audit['anchor_low_rms'] is None
    assert audit['rejected_count'] == 0


def test_deduplicating_weak_anchors_cannot_raise_rejection_threshold():
    audio, kicks = fixture_audio()
    # Many duplicates at a weak, genuine attack must not pull the new distinct
    # median above the old median and cause additional deletions.
    add_kick(audio, 7.4, gain=.1)
    weak = [(7.4 + i * .0001, 7.5 + i * .0001, 36, .1) for i in range(20)]
    _, audit = suppress_unsupported_kicks([*kicks, *weak], audio, SR)
    qualifying = [e['low_rms'] for e in audit['candidates'] if e['eligible']
                  and e['low_rms'] > 1e-8 and e['attack_ratio'] >= 2]
    assert audit['anchor_count'] == 8
    assert audit['anchor_low_rms'] <= float(np.median(qualifying))


def test_preserves_recording_edge_events_without_enough_context():
    audio, kicks = fixture_audio()
    events = [*kicks, (0., .1, 36, .5), (7.95, 8.05, 35, .5)]
    kept, audit = suppress_unsupported_kicks(events, audio, SR)
    assert kept == events
    edges = [e for e in audit['candidates'] if not e['eligible']]
    assert len(edges) == 2
    assert all(e['reason'] == 'recording-edge' for e in edges)


def test_non_kick_gm_types_and_order_are_never_modified():
    audio, kicks = fixture_audio()
    events = [(3.9, 4., 38, .1), *kicks, (3.9, 4., 54, .8), (2.1, 2.2, 46, .3)]
    original = list(events)
    assert suppress_unsupported_kicks(events, audio, SR)[0] == original
    assert events == original


def test_supports_acoustic_bass_drum_alias_35():
    audio, kicks = fixture_audio()
    events = [(a, b, 35, v) for a, b, _, v in kicks] + [(3.9, 4., 35, .2)]
    kept, audit = suppress_unsupported_kicks(events, audio, SR)
    assert kept == events[:-1]
    assert audit['rejected_count'] == 1


def test_short_audio_does_not_fail_filter_padding():
    kept, audit = suppress_unsupported_kicks([(0., .1, 36, .1)], np.zeros(10), SR)
    assert kept == [(0., .1, 36, .1)]
    assert audit['rejected_count'] == 0


@pytest.mark.parametrize('audio', [[], [float('nan')], [float('inf')], [1e300], [[1, 2, 3]], [[[1]]], ['x'], [1j]])
def test_rejects_invalid_audio(audio):
    with pytest.raises(ValueError):
        suppress_unsupported_kicks([], audio, SR)


@pytest.mark.parametrize('rate', [True, 0, 7999, 192001, float('nan'), float('inf')])
def test_rejects_invalid_sample_rate(rate):
    with pytest.raises(ValueError):
        suppress_unsupported_kicks([], np.zeros(100), rate)


@pytest.mark.parametrize('event', [(0, 0, 36, .5), (-1, 1, 36, .5), (0, 1, 36.5, .5),
                                  (0, 1, 36, 0), (0, 1, 36, 2), (0, 1, 34, .5),
                                  (0, 1, 82, .5), (0, 1, 36, True), (0, 1, 36),
                                  (8, 9, 36, .5), (float('nan'), 1, 36, .5)])
def test_rejects_invalid_events(event):
    with pytest.raises(ValueError):
        suppress_unsupported_kicks([event], np.zeros(SR * 8), SR)


def test_empty_events_returns_empty():
    kept, audit = suppress_unsupported_kicks([], np.zeros(1), SR)
    assert kept == []
    assert audit['rejected_count'] == 0


def test_event_count_bounded():
    with pytest.raises(ValueError):
        suppress_unsupported_kicks([(0., .1, 36, .1)] * 30001, np.zeros(10), SR)
