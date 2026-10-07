"""Frozen manual-clip evaluation contracts; no models, audio or disk writes."""
from copy import deepcopy
import json

import pytest

from backend.config import INSTRUMENTS
from backend.review_case_evaluation import (
    LAYERS, MAX_EVENTS, ReviewCaseEvaluationError, canonical_sha256,
    evaluate_review_case, export_review_case, validate_review_case,
)


def event(identifier="n1", start=1.25, end=1.75, pitch=60, amplitude=.8):
    return {"id": identifier, "start": start, "end": end, "pitch": pitch, "amplitude": amplitude}


def seal(case, *, reviewed=True):
    case["snapshot_id"] = canonical_sha256(case["snapshot"])
    case["status"] = "reviewed" if reviewed else "draft"
    case["confirmation"] = ({"revision": case["revision"], "confirmed_at": case["updated_at"],
                             "reference_sha256": canonical_sha256(case["reference"])} if reviewed else None)
    return case


def example(inst="bass", *, reviewed=True):
    note = event(pitch=42 if inst == "drums" else 60)
    snapshot = {
        "schema": "akbo.transcription-review", "schema_version": 1, "instrument": inst, "duration": 8,
        "window": {"start": 1, "end": 5},
        "source": {"kind": "stem", "file": f"{inst}.wav", "sha256": "a" * 64, "verified": True},
        "score": {"revision": "score-v1", "edited": False, "bpm": 180, "timing_bpm": 120, "audio_offset": .2},
        "layers": {name: {"events": [deepcopy(note)], "total": 1, "in_window": 1} for name in LAYERS},
        "warnings": ["Recorded events are not ground truth."],
        "provenance": {"notes_file_sha256": "b" * 64, "automatic_file_sha256": "c" * 64,
                       "current_file_sha256": "d" * 64, "raw_artifact_id": "e" * 32,
                       "automatic_revision": "auto-v1", "inference": {"engine": "test-engine", "profile": "instrument",
                           "host_packages": {"numpy": "2.0.0"}}},
    }
    case = {"schema": "akbo.review-case", "schema_version": 1, "id": "1" * 32, "job_id": "2" * 32,
            "instrument": inst, "revision": "3" * 32, "created_at": "2026-10-06T07:00:00+00:00",
            "updated_at": "2026-10-06T07:00:00+00:00", "status": "draft", "snapshot_id": "",
            "snapshot": snapshot, "reference": {"events": [deepcopy(note)], "reviewer": "검수자",
            "basis": "허용된 같은 연주의 원음과 기준 악보를 대조했습니다.", "coverage_complete": True},
            "annotations": [], "confirmation": None}
    return seal(case, reviewed=reviewed)


def replace_layer(case, name, events):
    case["snapshot"]["layers"][name] = {"events": events, "total": len(events), "in_window": len(events)}
    return seal(case, reviewed=case["status"] == "reviewed")


@pytest.mark.parametrize("inst", INSTRUMENTS)
def test_all_six_instruments_source_time_metrics_and_no_input_mutation(inst):
    case = example(inst)
    before = deepcopy(case)
    report = evaluate_review_case(case)
    assert case == before
    assert report["status"] == "complete"
    assert report["reference_kind"] == "reviewer-confirmed"
    assert report["independent_ground_truth"] is False
    assert report["scope"] == "selected-reviewed-source-time-onsets"
    assert report["snapshot_id"] == case["snapshot_id"]
    for metric in report["metrics"].values():
        assert metric["reference_present"] is True
        assert metric["matched_notes"] == 1 and metric["f1"] == 1
        assert metric["median_onset_error_ms"] == 0
        assert metric["onset_tolerance_seconds"] == .05
        if inst == "drums":
            assert metric["offset_evaluated"] is False
            assert "duration_diagnostics" not in metric and "median_offset_error_ms" not in metric
        else:
            assert metric["offset_evaluated"] is True
            assert metric["duration_diagnostics"]["matched_with_valid_offset"] == 1


