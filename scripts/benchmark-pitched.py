"""Compare actual installed CPU pitched engines on fixed synthetic fixtures.

No downloads, fitting, reference-guided inference, or threshold tuning occur.
Development and validation partitions stay separate. Synthetic results are NOT
real-song accuracy. Freeze a candidate before running the validation partition.
"""
import argparse
import datetime
import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import soundfile as sf

from backend import evaluation_fixtures, pitched_validation_fixtures
from backend.evaluation import compare_events
from backend.score import transcribe
from backend.transcription import engine_description

POLYPHONIC = frozenset(("guitar", "piano", "synthesizer"))
OFFSET_RATIO = .2
ONSET_TOLERANCE = .05


def argument_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--partition", choices=("development", "validation"), required=True)
    parser.add_argument("--output", "--outputpath", dest="output", type=Path, required=True,
                        help="New or empty directory; existing results are never overwritten")
    return parser


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_sha256(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
    temporary.replace(path)


def source_hashes():
    names = {"scripts/benchmark-pitched.py", "backend/config.py", "backend/score.py",
             "backend/transcription.py", "backend/evaluation.py", "backend/evaluation_fixtures.py"}
    for pattern in ("pitched*.py", "polyphonic*.py"):
        names.update(str(path.relative_to(ROOT)) for path in (ROOT / "backend").glob(pattern))
    return {name: file_sha256(ROOT / name) for name in sorted(names)}


def runtime_provenance():
    versions = {}
    for name in ("basic-pitch", "onnxruntime", "numpy", "scipy", "librosa", "soundfile", "pretty-midi", "mido"):
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    models = {}
    try:
        distribution = metadata.distribution("basic-pitch")
        for relative in distribution.files or ():
            if str(relative).endswith(".onnx"):
                path = Path(distribution.locate_file(relative))
                if path.is_file():
                    models[str(relative)] = {"sha256": file_sha256(path), "bytes": path.stat().st_size}
    except metadata.PackageNotFoundError:
        pass
    return {"python": platform.python_version(), "platform": platform.platform(),
            "package_versions": versions, "bundled_onnx_models": models,
            "source_sha256": source_hashes()}


def load_cases(partition):
    if partition == "validation":
        return list(pitched_validation_fixtures.generate_validation_cases())
    if partition != "development":
        raise ValueError("Unknown benchmark partition")
    audio, events = evaluation_fixtures.generate_challenges()
    return [{"id": f"development-{inst}", "instrument": inst,
             "description": "Original fixed analytical-tone development challenge",
             "duration": evaluation_fixtures.DURATION, "sample_rate": evaluation_fixtures.SAMPLE_RATE,
             "events": events[inst], "audio": audio[inst]}
            for inst in evaluation_fixtures.INSTRUMENTS]


def aggregate(results):
    metrics = [result["metrics"] for result in results]
    reference = sum(item["reference_notes"] for item in metrics)
    estimated = sum(item["estimated_notes"] for item in metrics)
    matched = sum(item["matched_notes"] for item in metrics)
    with_offsets = sum(item["duration_diagnostics"]["matched_with_valid_offset"] for item in metrics)
    def scores(hits):
        return {"precision": hits / estimated if estimated else 0., "recall": hits / reference if reference else 0.,
                "f1": 2 * hits / (reference + estimated) if reference + estimated else 0.}
    return {"case_count": len(results), "reference_notes": reference, "estimated_notes": estimated,
            "matched_notes": matched, **scores(matched),
            "duration_diagnostics": {"method": "existing-onset-pairs-offset-check-v1", "offset_ratio": OFFSET_RATIO,
                                     "minimum_offset_tolerance_seconds": .05,
                                     "matched_with_valid_offset": with_offsets,
                                     "onset_matches_with_wrong_offset": matched - with_offsets, **scores(with_offsets)},
            "elapsed_seconds_sum": round(sum(result["elapsed_seconds"] for result in results), 3)}


def summarize(cases):
    def completed(case, engine):
        return case["engines"].get(engine, {}).get("status") == "complete"
    standard = [case["engines"]["standard"] for case in cases.values() if completed(case, "standard")]
    paired = [case for case in cases.values() if completed(case, "standard") and completed(case, "adaptive")]
    by_instrument = {}
    for inst in sorted({case["instrument"] for case in cases.values()}):
        by_instrument[inst] = {engine: aggregate([case["engines"][engine] for case in cases.values()
                                                if case["instrument"] == inst and completed(case, engine)])
                               for engine in ("standard", "adaptive")
                               if any(case["instrument"] == inst and completed(case, engine) for case in cases.values())}
    return {"standard_all": aggregate(standard), "paired_polyphonic": {
                "case_ids": [case["id"] for case in paired],
                "standard": aggregate([case["engines"]["standard"] for case in paired]),
                "adaptive": aggregate([case["engines"]["adaptive"] for case in paired])},
            "by_instrument": by_instrument}


def main(argv=None):
    parser = argument_parser()
    args = parser.parse_args(argv)
    if args.output.exists() and (not args.output.is_dir() or any(args.output.iterdir())):
        parser.error("Output must be a new or empty directory; choose another path to preserve previous results")
    cases = load_cases(args.partition)
    if len({case["id"] for case in cases}) != len(cases) or any(not re.fullmatch(r"[A-Za-z0-9_-]+", case["id"]) for case in cases):
        raise ValueError("Fixture IDs must be unique safe directory names")
    fixture = evaluation_fixtures if args.partition == "development" else pitched_validation_fixtures
    specifications = [{key: value for key, value in case.items() if key != "audio"} for case in cases]
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"status": "running", "synthetic": True, "partition": args.partition,
              "fixture_id": fixture.FIXTURE_ID, "fixture_specification_sha256": json_sha256(specifications),
              "started_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "options": {"profile": "instrument", "standard_engine": "standard", "adaptive_engine": "adaptive",
                          "adaptive_instruments": sorted(POLYPHONIC), "onset_tolerance_seconds": ONSET_TOLERANCE,
                          "duration_tolerance_ratio": OFFSET_RATIO, "minimum_offset_tolerance_seconds": .05,
                          "reference_guided_inference": False, "downloads": False},
              "limitations": [*fixture.LIMITATIONS,
                              "Duration diagnostics check existing onset pairs, not a new maximum matching.",
                              "Bass/vocal remain standard-only controls, not improvements attributed to adaptive decoding.",
                              "Paired summaries include only cases completed by both engines; elapsed time is not quality.",
                              "Use validation only after freezing the candidate; do not tune against validation outcomes."],
              "provenance": runtime_provenance(), "cases": {}}

    def checkpoint():
        report["micro_average"] = summarize(report["cases"])
        write_json(args.output / "evaluation.json", report)

    checkpoint()
    active_case, active_engine = None, None
    try:
        # Freeze every input/reference before the first prediction is requested.
        for case in cases:
            folder = args.output / case["id"]
            folder.mkdir()
            path = folder / "input.wav"
            sf.write(path, case["audio"], case["sample_rate"], subtype="FLOAT")
            result = {key: value for key, value in case.items() if key not in {"audio", "events"}}
            result.update({"reference": case["events"], "reference_sha256": json_sha256(case["events"]),
                           "audio_file": str(path.relative_to(args.output)), "audio_sha256": file_sha256(path),
                           "audio_pcm_sha256": hashlib.sha256(case["audio"].astype("<f4", copy=False).tobytes()).hexdigest(),
                           "comparison_role": "paired-polyphonic" if case["instrument"] in POLYPHONIC else "unchanged-standard-control",
                           "engines": {}})
            report["cases"][case["id"]] = result
        write_json(args.output / "frozen-manifest.json", {key: value for key, value in report.items() if key != "micro_average"})
        checkpoint()
        for case in report["cases"].values():
            active_case = case["id"]
            for engine in (("standard", "adaptive") if case["instrument"] in POLYPHONIC else ("standard",)):
                active_engine = engine
                folder = args.output / case["id"] / engine
                folder.mkdir()
                case["engines"][engine] = {"status": "running"}
                checkpoint()
                details = engine_description(case["instrument"]) if engine == "standard" else {"engine": "adaptive", "profile": "instrument"}
                started = time.monotonic()
                estimated = transcribe(args.output / case["audio_file"], case["instrument"], profile="instrument",
                                       engine=engine, details=details, artifacts=folder)
                elapsed = round(time.monotonic() - started, 3)
                events_path = folder / "events.json"
                write_json(events_path, {"reference": case["reference"], "estimated": estimated})
                metrics = compare_events(case["reference"], estimated, tolerance=ONSET_TOLERANCE,
                                         include_errors=True, duration_tolerance_ratio=OFFSET_RATIO)
                case["engines"][engine] = {"status": "complete", "estimated": estimated, "metrics": metrics,
                                           "runtime": details, "elapsed_seconds": elapsed,
                                           "events_file": str(events_path.relative_to(args.output)),
                                           "events_sha256": file_sha256(events_path),
                                           "artifacts_sha256": {artifact.name: file_sha256(artifact)
                                                                for artifact in sorted(folder.iterdir()) if artifact.is_file()}}
                checkpoint()
                print(f'{case["id"]}/{engine}: onset F1={metrics["f1"]:.4f}, '
                      f'paired-offset F1={metrics["duration_diagnostics"]["f1"]:.4f}, '
                      f'missing={metrics["missing_notes"]}, extra={metrics["extra_notes"]}', flush=True)
        active_case = active_engine = None
        current_sources = source_hashes()
        if current_sources != report["provenance"]["source_sha256"]:
            report["source_sha256_at_finish"] = current_sources
            raise RuntimeError("Source code changed during evaluation; results are not a frozen candidate comparison")
        report["status"] = "complete"
        report["finished_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        checkpoint()
        print(json.dumps(report["micro_average"], indent=2), flush=True)
        return report
    except Exception as error:
        report["status"] = "failed"
        report["failure"] = {"case_id": active_case, "engine": active_engine,
                             "type": type(error).__name__, "message": str(error)[:2000]}
        if active_case and active_engine and report["cases"][active_case]["engines"][active_engine]["status"] == "running":
            report["cases"][active_case]["engines"][active_engine]["status"] = "failed"
        checkpoint()
        raise


if __name__ == "__main__":
    main()
