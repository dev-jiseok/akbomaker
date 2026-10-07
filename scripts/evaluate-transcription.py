"""Run real inference on a short reference clip, optionally against the old engine.

The built-in composition checks all six routes but is NOT real-song accuracy.
--synthetic-challenges adds harder known-tone fixtures for five pitched routes;
these are also synthetic diagnostics, not real-song accuracy measurements.
Audio and reference must describe the same performance and time interval.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import soundfile as sf
from backend import demo
from backend import evaluation_fixtures
from backend.config import INSTRUMENTS, SAMPLE_RATE
from backend.editing import generate_score
from backend.evaluation import compare_events
from backend.score import drum_events, transcribe, transcription_model
from backend.transcription import engine_description


def legacy(path, inst):
    if inst == "drums":
        return drum_events(path)
    from basic_pitch.inference import predict
    return [tuple(e[:4]) for e in predict(str(path), model_or_model_path=transcription_model(),
                                         onset_threshold=.5, frame_threshold=.3, minimum_note_length=100)[2]]


def argument_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    synthetic = parser.add_mutually_exclusive_group()
    synthetic.add_argument("--demo-seconds", type=float)
    synthetic.add_argument("--synthetic-challenges", action="store_true",
                           help="Run five fixed 8-second pitched fixtures; NOT real-song accuracy")
    parser.add_argument("--audio", type=Path)
    parser.add_argument("--reference", type=Path, help='JSON: {"events": [[start_seconds, end_seconds, MIDI_pitch, amplitude]], "bpm": 120}')
    parser.add_argument("--instrument", choices=INSTRUMENTS)
    parser.add_argument("--compare-legacy", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    return parser


def parse_args(argv=None):
    parser = argument_parser()
    args = parser.parse_args(argv)
    if args.demo_seconds is None and not args.synthetic_challenges and (not args.audio or not args.reference or not args.instrument):
        parser.error("Use --demo-seconds, --synthetic-challenges, or provide --audio, --reference and --instrument")
    if args.demo_seconds is not None and not 3 <= args.demo_seconds <= demo.DURATION:
        parser.error(f"Demo duration must be between 3 and {demo.DURATION:.2f} seconds")
    if (args.demo_seconds is not None or args.synthetic_challenges) and any((args.audio, args.reference, args.instrument)):
        parser.error("Do not combine the synthetic fixture with external audio/reference arguments")
    return args


def file_sha256(path):
    """Hash explicit local inputs/artifacts without loading a large audio file."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def pcm_sha256(path):
    """Hash canonical interleaved float32 PCM, independent of WAV timestamps."""
    digest = hashlib.sha256()
    with sf.SoundFile(path) as audio:
        while True:
            block = audio.read(65536, dtype="float32", always_2d=True)
            if not len(block):
                break
            digest.update(block.astype("<f4", copy=False).tobytes(order="C"))
    return digest.hexdigest()


def main(argv=None):
    args = parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    if args.demo_seconds is not None or args.synthetic_challenges:
        if args.synthetic_challenges:
            audio, events = evaluation_fixtures.generate_challenges()
            duration, bpm = evaluation_fixtures.DURATION, evaluation_fixtures.BPM
            dataset = "self-authored-synthetic-pitched-challenges; not real-song accuracy"
            instruments = evaluation_fixtures.INSTRUMENTS
        else:
            audio, events = demo.generate()
            duration, bpm, dataset = args.demo_seconds, demo.BPM, "self-authored-synthetic; not real-song accuracy"
            instruments = INSTRUMENTS
        cases = []
        for inst in instruments:
            path = args.output / f"input-{inst}.wav"
            sf.write(path, audio[inst][:round(duration * SAMPLE_RATE)], SAMPLE_RATE, subtype="FLOAT")
            reference = [(a, min(b, duration), p, v) for a, b, p, v in events[inst] if a < duration]
            cases.append((inst, path, reference))
    else:
        doc = json.loads(args.reference.read_text())
        duration, bpm, dataset = sf.info(args.audio).duration, doc["bpm"], str(args.reference)
        if not duration > 0 or not isinstance(bpm, int) or isinstance(bpm, bool) or not 40 <= bpm <= 240:
            argument_parser().error("Reference BPM must be an integer from 40 to 240 and the clip must not be empty")
        cases = [(args.instrument, args.audio, doc["events"])]
    report = {"dataset": dataset, "duration": duration, "bpm": bpm, "instruments": {}}
    sources = ("backend/evaluation_fixtures.py", "backend/demo.py", "backend/transcription.py", "backend/score.py",
               "backend/editing.py", "backend/rhythm.py", "backend/tablature.py", "backend/evaluation.py",
               "scripts/evaluate-transcription.py")
    report["provenance"] = {"source_sha256": {name: file_sha256(ROOT / name) for name in sources}}
    report["synthetic"] = args.demo_seconds is not None or args.synthetic_challenges
    if args.synthetic_challenges:
        report["fixture_id"] = evaluation_fixtures.FIXTURE_ID
        report["limitations"] = evaluation_fixtures.LIMITATIONS
    elif args.demo_seconds is not None:
        report["limitations"] = ["Self-authored synthetic isolated tones, not real-song accuracy or separation quality."]
    else:
        report["provenance"]["reference_file_sha256"] = file_sha256(args.reference)
    for inst, path, reference in cases:
        print(f"Transcribing {inst} with actual engine...", flush=True)
        started = time.monotonic()
        estimated = transcribe(path, inst)
        result = {"current": compare_events(reference, estimated, include_errors=True), "elapsed_seconds": round(time.monotonic() - started, 2), "engine": engine_description(inst)}
        if args.synthetic_challenges:
            result["diagnostics_by_tolerance_ms"] = {
                str(ms): compare_events(reference, estimated, tolerance=ms / 1000, include_errors=True)
                for ms in (100, 200)}
        generate_score(estimated, inst, f"Quality check · {inst}", bpm, duration, args.output, transcription=result["engine"])
        (args.output / f"{inst}.events.json").write_text(json.dumps({"reference": reference, "estimated": estimated}, ensure_ascii=False, indent=2))
        if args.compare_legacy:
            result["legacy"] = compare_events(reference, legacy(path, inst), include_errors=True)
        result["input_audio_sha256"] = file_sha256(path)
        result["input_pcm_sha256"] = pcm_sha256(path)
        result["input_sample_rate"] = sf.info(path).samplerate
        result["input_channels"] = sf.info(path).channels
        result["reference_events_sha256"] = hashlib.sha256(
            json.dumps(reference, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        result["artifacts_sha256"] = {
            artifact.name: file_sha256(artifact)
            for artifact in (args.output / f"{inst}.{suffix}" for suffix in
                             ("events.json", "score.json", "auto.json", "musicxml", "mid"))
            if artifact.is_file()}
        report["instruments"][inst] = result
        print(json.dumps({inst: result}, ensure_ascii=False), flush=True)
    (args.output / "evaluation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
