"""Offline MulTTiPop audit/evaluation contract; no models or network are used.

The tiny MIDI and silent WAV files are wiring fixtures, not accuracy evidence.
All reference-based prediction construction below is deliberately test-only.
"""
import copy
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path

import mido
import numpy as np
import pytest
import soundfile as sf

from backend.config import INSTRUMENTS
from backend.multtipop import DATASET_REVISION


def write_json(path, document):
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def benchmark():
    return importlib.import_module("backend.multtipop_evaluation")


@pytest.fixture
def fixture(tmp_path):
    """One second, six independently mapped parts, explicit empty-capable output."""
    midi = mido.MidiFile(type=1, ticks_per_beat=480)
    assignments = {}
    pitches = dict(vocal=67, bass=40, drums=35, synthesizer=72, guitar=60, piano=55)
    programs = dict(vocal=65, bass=33, synthesizer=81, guitar=25, piano=0)
    channels = dict(vocal=0, bass=1, drums=9, synthesizer=3, guitar=4, piano=5)
    for index, inst in enumerate(INSTRUMENTS):
        track = mido.MidiTrack()
        midi.tracks.append(track)
        channel = channels[inst]
        track.append(mido.MetaMessage("track_name", name=inst, time=0))
        if inst != "drums":
            track.append(mido.Message("program_change", channel=channel, program=programs[inst], time=0))
        track.append(mido.Message("note_on", channel=channel, note=pitches[inst], velocity=100, time=96))
        track.append(mido.Message("note_off", channel=channel, note=pitches[inst], velocity=0, time=384))
        part = f"t{index}:c{channel}:" + ("drums" if inst == "drums" else f"p{programs[inst]}")
        assignments[part] = {"instrument": inst, "reason": "Test fixture assignment; not a real recording review"}
        if inst == "vocal":
            assignments[part]["role"] = "lead"
    midi_path = tmp_path / "aligned.mid"
    midi.save(midi_path)
    metadata = {"id": "fixture", "split_name": "dev", "audio_length": 1.,
                "aligned_midi_checksum": sha256(midi_path),
                "youtube": {"ytid": "abcdefghijk", "timestampType": "absolute", "start": 12., "end": 13.}}
    mapping = {"schema": "akbo.multtipop.mapping", "schema_version": 1, "case_id": "fixture",
               "midi_sha256": sha256(midi_path), "reviewed": True, "reviewer": "test-reviewer",
               "assignments": assignments}
    write_json(tmp_path / "meta.json", metadata)
    write_json(tmp_path / "mapping.json", mapping)
    sf.write(tmp_path / "mix.wav", np.zeros(8000, dtype=np.float32), 8000)
    case = {"id": "fixture", "midi": "aligned.mid", "metadata": "meta.json", "mapping": "mapping.json",
            "audio": "mix.wav", "audio_sha256": sha256(tmp_path / "mix.wav"),
            "audio_rights_confirmed": True, "audio_alignment_reviewed": True}
    manifest = {"schema": "akbo.multtipop.manifest", "schema_version": 1, "dataset": "MulTTiPop",
                "revision": DATASET_REVISION, "partition": "dev", "cases": [case]}
    manifest_path = write_json(tmp_path / "manifest.json", manifest)
    instruments = {}
    notes = {}
    for inst in INSTRUMENTS:
        stem_path = tmp_path / f"{inst}.wav"
        sf.write(stem_path, np.zeros(8000, dtype=np.float32), 8000)
        artifact = {
            "schema": "akbo.pre-quantization-notes", "schema_version": 1,
            "artifact_id": f"fixture-{inst}", "instrument": inst, "duration": 1., "event_count": 1,
            "events": [[.1, .5, pitches[inst], 100 / 127]],
            "event_fields": ["start", "end", "pitch", "amplitude"],
            "source": {"file": stem_path.name, "kind": "stem", "instrument": inst, "sha256": sha256(stem_path)},
            "provenance": {"engine": "mock-offline-test", "profile": "instrument"},
            "semantics": {"stage": "input-to-notation-after-instrument-postprocessing", "time_unit": "seconds",
                          "time_origin": "source-audio-start-before-score-offset", "pitch": "MIDI note; GM percussion for drums",
                          "confidence_available": False, "ground_truth": False, "reflects_manual_score_edits": False},
        }
        notes[inst] = artifact
        write_json(tmp_path / f"{inst}.notes.json", artifact)
        instruments[inst] = {"notes": f"{inst}.notes.json", "audio": stem_path.name}
    prediction = {"id": "fixture", "mix_sha256": case["audio_sha256"], "pipeline": "sam-separated-stems",
                  "separation_reviewed": True,
                  "separation_provenance": {"engine": "sam-audio", "model_revision": "test-only", "mode": "sequential-residual"},
                  "instruments": instruments}
    predictions = {"schema": "akbo.multtipop.predictions", "schema_version": 1, "cases": [prediction]}
    predictions_path = write_json(tmp_path / "predictions.json", predictions)
    return {"root": tmp_path, "manifest": manifest, "manifest_path": manifest_path, "case": case,
            "metadata": metadata, "mapping": mapping, "predictions": predictions, "prediction": prediction,
            "predictions_path": predictions_path, "notes": notes}


