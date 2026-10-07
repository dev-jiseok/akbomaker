"""Offline regression tests use local silence bytes and authored event fixtures."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import wave

import pytest

from backend import review_case_regression as regression
from backend.review_case_evaluation import export_review_case
from backend.tests.test_review_case_evaluation import event, example, replace_layer, seal


def json_file(path, document):
    path.write_text(json.dumps(document, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def inputs(tmp_path, inst="bass", *, reviewed=True):
    audio = tmp_path / f"{inst}.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\0\0" * 64000)
    source_hash = hashlib.sha256(audio.read_bytes()).hexdigest()
    case = example(inst, reviewed=reviewed)
    case["snapshot"]["source"]["sha256"] = source_hash
    seal(case, reviewed=reviewed)
    exported = export_review_case(case)
    document = {
        "schema": "akbo.pre-quantization-notes", "schema_version": 1, "artifact_id": "a" * 32,
        "instrument": inst, "duration": 8, "event_fields": ["start", "end", "pitch", "amplitude"],
        "event_count": 1, "events": [[1.25, 1.75, 42 if inst == "drums" else 60, .8]],
        "source": {"kind": "stem", "file": f"{inst}.wav", "instrument": inst, "sha256": source_hash},
        "provenance": {"engine": "candidate-engine"},
        "semantics": {"stage": "input-to-notation-after-instrument-postprocessing", "reflects_manual_score_edits": False,
                      "time_unit": "seconds", "time_origin": "source-audio-start-before-score-offset",
                      "ground_truth": False, "confidence_available": False},
    }
    case_path, prediction = tmp_path / "review.json", tmp_path / "candidate.notes.json"
    json_file(case_path, exported)
    json_file(prediction, document)
    return case_path, prediction, audio, exported, document


@pytest.mark.parametrize("inst", ["vocal", "bass", "drums", "synthesizer", "guitar", "piano"])
def test_all_six_offline_same_input_baseline_candidate_and_hashes(tmp_path, inst):
    case_path, prediction, audio, exported, _ = inputs(tmp_path, inst)
    before = {path: path.read_bytes() for path in (case_path, prediction, audio)}
    report = regression.evaluate_regression(case_path, prediction, audio)
    assert report["same_input_only"] is True and report["inference_executed"] is False
    assert report["independent_ground_truth"] is False
    assert report["candidate"]["metrics"]["f1"] == report["baseline"]["metrics"]["f1"] == 1
    assert report["deltas"]["f1"] == 0
    assert report["hashes"]["reference_sha256"] == exported["case"]["confirmation"]["reference_sha256"]
    assert report["hashes"]["snapshot_sha256"] == exported["case"]["snapshot_id"]
    for path, key in ((case_path, "case_export_sha256"), (prediction, "candidate_notes_file_sha256"), (audio, "source_audio_sha256")):
        assert report["hashes"][key] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert {path: path.read_bytes() for path in before} == before
    assert len(report["hashes"]["code_sha256"]) == 7
    assert "winner" not in report and "accuracy" not in report
    if inst == "drums":
        assert "duration_diagnostics" not in report["candidate"]["metrics"]
        assert "median_offset_error_ms" not in report["candidate"]["metrics"]


def test_draft_export_refused_even_if_top_level_is_forged_reviewed(tmp_path):
    case_path, prediction, audio, exported, _ = inputs(tmp_path, reviewed=False)
    with pytest.raises(ValueError):
        regression.evaluate_regression(case_path, prediction, audio)
    exported["reference_status"] = "reviewer-confirmed"
    json_file(case_path, exported)
    with pytest.raises(ValueError, match="초안"):
        regression.evaluate_regression(case_path, prediction, audio)


def test_unreviewed_or_modified_reference_confirmation_rejected(tmp_path):
    case_path, prediction, audio, exported, _ = inputs(tmp_path)
    exported["case"]["reference"]["events"][0]["pitch"] = 61
    json_file(case_path, exported)
    with pytest.raises(ValueError):
        regression.evaluate_regression(case_path, prediction, audio)


def test_empty_reviewed_reference_has_no_false_perfect_score_or_f1_delta(tmp_path):
    case_path, prediction, audio, exported, document = inputs(tmp_path)
    case = exported["case"]
    case["reference"]["events"] = []
    json_file(case_path, export_review_case(seal(case)))
    report = regression.evaluate_regression(case_path, prediction, audio)
    assert report["reference_present"] is False and report["deltas"]["f1"] is None
    assert report["candidate"]["metrics"]["extra_notes"] == 1
    document.update(events=[], event_count=0)
    json_file(prediction, document)
    report = regression.evaluate_regression(case_path, prediction, audio)
    assert report["candidate"]["metrics"]["f1"] == 0
    assert report["deltas"]["extra_notes"] == -1


def test_recomputes_baseline_instead_of_trusting_exported_scores(tmp_path):
    case_path, prediction, audio, exported, _ = inputs(tmp_path)
    exported["report"] = {"metrics": {"recognized": {"f1": 0}}}
    json_file(case_path, exported)
    assert regression.evaluate_regression(case_path, prediction, audio)["baseline"]["metrics"]["f1"] == 1


def test_source_time_crop_does_not_invent_attack_or_clip_note_end(tmp_path):
    case_path, prediction, audio, _, document = inputs(tmp_path)
    document.update(events=[[.5, 2, 60, .8], [1.25, 7, 60, .8], [5, 6, 60, .8]], event_count=3)
    json_file(prediction, document)
    report = regression.evaluate_regression(case_path, prediction, audio)
    metric = report["candidate"]["metrics"]
    assert metric["estimated_notes"] == 1 and metric["matched_notes"] == 1
    assert metric["duration_diagnostics"]["wrong_offset_events"][0]["estimated_end"] == 7
    assert report["deltas"]["duration_f1"] == -1


def test_raw_gm_drum_candidate_not_collapsed_to_notation_kit(tmp_path):
    case_path, prediction, audio, _, document = inputs(tmp_path, "drums")
    document["events"][0][2] = 36
    json_file(prediction, document)
    metric = regression.evaluate_regression(case_path, prediction, audio)["candidate"]["metrics"]
    assert metric["confusion_counts"] == {"42->36": 1}
    assert metric["missing_notes"] == metric["extra_notes"] == 1


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(instrument="guitar"),
    lambda d: d.update(duration=9),
    lambda d: d.update(schema_version=True),
    lambda d: d.update(event_count=2),
    lambda d: d["source"].update(sha256="f" * 64),
    lambda d: d["source"].update(kind="original", file="original.wav", instrument=None),
    lambda d: d["semantics"].update(stage="after-quantization"),
    lambda d: d["semantics"].update(time_origin="crop-start"),
    lambda d: d["semantics"].update(reflects_manual_score_edits=True),
    lambda d: d["semantics"].update(ground_truth=True),
    lambda d: d.update(events=[[1, 2, True, .8]]),
    lambda d: d.update(artifact_id="not-an-id"),
])
def test_mismatched_artifact_and_semantics_rejected(tmp_path, mutation):
    case_path, prediction, audio, _, document = inputs(tmp_path)
    mutation(document)
    json_file(prediction, document)
    with pytest.raises(ValueError):
        regression.evaluate_regression(case_path, prediction, audio)


def test_actual_audio_hash_must_match_both_records(tmp_path):
    case_path, prediction, audio, _, _ = inputs(tmp_path)
    with audio.open("ab") as stream:
        stream.write(b"different source")
    with pytest.raises(ValueError, match="WAV 해시"):
        regression.evaluate_regression(case_path, prediction, audio)


@pytest.mark.parametrize("malformed", ['{"schema":1,"schema":2}', '{"x":NaN}', '{"x":1e999}', '[]'])
def test_strict_json_rejects_duplicate_or_nonfinite_data(tmp_path, malformed):
    case_path, prediction, audio, _, _ = inputs(tmp_path)
    prediction.write_text(malformed)
    with pytest.raises(ValueError):
        regression.evaluate_regression(case_path, prediction, audio)


@pytest.mark.parametrize("input_index", [0, 1, 2])
def test_no_symlink_inputs(tmp_path, input_index):
    case_path, prediction, audio, _, _ = inputs(tmp_path)
    paths = [case_path, prediction, audio]
    link = tmp_path / "linked"
    link.symlink_to(paths[input_index])
    paths[input_index] = link
    with pytest.raises(ValueError, match="심볼릭"):
        regression.evaluate_regression(*paths)


def test_directory_and_oversized_file_rejected_before_parse(tmp_path):
    case_path, prediction, audio, _, _ = inputs(tmp_path)
    with pytest.raises(ValueError):
        regression.evaluate_regression(case_path, tmp_path, audio)
    with prediction.open("wb") as stream:
        stream.truncate(regression.MAX_ARTIFACT_BYTES + 1)
    with pytest.raises(ValueError, match="크기"):
        regression.evaluate_regression(case_path, prediction, audio)


def test_non_wav_with_matching_hash_is_not_accepted(tmp_path):
    case_path, prediction, audio, exported, document = inputs(tmp_path)
    audio.write_bytes(b"not a WAV")
    digest = hashlib.sha256(audio.read_bytes()).hexdigest()
    exported["case"]["snapshot"]["source"]["sha256"] = digest
    json_file(case_path, export_review_case(seal(exported["case"])))
    document["source"]["sha256"] = digest
    json_file(prediction, document)
    with pytest.raises(ValueError, match="WAV"):
        regression.evaluate_regression(case_path, prediction, audio)


def test_midrun_input_drift_refused(tmp_path, monkeypatch):
    case_path, prediction, audio, _, _ = inputs(tmp_path)
    original = regression.compare_events
    def compare(*args, **kwargs):
        prediction.write_bytes(prediction.read_bytes() + b" ")
        return original(*args, **kwargs)
    monkeypatch.setattr(regression, "compare_events", compare)
    with pytest.raises(ValueError, match="변경"):
        regression.evaluate_regression(case_path, prediction, audio)


def test_midrun_code_drift_refused(tmp_path, monkeypatch):
    case_path, prediction, audio, _, _ = inputs(tmp_path)
    snapshots = iter([{"evaluator": "a"}, {"evaluator": "b"}])
    monkeypatch.setattr(regression, "_code_hashes", lambda: next(snapshots))
    with pytest.raises(ValueError, match="코드"):
        regression.evaluate_regression(case_path, prediction, audio)


def test_code_drift_between_startup_and_module_load_is_refused_before_inputs(tmp_path):
    with pytest.raises(ValueError, match="불러오는"):
        regression.evaluate_regression(tmp_path / "not-read.json", tmp_path / "not-read.notes.json", tmp_path / "not-read.wav",
                                       expected_code_hashes={"outdated-evaluator": "a" * 64})


def test_output_is_exclusive_and_rejects_symlinks(tmp_path):
    output = tmp_path / "report.json"
    regression.write_new_report(output, {"value": 1})
    before = output.read_bytes()
    with pytest.raises(FileExistsError):
        regression.write_new_report(output, {"value": 2})
    assert output.read_bytes() == before
    link = tmp_path / "linked.json"
    link.symlink_to(output)
    with pytest.raises(ValueError):
        regression.write_new_report(link, {})
    assert output.read_bytes() == before


def test_cli_success_and_refusal_to_overwrite(tmp_path):
    case_path, prediction, audio, _, _ = inputs(tmp_path)
    output = tmp_path / "report.json"
    root = Path(__file__).resolve().parents[2]
    args = [sys.executable, str(root / "scripts/evaluate-review-case.py"), "--case", str(case_path),
            "--prediction", str(prediction), "--audio", str(audio), "--output", str(output)]
    result = subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text())["inference_executed"] is False
    before = output.read_bytes()
    repeated = subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=20)
    assert repeated.returncode == 2 and output.read_bytes() == before


def test_cli_failure_does_not_emit_report(tmp_path):
    case_path, prediction, audio, _, _ = inputs(tmp_path, reviewed=False)
    output = tmp_path / "report.json"
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, str(root / "scripts/evaluate-review-case.py"), "--case", str(case_path),
                             "--prediction", str(prediction), "--audio", str(audio), "--output", str(output)],
                            cwd=root, capture_output=True, text=True, timeout=20)
    assert result.returncode == 2 and not output.exists()
