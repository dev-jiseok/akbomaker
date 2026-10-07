"""The optional challenger must not claim unsupported detail or invent beats."""
import math
import pytest

from backend.drum_challenger import (CLASS_NAMES, REPRESENTATIVE_GM, family_events,
                                    representative_events, segment_starts, verify_assets)


@pytest.mark.parametrize('total,width,hop', [(1, 861, 430), (861, 861, 430),
                                          (862, 861, 430), (1034, 861, 430),
                                          (1291, 861, 430), (5168, 861, 430)])
def test_chunks_cover_every_frame_including_short_tail(total, width, hop):
    covered = set()
    for start in segment_starts(total, width, hop):
        covered.update(range(start, min(start+width, total)))
    assert covered == set(range(total))


@pytest.mark.parametrize('args', [(0, 10, 5), (10, 0, 5), (10, 10, 0),
                                 (True, 10, 5), (10, 10, 11), (10., 10, 5)])
def test_invalid_chunks_rejected(args):
    with pytest.raises(ValueError):
        segment_starts(*args)


def test_no_argmax_class_fabrication_or_rhythm_fill():
    events, evidence = representative_events([.13, .87], [[.49]*8, [.1]*8], 2.)
    assert events == []
    assert len(evidence) == 2
    assert all(not row['classes'] for row in evidence)


def test_simultaneous_classes_preserved_not_silently_pruned():
    events, evidence = representative_events([.137], [[.5]*8], 1.)
    assert [event[2] for event in events] == sorted(REPRESENTATIVE_GM)
    assert all(event[0] == .137 and event[3] == .8 for event in events)
    assert evidence[0]['classes'] == list(CLASS_NAMES)
    assert 'hihat-unspecified' in evidence[0]['classes']


@pytest.mark.parametrize('probabilities', [[[math.nan]*8], [[math.inf]*8],
                                         [[-0.1]*8], [[1.1]*8], [[.5]*7], []])
def test_malformed_probabilities_fail_closed(probabilities):
    with pytest.raises(ValueError):
        representative_events([.2], probabilities, 1.)


@pytest.mark.parametrize('start', [-.1, 1., math.nan, math.inf])
def test_out_of_clip_onsets_rejected(start):
    with pytest.raises(ValueError):
        representative_events([start], [[.5]*8], 1.)


def test_family_mapping_keeps_unsupported_and_folded_duplicates():
    source = [(0., .1, 42, .5), (0., .1, 46, .5), (.2, .3, 56, .5)]
    assert family_events(source) == [(0., .1, 42, .5), (0., .1, 42, .5), (.2, .3, 56, .5)]


def test_foot_hat_and_rim_are_lossy_family_not_exact_articulation():
    assert family_events([(0., .1, 44, .5), (.2, .3, 37, .5)]) == [(0., .1, 42, .5), (.2, .3, 38, .5)]


def test_unverified_assets_rejected_before_any_source_import(tmp_path):
    with pytest.raises(ValueError, match='Unverified STRUM source'):
        verify_assets(tmp_path, tmp_path)


def test_existing_default_routes_do_not_import_challenger():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for filename in ('pipeline.py', 'transcription.py', 'drum_worker.py'):
        assert 'drum_challenger' not in (root / filename).read_text()