def save_fixture(fixture):
    root = fixture["root"]
    write_json(fixture["manifest_path"], fixture["manifest"])
    write_json(root / "meta.json", fixture["metadata"])
    write_json(root / "mapping.json", fixture["mapping"])
    write_json(fixture["predictions_path"], fixture["predictions"])
    for inst, artifact in fixture["notes"].items():
        write_json(root / f"{inst}.notes.json", artifact)


def add_second_case(fixture):
    """Test-only duplicate content, with its own correctly linked case identity."""
    root = fixture["root"]
    (root / "second").mkdir()
    (root / "second/aligned.mid").write_bytes((root / "aligned.mid").read_bytes())
    metadata = copy.deepcopy(fixture["metadata"])
    metadata["id"] = "second"
    write_json(root / "second/meta.json", metadata)
    mapping = copy.deepcopy(fixture["mapping"])
    mapping["case_id"] = "second"
    write_json(root / "second/mapping.json", mapping)
    case = copy.deepcopy(fixture["case"])
    case.update(id="second", midi="second/aligned.mid", metadata="second/meta.json", mapping="second/mapping.json")
    fixture["manifest"]["cases"].append(case)
    prediction = copy.deepcopy(fixture["prediction"])
    prediction["id"] = "second"
    fixture["predictions"]["cases"].append(prediction)
    return case, prediction


def assert_blocked(report):
    assert report["status"] == "blocked"
    assert report.get("aggregate") is None
    blocked = [case for case in report["cases"] if case["status"] == "blocked"]
    assert blocked
    for case in blocked:
        assert case["issues"]
        assert all(isinstance(issue["code"], str) and issue["code"] and isinstance(issue["message"], str)
                   and issue["message"] for issue in case["issues"])
        assert case.get("metrics") is None


def test_ready_audit_preserves_reference_coverage_and_does_not_write(benchmark, fixture, monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket, "connect", lambda *args, **kwargs: pytest.fail("Audit must stay offline"))
    before = {path.name: path.read_bytes() for path in fixture["root"].iterdir()}
    report = benchmark.audit_manifest(fixture["manifest_path"])
    assert report["schema"] == "akbo.multtipop.audit" and report["schema_version"] == 1
    assert report["status"] == "ready"
    assert report["summary"] == {"total_cases": 1, "ready_cases": 1, "blocked_cases": 0}
    assert report["cases"][0]["reference_notes"] == {inst: 1 for inst in INSTRUMENTS}
    assert report["cases"][0]["duration"] == 1.
    assert {path.name: path.read_bytes() for path in fixture["root"].iterdir()} == before


@pytest.mark.parametrize("key,value", [
    ("schema", "other"), ("schema_version", 2), ("schema_version", True), ("dataset", "other"),
    ("revision", "main"), ("partition", "test"), ("cases", []), ("cases", {}),
])
def test_invalid_top_level_manifest_is_rejected(benchmark, fixture, key, value):
    fixture["manifest"][key] = value
    save_fixture(fixture)
    with pytest.raises(ValueError):
        benchmark.audit_manifest(fixture["manifest_path"])


