"""Offline contract tests: no SAM, torch, GPU, network, or models are required."""
import builtins
import importlib.util
import json
from pathlib import Path
import random
import threading
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from backend.config import INSTRUMENTS, SAMPLE_RATE
from backend.separation_experiment import ExperimentCancelled, run_strategy

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "compare-separation.py"
SPEC = importlib.util.spec_from_file_location("compare_separation", SCRIPT)
comparison = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(comparison)


def waveform():
    return np.linspace(-.8, .8, 100, dtype=np.float32)


def input_wav(tmp_path, *, audio=None, samplerate=SAMPLE_RATE, subtype="FLOAT"):
    path = tmp_path / "source.wav"
    sf.write(path, waveform() if audio is None else audio, samplerate, subtype=subtype)
    return path


def test_exactly_six_calls_same_order_explicit_sequential_residual_and_isolation():
    audio = waveform()
    original = audio.copy()
    requests, stems, progress = [], {}, []

    def extract(request, instrument, event, report):
        requests.append((instrument, request.copy()))
        assert not np.shares_memory(request, audio)
        report(.5)
        return request * np.float32(.2)

    residual = run_strategy(audio, "sequential", extract,
                            emit=lambda inst, stem, index: stems.update({inst: stem.copy()}),
                            progress=lambda *args: progress.append(args))
    assert [inst for inst, _ in requests] == list(INSTRUMENTS)
    np.testing.assert_array_equal(audio, original)
    expected = original.copy()
    for instrument, request in requests:
        np.testing.assert_array_equal(request, expected)
        expected = expected - stems[instrument]
    np.testing.assert_array_equal(residual, expected)
    np.testing.assert_allclose(sum(stems.values()) + residual, original, rtol=1e-6, atol=1e-7)
    assert progress[0] == ("vocal", 0, 0.) and progress[-1] == ("piano", 5, 1.)


def test_independent_preserves_overlap_and_each_request_uses_original():
    audio = waveform()
    originals, stems = [], []

    def extract(request, *args):
        originals.append(request.copy())
        return request * np.float32(2.)  # Intentionally overlapping and >1.

    residual = run_strategy(audio, "independent", extract, emit=lambda _, target, __: stems.append(target.copy()))
    assert residual is None and len(stems) == len(INSTRUMENTS)
    for request, stem in zip(originals, stems):
        np.testing.assert_array_equal(request, audio)
        np.testing.assert_array_equal(stem, audio * 2)
    assert np.max(np.abs(stems[0])) > 1
    assert not np.allclose(sum(stems), audio)


@pytest.mark.parametrize("strategy", ["sequential", "independent"])
def test_unexpected_extractor_mutation_rejected_source_unchanged(strategy):
    audio = waveform()
    original = audio.copy()
    emitted = []

    def mutating(request, *args):
        request[0] = 0.
        return request

    with pytest.raises(ValueError, match="mutated"):
        run_strategy(audio, strategy, mutating, emit=lambda *args: emitted.append(args))
    assert not emitted
    np.testing.assert_array_equal(audio, original)


def test_output_alias_and_emit_mutation_cannot_change_next_input():
    audio = waveform()
    requests = []

    def extract(request, *args):
        requests.append(request.copy())
        return request  # Valid unchanged alias.

    def emit(inst, target, index):
        target.fill(123.)

    residual = run_strategy(audio, "sequential", extract, emit=emit)
    np.testing.assert_array_equal(requests[0], audio)
    for request in requests[1:]:
        np.testing.assert_array_equal(request, np.zeros_like(audio))
    np.testing.assert_array_equal(residual, np.zeros_like(audio))


@pytest.mark.parametrize("bad", [np.array([], dtype=np.float32), np.ones((2, 2), dtype=np.float32),
                                 np.array([0, 1]), np.array([np.nan]), np.array([np.inf]), np.array([1.01])])
def test_invalid_source_rejected_before_extraction(bad):
    calls = []
    with pytest.raises(ValueError):
        run_strategy(bad, "sequential", lambda *args: calls.append(args))
    assert not calls


@pytest.mark.parametrize("bad", [np.zeros(99, dtype=np.float32), np.zeros((100, 1), dtype=np.float32),
                                 np.zeros(100, dtype=int), np.full(100, np.nan), np.full(100, 1e300)])
def test_invalid_extraction_rejected(bad):
    with pytest.raises(ValueError, match="Extraction"):
        run_strategy(waveform(), "sequential", lambda *args: bad)


