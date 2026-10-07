"""Offline sequential-vs-independent SAM Audio comparison (dry run by default).

Supply an already normalized mono 48 kHz FLOAT WAV. --run-sam explicitly opts
into the existing SAM loader/inference on this machine; it may resolve/download
the configured SAM checkpoint using the usual authorized GPU setup. Dry run
does not import torch, load models, run inference, or make network requests.
Neither strategy changes app defaults. Without gold reference stems this tool
reports artifacts/provenance, NOT accuracy, SDR, SIR, or a strategy winner.
"""
import argparse
import datetime
import hashlib
from importlib import metadata
import json
import math
import os
from pathlib import Path
import platform
import sys
import threading
import time

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import INSTRUMENTS, PROMPTS, SAMPLE_RATE
from backend.separation_experiment import (ExperimentCancelled, MAX_INPUT_BYTES, MAX_SECONDS,
                                          STRATEGIES, run_strategy, seed_rng, validate_audio)

SOURCE_FILES = ("backend/separation_experiment.py", "scripts/compare-separation.py",
                "backend/separator.py", "backend/config.py", "backend/gpu.py")


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def pcm_sha256(audio):
    return hashlib.sha256(np.asarray(audio, dtype="<f4").tobytes()).hexdigest()


def json_sha256(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def source_hashes():
    return {name: file_sha256(ROOT / name) for name in SOURCE_FILES}


def validate_frozen_plan(plan):
    """Reject source/config drift without changing the original receipt."""
    if source_hashes() != plan["provenance"]["source_sha256"]:
        raise RuntimeError("Experiment source files changed after the plan was frozen")
    if configured_parameters() != plan["config"]:
        raise RuntimeError("SAM configuration changed after the experiment plan was frozen")


def write_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    temporary.replace(path)


def ensure_empty_output(output):
    if output.is_symlink() or output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("Use a new or empty output directory; existing comparison files are never overwritten")


def load_audio(path):
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_INPUT_BYTES:
        raise ValueError("Input must be a regular local file of at most 128 MiB, not a symbolic link")
    before = file_sha256(path)
    info = sf.info(path)
    if (info.format != "WAV" or info.subtype != "FLOAT" or info.channels != 1
            or info.samplerate != SAMPLE_RATE or not 0 < info.frames <= MAX_SECONDS * SAMPLE_RATE):
        raise ValueError("Input must already be mono 48 kHz FLOAT WAV, with duration >0 and <=600s; no resampling/cropping")
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=False)
    if sample_rate != SAMPLE_RATE or len(audio) != info.frames or file_sha256(path) != before:
        raise ValueError("Input changed while being inspected")
    validate_audio(audio)
    return audio, {"path": str(path.resolve()), "file_sha256": before, "pcm_sha256": pcm_sha256(audio),
                   "pcm_hash_format": "mono little-endian float32 contiguous samples",
                   "sample_rate": SAMPLE_RATE, "samples": len(audio), "duration": len(audio) / SAMPLE_RATE,
                   "format": info.format, "subtype": info.subtype, "peak_absolute": float(np.max(np.abs(audio)))}