@pytest.mark.parametrize("key,value", [
    ("audio_rights_confirmed", False), ("audio_rights_confirmed", 1),
    ("audio_alignment_reviewed", False), ("audio_alignment_reviewed", "true"),
    ("audio_sha256", "0" * 64), ("audio", "missing.wav"),
])
def test_audio_review_and_hash_are_required_not_zero_f1(benchmark, fixture, key, value):
    fixture["case"][key] = value
    save_fixture(fixture)
    assert_blocked(benchmark.audit_manifest(fixture["manifest_path"]))


@pytest.mark.parametrize("key,value", [
    ("schema", "other"), ("schema_version", 2), ("case_id", "wrong-case"),
    ("midi_sha256", "0" * 64), ("reviewed", False), ("reviewed", 1), ("reviewer", "  "),
    ("assignments", {}),
])
def test_mapping_requires_complete_reviewed_source_link(benchmark, fixture, key, value):
    fixture["mapping"][key] = value
    save_fixture(fixture)
    assert_blocked(benchmark.audit_manifest(fixture["manifest_path"]))


def test_vocal_role_is_checked_by_real_adapter(benchmark, fixture):
    del fixture["mapping"]["assignments"]["t0:c0:p65"]["role"]
    save_fixture(fixture)
    assert_blocked(benchmark.audit_manifest(fixture["manifest_path"]))


@pytest.mark.parametrize("key", ["midi", "metadata", "mapping"])
def test_reference_paths_cannot_escape_manifest_root(benchmark, fixture, key):
    fixture["case"][key] = "../" + fixture["case"][key]
    save_fixture(fixture)
    assert_blocked(benchmark.audit_manifest(fixture["manifest_path"]))


def test_manifest_audio_may_be_explicit_absolute_local_path(benchmark, fixture):
    fixture["case"]["audio"] = str(fixture["root"] / "mix.wav")
    save_fixture(fixture)
    assert benchmark.audit_manifest(fixture["manifest_path"])["status"] == "ready"


@pytest.mark.parametrize("length,samplerate,channels", [(8500, 8000, 1), (8000, 8000, 3), (8000, 192001, 1)])
def test_audio_bounds_and_duration_are_checked(benchmark, fixture, length, samplerate, channels):
    samples = np.zeros(length if channels == 1 else (length, channels), dtype=np.float32)
    sf.write(fixture["root"] / "mix.wav", samples, samplerate)
    fixture["case"]["audio_sha256"] = sha256(fixture["root"] / "mix.wav")
    save_fixture(fixture)
    assert_blocked(benchmark.audit_manifest(fixture["manifest_path"]))


def test_nonfinite_audio_is_rejected_even_when_its_hash_matches(benchmark, fixture):
    samples = np.zeros(8000, dtype=np.float32)
    samples[4000] = np.nan
    sf.write(fixture["root"] / "mix.wav", samples, 8000, subtype="FLOAT")
    fixture["case"]["audio_sha256"] = sha256(fixture["root"] / "mix.wav")
    save_fixture(fixture)
    assert_blocked(benchmark.audit_manifest(fixture["manifest_path"]))


def test_complete_evaluation_uses_raw_gm_without_kit_alias_collapse(benchmark, fixture):
    # GM35 and GM36 are different references. Collapsing them would hide an error.
    fixture["notes"]["drums"]["events"][0][2] = 36
    save_fixture(fixture)
    report = benchmark.evaluate_predictions(fixture["manifest_path"], fixture["predictions_path"])
    assert report["schema"] == "akbo.multtipop.evaluation" and report["status"] == "complete"
    assert report["cases"][0]["status"] == "complete"
    assert set(report["aggregate"]["by_instrument"]) == set(INSTRUMENTS)
    metrics = report["cases"][0]["metrics"]
    assert metrics["drums"]["matched_notes"] == 0 and metrics["drums"]["f1"] == 0
    assert metrics["drums"]["reference_notes"] == metrics["drums"]["estimated_notes"] == 1
    assert all(metrics[inst]["f1"] == 1 for inst in INSTRUMENTS if inst != "drums")


