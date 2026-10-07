"""Benchmark wiring/provenance tests; actual inference is always replaced."""
import hashlib
import importlib.util
from importlib import metadata
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from backend.evaluation import compare_events

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def cli():
    spec = importlib.util.spec_from_file_location("pitched_benchmark_test_module", ROOT / "scripts/benchmark-pitched.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tiny_cases(instruments=("vocal", "bass", "guitar", "piano", "synthesizer")):
    return [{"id": f"test-{inst}", "instrument": inst, "description": "Mock wiring fixture, not an accuracy result",
             "duration": 1., "sample_rate": 8000, "events": [(.1, .8, 60, .8)],
             "audio": np.zeros(8000, dtype=np.float32)} for inst in instruments]


@pytest.fixture
def mocked_runtime(cli, monkeypatch):
    monkeypatch.setattr(cli, "source_hashes", lambda: {"fixed.py": "a" * 64})
    monkeypatch.setattr(cli, "runtime_provenance", lambda: {
        "python": "test", "package_versions": {"basic-pitch": "test"},
        "bundled_onnx_models": {"test.onnx": {"sha256": "b" * 64, "bytes": 4}},
        "source_sha256": cli.source_hashes(),
    })
    return cli


@pytest.mark.parametrize("flags", [[], ["--partition", "bad"], ["--partition", "development"], ["--output", "unused"]])
def test_invalid_arguments_never_invoke_models(cli, flags, monkeypatch):
    monkeypatch.setattr(cli, "transcribe", lambda *args, **kwargs: pytest.fail("No model on invalid CLI"))
    with pytest.raises(SystemExit) as error:
        cli.main(flags)
    assert error.value.code == 2


@pytest.mark.parametrize("flag", ["--output", "--outputpath"])
def test_output_alias_and_explicit_partition_parse(cli, flag, tmp_path):
    args = cli.argument_parser().parse_args(["--partition", "validation", flag, str(tmp_path)])
    assert args.partition == "validation" and args.output == tmp_path


def test_real_fixture_partitions_preserve_frozen_counts_and_specs(cli):
    development = cli.load_cases("development")
    validation = cli.load_cases("validation")
    assert len(development) == 5 and sum(len(case["events"]) for case in development) == 76
    assert all(case["duration"] == 8 for case in development)
    assert len(validation) == 10 and sum(len(case["events"]) for case in validation) == 82
    assert all(case["duration"] == 6 for case in validation)
    specs = [{key: value for key, value in case.items() if key != "audio"} for case in validation]
    assert cli.json_sha256(specs) == "ae9d5116e29e61ce603ef69001f8a3457e49d3f6f9a18e8e66022a02fa014501"
    assert {case["id"] for case in development}.isdisjoint(case["id"] for case in validation)
    with pytest.raises(ValueError, match="Unknown"):
        cli.load_cases("invalid")


def test_comparison_keeps_controls_standard_only_and_reports_duration_separately(mocked_runtime, monkeypatch, tmp_path):
    cli = mocked_runtime
    cases = tiny_cases()
    monkeypatch.setattr(cli, "load_cases", lambda partition: cases)
    calls = []
    def fake_transcribe(path, inst, profile, *, engine, details, artifacts):
        # Truth is deliberately used ONLY by this mocked wiring test.
        calls.append((inst, engine))
        assert profile == "instrument" and path.is_file() and artifacts.is_dir()
        frozen = json.loads((tmp_path / "frozen-manifest.json").read_text())
        assert len(frozen["cases"]) == 5  # All inputs fixed before first prediction.
        if engine == "adaptive":
            details.update({"engine": "mock-adaptive", "quality_review": {"flagged": 0}})
        (artifacts / "trace.json").write_text("{}")
        return [(.1, .8 if engine == "adaptive" else .25, 60, .8)]
    monkeypatch.setattr(cli, "transcribe", fake_transcribe)
    report = cli.main(["--partition", "development", "--output", str(tmp_path)])
    assert calls == [(inst, engine) for inst in ("vocal", "bass", "guitar", "piano", "synthesizer")
                     for engine in (("standard", "adaptive") if inst in cli.POLYPHONIC else ("standard",))]
    assert report["status"] == "complete" and report["synthetic"] is True
    assert report["options"]["reference_guided_inference"] is False
    assert report["options"]["duration_tolerance_ratio"] == .2
    paired = report["micro_average"]["paired_polyphonic"]
    assert paired["case_ids"] == ["test-guitar", "test-piano", "test-synthesizer"]
    assert paired["standard"]["f1"] == paired["adaptive"]["f1"] == 1
    assert paired["standard"]["duration_diagnostics"]["f1"] == 0
    assert paired["adaptive"]["duration_diagnostics"]["f1"] == 1
    assert report["micro_average"]["standard_all"]["case_count"] == 5
    for inst in ("vocal", "bass"):
        assert set(report["cases"][f"test-{inst}"]["engines"]) == {"standard"}
        assert set(report["micro_average"]["by_instrument"][inst]) == {"standard"}
        assert report["cases"][f"test-{inst}"]["comparison_role"] == "unchanged-standard-control"
    for case in report["cases"].values():
        assert case["audio_pcm_sha256"] == hashlib.sha256(np.zeros(8000, dtype="<f4").tobytes()).hexdigest()
        assert case["audio_sha256"] == cli.file_sha256(tmp_path / case["audio_file"])
        for engine, result in case["engines"].items():
            assert result["metrics"]["confusion_diagnostics"]["heuristic_only"] is True
            assert result["runtime"]["engine"]
            assert result["events_sha256"] == cli.file_sha256(tmp_path / result["events_file"])
            events = json.loads((tmp_path / result["events_file"]).read_text())
            assert events["reference"] == [list(note) for note in case["reference"]]
            assert events["estimated"] == [list(note) for note in result["estimated"]]
            assert set(result["artifacts_sha256"]) == {"events.json", "trace.json"}
    assert json.loads((tmp_path / "evaluation.json").read_text())["status"] == "complete"


def test_failure_preserves_standard_predictions_and_excludes_unpaired_comparison(mocked_runtime, monkeypatch, tmp_path):
    cli = mocked_runtime
    monkeypatch.setattr(cli, "load_cases", lambda partition: tiny_cases(("guitar", "piano")))
    def fail_adaptive(path, inst, profile, *, engine, details, artifacts):
        if engine == "adaptive":
            raise RuntimeError("candidate failed deliberately")
        return [(.1, .8, 60, .8)]
    monkeypatch.setattr(cli, "transcribe", fail_adaptive)
    with pytest.raises(RuntimeError, match="candidate failed"):
        cli.main(["--partition", "validation", "--output", str(tmp_path)])
    report = json.loads((tmp_path / "evaluation.json").read_text())
    assert report["status"] == "failed"
    assert report["failure"]["case_id"] == "test-guitar" and report["failure"]["engine"] == "adaptive"
    assert report["cases"]["test-guitar"]["engines"]["standard"]["estimated"] == [[.1, .8, 60, .8]]
    assert report["cases"]["test-guitar"]["engines"]["adaptive"]["status"] == "failed"
    assert report["cases"]["test-piano"]["engines"] == {}
    assert report["micro_average"]["standard_all"]["case_count"] == 1
    assert report["micro_average"]["paired_polyphonic"]["case_ids"] == []
    assert (tmp_path / "test-guitar/standard/events.json").is_file()
    assert not list(tmp_path.rglob("*.tmp"))


def test_micro_scores_use_total_counts_not_average_case_f1(cli):
    one = compare_events([(0, 1, 60), (2, 3, 62)], [(0, 1, 60)], duration_tolerance_ratio=.2)
    two = compare_events([(0, 1, 60)], [(0, 1, 60), (2, 3, 62), (4, 5, 64)], duration_tolerance_ratio=.2)
    result = cli.aggregate([{"metrics": one, "elapsed_seconds": 10}, {"metrics": two, "elapsed_seconds": 20}])
    assert result["matched_notes"] == 2 and result["reference_notes"] == 3 and result["estimated_notes"] == 4
    assert result["precision"] == .5 and result["recall"] == pytest.approx(2 / 3)
    assert result["f1"] == pytest.approx(4 / 7)
    assert result["duration_diagnostics"]["f1"] == pytest.approx(4 / 7)
    assert result["elapsed_seconds_sum"] == 30


def test_existing_results_are_not_overwritten(cli, monkeypatch, tmp_path):
    saved = tmp_path / "evaluation.json"
    saved.write_text("previous diagnosis")
    monkeypatch.setattr(cli, "load_cases", lambda *args: pytest.fail("No work before overwrite guard"))
    with pytest.raises(SystemExit) as error:
        cli.main(["--partition", "development", "--output", str(tmp_path)])
    assert error.value.code == 2 and saved.read_text() == "previous diagnosis"


@pytest.mark.parametrize("ids", [["../outside"], ["same", "same"]])
def test_unsafe_or_duplicate_fixture_ids_fail_before_writes(cli, monkeypatch, tmp_path, ids):
    cases = tiny_cases(("guitar",) * len(ids))
    for case, case_id in zip(cases, ids):
        case["id"] = case_id
    monkeypatch.setattr(cli, "load_cases", lambda partition: cases)
    with pytest.raises(ValueError, match="unique safe"):
        cli.main(["--partition", "development", "--output", str(tmp_path)])
    assert not list(tmp_path.iterdir())


def test_source_change_marks_completed_predictions_as_nonfrozen_failure(mocked_runtime, monkeypatch, tmp_path):
    cli = mocked_runtime
    monkeypatch.setattr(cli, "load_cases", lambda partition: tiny_cases(("vocal",)))
    def source_changing_mock(*args, **kwargs):
        monkeypatch.setattr(cli, "source_hashes", lambda: {"fixed.py": "c" * 64})
        return [(.1, .8, 60, .8)]
    monkeypatch.setattr(cli, "transcribe", source_changing_mock)
    with pytest.raises(RuntimeError, match="Source code changed"):
        cli.main(["--partition", "development", "--output", str(tmp_path)])
    report = json.loads((tmp_path / "evaluation.json").read_text())
    assert report["status"] == "failed" and report["failure"]["case_id"] is None
    assert report["cases"]["test-vocal"]["engines"]["standard"]["status"] == "complete"
    assert report["source_sha256_at_finish"] != report["provenance"]["source_sha256"]


def test_provenance_hashes_installed_model_without_importing_inference(cli, monkeypatch, tmp_path):
    model = tmp_path / "model.onnx"
    model.write_bytes(b"not a real model; hash-only test")
    distribution = SimpleNamespace(files=[Path("bundled/model.onnx"), Path("README.md")], locate_file=lambda path: model)
    monkeypatch.setattr(cli.metadata, "distribution", lambda name: distribution)
    monkeypatch.setattr(cli.metadata, "version", lambda name: "fixture-version")
    monkeypatch.setattr(cli, "source_hashes", lambda: {"frozen.py": "a" * 64})
    result = cli.runtime_provenance()
    assert result["bundled_onnx_models"] == {"bundled/model.onnx": {
        "sha256": hashlib.sha256(model.read_bytes()).hexdigest(), "bytes": model.stat().st_size}}
    assert result["package_versions"]["basic-pitch"] == "fixture-version"


def test_missing_optional_packages_are_recorded_without_downloading(cli, monkeypatch):
    def missing(name):
        raise metadata.PackageNotFoundError(name)
    monkeypatch.setattr(cli.metadata, "distribution", missing)
    monkeypatch.setattr(cli.metadata, "version", missing)
    monkeypatch.setattr(cli, "source_hashes", lambda: {})
    result = cli.runtime_provenance()
    assert result["bundled_onnx_models"] == {}
    assert all(version is None for version in result["package_versions"].values())
