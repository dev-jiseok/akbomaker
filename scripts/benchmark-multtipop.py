"""Audit a pinned dev-only reference set; optionally score exported predictions.

Never downloads audio, launches SAM/other models, changes defaults, or reads the
held-out test split. Reports are new files; existing evidence is not overwritten.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.multtipop_evaluation import audit_manifest, evaluate_predictions, file_sha256


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New report JSON file (never overwritten)")
    parser.add_argument("--predictions", type=Path, help="Reviewed exported prediction manifest; omission performs only audit")
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("Output already exists; use a new report path")
    sources = [ROOT / name for name in ("backend/multtipop.py", "backend/multtipop_evaluation.py",
                                      "backend/evaluation.py", "scripts/benchmark-multtipop.py")]
    source_hashes = {str(path.relative_to(ROOT)): file_sha256(path) for path in sources}
    report = (evaluate_predictions(args.manifest, args.predictions) if args.predictions
              else audit_manifest(args.manifest))
    if source_hashes != {str(path.relative_to(ROOT)): file_sha256(path) for path in sources}:
        raise RuntimeError("Evaluation code changed while running; rerun with a frozen evaluator")
    report.update(created_at=datetime.now(timezone.utc).isoformat(), evaluator_source_sha256=source_hashes)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": report["status"], **report["summary"], "output": str(args.output)}, ensure_ascii=False))
    return report


if __name__ == "__main__":
    main()