def test_explicit_empty_artifact_is_a_real_missing_note_score(benchmark, fixture):
    fixture["notes"]["guitar"].update(events=[], event_count=0)
    save_fixture(fixture)
    report = benchmark.evaluate_predictions(fixture["manifest_path"], fixture["predictions_path"])
    assert report["status"] == "complete"
    metrics = report["cases"][0]["metrics"]["guitar"]
    assert metrics["reference_notes"] == 1 and metrics["estimated_notes"] == 0 and metrics["f1"] == 0


@pytest.mark.parametrize("key,value", [
    ("mix_sha256", "0" * 64), ("pipeline", "original-audio-fallback"),
    ("separation_reviewed", False), ("separation_reviewed", 1),
    ("separation_provenance", {}), ("separation_provenance", {"engine": "other-separator"}),
])
def test_predictions_require_reviewed_sam_source_chain(benchmark, fixture, key, value):
    fixture["prediction"][key] = value
    save_fixture(fixture)
    assert_blocked(benchmark.evaluate_predictions(fixture["manifest_path"], fixture["predictions_path"]))


@pytest.mark.parametrize("mode", ["missing-case", "extra-case", "duplicate-case", "missing-instrument", "extra-instrument"])
def test_no_partial_scores_for_prediction_case_or_instrument_mismatch(benchmark, fixture, mode):
    if mode == "missing-case":
        fixture["predictions"]["cases"] = []
    elif mode in {"extra-case", "duplicate-case"}:
        extra = copy.deepcopy(fixture["prediction"])
        if mode == "extra-case":
            extra["id"] = "unexpected"
        fixture["predictions"]["cases"].append(extra)
    elif mode == "missing-instrument":
        del fixture["prediction"]["instruments"]["piano"]
    else:
        fixture["prediction"]["instruments"]["violin"] = fixture["prediction"]["instruments"]["piano"]
    save_fixture(fixture)
    if mode in {"missing-case", "extra-case", "duplicate-case"}:
        with pytest.raises(ValueError):
            benchmark.evaluate_predictions(fixture["manifest_path"], fixture["predictions_path"])
    else:
        assert_blocked(benchmark.evaluate_predictions(fixture["manifest_path"], fixture["predictions_path"]))


@pytest.mark.parametrize("field,value", [
    ("schema", "notation-after-quantization"), ("schema_version", 2), ("schema_version", True),
    ("instrument", "piano"), ("duration", 2.), ("event_count", 999),
    ("events", [[.1, .5, 60.5, .8]]), ("events", [[-.1, .5, 60, .8]]),
    ("events", [[.1, 1.1, 60, .8]]), ("events", [[.1, .5, 60, float("nan")]]),
])
def test_note_artifact_contract_is_validated(benchmark, fixture, field, value):
    fixture["notes"]["guitar"][field] = value
    save_fixture(fixture)
    assert_blocked(benchmark.evaluate_predictions(fixture["manifest_path"], fixture["predictions_path"]))


@pytest.mark.parametrize("section,key,value", [
    ("source", "kind", "original"), ("source", "instrument", "piano"), ("source", "sha256", "0" * 64),
    ("provenance", "engine", ""), ("semantics", "stage", "quantized-score"),
    ("semantics", "time_origin", "score-offset-applied"), ("semantics", "time_unit", "beats"),
])
def test_note_source_and_clock_cannot_be_relabelled(benchmark, fixture, section, key, value):
    fixture["notes"]["guitar"][section][key] = value
    save_fixture(fixture)
    assert_blocked(benchmark.evaluate_predictions(fixture["manifest_path"], fixture["predictions_path"]))


