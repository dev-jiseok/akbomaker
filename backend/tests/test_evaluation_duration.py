import pytest

from backend.evaluation import compare_events


def test_duration_diagnostic_does_not_hide_shortened_or_fragmented_sustains():
    reference = [(0, 2, 60, 1)]
    truncated = [(0, .3, 60, 1), (.8, 1, 60, 1)]
    old = compare_events(reference, truncated, include_errors=True)
    current = compare_events(reference, truncated, include_errors=True, duration_tolerance_ratio=.2)
    assert current["duration_diagnostics"]["matched_with_valid_offset"] == 0
    assert current["duration_diagnostics"]["onset_matches_with_wrong_offset"] == 1
    assert current["duration_diagnostics"]["f1"] == 0
    assert current["duration_diagnostics"]["wrong_offset_events"][0]["estimated_end"] == .3
    assert {k: v for k, v in current.items() if k != "duration_diagnostics"} == old


def test_real_reattacks_require_separate_predictions_and_offsets():
    reference = [(0, .4, 60), (.5, .9, 60)]
    merged = compare_events(reference, [(0, .9, 60)], duration_tolerance_ratio=.2)
    assert merged["matched_notes"] == 1
    assert merged["duration_diagnostics"]["f1"] == 0
    correct = compare_events(reference, reference, duration_tolerance_ratio=.2)
    assert correct["duration_diagnostics"]["f1"] == 1


def test_offset_tolerance_has_fifty_ms_floor_and_relative_duration():
    result = compare_events([(0, .1, 60), (1, 3, 62)], [(0, .15, 60), (1, 3.4, 62)], duration_tolerance_ratio=.2)
    assert result["duration_diagnostics"]["matched_with_valid_offset"] == 2
    assert "wrong_offset_events" not in result["duration_diagnostics"]
    assert compare_events([], [], duration_tolerance_ratio=.2)["duration_diagnostics"]["f1"] == 0


@pytest.mark.parametrize("ratio", [True, -.1, 1.1, float("inf"), float("nan")])
def test_invalid_duration_tolerance_rejected(ratio):
    with pytest.raises(ValueError, match="Duration"):
        compare_events([], [], duration_tolerance_ratio=ratio)