def test_draft_is_exportable_but_never_has_metrics_even_if_all_notes_match():
    case = example(reviewed=False)
    case["reference"].update(reviewer="", basis="", coverage_complete=False)
    assert evaluate_review_case(case) is None
    exported = export_review_case(case)
    assert exported["report"] is None and exported["reference_status"] == "draft-candidate"
    assert '"f1"' not in json.dumps(exported)
    assert "초안" in exported["warnings"][0]


@pytest.mark.parametrize("field,value", [("reviewer", "  "), ("basis", "\n"), ("coverage_complete", False),
                                         ("coverage_complete", 1)])
def test_reviewed_requires_explicit_complete_coverage_reviewer_and_basis(field, value):
    case = example()
    case["reference"][field] = value
    seal(case)
    with pytest.raises(ReviewCaseEvaluationError):
        evaluate_review_case(case)


@pytest.mark.parametrize("predicted", [False, True])
def test_explicitly_reviewed_empty_reference_not_perfect_accuracy(predicted):
    case = example()
    case["reference"]["events"] = []
    if not predicted:
        for name in LAYERS:
            replace_layer(case, name, [])
    seal(case)
    report = evaluate_review_case(case)
    for metric in report["metrics"].values():
        assert metric["reference_present"] is False
        assert metric["reference_notes"] == 0 and metric["matched_notes"] == 0
        assert metric["f1"] == 0 and metric["precision"] == 0 and metric["recall"] == 0
        assert metric["extra_notes"] == int(predicted)
    assert "기준 음표가 없어요" in " ".join(report["warnings"])


def test_pre_window_sustain_not_turned_into_new_attack_and_original_ends_preserved():
    case = example()
    context = event("context", start=.5, end=2)
    near_end = event("near-end", start=4.99, end=7)
    case["reference"]["events"].append(deepcopy(near_end))
    for name in LAYERS:
        replace_layer(case, name, [context, event(), near_end])
    report = evaluate_review_case(seal(case))
    for metric in report["metrics"].values():
        assert metric["estimated_notes"] == metric["reference_notes"] == 2
        assert metric["excluded_context_notes"] == 1
        assert metric["matched_notes"] == 2
        assert metric["duration_diagnostics"]["matched_with_valid_offset"] == 2
    assert case["snapshot"]["layers"]["recognized"]["events"][0]["start"] == .5
    assert case["reference"]["events"][-1]["end"] == 7


@pytest.mark.parametrize("start,end", [(0.5, 2), (5, 6), (0.5, 1)])
def test_reference_requires_onset_inside_half_open_window(start, end):
    case = example()
    case["reference"]["events"] = [event(start=start, end=end)]
    with pytest.raises(ReviewCaseEvaluationError):
        evaluate_review_case(seal(case))


def test_exact_window_start_is_counted_and_exact_window_end_prediction_rejected():
    case = example()
    case["reference"]["events"] = [event(start=1)]
    for name in LAYERS:
        replace_layer(case, name, [event(start=1)])
    assert evaluate_review_case(seal(case))["metrics"]["recognized"]["matched_notes"] == 1
    replace_layer(case, "recognized", [event(start=5, end=6)])
    with pytest.raises(ReviewCaseEvaluationError):
        evaluate_review_case(case)


def test_offset_diagnostic_does_not_clip_to_crop_or_audio_end():
    case = example("piano")
    case["reference"]["events"] = [event(end=7)]
    replace_layer(case, "recognized", [event(end=7)])
    replace_layer(case, "automatic", [event(end=9)])  # legitimate complete-bar padding
    replace_layer(case, "current", [event(end=9)])
    report = evaluate_review_case(seal(case))
    assert report["metrics"]["recognized"]["duration_diagnostics"]["f1"] == 1
    metric = report["metrics"]["automatic"]
    assert metric["f1"] == 1 and metric["duration_diagnostics"]["f1"] == 0
    assert metric["duration_diagnostics"]["wrong_offset_events"][0]["estimated_end"] == 9