@pytest.mark.parametrize("artifact", ["notes", "audio"])
def test_prediction_artifact_paths_are_confined(benchmark, fixture, artifact):
    fixture["prediction"]["instruments"]["guitar"][artifact] = "../outside.json" if artifact == "notes" else "../outside.wav"
    save_fixture(fixture)
    assert_blocked(benchmark.evaluate_predictions(fixture["manifest_path"], fixture["predictions_path"]))


@pytest.mark.parametrize("field,value", [("id", "different"), ("split_name", "test"), ("aligned_midi_checksum", "0" * 64)])
def test_reference_metadata_identity_split_and_integrity_are_checked(benchmark, fixture, field, value):
    fixture["metadata"][field] = value
    save_fixture(fixture)
    assert_blocked(benchmark.audit_manifest(fixture["manifest_path"]))


def test_symlink_directory_cannot_escape_manifest_root(benchmark, fixture, tmp_path):
    external = tmp_path.parent / (tmp_path.name + "-external")
    external.mkdir()
    write_json(external / "mapping.json", fixture["mapping"])
    (fixture["root"] / "linked").symlink_to(external, target_is_directory=True)
    fixture["case"]["mapping"] = "linked/mapping.json"
    save_fixture(fixture)
    report = benchmark.audit_manifest(fixture["manifest_path"])
    assert_blocked(report)
    assert any(issue["code"] == "unsafe_path" for issue in report["cases"][0]["issues"])


def test_audio_duration_difference_under_50_ms_is_accepted_for_alignment(benchmark, fixture):
    sf.write(fixture["root"] / "mix.wav", np.zeros(8320, dtype=np.float32), 8000)
    fixture["case"]["audio_sha256"] = sha256(fixture["root"] / "mix.wav")
    save_fixture(fixture)
    assert benchmark.audit_manifest(fixture["manifest_path"])["status"] == "ready"


def test_onsets_do_not_hide_bad_pitched_offsets(benchmark, fixture):
    fixture["notes"]["guitar"]["events"][0][1] = .2
    save_fixture(fixture)
    report = benchmark.evaluate_predictions(fixture["manifest_path"], fixture["predictions_path"])
    metrics = report["cases"][0]["metrics"]["guitar"]
    assert metrics["f1"] == 1
    assert metrics["duration_diagnostics"]["f1"] == 0
    assert report["aggregate"]["by_instrument"]["guitar"]["duration_diagnostics"]["f1"] == 0


@pytest.mark.parametrize("section,key,value", [
    ("semantics", "ground_truth", True), ("semantics", "reflects_manual_score_edits", True),
    ("source", "file", "original.wav"),
])
def test_edited_or_ground_truth_outputs_cannot_claim_unedited_predictions(benchmark, fixture, section, key, value):
    fixture["notes"]["guitar"][section][key] = value
    save_fixture(fixture)
    assert_blocked(benchmark.evaluate_predictions(fixture["manifest_path"], fixture["predictions_path"]))


def test_prediction_notes_duration_must_match_its_own_audio(benchmark, fixture):
    # This remains within reference tolerance but must not falsify its source clock.
    fixture["notes"]["guitar"]["duration"] = 1.04
    save_fixture(fixture)
    assert_blocked(benchmark.evaluate_predictions(fixture["manifest_path"], fixture["predictions_path"]))