def test_same_per_instrument_rng_sequences_across_strategies():
    draws, seeds = [], []

    def reset(seed):
        seeds.append(seed)
        comparison.seed_rng(seed)

    def extract(request, *args):
        draws.append((random.random(), np.random.random()))
        return request * np.float32(.1)

    for strategy in ("sequential", "independent"):
        run_strategy(waveform(), strategy, extract, seed=7, seed_callback=reset)
    assert seeds[:6] == seeds[6:] == list(range(7, 13))
    assert draws[:6] == draws[6:]


def test_cancellation_before_and_during_extract_has_no_partial_stem():
    event, calls, emitted = threading.Event(), [], []
    event.set()
    with pytest.raises(ExperimentCancelled):
        run_strategy(waveform(), "sequential", lambda *args: calls.append(args), event=event)
    assert not calls
    event.clear()

    def extract(request, _, cancel, report):
        calls.append(1)
        cancel.set()
        return request * 0.

    with pytest.raises(ExperimentCancelled):
        run_strategy(waveform(), "independent", extract, event=event, emit=lambda *args: emitted.append(args))
    assert calls == [1] and not emitted


@pytest.mark.parametrize("fractions", [[np.nan], [-.1], [1.1], [True], [.8, .3]])
def test_invalid_progress_rejected(fractions):
    def extract(request, _, event, progress):
        for fraction in fractions:
            progress(fraction)
        return request

    with pytest.raises(ValueError, match="progress"):
        run_strategy(waveform(), "independent", extract)


def test_invalid_strategy_and_seed():
    for strategy, seed in (("unknown", 0), ("sequential", -1), ("independent", 2 ** 32), ("independent", True)):
        with pytest.raises(ValueError):
            run_strategy(waveform(), strategy, lambda *args: None, seed=seed)


def test_cli_defaults_to_dry_run_and_explicit_run_flag(tmp_path):
    args = comparison.parse_args(["--audio", "source.wav", "--output", str(tmp_path)])
    assert args.run_sam is False and args.seed == 0
    assert comparison.parse_args(["--audio", "source.wav", "--output", str(tmp_path), "--run-sam"]).run_sam
    with pytest.raises(SystemExit):
        comparison.parse_args(["--audio", "source.wav", "--output", str(tmp_path), "--seed", "-1"])


def test_dry_run_never_imports_or_loads_models_and_freezes_plan(tmp_path, monkeypatch):
    audio = input_wav(tmp_path)
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.split(".")[0] in {"torch", "sam_audio"}:
            raise AssertionError("Dry run must not import model runtimes")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(comparison, "load_engine", lambda: pytest.fail("Dry run must not check/load the SAM engine"))
    output = tmp_path / "plan"
    result = comparison.compare(audio, output)
    plan = json.loads((output / "plan.json").read_text())
    assert result["status"] == "dry_run" and result["accuracy_evaluated"] is False
    assert sorted(path.name for path in output.iterdir()) == ["manifest.json", "plan.json"]
    assert plan["extract_calls_per_strategy"] == 6 and plan["config"]["instrument_order"] == list(INSTRUMENTS)
    assert plan["input"]["file_sha256"] == comparison.file_sha256(audio)
    assert plan["config_sha256"] == comparison.json_sha256(plan["config"])
    assert result["plan_sha256"] == comparison.file_sha256(output / "plan.json")
    assert "backend/separator.py" in plan["provenance"]["source_sha256"]
    assert plan["provenance"]["checkpoint"]["weights_sha256"] is None


@pytest.mark.parametrize("kwargs", [{"samplerate": 44100}, {"subtype": "PCM_16"},
                                    {"audio": np.ones((100, 2), dtype=np.float32)},
                                    {"audio": np.full(100, 1.1, dtype=np.float32)},
                                    {"audio": np.full(100, np.nan, dtype=np.float32)}])
def test_cli_rejects_nonconforming_audio_without_conversion(tmp_path, kwargs):
    path = input_wav(tmp_path, **kwargs)
    with pytest.raises(ValueError):
        comparison.compare(path, tmp_path / "results")
    assert not (tmp_path / "results").exists()


def test_input_size_and_duration_limits(tmp_path, monkeypatch):
    path = input_wav(tmp_path)
    monkeypatch.setattr(comparison, "MAX_INPUT_BYTES", 1)
    with pytest.raises(ValueError, match="128 MiB"):
        comparison.load_audio(path)
    monkeypatch.setattr(comparison, "MAX_INPUT_BYTES", 128 * 1024 * 1024)
    monkeypatch.setattr(comparison.sf, "info", lambda path: SimpleNamespace(format="WAV", subtype="FLOAT", channels=1,
                                                                         samplerate=SAMPLE_RATE, frames=601 * SAMPLE_RATE))
    with pytest.raises(ValueError, match="600s"):
        comparison.load_audio(path)