@pytest.mark.parametrize("reference_pitch,estimated_pitch", [(35, 36), (44, 36), (33, 54), (70, 42)])
def test_raw_gm_classes_are_never_collapsed_and_unmatched_confusions_remain_diagnostic(reference_pitch, estimated_pitch):
    case = example("drums")
    case["reference"]["events"] = [event(pitch=reference_pitch)]
    replace_layer(case, "recognized", [event(pitch=estimated_pitch)])
    metric = evaluate_review_case(seal(case))["metrics"]["recognized"]
    assert metric["f1"] == 0 and metric["missing_notes"] == metric["extra_notes"] == 1
    assert metric["confusion_counts"] == {f"{reference_pitch}->{estimated_pitch}": 1}
    assert metric["confusion_diagnostics"]["heuristic_only"] is True


def test_true_simultaneous_kick_not_reused_as_missing_hat_confusion():
    case = example("drums")
    case["reference"]["events"] = [event("kick", pitch=36), event("hat", pitch=42)]
    replace_layer(case, "recognized", [event("kick", pitch=36)])
    metric = evaluate_review_case(seal(case))["metrics"]["recognized"]
    assert metric["matched_notes"] == 1 and metric["missing_notes"] == 1
    assert metric["possible_confusions"] == []


def test_distinct_unisons_preserved_but_one_prediction_matches_only_once():
    case = example("guitar")
    case["reference"]["events"] = [event("string-a"), event("string-b")]
    metric = evaluate_review_case(seal(case))["metrics"]["recognized"]
    assert metric["reference_notes"] == 2 and metric["matched_notes"] == 1 and metric["missing_notes"] == 1
    replace_layer(case, "recognized", [event("a"), event("b"), event("c")])
    metric = evaluate_review_case(case)["metrics"]["recognized"]
    assert metric["matched_notes"] == 2 and metric["extra_notes"] == 1


@pytest.mark.parametrize("target", ["reference", *LAYERS])
def test_duplicate_ids_rejected_within_list_not_across_layers(target):
    case = example()
    if target == "reference":
        case["reference"]["events"].append(event())
    else:
        replace_layer(case, target, [event(), event()])
    with pytest.raises(ReviewCaseEvaluationError):
        evaluate_review_case(seal(case))


@pytest.mark.parametrize("field,value", [("start", True), ("end", float("nan")), ("end", float("inf")),
    ("start", -1), ("end", 1.25), ("pitch", 60.0), ("pitch", True), ("pitch", 128),
    ("pitch", -1), ("amplitude", 0), ("amplitude", True), ("amplitude", 1.1), ("id", "../bad")])
def test_malformed_events_never_rely_on_ui_validation(field, value):
    case = example()
    case["reference"]["events"][0][field] = value
    with pytest.raises(ReviewCaseEvaluationError):
        # Some invalid values are intentionally rejected by canonical hashing first.
        evaluate_review_case(seal(case))


@pytest.mark.parametrize("mutation", [
    lambda c: c["snapshot"]["layers"]["recognized"]["events"][0].update(pitch=61),
    lambda c: c["snapshot"]["provenance"].update(notes_file_sha256="f" * 64),
    lambda c: c["reference"]["events"][0].update(pitch=61),
    lambda c: c["reference"].update(basis="Changed review basis"),
    lambda c: c.update(revision="9" * 32),
    lambda c: c.update(confirmation=None),
])
def test_mutated_frozen_or_confirmed_content_is_rejected(mutation):
    case = example()
    mutation(case)
    with pytest.raises(ReviewCaseEvaluationError):
        evaluate_review_case(case)


def test_seed_and_manual_edit_are_preserved_without_claiming_independent_model_accuracy():
    case = example()
    case["reference"]["seed_layer"] = "current"
    case["snapshot"]["score"]["edited"] = True
    report = evaluate_review_case(seal(case))
    assert report["metrics"]["current"]["manually_edited_estimate"] is True
    assert report["metrics"]["recognized"]["manually_edited_estimate"] is False
    assert report["independent_ground_truth"] is False
    assert "복사" in " ".join(report["warnings"])
    assert "winner" not in report and "aggregate" not in report and "accuracy" not in report


