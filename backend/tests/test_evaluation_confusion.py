"""Possible pitch substitutions must never inflate the primary onset score."""
import random

import pytest

from backend.evaluation import compare_events


def note(start, pitch, duration=.1):
    return (start, start + duration, pitch, 1.)


def evaluate(reference, estimated, **kwargs):
    return compare_events(reference, estimated, include_errors=True, **kwargs)


def test_nearby_wrong_pitch_is_a_possible_confusion_not_a_match():
    result = evaluate([note(1, 42)], [note(1.025, 36)])
    assert result["possible_confusions"] == [{
        "reference_pitch": 42, "estimated_pitch": 36,
        "reference_start": 1, "estimated_start": 1.025, "delta_ms": 25.,
        "ambiguous": False, "ambiguity_sides": [],
    }]
    assert result["confusion_counts"] == {"42->36": 1}
    assert result["matched_notes"] == result["f1"] == 0
    assert result["missing_notes"] == result["extra_notes"] == 1
    assert result["missing_events"][0]["pitch"] == 42
    assert result["extra_events"][0]["pitch"] == 36
    assert result["confusion_diagnostics"]["heuristic_only"] is True
    assert "not proof" in result["confusion_diagnostics"]["warning"]


def test_matched_simultaneous_kick_is_not_reused_as_missing_hihat():
    result = evaluate([note(0, 36), note(0, 42)], [note(0, 36)])
    assert result["matched_notes"] == 1
    assert result["missing_notes"] == 1
    assert result["extra_notes"] == 0
    assert result["possible_confusions"] == []
    assert result["confusion_counts"] == {}


def test_matched_hihat_and_extra_kick_do_not_claim_hihat_confusion():
    result = evaluate([note(0, 42)], [note(0, 42), note(.01, 36)])
    assert result["matched_notes"] == 1
    assert result["extra_notes"] == 1
    assert result["possible_confusions"] == []


@pytest.mark.parametrize("estimated_start", [.949, 1.051])
def test_outside_tolerance_is_not_paired(estimated_start):
    result = evaluate([note(1, 42)], [note(estimated_start, 36)])
    assert result["possible_confusions"] == []


def test_custom_tolerance_and_signed_delta():
    result = evaluate([note(1, 42)], [note(.925, 36)], tolerance=.08)
    assert result["possible_confusions"][0]["delta_ms"] == -75.


def test_exact_tolerance_boundary_is_included():
    result = evaluate([note(0, 42)], [note(.05, 36)])
    assert result["confusion_counts"] == {"42->36": 1}


@pytest.mark.parametrize("reference_start,estimated_start", [(1.25, 1.30), (1.30, 1.25), (599.90, 599.95), (599.95, 599.90)])
def test_decimal_fifty_ms_boundary_is_consistent_for_matches_and_confusions(reference_start, estimated_start):
    matched = evaluate([note(reference_start, 42)], [note(estimated_start, 42)])
    assert matched["matched_notes"] == 1 and matched["f1"] == 1
    confused = evaluate([note(reference_start, 42)], [note(estimated_start, 36)])
    assert confused["matched_notes"] == 0
    assert confused["confusion_counts"] == {"42->36": 1}


@pytest.mark.parametrize("direction", [-1, 1])
def test_actual_distance_outside_fifty_ms_is_not_relaxed(direction):
    start = 1.25 + direction * (.05 + 2e-12)
    matched = evaluate([note(1.25, 42)], [note(start, 42)])
    assert matched["matched_notes"] == 0
    confused = evaluate([note(1.25, 42)], [note(start, 36)])
    assert confused["possible_confusions"] == []


def test_decimal_boundary_alternatives_stay_marked_ambiguous():
    result = evaluate([note(1.25, 42)], [note(1.20, 36), note(1.30, 38)])
    assert result["possible_confusions"][0]["ambiguity_sides"] == ["estimated"]
    reverse = evaluate([note(1.20, 42), note(1.30, 46)], [note(1.25, 36)])
    assert reverse["possible_confusions"][0]["ambiguity_sides"] == ["reference"]


def test_nearest_candidate_not_first_time_candidate():
    result = evaluate([note(1, 42)], [note(.96, 36), note(1.01, 38)])
    assert result["confusion_counts"] == {"42->38": 1}
    assert result["extra_notes"] == 2


def test_duplicate_notes_are_preserved_and_paired_one_to_one():
    result = evaluate([note(1, 42)] * 3, [note(1, 36)] * 2)
    assert len(result["possible_confusions"]) == 2
    assert result["confusion_counts"] == {"42->36": 2}
    assert result["missing_notes"] == 3
    assert result["extra_notes"] == 2
    assert all(pair["ambiguity_sides"] == ["reference", "estimated"]
               for pair in result["possible_confusions"])