class FakeEngine:
    def __init__(self, failure_at=None, load_failure=False):
        self.calls, self.loads, self.offloads = [], 0, 0
        self.failure_at, self.load_failure = failure_at, load_failure
        self.device = "mock"
        self.model = SimpleNamespace(config=SimpleNamespace(_commit_hash="fake-local-test"))

    def load(self):
        self.loads += 1
        if self.load_failure:
            raise RuntimeError("mock partial model load failure")

    def extract(self, request, instrument, event, report):
        self.calls.append((instrument, request.copy()))
        if len(self.calls) == self.failure_at:
            raise RuntimeError("mock inference failure")
        report(.5)
        return request * np.float32(.2)

    def offload(self):
        self.offloads += 1


def fake_runtime(monkeypatch, engine):
    seeds = []
    torch = SimpleNamespace(manual_seed=seeds.append, are_deterministic_algorithms_enabled=lambda: False,
                            backends=SimpleNamespace(cudnn=SimpleNamespace(deterministic=False, benchmark=False)))
    monkeypatch.setattr(comparison, "load_engine", lambda: (engine, torch, {"available": True}))
    return seeds


def test_explicit_mock_run_writes_12_stems_residual_hashes_and_no_independent_residual(tmp_path, monkeypatch):
    audio = input_wav(tmp_path)
    engine = FakeEngine()
    seeds = fake_runtime(monkeypatch, engine)
    output = tmp_path / "comparison"
    manifest = comparison.compare(audio, output, run_sam=True, seed=21)
    assert manifest["status"] == "complete" and manifest["accuracy_evaluated"] is False
    assert engine.loads == engine.offloads == 1
    assert [inst for inst, _ in engine.calls] == list(INSTRUMENTS) * 2
    assert seeds == list(range(21, 27)) * 2
    assert not (output / "independent" / "residual.wav").exists()
    assert manifest["strategies"]["independent"]["residual"] is None
    for strategy in ("sequential", "independent"):
        info = manifest["strategies"][strategy]
        assert info["status"] == "complete" and list(info["stems"]) == list(INSTRUMENTS)
        for artifact in info["stems"].values():
            path = output / artifact["file"]
            samples, _ = sf.read(path, dtype="float32")
            assert artifact["file_sha256"] == comparison.file_sha256(path)
            assert artifact["pcm_sha256"] == comparison.pcm_sha256(samples)
            assert sf.info(path).subtype == "FLOAT"
    assert (output / "sequential" / "residual.wav").is_file()
    np.testing.assert_array_equal(engine.calls[6][1], waveform())
    assert json.loads((output / "manifest.json").read_text()) == manifest


def test_inference_failure_preserves_completed_artifacts_and_plan(tmp_path, monkeypatch):
    audio = input_wav(tmp_path)
    engine = FakeEngine(failure_at=8)
    fake_runtime(monkeypatch, engine)
    output = tmp_path / "partial"
    with pytest.raises(RuntimeError, match="mock inference"):
        comparison.compare(audio, output, run_sam=True)
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["status"] == "failed" and engine.offloads == 1
    assert manifest["strategies"]["sequential"]["status"] == "complete"
    independent = manifest["strategies"]["independent"]
    assert independent["status"] == "failed" and list(independent["stems"]) == ["vocal"]
    assert independent["progress"]["instrument"] == "bass"
    assert (output / "sequential" / "residual.wav").exists()
    assert (output / "independent" / "vocal.wav").exists()
    assert manifest["plan_sha256"] == comparison.file_sha256(output / "plan.json")


def test_partial_model_load_failure_still_offloads(tmp_path, monkeypatch):
    engine = FakeEngine(load_failure=True)
    fake_runtime(monkeypatch, engine)
    with pytest.raises(RuntimeError, match="partial model load"):
        comparison.compare(input_wav(tmp_path), tmp_path / "load-failed", run_sam=True)
    assert engine.offloads == 1 and not engine.calls


def test_unavailable_engine_preserves_failed_manifest_without_extracting(tmp_path, monkeypatch):
    def unavailable():
        raise RuntimeError("SAM engine unavailable")

    monkeypatch.setattr(comparison, "load_engine", unavailable)
    output = tmp_path / "unavailable"
    with pytest.raises(RuntimeError, match="unavailable"):
        comparison.compare(input_wav(tmp_path), output, run_sam=True)
    assert json.loads((output / "manifest.json").read_text())["status"] == "failed"
    assert (output / "plan.json").is_file()


def test_cancellation_before_model_load_retains_cancelled_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(comparison, "load_engine", lambda: pytest.fail("Already cancelled; do not load model"))
    event = threading.Event()
    event.set()
    output = tmp_path / "cancelled"
    with pytest.raises(ExperimentCancelled):
        comparison.compare(input_wav(tmp_path), output, run_sam=True, event=event)
    assert json.loads((output / "manifest.json").read_text())["status"] == "cancelled"


