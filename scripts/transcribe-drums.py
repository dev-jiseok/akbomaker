"""ADT_STR bridge. Execute with a separate, operator-provisioned Python environment.

Official integration API: https://github.com/pier-maker92/ADT_STR
No network download or installation is performed by this bridge.
"""
import argparse
from collections import Counter
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import time
from types import MethodType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.drum_mapping import adt_to_gm
from backend.adt_tokens import decode_tokens
from backend.drum_audio import prepare_audio
from backend.adt_decoding import constrained_sample
from backend.adt_recovery import METHOD as RECOVERY_METHOD, retrying_sample
from backend.drum_consensus import consensus_events
from backend.drum_timing import restore_drum_timing, LEADING_TOLERANCE_SECONDS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--normalization", choices=("none", "peak"), default="none")
    parser.add_argument("--decoding", choices=("greedy", "constrained", "guarded"), default="greedy")
    parser.add_argument("--context-passes", type=int, choices=(1, 3), default=1)
    args = parser.parse_args()
    if not all((args.model / name).is_file() for name in ("adt_config.yaml", "config.json", "model.safetensors")):
        parser.error("Expected a locally provisioned ADT_STR Hugging Face bundle")
    if not 1 <= args.threads <= 32:
        parser.error("--threads must be between 1 and 32")
    manifest_path = args.model / "akbo-model.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else None
    if manifest:
        for name in ("adt_config.yaml", "config.json", "model.safetensors"):
            with (args.model / name).open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != manifest["sha256"][name]:
                    raise ValueError(f"Model checksum mismatch: {name}")
        revision = subprocess.run(["git", "-C", str(args.source), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        if revision != manifest["source_revision"]:
            raise ValueError("ADT source revision differs from the provisioned model manifest")
    # No implicit downloads during inference; model code is an explicit local checkout.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    sys.path.insert(0, str(args.source.resolve()))
    import pretty_midi
    import soundfile as sf
    import torch
    import torchaudio
    from adt_transcriber import ADTTranscriber
    torch.set_num_threads(args.threads)
    started = time.monotonic()
    model = ADTTranscriber.from_pretrained(str(args.model.resolve()), device=args.device)
    if model.cfg["tokenizer"].get("ADTOF_mapping"):
        raise ValueError("This adapter requires the 26-class checkpoint, not collapsed ADTOF classes")
    token_cfg = model.cfg["tokenizer"]
    if [token_cfg[key] for key in ("BOS_token", "EOS_token", "pad_token", "silence_token")] != [2, 3, 1, 0]:
        raise ValueError("Unexpected ADT tokenizer special tokens")
    if args.decoding == "constrained":
        model.model._akbo_add_velocity = token_cfg["add_velocity"]
        model.model.sample = MethodType(constrained_sample, model.model)
    elif args.decoding == "guarded":
        model.model._akbo_add_velocity = token_cfg["add_velocity"]
        model.model._akbo_greedy_sample = model.model.sample
        model.model.sample = MethodType(retrying_sample, model.model)
    decoded_chunks = []
    pass_id, pass_offset, pass_chunk = 0, 0., 0
    def strict_decode(tokens):
        nonlocal pass_chunk
        notes, diagnostics = decode_tokens(tokens, input_seconds=model.input_sec,
                                           add_velocity=token_cfg["add_velocity"], max_length=model.cfg["inference"]["max_length"])
        decoded_chunks.append({"start_seconds": pass_chunk * model.input_sec - pass_offset,
                               "pass_id": pass_id, **diagnostics,
                               "sampling": getattr(model.model, "_akbo_decoding_stats", None),
                               "recovery": getattr(model.model, "_akbo_recovery_stats", None)})
        pass_chunk += 1
        return torch.tensor(notes, dtype=torch.float32).reshape(-1, 4)
    model.tokenizer.decode = strict_decode
    audio, sr = sf.read(args.audio, dtype="float32", always_2d=True)
    # Match the official evaluation order: downmix, resample, then peak scale.
    # All routes use exactly one resample; passing the target rate below avoids
    # a second resample inside ADTTranscriber.
    audio, _ = prepare_audio(audio, normalization="none")
    if sr != model.sample_rate:
        audio = torchaudio.transforms.Resample(sr, model.sample_rate)(torch.from_numpy(audio)).numpy()
        sr = model.sample_rate
    audio, conditioning = prepare_audio(audio, normalization=args.normalization)
    # Tensor input avoids TorchCodec / FFmpeg loading differences on macOS.
    waveform = torch.from_numpy(audio.copy())
    silent = conditioning["silent_input"]
    agreement = None
    timing_passes = []
    with tempfile.TemporaryDirectory(prefix="adt-output-") as temporary:
        custom_counts = Counter()
        passes = []
        duration = len(audio) / sr
        for pass_id in range(args.context_passes):
            # Shifting the chunk boundaries gives each hit different surrounding
            # context, while the original audio samples and gain stay unchanged.
            padding = round(pass_id * model.chunk_samples / args.context_passes)
            pass_offset, pass_chunk = padding / sr, 0
            current = []
            if not silent:
                padded = torch.nn.functional.pad(waveform, (padding, 0))
                result = model.transcribe(padded, sample_rate=sr, output_dir=temporary, batch_size=1)
                midi = pretty_midi.PrettyMIDI(str(result))
                for instrument in midi.instruments:
                    if not instrument.is_drum:
                        raise ValueError("ADT output contains a non-drum track")
                    for note in instrument.notes:
                        custom_counts[note.pitch] += 1
                        current.append((note.start, note.end, adt_to_gm(note.pitch), note.velocity / 127))
            current, timing = restore_drum_timing(current, pass_offset, duration)
            timing_passes.append({"pass_id": pass_id, **timing})
            passes.append(sorted(current))
        events = passes[0]
        if args.context_passes > 1:
            events, agreement = consensus_events(passes)
        midi = pretty_midi.PrettyMIDI()
        track = pretty_midi.Instrument(program=0, is_drum=True)
        for start, end, pitch, amplitude in events:
            track.notes.append(pretty_midi.Note(velocity=max(1, round(amplitude * 127)), pitch=pitch,
                                               start=start, end=min(duration, end)))
        midi.instruments.append(track)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        midi.write(str(args.output))
    metadata = {"engine": "adt-str", "mapping": "adt-custom-26-to-gm-v1", "device": args.device,
                "elapsed_seconds": round(time.monotonic() - started, 3), "custom_pitch_counts": dict(custom_counts),
                "decoder": {"constrained": "constrained-triples-v1", "guarded": RECOVERY_METHOD,
                            "greedy": "strict-triples-v1"}[args.decoding],
                "recovery_review": {"attempted_chunks": sum(bool((c["recovery"] or {}).get("attempted")) for c in decoded_chunks),
                                    "replaced_chunks": sum((c["recovery"] or {}).get("selected") == "constrained" for c in decoded_chunks),
                                    "unresolved_chunks": sum(bool((c["recovery"] or {}).get("unresolved")) for c in decoded_chunks),
                                    "syntax_is_accuracy": False},
                "silent_input": silent, "conditioning": conditioning,
                "context_passes": args.context_passes,
                "timing_review": {"leading_tolerance_seconds": LEADING_TOLERANCE_SECONDS,
                                  **{key: sum(p[key] for p in timing_passes) for key in
                                     ("adjusted_leading", "dropped_leading", "dropped_trailing", "clipped_trailing")},
                                  "passes": timing_passes},
                "sampling_review": {"forced_tokens": sum((c["sampling"] or {}).get("forced_tokens", 0) for c in decoded_chunks),
                                    "duplicate_pitch_avoided": sum((c["sampling"] or {}).get("duplicate_pitch_avoided", 0) for c in decoded_chunks),
                                    "budget_limited_chunks": sum(bool((c["sampling"] or {}).get("budget_limited")) for c in decoded_chunks)},
                "decode_review": {"invalid_tokens": sum(c["invalid_tokens"] for c in decoded_chunks),
                                  "incomplete_events": sum(c["incomplete_events"] for c in decoded_chunks),
                                  "nonmonotonic_events": sum(c["nonmonotonic_events"] for c in decoded_chunks),
                                  "truncated_chunks": sum(c["truncated"] for c in decoded_chunks)},
                "model": manifest, "packages": {name: importlib.metadata.version(name) for name in ("torch", "torchaudio", "transformers", "numpy")}}
    args.output.with_suffix(".json").write_text(json.dumps({**metadata, "consensus": agreement, "decoded_chunks": decoded_chunks}, indent=2) + "\n")
    print(json.dumps(metadata))


if __name__ == "__main__":
    main()
