"""Selection/lineage tests use local fake data, never download recordings."""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf


@pytest.fixture
def preparation():
    path = Path(__file__).resolve().parents[2] / "scripts/prepare-drum-benchmark.py"
    spec = importlib.util.spec_from_file_location("prepare_drum_benchmark", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_manifest(module, output, partition="development", seconds=12., **updates):
    output.mkdir(exist_ok=True)
    manifest = {"revision": module.REVISION, "partition": partition,
                "excerpt_start": 0., "excerpt_seconds": seconds,
                "cases": [{"name": track} for track in module.PARTITIONS[partition]]}
    manifest.update(updates)
    (output / "manifest.json").write_text(json.dumps(manifest))
    return manifest


def test_partitions_are_fixed_disjoint_and_scope_does_not_claim_training_holdout(preparation):
    assert preparation.TRACKS == ("MusicDelta_80sRock", "MusicDelta_Rock", "MusicDelta_FunkJazz",
                                  "MusicDelta_Reggae", "MusicDelta_SpeedMetal")
    assert preparation.VALIDATION_TRACKS == ("MusicDelta_Beatles", "MusicDelta_Country1", "MusicDelta_Disco",
                                             "MusicDelta_Grunge", "MusicDelta_LatinJazz")
    assert not set(preparation.TRACKS) & set(preparation.VALIDATION_TRACKS)
    assert "no claim of exclusion from model training" in preparation.PARTITION_SCOPES["validation"]
    assert "not held out" in preparation.PARTITION_SCOPES["development"]


def test_cli_preserves_legacy_default_and_separates_validation_output(preparation):
    development = preparation.parse_args(["--accept-noncommercial"])
    validation = preparation.parse_args(["--accept-noncommercial", "--partition", "validation"])
    assert development.partition == "development" and development.seconds == 12
    assert development.output == Path(".data/drum-benchmark/mdb")
    assert validation.output == Path(".data/drum-benchmark/mdb-validation")
    custom = preparation.parse_args(["--accept-noncommercial", "--partition", "validation", "--output", "custom", "--seconds", "3"])
    assert custom.output == Path("custom") and custom.seconds == 3


@pytest.mark.parametrize("arguments", [[], ["--accept-noncommercial", "--partition", "test"],
                                       ["--accept-noncommercial", "--seconds", "nan"],
                                       ["--accept-noncommercial", "--seconds", "inf"],
                                       ["--accept-noncommercial", "--seconds", "2"],
                                       ["--accept-noncommercial", "--seconds", "31"]])
def test_cli_rejects_missing_license_unknown_partition_and_invalid_duration(preparation, arguments):
    with pytest.raises(SystemExit) as error:
        preparation.parse_args(arguments)
    assert error.value.code == 2


def test_source_paths_are_pinned_and_track_names_cannot_escape_selection(preparation):
    assert preparation.REVISION in preparation.BASE
    paths = preparation.source_files("MusicDelta_Disco")
    assert paths == [("MDB Drums/audio/drum_only/MusicDelta_Disco_Drum.wav", "full-drums.wav"),
                     ("MDB Drums/audio/full_mix/MusicDelta_Disco_MIX.wav", "full-mix.wav"),
                     ("MDB Drums/annotations/subclass/MusicDelta_Disco_subclass.txt", "subclass.txt")]
    with pytest.raises(ValueError, match="fixed benchmark"):
        preparation.source_files("../MusicDelta_Disco")


def test_annotation_adapter_keeps_rare_voices_and_strict_excerpt_boundary(preparation):
    events = preparation.parse_annotations("0 KD\n0 PHH\n0.5 SST\n0.7 TMB\n1 OHH", 1.)
    assert [event[2] for event in events] == [35, 44, 37, 54]
    assert events[0][0] == events[1][0] == 0
    with pytest.raises(ValueError, match="Unsupported"):
        preparation.parse_annotations("nan KD", 1.)


def test_new_empty_and_identical_outputs_are_compatible(preparation, tmp_path):
    output = tmp_path / "dataset"
    preparation.ensure_compatible_output(output, "validation", 12.)
    output.mkdir()
    preparation.ensure_compatible_output(output, "validation", 12.)
    write_manifest(preparation, output, "validation")
    preparation.ensure_compatible_output(output, "validation", 12.)


@pytest.mark.parametrize("updates", [{"revision": "other"}, {"partition": "validation"},
                                     {"cases": []}, {"excerpt_seconds": 8.}, {"excerpt_start": 1.}])
def test_incompatible_manifest_is_never_overwritten(preparation, tmp_path, updates):
    write_manifest(preparation, tmp_path, **updates)
    before = (tmp_path / "manifest.json").read_bytes()
    with pytest.raises(ValueError, match="different --output"):
        preparation.ensure_compatible_output(tmp_path, "development", 12.)
    assert (tmp_path / "manifest.json").read_bytes() == before


def test_nonempty_unmanifested_output_is_rejected_before_download(preparation, tmp_path, monkeypatch):
    (tmp_path / "unrelated.txt").write_text("preserve")
    monkeypatch.setattr(preparation, "download", lambda *args: pytest.fail("must fail before network or writes"))
    with pytest.raises(ValueError, match="different --output"):
        preparation.main(["--accept-noncommercial", "--output", str(tmp_path)])
    assert list(tmp_path.iterdir()) == [tmp_path / "unrelated.txt"]


def test_legacy_development_cache_requires_matching_actual_excerpt(preparation, tmp_path):
    manifest = {"revision": preparation.REVISION, "cases": [{"name": name} for name in preparation.TRACKS]}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    for track in preparation.TRACKS:
        folder = tmp_path / track
        folder.mkdir()
        sf.write(folder / "full-drums.wav", np.zeros(14000), 1000)
        (folder / "reference.json").write_text(json.dumps({"revision": preparation.REVISION,
            "track": track, "excerpt_start": 0., "duration": 12.}))
    preparation.ensure_compatible_output(tmp_path, "development", 12.)
    with pytest.raises(ValueError, match="different --output"):
        preparation.ensure_compatible_output(tmp_path, "development", 10.)
    with pytest.raises(ValueError, match="different --output"):
        preparation.ensure_compatible_output(tmp_path, "validation", 12.)


def fake_download(relative, destination):
    if destination.suffix == ".wav":
        sf.write(destination, np.zeros((4000, 2)), 1000)
    elif destination.name == "subclass.txt":
        destination.write_text("0 KD\n0 CHH\n3 SD\n")
    else:
        destination.write_text("Test fixture; not actual dataset source.")
    return "fake-source-hash"


def test_generated_manifest_and_references_record_same_partition(preparation, tmp_path, monkeypatch):
    monkeypatch.setattr(preparation, "download", fake_download)
    preparation.main(["--accept-noncommercial", "--partition", "validation", "--seconds", "3", "--output", str(tmp_path)])
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["partition"] == "validation" and manifest["excerpt_seconds"] == 3
    assert [case["name"] for case in manifest["cases"]] == list(preparation.VALIDATION_TRACKS)
    assert manifest["revision"] == preparation.REVISION
    for case in manifest["cases"]:
        assert Path(case["audio"]).parent == tmp_path / case["name"]
        reference = json.loads(Path(case["reference"]).read_text())
        assert reference["partition"] == "validation"
        assert reference["partition_scope"] == manifest["partition_scope"]
        assert reference["duration"] == 3 and len(reference["events"]) == 2
        assert len(reference["source_sha256"]) == 3


@pytest.mark.parametrize("fail_at", [1, 3, 8])
def test_failed_download_can_retry_same_selection_but_not_change_it(preparation, tmp_path, monkeypatch, fail_at):
    output = tmp_path / "partial"
    arguments = ["--accept-noncommercial", "--partition", "validation", "--seconds", "3", "--output", str(output)]
    calls = []
    def interrupted_download(relative, destination):
        assert json.loads((output / ".selection.json").read_text()) == preparation.selection_receipt("validation", 3.)
        calls.append(relative)
        if len(calls) == fail_at:
            raise OSError("simulated network interruption")
        return fake_download(relative, destination)
    monkeypatch.setattr(preparation, "download", interrupted_download)
    with pytest.raises(OSError, match="simulated"):
        preparation.main(arguments)
    assert not (output / "manifest.json").exists()
    preparation.ensure_compatible_output(output, "validation", 3.)
    before = {path.relative_to(output): path.read_bytes() for path in output.rglob("*") if path.is_file()}
    with pytest.raises(ValueError, match="different --output"):
        preparation.main(["--accept-noncommercial", "--partition", "development", "--seconds", "3", "--output", str(output)])
    with pytest.raises(ValueError, match="different --output"):
        preparation.main(["--accept-noncommercial", "--partition", "validation", "--seconds", "4", "--output", str(output)])
    assert len(calls) == fail_at
    assert before == {path.relative_to(output): path.read_bytes() for path in output.rglob("*") if path.is_file()}
    monkeypatch.setattr(preparation, "download", fake_download)
    preparation.main(arguments)
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["partition"] == "validation" and len(manifest["cases"]) == 5
    preparation.ensure_compatible_output(output, "validation", 3.)


def test_invalid_or_conflicting_receipt_cannot_adopt_existing_files(preparation, tmp_path):
    receipt = tmp_path / ".selection.json"
    for content in ("broken", json.dumps({**preparation.selection_receipt("validation", 12.), "revision": "other"})):
        receipt.write_text(content)
        with pytest.raises(ValueError, match="different --output"):
            preparation.ensure_compatible_output(tmp_path, "validation", 12.)
    # A complete manifest does not hide a conflicting receipt.
    write_manifest(preparation, tmp_path, "validation")
    with pytest.raises(ValueError, match="different --output"):
        preparation.ensure_compatible_output(tmp_path, "validation", 12.)