def test_duplicate_json_key_is_not_silently_overwritten(benchmark, fixture):
    fixture["manifest_path"].write_text('{"schema":"bad","schema":"akbo.multtipop.manifest"}', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        benchmark.audit_manifest(fixture["manifest_path"])


@pytest.fixture
def cli():
    script = Path(__file__).resolve().parents[2] / "scripts/benchmark-multtipop.py"
    spec = importlib.util.spec_from_file_location("multtipop_benchmark_cli_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_writes_new_audit_report_without_claiming_accuracy(cli, fixture):
    output = fixture["root"] / "report.json"
    report = cli.main(["--manifest", str(fixture["manifest_path"]), "--output", str(output)])
    assert report["status"] == "ready" and report["schema"] == "akbo.multtipop.audit"
    assert "aggregate" not in report and "metrics" not in report["cases"][0]
    assert set(report["evaluator_source_sha256"]) == {
        "backend/multtipop.py", "backend/multtipop_evaluation.py", "backend/evaluation.py", "scripts/benchmark-multtipop.py"}
    assert json.loads(output.read_text()) == report


def test_cli_never_overwrites_existing_evidence(cli, fixture, monkeypatch):
    output = fixture["root"] / "existing.json"
    output.write_text("preserve previous diagnosis", encoding="utf-8")
    monkeypatch.setattr(cli, "audit_manifest", lambda *args: pytest.fail("No audit after overwrite guard"))
    with pytest.raises(SystemExit) as error:
        cli.main(["--manifest", str(fixture["manifest_path"]), "--output", str(output)])
    assert error.value.code == 2
    assert output.read_text() == "preserve previous diagnosis"


def test_cli_refuses_report_if_evaluator_code_changes_during_audit(cli, fixture, monkeypatch):
    output = fixture["root"] / "not-written.json"
    calls = []
    def changing_hash(path):
        calls.append(path)
        return "a" * 64 if len(calls) <= 4 else "b" * 64
    monkeypatch.setattr(cli, "file_sha256", changing_hash)
    with pytest.raises(RuntimeError, match="code changed"):
        cli.main(["--manifest", str(fixture["manifest_path"]), "--output", str(output)])
    assert not output.exists()


def test_cli_can_write_blocked_audit_without_fabricating_zero_metrics(cli, fixture):
    fixture["case"]["audio_alignment_reviewed"] = False
    save_fixture(fixture)
    output = fixture["root"] / "blocked.json"
    report = cli.main(["--manifest", str(fixture["manifest_path"]), "--output", str(output)])
    assert_blocked(report)
    assert report["summary"] == {"total_cases": 1, "ready_cases": 0, "blocked_cases": 1}
    assert json.loads(output.read_text())["status"] == "blocked"


def test_any_blocked_case_prevents_partial_aggregate(benchmark, fixture):
    second, _ = add_second_case(fixture)
    second["audio_alignment_reviewed"] = False
    save_fixture(fixture)
    report = benchmark.evaluate_predictions(fixture["manifest_path"], fixture["predictions_path"])
    assert_blocked(report)
    assert report["summary"] == {"total_cases": 2, "ready_cases": 1, "blocked_cases": 1}
    assert report["cases"][0]["status"] == "complete"
    assert report["cases"][1]["status"] == "blocked" and "metrics" not in report["cases"][1]


def test_aggregate_sums_note_counts_instead_of_averaging_clip_f1(benchmark, fixture):
    _, prediction = add_second_case(fixture)
    notes = copy.deepcopy(fixture["notes"]["guitar"])
    notes["events"].extend([[.2, .5, 61, .8], [.3, .5, 62, .8]])
    notes["event_count"] = 3
    write_json(fixture["root"] / "second/guitar.notes.json", notes)
    prediction["instruments"]["guitar"]["notes"] = "second/guitar.notes.json"
    save_fixture(fixture)
    report = benchmark.evaluate_predictions(fixture["manifest_path"], fixture["predictions_path"])
    assert report["status"] == "complete"
    assert report["cases"][0]["metrics"]["guitar"]["f1"] == 1
    assert report["cases"][1]["metrics"]["guitar"]["f1"] == .5
    aggregate = report["aggregate"]["by_instrument"]["guitar"]
    assert aggregate["reference_notes"] == 2 and aggregate["estimated_notes"] == 4 and aggregate["matched_notes"] == 2
    assert aggregate["precision"] == .5 and aggregate["recall"] == 1
    assert aggregate["f1"] == pytest.approx(2 / 3) and aggregate["f1"] != .75


def test_evaluation_is_offline_and_read_only(benchmark, fixture, monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket, "connect", lambda *args, **kwargs: pytest.fail("Evaluation must stay offline"))
    before = {path.name: path.read_bytes() for path in fixture["root"].iterdir()}
    report = benchmark.evaluate_predictions(fixture["manifest_path"], fixture["predictions_path"])
    assert report["status"] == "complete"
    assert "execution chain not independently verified" in report["pipeline_verification"]
    assert {path.name: path.read_bytes() for path in fixture["root"].iterdir()} == before


@pytest.mark.parametrize("document", ["manifest", "predictions"])
@pytest.mark.parametrize("mutation_timing", ["before-read", "after-read"])
def test_top_document_mutation_around_json_read_is_detected(benchmark, fixture, monkeypatch, document, mutation_timing):
    target = fixture[f"{document}_path"]
    original_read = benchmark._json
    changed = False
    def mutate():
        nonlocal changed
        replacement = copy.deepcopy(fixture[document])
        replacement["test_mutation_marker"] = "changed after original digest was computed"
        write_json(target, replacement)
        changed = True
    def read_with_mutation(path):
        if Path(path) == target and mutation_timing == "before-read":
            mutate()
        result = original_read(path)
        if Path(path) == target and mutation_timing == "after-read":
            mutate()
        return result
    monkeypatch.setattr(benchmark, "_json", read_with_mutation)
    with pytest.raises(benchmark.AuditError) as error:
        if document == "manifest":
            benchmark.audit_manifest(fixture["manifest_path"])
        else:
            benchmark.evaluate_predictions(fixture["manifest_path"], fixture["predictions_path"])
    assert changed and error.value.code == "input_changed"


@pytest.mark.parametrize("operation,document", [("audit", "manifest"), ("evaluate", "manifest"), ("evaluate", "predictions")])
def test_top_document_changed_after_case_audit_prevents_final_report(benchmark, fixture, monkeypatch, operation, document):
    original_audit = benchmark._audit_case
    def audit_then_change(root, case):
        result = original_audit(root, case)
        replacement = copy.deepcopy(fixture[document])
        replacement["test_mutation_marker"] = "changed after parsed document had been used"
        write_json(fixture[f"{document}_path"], replacement)
        return result
    monkeypatch.setattr(benchmark, "_audit_case", audit_then_change)
    with pytest.raises(benchmark.AuditError) as error:
        if operation == "audit":
            benchmark.audit_manifest(fixture["manifest_path"])
        else:
            benchmark.evaluate_predictions(fixture["manifest_path"], fixture["predictions_path"])
    assert error.value.code == "input_changed"


@pytest.mark.parametrize("field", ["midi_sha256", "metadata_sha256"])
def test_preparation_reference_hash_mismatch_blocks_case(benchmark, fixture, field):
    fixture["case"][field] = "0" * 64
    save_fixture(fixture)
    report = benchmark.audit_manifest(fixture["manifest_path"])
    assert_blocked(report)
    assert any(issue["code"] == "reference_hash" for issue in report["cases"][0]["issues"])


def test_preparation_reference_hashes_are_accepted_and_preserved(benchmark, fixture):
    fixture["case"].update(midi_sha256=sha256(fixture["root"] / "aligned.mid"),
                           metadata_sha256=sha256(fixture["root"] / "meta.json"))
    save_fixture(fixture)
    report = benchmark.audit_manifest(fixture["manifest_path"])
    assert report["status"] == "ready"
    for field in ("midi_sha256", "metadata_sha256"):
        assert report["cases"][0]["reference_source"][field] == fixture["case"][field]


@pytest.mark.parametrize("document", ["metadata", "mapping"])
def test_reference_inputs_changed_between_inspection_and_build_are_blocked(benchmark, fixture, monkeypatch, document):
    original_build = benchmark.build_reference
    def build_after_change(*args, **kwargs):
        replacement = copy.deepcopy(fixture[document])
        replacement["test_mutation_marker"] = "different bytes but still parseable"
        write_json(fixture["root"] / ("meta.json" if document == "metadata" else "mapping.json"), replacement)
        return original_build(*args, **kwargs)
    monkeypatch.setattr(benchmark, "build_reference", build_after_change)
    report = benchmark.audit_manifest(fixture["manifest_path"])
    assert_blocked(report)
    assert any(issue["code"] == "input_changed" for issue in report["cases"][0]["issues"])