def configured_parameters():
    chunk_seconds = float(os.getenv("SAM_CHUNK_SECONDS", "20"))
    if not math.isfinite(chunk_seconds) or not 0 < chunk_seconds <= MAX_SECONDS:
        raise ValueError("SAM_CHUNK_SECONDS must be finite, positive, and <=600")
    chunk_samples = max(4 * SAMPLE_RATE, int(chunk_seconds * SAMPLE_RATE))
    return {"sample_rate": SAMPLE_RATE, "instrument_order": list(INSTRUMENTS), "prompts": dict(PROMPTS),
            "model": os.getenv("SAM_MODEL", "facebook/sam-audio-base"),
            "configured_device": os.getenv("SAM_DEVICE", "cuda"),
            "chunk_seconds_requested": chunk_seconds, "chunk_samples": chunk_samples,
            "overlap_samples": min(2 * SAMPLE_RATE, chunk_samples // 4),
            "predict_spans": False, "reranking_candidates": 1,
            "auxiliary_models": {"visual_ranker": None, "text_ranker": None, "span_predictor": None}}


def provenance(config):
    versions = {}
    for package in ("sam-audio", "torch", "numpy", "soundfile", "huggingface-hub"):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = None
    model_path = Path(config["model"])
    local_configs = {}
    if model_path.is_dir():
        for name in ("config.json", "preprocessor_config.json", "processor_config.json", "generation_config.json"):
            candidate = model_path / name
            if candidate.is_file() and not candidate.is_symlink() and candidate.stat().st_size <= 2 * 1024 * 1024:
                local_configs[name] = file_sha256(candidate)
    return {"python": platform.python_version(), "platform": platform.platform(), "package_versions": versions,
            "source_sha256": source_hashes(),
            "checkpoint": {"configured_model": config["model"], "local_directory": model_path.is_dir(),
                           "local_config_sha256": local_configs, "weights_sha256": None,
                           "resolved_commit": None,
                           "identity_limit": "Weight identity is not attested; configured remote model names may change. Prefer an immutable local checkpoint."}}


def build_plan(source, config, seed, run_sam):
    return {"schema": "akbo.separation-comparison.plan", "schema_version": 1,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "mode": "run_sam" if run_sam else "dry_run", "input": source, "config": config,
            "config_sha256": json_sha256(config), "provenance": provenance(config),
            "strategies": {"sequential": {"input": "previous residual", "residual": "input minus the exact six extracted targets"},
                           "independent": {"input": "original input for every instrument", "residual": None,
                                           "overlap": "preserved; stems need not sum to input"}},
            "execution_order": list(STRATEGIES), "extract_calls_per_strategy": len(INSTRUMENTS),
            "randomness": {"base_seed": seed, "per_instrument_seed": {inst: (seed + index) % 2 ** 32 for index, inst in enumerate(INSTRUMENTS)},
                           "reset": "Python, NumPy, and torch seeded identically for each paired instrument after one model preload",
                           "guarantee": "Best effort only; GPU/kernel/model nondeterminism is not eliminated"},
            "limitations": ["No reference stems supplied: no accuracy, SDR, SIR, or strategy ranking is computed.",
                            "Elapsed time is operational information, not a quality measurement.",
                            "Independent prompts may reproduce the same sound in multiple stems; no mixture-consistency normalization is applied.",
                            "Sequential sum plus residual agrees with input only up to float32 arithmetic rounding.",
                            "Instrument-level extraction calls each include the production overlapping-chunk loop.",
                            "Production application defaults and transcription engines are unchanged.",
                            "Explicit --run-sam uses the existing model loader, which may access the configured checkpoint service."]}


def load_engine():
    """Called only after explicit --run-sam and a saved plan; no dry-run imports."""
    from backend.separator import ENGINE, engine_status
    status = engine_status()
    if not status.get("available"):
        raise RuntimeError("SAM engine unavailable: " + "; ".join(status.get("issues", [])))
    import torch
    return ENGINE, torch, status


def save_waveform(path, audio):
    sf.write(path, audio, SAMPLE_RATE, subtype="FLOAT")
    return {"file": str(path.name), "file_sha256": file_sha256(path), "pcm_sha256": pcm_sha256(audio),
            "samples": len(audio), "sample_rate": SAMPLE_RATE, "subtype": "FLOAT",
            "peak_absolute": float(np.max(np.abs(audio)))}


def _loaded_model_details(engine, torch):
    model_config = getattr(getattr(engine, "model", None), "config", None)
    commit = getattr(model_config, "_commit_hash", None)
    return {"selected_device": getattr(engine, "device", None),
            "resolved_commit": commit if isinstance(commit, str) else None,
            "deterministic_algorithms_enabled": bool(torch.are_deterministic_algorithms_enabled()),
            "cudnn_deterministic": bool(torch.backends.cudnn.deterministic),
            "cudnn_benchmark": bool(torch.backends.cudnn.benchmark)}


def compare(audio_path, output, *, run_sam=False, seed=0, event=None):
    if type(seed) is not int or not 0 <= seed <= 2 ** 32 - 1:
        raise ValueError("Seed must be an integer in 0..2^32-1")
    audio_path, output = Path(audio_path), Path(output)
    ensure_empty_output(output)
    audio, source = load_audio(audio_path)
    config = configured_parameters()
    plan = build_plan(source, config, seed, run_sam)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "plan.json", plan)
    manifest = {"schema": "akbo.separation-comparison.manifest", "schema_version": 1,
                "status": "running" if run_sam else "dry_run", "plan": "plan.json",
                "plan_sha256": file_sha256(output / "plan.json"), "accuracy_evaluated": False,
                "reference_stems": None, "strategies": {strategy: {"status": "pending", "stems": {}, "residual": None}
                                                         for strategy in STRATEGIES},
                "elapsed_seconds": 0., "error": None}
    manifest_path = output / "manifest.json"
    write_json(manifest_path, manifest)
    if not run_sam:
        return manifest
    event = event if event is not None else threading.Event()
    engine = None
    started = time.perf_counter()
    active = None
    try:
        if event.is_set():
            raise ExperimentCancelled("Comparison cancelled before loading the model")
        manifest["input_artifact"] = save_waveform(output / "input.wav", audio)
        write_json(manifest_path, manifest)
        validate_frozen_plan(plan)  # separator is imported lazily by load_engine.
        engine, torch, status = load_engine()
        manifest["engine_status"] = status
        # Assignment above ensures finally can offload even a partial load.
        # Preload once before paired seeds: first-call initialization must not
        # consume only the first sequential instrument's random stream.
        engine.load()
        manifest["loaded_model"] = _loaded_model_details(engine, torch)
        for strategy in STRATEGIES:
            active = None  # A between-strategy failure must retain prior completion.
            validate_frozen_plan(plan)
            active = manifest["strategies"][strategy]
            active["status"] = "running"
            strategy_started = time.perf_counter()
            folder = output / strategy
            folder.mkdir()

            def progress(instrument, index, fraction):
                active["progress"] = {"instrument": instrument, "index": index, "fraction": fraction}
                active["elapsed_seconds"] = time.perf_counter() - strategy_started
                write_json(manifest_path, manifest)

            def emit(instrument, samples, index):
                artifact = save_waveform(folder / f"{instrument}.wav", samples)
                artifact["file"] = f"{strategy}/{instrument}.wav"
                artifact["index"] = index
                active["stems"][instrument] = artifact
                write_json(manifest_path, manifest)

            def reset_seed(value):
                if configured_parameters() != config:
                    raise RuntimeError("SAM configuration changed after the experiment plan was frozen")
                seed_rng(value, torch_module=torch)

            residual = run_strategy(audio, strategy, engine.extract, event=event, progress=progress,
                                    emit=emit, seed=seed, seed_callback=reset_seed)
            if residual is not None:
                active["residual"] = save_waveform(folder / "residual.wav", residual)
                active["residual"]["file"] = f"{strategy}/residual.wav"
            active["status"] = "complete"
            active["elapsed_seconds"] = time.perf_counter() - strategy_started
            write_json(manifest_path, manifest)
        active = None
        validate_frozen_plan(plan)
        manifest["status"] = "complete"
    except (Exception, KeyboardInterrupt) as error:
        cancelled = isinstance(error, (ExperimentCancelled, KeyboardInterrupt)) or event.is_set()
        manifest["status"] = "cancelled" if cancelled else "failed"
        manifest["error"] = {"type": type(error).__name__, "message": str(error)[:1000]}
        if active is not None:
            active["status"] = manifest["status"]
        raise
    finally:
        manifest["elapsed_seconds"] = time.perf_counter() - started
        if engine is not None:
            try:
                engine.offload()
            except Exception as error:
                manifest["offload_error"] = {"type": type(error).__name__, "message": str(error)[:1000]}
        write_json(manifest_path, manifest)
    return manifest


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, required=True, help="Local normalized mono 48 kHz FLOAT WAV; max 600 seconds")
    parser.add_argument("--output", type=Path, required=True, help="New or empty directory; no overwrites")
    parser.add_argument("--run-sam", action="store_true", help="Explicitly enable existing SAM loader and 12 extraction calls")
    parser.add_argument("--seed", type=int, default=0, help="Per-instrument seed base (0..2^32-1)")
    args = parser.parse_args(argv)
    if not 0 <= args.seed <= 2 ** 32 - 1:
        parser.error("--seed must be between 0 and 2^32-1")
    return args


def main(argv=None):
    args = parse_args(argv)
    result = compare(args.audio, args.output, run_sam=args.run_sam, seed=args.seed)
    print(f"{result['status']}: {args.output / 'manifest.json'}; accuracy was not evaluated")


if __name__ == "__main__":
    main()
