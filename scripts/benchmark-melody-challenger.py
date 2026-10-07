"""Paired pYIN/CREPE diagnostics on frozen self-authored synthetic fixtures.

Run this script with the main application Python and point --worker at an
isolated torchcrepe Python. Do not mistake these oscillators for real songs.
The complete candidate is frozen before any evaluation; no threshold search.
"""
import argparse
import datetime
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import evaluation_fixtures, pitched_validation_fixtures
from backend.evaluation import compare_events
from backend.neural_melody import sha256
from backend.transcription import melody_events
import soundfile as sf


def source_hashes():
    return {name: sha256(ROOT / name) for name in (
        "backend/neural_melody.py", "backend/transcription.py", "backend/audio_channels.py",
        "backend/melody_decoding.py", "backend/evaluation.py", "backend/evaluation_fixtures.py",
        "backend/pitched_validation_fixtures.py", "scripts/transcribe-melody-challenger.py",
        "scripts/benchmark-melody-challenger.py")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path, required=True)
    parser.add_argument("--partition", choices=("development", "validation"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("Output must be new; previous results are never overwritten")
    if not args.worker.is_file():
        parser.error("An existing isolated torchcrepe Python is required")
    if args.partition == "development":
        audio, events = evaluation_fixtures.generate_challenges()
        cases = [{"id": f"development-{inst}", "instrument": inst, "events": events[inst],
                  "audio": audio[inst], "sample_rate": evaluation_fixtures.SAMPLE_RATE}
                 for inst in ("vocal", "bass")]
    else:
        cases = [case for case in pitched_validation_fixtures.generate_validation_cases()
                 if case["instrument"] in {"vocal", "bass"}]
    args.output.mkdir(parents=True)
    report = {"status": "running", "synthetic": True, "partition": args.partition,
              "started_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "source_sha256": source_hashes(), "cases": {},
              "limitations": ["Analytical oscillators, not real-song accuracy or model-training-excluded data.",
                              "Development and validation inputs are previously reused diagnostics.",
                              "CREPE periodicity is not calibrated correctness; no model ensemble is applied.",
                              "Paired-offset metric checks existing onset matches, not a fresh matching."]}
    def checkpoint():
        temporary = args.output / "report.tmp.json"
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
        temporary.replace(args.output / "report.json")
    checkpoint()
    try:
        for case in cases:
            path = args.output / f'{case["id"]}.wav'
            sf.write(path, case["audio"], case["sample_rate"], subtype="FLOAT")
            row = {"instrument": case["instrument"], "audio_sha256": sha256(path),
                   "reference": case["events"], "engines": {}}
            report["cases"][case["id"]] = row
            checkpoint()
            details = {}
            started = time.monotonic()
            baseline = melody_events(path, case["instrument"], details=details)
            row["engines"]["pyin"] = {"events": baseline, "runtime": details,
                "elapsed_seconds": time.monotonic() - started,
                "metrics": compare_events(case["events"], baseline, tolerance=.05,
                                          include_errors=True, duration_tolerance_ratio=.2)}
            checkpoint()
            candidate = args.output / f'{case["id"]}.crepe.json'
            started = time.monotonic()
            # Do not resolve the venv interpreter symlink into system Python.
            subprocess.run([str(args.worker.absolute()), str(ROOT / "scripts/transcribe-melody-challenger.py"),
                "--audio", str(path.resolve()), "--instrument", case["instrument"], "--output", str(candidate.resolve())],
                check=True, capture_output=True, text=True, timeout=600, cwd=ROOT)
            prediction = json.loads(candidate.read_text())
            row["engines"]["crepe"] = {**prediction, "elapsed_seconds": time.monotonic() - started,
                "metrics": compare_events(case["events"], prediction["events"], tolerance=.05,
                                          include_errors=True, duration_tolerance_ratio=.2)}
            checkpoint()
            print(case["id"], {name: {key: value["metrics"][key] for key in
                  ("matched_notes", "missing_notes", "extra_notes", "f1")} for name, value in row["engines"].items()}, flush=True)
        if source_hashes() != report["source_sha256"]:
            raise RuntimeError("Source changed while comparing; this is not a frozen experiment")
        report["status"] = "complete"
        checkpoint()
        return report
    except Exception as error:
        report["status"] = "failed"
        report["failure"] = {"type": type(error).__name__, "message": str(error)[:2000]}
        if isinstance(error, subprocess.CalledProcessError):
            report["failure"]["worker_stderr"] = (error.stderr or "")[-6000:]
        checkpoint()
        raise


if __name__ == "__main__":
    main()