def test_reference_side_simultaneous_ambiguity():
    result = evaluate([note(1, 42), note(1, 46)], [note(1, 36)])
    assert result["possible_confusions"][0]["ambiguity_sides"] == ["reference"]


def test_estimate_side_equal_distance_ambiguity():
    result = evaluate([note(1, 42)], [note(.99, 36), note(1.01, 38)])
    pair = result["possible_confusions"][0]
    assert pair["ambiguity_sides"] == ["estimated"]
    assert pair["estimated_start"] == .99


def test_all_pairs_of_ambiguous_polyphony_remain_marked():
    result = evaluate([note(1, 42), note(1, 46)], [note(1, 36), note(1, 38)])
    assert len(result["possible_confusions"]) == 2
    assert result["confusion_diagnostics"]["ambiguous_pairs"] == 2
    assert all(pair["ambiguity_sides"] == ["reference", "estimated"]
               for pair in result["possible_confusions"])


def test_primary_metrics_identical_and_diagnostics_opt_in():
    reference = [note(0, 36), note(.02, 36), note(.04, 42), note(2, 38)]
    estimated = [note(.01, 36), note(.03, 38), note(2.01, 38), note(3, 42)]
    primary = compare_events(reference, estimated)
    detailed = evaluate(reference, estimated)
    assert {key: detailed[key] for key in primary} == primary
    assert "possible_confusions" not in primary
    assert all(pair["reference_pitch"] != pair["estimated_pitch"]
               for pair in detailed["possible_confusions"])


def test_primary_same_pitch_matching_takes_priority_over_closer_wrong_pitch():
    result = evaluate([note(1, 42)], [note(1, 36), note(1.04, 42)])
    assert result["matched_notes"] == 1
    assert result["possible_confusions"] == []


def test_deleted_nearest_candidate_does_not_hide_next_candidate():
    result = evaluate([note(1, 42), note(1.01, 42), note(1.02, 42)],
                      [note(1, 36), note(1.015, 36), note(1.025, 36)])
    assert [pair["estimated_start"] for pair in result["possible_confusions"]] == [1, 1.015, 1.025]


def test_deterministic_under_input_permutations():
    reference = [note(1, 46), note(1, 42), note(2, 45), note(2.03, 45)]
    estimated = [note(1, 38), note(1, 36), note(2.02, 36), note(2, 38)]
    expected = evaluate(reference, estimated)
    rng = random.Random(907)
    for _ in range(20):
        rng.shuffle(reference)
        rng.shuffle(estimated)
        assert evaluate(reference, estimated) == expected


def test_indexed_pairing_agrees_with_small_brute_force_matching():
    rng = random.Random(819)
    for _ in range(50):
        reference = [note(rng.randrange(400) / 1000, rng.choice((36, 38, 42, 46))) for _ in range(80)]
        estimated = [note(rng.randrange(400) / 1000, rng.choice((36, 38, 42, 46))) for _ in range(80)]
        result = evaluate(reference, estimated)
        event_key = lambda event: (event["start"], event["pitch"], event["end"])
        remaining = list(enumerate(sorted(result["extra_events"], key=event_key)))
        expected = []
        for event in sorted(result["missing_events"], key=event_key):
            candidates = [(abs(other["start"] - event["start"]), other["start"], other["pitch"], other["end"], index)
                          for index, other in remaining
                          if other["pitch"] != event["pitch"] and abs(other["start"] - event["start"]) <= .05 + 1e-12]
            if not candidates:
                continue
            _, start, pitch, _, selected_id = min(candidates)
            expected.append((event["pitch"], pitch, event["start"], start))
            remaining = [(index, other) for index, other in remaining if index != selected_id]
        assert [(pair["reference_pitch"], pair["estimated_pitch"], pair["reference_start"], pair["estimated_start"])
                for pair in result["possible_confusions"]] == expected


@pytest.mark.parametrize("reference,estimated", [([], []), ([note(0, 42)], []), ([], [note(0, 36)])])
def test_empty_side_returns_empty_diagnostics(reference, estimated):
    result = evaluate(reference, estimated)
    assert result["possible_confusions"] == []
    assert result["confusion_diagnostics"]["possible_confusion_count"] == 0
    assert result["confusion_diagnostics"]["ambiguous_pairs"] == 0


def test_dense_30000_note_case_avoids_cartesian_pair_materialization():
    # Every reference is in range of every estimate: all-pairs matching would
    # create 900 million edges. This exercises bounded pitch-indexed matching.
    result = evaluate([note(1, 42)] * 30_000, [note(1, 36)] * 30_000)
    assert result["confusion_counts"] == {"42->36": 30_000}
    assert result["confusion_diagnostics"]["ambiguous_pairs"] == 30_000
    assert result["matched_notes"] == 0