def test_nonempty_output_and_symlink_never_overwritten(tmp_path):
    audio = input_wav(tmp_path)
    output = tmp_path / "nonempty"
    output.mkdir()
    (output / "keep").write_text("preserve")
    link = tmp_path / "link"
    link.symlink_to(output, target_is_directory=True)
    for destination in (output, link):
        with pytest.raises(ValueError, match="new or empty"):
            comparison.compare(audio, destination)
    assert (output / "keep").read_text() == "preserve"


def test_configuration_change_during_run_fails_instead_of_unpaired_settings(tmp_path, monkeypatch):
    engine = FakeEngine()
    fake_runtime(monkeypatch, engine)
    original = engine.extract

    def changing(*args):
        value = original(*args)
        monkeypatch.setenv("SAM_CHUNK_SECONDS", "17")
        return value

    engine.extract = changing
    monkeypatch.setenv("SAM_CHUNK_SECONDS", "20")
    output = tmp_path / "changed"
    with pytest.raises(RuntimeError, match="configuration changed"):
        comparison.compare(input_wav(tmp_path), output, run_sam=True)
    assert len(engine.calls) == 1
    assert json.loads((output / "manifest.json").read_text())["status"] == "failed"


@pytest.mark.parametrize("changed_at", [1, 12])
def test_source_change_during_run_preserves_artifacts_but_never_completes(tmp_path, monkeypatch, changed_at):
    engine = FakeEngine()
    fake_runtime(monkeypatch, engine)
    original_extract = engine.extract
    original_hashes = comparison.source_hashes()
    changed = False

    def hashes():
        current = dict(original_hashes)
        if changed:
            current["backend/separator.py"] = "0" * 64
        return current

    def changing(*args):
        nonlocal changed
        value = original_extract(*args)
        if len(engine.calls) == changed_at:
            changed = True
        return value

    monkeypatch.setattr(comparison, "source_hashes", hashes)
    engine.extract = changing
    output = tmp_path / f"source-changed-{changed_at}"
    with pytest.raises(RuntimeError, match="source files changed"):
        comparison.compare(input_wav(tmp_path), output, run_sam=True)
    manifest = json.loads((output / "manifest.json").read_text())
    plan = json.loads((output / "plan.json").read_text())
    assert manifest["status"] == "failed" and engine.offloads == 1
    assert plan["provenance"]["source_sha256"] == original_hashes
    assert manifest["plan_sha256"] == comparison.file_sha256(output / "plan.json")
    assert manifest["strategies"]["sequential"]["status"] == "complete"
    assert (output / "sequential" / "residual.wav").is_file()
    if changed_at == 1:
        assert len(engine.calls) == 6
        assert manifest["strategies"]["independent"]["status"] == "pending"
        assert not (output / "independent").exists()
    else:
        assert len(engine.calls) == 12
        assert manifest["strategies"]["independent"]["status"] == "complete"
        assert len(manifest["strategies"]["independent"]["stems"]) == 6


def test_configuration_change_in_last_extract_fails_final_frozen_check(tmp_path, monkeypatch):
    engine = FakeEngine()
    fake_runtime(monkeypatch, engine)
    original = engine.extract

    def changing(*args):
        value = original(*args)
        if len(engine.calls) == 12:
            monkeypatch.setenv("SAM_CHUNK_SECONDS", "17")
        return value

    engine.extract = changing
    monkeypatch.setenv("SAM_CHUNK_SECONDS", "20")
    output = tmp_path / "changed-last"
    with pytest.raises(RuntimeError, match="configuration changed"):
        comparison.compare(input_wav(tmp_path), output, run_sam=True)
    assert len(engine.calls) == 12
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert len(manifest["strategies"]["independent"]["stems"]) == 6


def test_source_change_before_lazy_engine_load_prevents_import(tmp_path, monkeypatch):
    original_hashes = comparison.source_hashes()
    calls = 0

    def hashes():
        nonlocal calls
        calls += 1
        current = dict(original_hashes)
        if calls > 1:
            current["backend/separator.py"] = "f" * 64
        return current

    monkeypatch.setattr(comparison, "source_hashes", hashes)
    monkeypatch.setattr(comparison, "load_engine", lambda: pytest.fail("Source drift must prevent delayed engine import"))
    output = tmp_path / "changed-before-load"
    with pytest.raises(RuntimeError, match="source files changed"):
        comparison.compare(input_wav(tmp_path), output, run_sam=True)
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert all(strategy["status"] == "pending" for strategy in manifest["strategies"].values())
