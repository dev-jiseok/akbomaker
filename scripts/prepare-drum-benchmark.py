"""Download a small, fixed MDB Drums sample for local noncommercial evaluation.

Authors: C. Southall, C. Wu, A. Lerch, J. Hockman, ISMIR 2017.
Source: https://github.com/CarlSouthall/MDBDrums
License: CC-BY-NC-SA-4.0. Never bundle these recordings with the application.
Both partitions are diagnostic subsets, NOT commercial-song accuracy estimates.
Validation tracks are held out from this improvement iteration, not necessarily
from the pretrained model's training data. Do not tune on validation results.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import ssl
import sys
from urllib.parse import quote
from urllib.request import urlopen

import soundfile as sf
import certifi

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REVISION = "b29e2d63c3a023506f4bf353c5b2e8a558eed135"
BASE = f"https://raw.githubusercontent.com/CarlSouthall/MDBDrums/{REVISION}/"
TRACKS = ("MusicDelta_80sRock", "MusicDelta_Rock", "MusicDelta_FunkJazz", "MusicDelta_Reggae", "MusicDelta_SpeedMetal")
VALIDATION_TRACKS = ("MusicDelta_Beatles", "MusicDelta_Country1", "MusicDelta_Disco", "MusicDelta_Grunge", "MusicDelta_LatinJazz")
PARTITIONS = {"development": TRACKS, "validation": VALIDATION_TRACKS}
PARTITION_SCOPES = {
    "development": "used for this improvement iteration's development; not held out from tuning or model training",
    "validation": "held out from this improvement iteration only; no claim of exclusion from model training",
}
MDB_TO_GM = {"KD": 35, "SD": 38, "SDB": 38, "SDD": 38, "SDF": 38, "SDG": 38, "SDNS": 38,
             "CHH": 42, "OHH": 46, "PHH": 44, "HIT": 50, "MHT": 48, "HFT": 43, "LFT": 41,
             "RDC": 51, "RDB": 53, "CRC": 49, "CHC": 52, "SPC": 55, "SST": 37, "TMB": 54}


def download(relative, destination):
    with urlopen(BASE + quote(relative, safe="/"), timeout=60, context=ssl.create_default_context(cafile=certifi.where())) as response:
        data = response.read(32 * 1024 * 1024 + 1)
    if len(data) > 32 * 1024 * 1024:
        raise ValueError("Unexpectedly large benchmark file")
    destination.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def parse_annotations(text, duration):
    events = []
    for line in text.splitlines():
        if not line.strip():
            continue
        onset, label = line.split()
        start = float(onset)
        if not math.isfinite(start) or start < 0 or label not in MDB_TO_GM:
            raise ValueError(f"Unsupported MDB annotation: {line}")
        if start < duration:
            events.append((start, min(start + .1, duration), MDB_TO_GM[label], 1.))
    return events


def source_files(track):
    """Use only the fixed, preselected tracks at the pinned dataset revision."""
    if track not in TRACKS + VALIDATION_TRACKS:
        raise ValueError("Track is not in the fixed benchmark selection")
    return [(f"MDB Drums/audio/drum_only/{track}_Drum.wav", "full-drums.wav"),
            (f"MDB Drums/audio/full_mix/{track}_MIX.wav", "full-mix.wav"),
            (f"MDB Drums/annotations/subclass/{track}_subclass.txt", "subclass.txt")]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accept-noncommercial", action="store_true", help="Acknowledge CC-BY-NC-SA-4.0 research-only local use")
    parser.add_argument("--partition", choices=PARTITIONS, default="development",
                        help="Fixed development or iteration-held-out validation tracks (default: development)")
    parser.add_argument("--output", type=Path,
                        help="Default: .data/drum-benchmark/mdb for development; mdb-validation for validation")
    parser.add_argument("--seconds", type=float, default=12.)
    args = parser.parse_args(argv)
    if not args.accept_noncommercial:
        parser.error("Read the source license and pass --accept-noncommercial for local noncommercial evaluation")
    if not 3 <= args.seconds <= 30:
        parser.error("Use a 3–30 second diagnostic excerpt")
    if args.output is None:
        name = "mdb" if args.partition == "development" else "mdb-validation"
        args.output = Path(".data/drum-benchmark") / name
    return args


def selection_receipt(partition, seconds):
    return {"schema_version": 1, "revision": REVISION, "partition": partition,
            "excerpt_start": 0., "excerpt_seconds": seconds,
            "tracks": list(PARTITIONS[partition])}


def ensure_compatible_output(output, partition, seconds):
    """Reject changes to a populated selection before writing any dataset files.

    Legacy development manifests lacked partition and requested duration. Accept
    them only when their pinned revision, exact track list and source durations
    prove the same selection. This preserves the original default command.
    A matching pre-download receipt also permits retrying an incomplete run;
    an unknown nonempty directory is never adopted implicitly.
    """
    if not output.exists():
        return
    if not output.is_dir():
        raise ValueError("Benchmark output must be a directory")
    if not any(output.iterdir()):
        return
    message = "Existing benchmark selection is incompatible; use a different --output directory"
    try:
        receipt_path = output / ".selection.json"
        manifest_path = output / "manifest.json"
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text())
            if receipt != selection_receipt(partition, seconds):
                raise ValueError(message)
            if not manifest_path.exists():
                return
        manifest = json.loads(manifest_path.read_text())
        cases = manifest["cases"]
        names = [case["name"] for case in cases]
        if (manifest["revision"] != REVISION or names != list(PARTITIONS[partition])
                or manifest.get("partition", "development") != partition
                or manifest.get("excerpt_start", 0.) != 0.):
            raise ValueError(message)
        if "excerpt_seconds" in manifest:
            if manifest["excerpt_seconds"] != seconds:
                raise ValueError(message)
        else:
            # Only original development manifests may omit selection metadata.
            if partition != "development" or "partition" in manifest:
                raise ValueError(message)
            for track in names:
                folder = output / track
                reference = json.loads((folder / "reference.json").read_text())
                audio = sf.info(folder / "full-drums.wav")
                expected_duration = min(audio.frames, round(seconds * audio.samplerate)) / audio.samplerate
                if (reference["revision"] != REVISION or reference["track"] != track
                        or reference["excerpt_start"] != 0.
                        or not math.isclose(reference["duration"], expected_duration, abs_tol=1e-6)):
                    raise ValueError(message)
    except (OSError, ValueError, TypeError, KeyError, ZeroDivisionError) as exc:
        raise ValueError(message) from exc


def main(argv=None):
    args = parse_args(argv)
    ensure_compatible_output(args.output, args.partition, args.seconds)
    args.output.mkdir(parents=True, exist_ok=True)
    # Record the selected revision/tracks/timeline before the first network call.
    # Retries can redownload partial files, but cannot silently change a dataset.
    receipt = args.output / ".selection.json"
    receipt_tmp = args.output / ".selection.json.tmp"
    receipt_tmp.write_text(json.dumps(selection_receipt(args.partition, args.seconds), indent=2) + "\n")
    receipt_tmp.replace(receipt)
    download("README.md", args.output / "SOURCE-README.md")
    cases = []
    for track in PARTITIONS[args.partition]:
        folder = args.output / track
        folder.mkdir(exist_ok=True)
        hashes = {}
        for relative, name in source_files(track):
            hashes[relative] = download(relative, folder / name)
        audio, sr = sf.read(folder / "full-drums.wav", dtype="float32", always_2d=True)
        frames = min(len(audio), round(args.seconds * sr))
        duration = frames / sr
        sf.write(folder / "drums.wav", audio[:frames], sr, subtype="FLOAT")
        mix, mix_sr = sf.read(folder / "full-mix.wav", dtype="float32", always_2d=True)
        if mix_sr != sr or len(mix) < frames:
            raise ValueError("Drum and mix references do not share a timeline")
        sf.write(folder / "mix.wav", mix[:frames], sr, subtype="FLOAT")
        reference = {"dataset": "MDB Drums", "track": track, "revision": REVISION,
                     "partition": args.partition, "partition_scope": PARTITION_SCOPES[args.partition],
                     "license": "CC-BY-NC-SA-4.0", "excerpt_start": 0., "duration": duration,
                     "annotation": "human subclass onsets; 100ms placeholder offsets, NOT note lengths",
                     "events": parse_annotations((folder / "subclass.txt").read_text(), duration), "source_sha256": hashes}
        (folder / "reference.json").write_text(json.dumps(reference, indent=2) + "\n")
        cases.append({"name": track, "audio": str((folder / "drums.wav").resolve()),
                      "mix": str((folder / "mix.wav").resolve()), "reference": str((folder / "reference.json").resolve())})
        print(f"Prepared {track}: {duration:.1f}s, {len(reference['events'])} human-annotated onsets", flush=True)
    manifest = {"dataset": "MDB Drums diagnostic subset", "revision": REVISION,
                "partition": args.partition, "partition_scope": PARTITION_SCOPES[args.partition],
                "excerpt_start": 0., "excerpt_seconds": args.seconds,
                "license": "CC-BY-NC-SA-4.0; local noncommercial research only", "cases": cases}
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
