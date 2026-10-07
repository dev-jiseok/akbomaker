"""Run independent CREPE CPU inference without modifying a saved project.

Install torchcrepe==0.0.24 in an isolated environment first. The model ships
inside the package; this script makes no network requests and never uploads
audio. Outputs are research drafts, not an accuracy-approved replacement.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--instrument", choices=("vocal", "bass"), required=True)
    parser.add_argument("--capacity", choices=("tiny", "full"), default="full")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("Output already exists; choose a new path to preserve previous results")
    if not args.audio.is_file() or not 1 <= args.threads <= 8:
        parser.error("Audio must exist and threads must be between 1 and 8")
    import torch
    from backend.neural_melody import transcribe_crepe
    torch.set_num_threads(args.threads)
    details = {}
    events = transcribe_crepe(args.audio, args.instrument, capacity=args.capacity, details=details)
    payload = {"instrument": args.instrument, "events": events, "runtime": details}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation protects even a path created while inference ran.
    with args.output.open("x") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({"output": str(args.output), "events": len(events), "engine": details["engine"]}))


if __name__ == "__main__":
    main()