def test_annotations_bind_to_layer_and_real_frozen_ids_point_notes_allowed():
    case = example()
    note = {"id": "a", "kind": "pitch", "layer": "recognized", "event_id": "n1",
            "start": 1.25, "end": 1.25, "note": "실제 소리를 다시 확인"}
    case["annotations"] = [note]
    validate_review_case(case)
    note["event_id"] = "not-in-snapshot"
    with pytest.raises(ReviewCaseEvaluationError):
        validate_review_case(case)


@pytest.mark.parametrize("mutation", [
    lambda c: c.update(schema_version=True),
    lambda c: c["snapshot"].update(schema_version=True),
    lambda c: c.update(instrument="strings"),
    lambda c: c["snapshot"].update(instrument="guitar"),
    lambda c: c["snapshot"]["source"].update(verified=1),
    lambda c: c["snapshot"]["source"].update(file="../bass.wav"),
    lambda c: c["snapshot"]["layers"]["recognized"].update(total=0),
    lambda c: c["snapshot"]["layers"]["recognized"].update(in_window=True),
    lambda c: c["snapshot"]["provenance"].update(raw_artifact_id="bad-id"),
    lambda c: c["snapshot"]["provenance"]["inference"].update(engine="known-composition"),
    lambda c: c["snapshot"]["provenance"]["inference"].update(path="/private/model"),
    lambda c: c["reference"].update(seed_layer="imported"),
    lambda c: c.update(created_at="yesterday"),
    lambda c: c.update(updated_at="2026-10-06T07:00:00"),
])
def test_schema_provenance_counts_timestamps_strict(mutation):
    case = example()
    mutation(case)
    with pytest.raises(ReviewCaseEvaluationError):
        validate_review_case(seal(case))


def test_draft_cannot_retain_review_confirmation():
    case = example()
    case["status"] = "draft"
    with pytest.raises(ReviewCaseEvaluationError):
        evaluate_review_case(case)


def test_bounded_reference_and_annotations():
    case = example()
    case["reference"]["events"] = [event(str(index)) for index in range(MAX_EVENTS + 1)]
    with pytest.raises(ReviewCaseEvaluationError):
        validate_review_case(seal(case))
    case = example()
    case["annotations"] = [{}] * 301
    with pytest.raises(ReviewCaseEvaluationError):
        validate_review_case(case)


def test_export_detached_json_safe_preserves_all_hashes_without_audio_or_urls():
    case = example()
    before = deepcopy(case)
    exported = export_review_case(case)
    assert case == before
    assert exported["audio_included"] is False and exported["reference_status"] == "reviewer-confirmed"
    assert exported["case"] == case
    assert json.loads(json.dumps(exported, allow_nan=False))["case"] == case
    assert canonical_sha256(exported["case"]["snapshot"]) == case["snapshot_id"]
    assert canonical_sha256(exported["case"]["reference"]) == case["confirmation"]["reference_sha256"]
    assert '"audio"' not in json.dumps(exported) and '"original_url"' not in json.dumps(exported)
    exported["case"]["reference"]["events"][0]["pitch"] = 80
    exported["report"]["window"]["start"] = 4
    assert case == before


@pytest.mark.parametrize("target", ["case", "snapshot", "inference"])
def test_export_does_not_accept_unrecognized_audio_or_private_path_fields(target):
    case = example()
    destination = case if target == "case" else case["snapshot"] if target == "snapshot" else case["snapshot"]["provenance"]["inference"]
    destination["audio"] = {"url": "/private/audio.wav", "data": "unwanted bytes"}
    with pytest.raises(ReviewCaseEvaluationError):
        export_review_case(seal(case))


def test_old_raw_id_optional_but_full_file_hash_still_mandatory():
    case = example()
    case["snapshot"]["provenance"]["raw_artifact_id"] = None
    report = evaluate_review_case(seal(case))
    assert "artifact ID" in " ".join(report["warnings"])
    del case["snapshot"]["provenance"]["notes_file_sha256"]
    with pytest.raises(ReviewCaseEvaluationError):
        evaluate_review_case(seal(case))
