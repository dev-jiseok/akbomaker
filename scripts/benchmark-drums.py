"""Compare real drum onsets across legacy, multiband and an actual ADT worker.

Requires the explicit AKBO_DRUM_* environment configuration. Inputs are local;
this script never downloads a model, trains on references, or adjusts thresholds.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.drum_mapping import DRUM_MAP
from backend.drum_fusion import recover_hihats
from backend.drum_evidence import suppress_unsupported_kicks
from backend.drum_worker import read_drum_midi, transcribe_external, worker_status
from backend.evaluation import compare_events
from backend.score import drum_events
from backend.transcription import multiband_drums


def canonical(events):
    # Do not drop unsupported classes or duplicates to inflate precision.
    return [(a, b, DRUM_MAP.get(p, p), v) for a, b, p, v in events]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--include-mix", action="store_true", help="Also test original full mixes against exactly the same human drum onsets")
    args = parser.parse_args()
    if not worker_status()["paths_ready"]:
        parser.error("Prepare the isolated ADT_STR worker and set AKBO_DRUM_* first")
    manifest = json.loads(args.manifest.read_text())
    context_passes = int(os.getenv("AKBO_DRUM_CONTEXT_PASSES", "1"))
    app_equivalent = (os.getenv("AKBO_DRUM_NORMALIZATION", "none") == "peak"
                      and os.getenv("AKBO_DRUM_DECODING", "greedy") == "greedy" and context_passes == 1)
    fusion_name = "hybrid" if app_equivalent else "hihat-fusion-experiment"
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"dataset": manifest["dataset"], "dataset_revision": manifest.get("revision"), "license": manifest.get("license"),
              "partition": manifest.get("partition", "development"), "partition_scope": manifest.get("partition_scope", "development diagnostic excerpts"),
              "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
              "configuration": {"normalization": os.getenv("AKBO_DRUM_NORMALIZATION", "none"), "decoding": os.getenv("AKBO_DRUM_DECODING", "greedy"), "context_passes": context_passes},
              "fusion_matches_application_hybrid": app_equivalent,
              "source_code_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                                     ("scripts/benchmark-drums.py", "scripts/transcribe-drums.py", "backend/drum_audio.py",
                                      "backend/drum_fusion.py", "backend/drum_worker.py", "backend/transcription.py",
                                      "backend/drum_mapping.py", "backend/drum_consensus.py", "backend/drum_evidence.py", "backend/drum_timing.py", "backend/adt_tokens.py", "backend/adt_decoding.py", "backend/adt_recovery.py", "backend/evaluation.py")},
              "metric": "Per-pitch onset F1 ±50ms; kit11 aliases normalized, other GM retained. NOT score/offset/technique accuracy.",
              "limitations": ["Small diagnostic excerpts, not a held-out statistical benchmark", "Reference offsets are placeholders", "No SAM separation involved in these recording/mix comparisons"],
              "cases": []}
    totals = {}
    for case in manifest["cases"]:
        reference_doc = json.loads(Path(case["reference"]).read_text())
        reference = canonical(reference_doc["events"])
        for source in (["audio", "mix"] if args.include_mix else ["audio"]):
            path = Path(case[source])
            duration = sf.info(path).duration
            if abs(duration - reference_doc["duration"]) > .005:
                raise ValueError("Audio and reference durations differ")
            folder = args.output / case["name"] / source
            folder.mkdir(parents=True, exist_ok=True)
            samples, sample_rate = sf.read(path, dtype="float32", always_2d=True)
            # The legacy production route received normalized mono WAVs too.
            path = folder / "input.wav"
            sf.write(path, samples.mean(axis=1), sample_rate, subtype="FLOAT")
            result = {"name": case["name"], "source": source, "duration": duration, "engines": {}}
            cached = {}
            for name, run in [("legacy", drum_events), ("multiband", multiband_drums), ("adt-str", None), ("adt-str-evidence", None), (fusion_name, None)]:
                started = time.monotonic()
                metadata = {}
                if name == "adt-str-evidence":
                    events, metadata = suppress_unsupported_kicks(cached["adt-str"], samples.mean(axis=1), sample_rate)
                elif name == fusion_name:
                    silent = bool((result["engines"]["adt-str"]["runtime"].get("conditioning") or {}).get("silent_input"))
                    events, metadata = recover_hihats(cached["adt-str-evidence"], [] if silent else cached["multiband"])
                elif run is None:
                    transcribe_external(path, duration, artifacts=folder, details=metadata)
                    # Evaluate raw GM results, including percussion not shown by the editor.
                    events = read_drum_midi(folder / "drums.raw.mid", duration, raw=True)
                else:
                    events = run(path)
                cached[name] = events
                metrics = compare_events(reference, canonical(events), include_errors=True)
                elapsed = round(time.monotonic() - started, 3)
                if name == "adt-str-evidence":
                    metadata = {"kick_evidence": metadata, "base_runtime": result["engines"]["adt-str"]["runtime"],
                                "postprocessing_seconds": elapsed}
                    elapsed += result["engines"]["adt-str"]["elapsed_seconds"]
                if name == fusion_name:
                    metadata = {"recovery": metadata, "base_runtime": result["engines"]["adt-str"]["runtime"],
                                "fusion_seconds": elapsed}
                    elapsed += sum(result["engines"][key]["elapsed_seconds"] for key in ("adt-str-evidence", "multiband"))
                result["engines"][name] = {"metrics": metrics, "elapsed_seconds": elapsed, "runtime": metadata}
                (folder / f"{name}.events.json").write_text(json.dumps({"reference": reference, "estimated": canonical(events)}, indent=2))
                total = totals.setdefault(f"{source}/{name}", {"matched": 0, "reference": 0, "estimated": 0, "elapsed_seconds": 0.})
                for key, metric in [("matched", "matched_notes"), ("reference", "reference_notes"), ("estimated", "estimated_notes")]:
                    total[key] += metrics[metric]
                total["elapsed_seconds"] += elapsed
                print(f'{case["name"]}/{source} {name}: F1={metrics["f1"]}, missing={metrics["missing_notes"]}, extra={metrics["extra_notes"]}, {elapsed:.1f}s', flush=True)
            report["cases"].append(result)
            # Keep completed cases if a later model invocation fails.
            (args.output / "evaluation.json").write_text(json.dumps(report, indent=2))
    for total in totals.values():
        total["precision"] = total["matched"] / total["estimated"] if total["estimated"] else 0.
        total["recall"] = total["matched"] / total["reference"] if total["reference"] else 0.
        total["f1"] = 2 * total["matched"] / (total["reference"] + total["estimated"]) if total["reference"] + total["estimated"] else 0.
    report["micro_average"] = totals
    (args.output / "evaluation.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(totals, indent=2))


if __name__ == "__main__":
    main()
