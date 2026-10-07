"""Explicit, offline YourMT3+ challenger; writes a new JSON draft, not a project.

Requires a separately prepared author-source runtime and verified manifest.
--audio can be repeated to amortize model loading; output is a new directory.
"""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--audio", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("Output directory must be new; previous drafts are never overwritten")
    if not 1 <= args.threads <= 8 or any(not path.is_file() for path in args.audio):
        parser.error("Audio inputs must exist and threads must be between 1 and 8")
    if len({path.stem for path in args.audio}) != len(args.audio):
        parser.error("Input basenames must be unique")
    import torch
    from backend.yourmt3_challenger import load_model, transcribe_with_model
    torch.set_num_threads(args.threads)
    torch.manual_seed(0)
    started = time.monotonic()
    model, provenance = load_model(args.runtime)
    provenance["load_seconds"] = time.monotonic() - started
    args.output.mkdir(parents=True)
    (args.output / "runtime.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2))
    for path in args.audio:
        started = time.monotonic()
        notes, details = transcribe_with_model(model, path)
        details["elapsed_seconds"] = time.monotonic() - started
        with (args.output / f"{path.stem}.json").open("x") as stream:
            json.dump({"notes": notes, "runtime": details}, stream, ensure_ascii=False, indent=2, allow_nan=False)
        print(json.dumps({"input": path.name, "notes": len(notes), "counts": details["counts_by_instrument"],
                          "seconds": details["elapsed_seconds"]}), flush=True)


if __name__ == "__main__":
    main()
