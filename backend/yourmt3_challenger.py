"""Offline, explicitly opt-in YourMT3+ multitrack CPU challenger.

Uses the author's unchanged inference/detokenization code and one SHA-pinned
Apache-2.0 checkpoint. No automatic model downloads, external audio upload,
program relabeling, note union, or application-default replacement occurs.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from importlib import metadata
from importlib.machinery import ModuleSpec
import json
import hashlib
import math
import os
from pathlib import Path
import sys
import tempfile
import shutil
from types import ModuleType

from .neural_melody import sha256

METHOD = "yourmt3-plus-moe-challenger-v1"
REVISION = "5e66c1ea173a8186e0d20432b841d3180cc015b5"
CHECKPOINT_SHA256 = "ae38e415c79efd5592dcb9b658cdb99ddb11d4c4e1eaa364cab04a052473fc25"
SOURCE_MANIFEST_SHA256 = "6be94d6389805d0e3be18ae9eea43f8eac900808339a1bb4dbc0fea05df697b4"
CHECKPOINT_RELATIVE = "amt/logs/2024/mc13_256_g4_all_v7_mt3f_sqr_rms_moe_wf4_n8k2_silu_rope_rp_b36_nops/checkpoints/last.ckpt"
MAX_SECONDS = 600


def isolate_source_namespaces(source):
    """Do not reuse cached generic modules or merge ambient namespace paths."""
    names = ("model", "utils", "config", "extras")
    if any(key == name or key.startswith(name + ".") for key in sys.modules for name in names):
        raise RuntimeError("YourMT3 requires a fresh isolated worker; model/utils/config namespaces are occupied")
    for name in names:
        module = ModuleType(name)
        module.__path__ = [str(Path(source) / name)]
        module.__package__ = name
        module.__spec__ = ModuleSpec(name, loader=None, is_package=True)
        module.__spec__.submodule_search_locations = module.__path__
        sys.modules[name] = module


def verify_runtime(runtime):
    """Require frozen local source/checkpoint identity before importing code."""
    runtime = Path(runtime)
    manifest = json.loads((runtime / "manifest.json").read_text())
    if (manifest.get("revision") != REVISION or manifest.get("checkpoint") != CHECKPOINT_RELATIVE
            or manifest.get("checkpoint_sha256") != CHECKPOINT_SHA256):
        raise ValueError("Unsupported YourMT3 source or checkpoint identity")
    source = runtime / "source"
    entries = manifest.get("files", [])
    # Anchor the entire author-verified source list in code, not mutable local
    # manifest assertions. Changing source + its recorded hash must still fail.
    canonical = json.dumps(sorted(entries, key=lambda item: item["path"]), sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(canonical.encode()).hexdigest() != SOURCE_MANIFEST_SHA256:
        raise ValueError("YourMT3 source manifest does not match the pinned author snapshot")
    checked = {}
    for item in entries:
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe YourMT3 manifest path")
        path = source / relative
        if not path.resolve().is_relative_to(source.resolve()):
            raise ValueError("YourMT3 source path escapes the runtime")
        if not path.is_file() or path.stat().st_size != item["bytes"] or sha256(path) != item["sha256"]:
            raise ValueError(f"YourMT3 runtime file changed: {relative}")
        checked[item["path"]] = item["sha256"]
    required = {"amt/src/model/ymt3.py", "amt/src/utils/task_manager.py",
                "amt/src/utils/event2note.py", "amt/src/utils/note2event.py", CHECKPOINT_RELATIVE}
    if not required.issubset(checked) or checked[CHECKPOINT_RELATIVE] != CHECKPOINT_SHA256:
        raise ValueError("Incomplete YourMT3 runtime manifest")
    actual_python = {str(path.relative_to(source)) for path in source.rglob("*.py")}
    expected_python = {path for path in checked if path.endswith(".py")}
    if actual_python != expected_python:
        raise ValueError("Unexpected Python source in YourMT3 runtime")
    return source, checked


def instrument_for_program(program, is_drum=False):
    """Conservative mapping; retain unmatched families as other, never guess.

    YourMT3 reserves programs 100/101 for lead/chorus singing, unlike GM FX.
    Synth bass stays bass; string/organ/FX families are NOT all 'synthesizer'.
    """
    if is_drum:
        return "drums"
    if program in {100, 101}:
        return "vocal"
    if 0 <= program <= 7:
        return "piano"
    if 24 <= program <= 31:
        return "guitar"
    if 32 <= program <= 39:
        return "bass"
    if 80 <= program <= 95:
        return "synthesizer"
    return "other"


def serialize_notes(notes, duration):
    """Keep program identity and raw times; reject invalid/padded-tail notes."""
    if not math.isfinite(duration) or not 0 < duration <= MAX_SECONDS:
        raise ValueError("Invalid YourMT3 audio duration")
    result, rejected, clipped = [], 0, 0
    for note in notes:
        values = (note.onset, note.offset, note.pitch, note.program, note.velocity)
        if (not all(math.isfinite(float(value)) for value in values)
                or not 0 <= note.pitch <= 127 or int(note.pitch) != note.pitch
                or not 0 <= note.program <= 128 or int(note.program) != note.program
                or not 0 <= note.onset < duration or note.offset <= note.onset or note.velocity <= 0):
            rejected += 1
            continue
        if note.offset > duration:
            clipped += 1
        result.append({"start": float(note.onset), "end": min(duration, float(note.offset)),
                       "pitch": int(note.pitch), "program": int(note.program),
                       "is_drum": bool(note.is_drum), "velocity_token": int(note.velocity),
                       "instrument": instrument_for_program(int(note.program), bool(note.is_drum))})
    result.sort(key=lambda item: (item["start"], item["program"], item["pitch"], item["end"]))
    return result, {"invalid_or_padded_notes_rejected": rejected, "offsets_clipped_to_audio": clipped}


def load_model(runtime):
    """Use restricted unpickling and strict weights; never weights_only=False."""
    source, files = verify_runtime(runtime)
    # Python may trust a timestamp-valid .pyc without rechecking source hashes.
    # Import only freshly copied verified .py files, never cached bytecode or
    # additional code left beside the model. Keep this directory with model.
    verified_source = tempfile.TemporaryDirectory(prefix="akbo-yourmt3-source-")
    for relative, expected_hash in files.items():
        if not relative.endswith(".py"):
            continue
        destination = Path(verified_source.name) / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, destination)
        if sha256(destination) != expected_hash:
            verified_source.cleanup()
            raise ValueError("YourMT3 source changed while preparing isolated imports")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["WANDB_MODE"] = "disabled"
    os.environ["WANDB_DISABLED"] = "true"
    isolate_source_namespaces(Path(verified_source.name) / "amt/src")
    import numpy as np
    import torch
    from utils.task_manager import TaskManager
    from utils.tokenizer import NoteEventTokenizer
    from utils.note_event_dataclasses import Event, EventRange
    from utils.event_codec import FastCodec
    from model.ymt3 import YourMT3
    allowed = [TaskManager, NoteEventTokenizer, Event, EventRange, FastCodec, np.ndarray, np.dtype,
               np.core.multiarray._reconstruct, np.core.multiarray.scalar,
               type(np.dtype(np.int64)), type(np.dtype(np.float64)), type(np.dtype(np.float32))]
    with torch.serialization.safe_globals(allowed):
        checkpoint = torch.load(source / CHECKPOINT_RELATIVE, map_location="cpu", weights_only=True)
    if sha256(source / CHECKPOINT_RELATIVE) != CHECKPOINT_SHA256:
        raise ValueError("YourMT3 checkpoint changed during loading")
    hparams = deepcopy(checkpoint["hyper_parameters"])
    # Reconstruct the exact checkpoint architecture, not guessed new defaults.
    hparams.update(write_output_dir=None, pretrained=False, test_pitch_shift_layer=None)
    hparams["shared_cfg"]["WANDB"]["mode"] = "disabled"
    model = YourMT3(**hparams).cpu()
    state = {key: value for key, value in checkpoint["state_dict"].items() if not key.startswith("pitchshift.")}
    # Some released configs retain training-only pitchshift state. Nothing else
    # may be silently ignored (upstream demo uses strict=False).
    model.load_state_dict(state, strict=True)
    model.eval()
    model._akbo_verified_source = verified_source
    audit = {"engine": METHOD, "experimental": True, "device": "cpu", "precision": "float32",
             "source": "https://huggingface.co/spaces/mimbres/YourMT3", "source_revision": REVISION,
             "checkpoint_sha256": CHECKPOINT_SHA256, "runtime_files_sha256": files,
             "weights_only": True, "strict_state_dict": True, "default_replacement": False,
             "requires_isolated_worker": True, "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
             "packages": {name: metadata.version(name) for name in
                          ("torch", "torchaudio", "transformers", "numpy", "pytorch-lightning")},
             "limitations": ["Independent multitrack model, not an accuracy-validated default replacement.",
                             "Synth instruments may be classified as other timbral families; raw programs are retained.",
                             "Velocity token is not a calibrated note confidence or expressive velocity.",
                             "Author token merging and note-overlap handling are retained without note union with baseline."]}
    return model, audit


def transcribe_with_model(model, path):
    import numpy as np
    import soundfile as sf
    import torch
    import torchaudio
    from .audio_channels import mono_with_cancellation_guard
    from utils.audio import slice_padded_array
    from utils.note2event import mix_notes
    from utils.event2note import merge_zipped_note_events_and_ties_to_notes
    info = sf.info(path)
    if not 0 < info.duration <= MAX_SECONDS or not 1 <= info.channels <= 8 or not 8000 <= info.samplerate <= 192000:
        raise ValueError("Unsupported or oversized YourMT3 audio")
    source_hash = sha256(path)
    audio, rate = sf.read(path, dtype="float32", always_2d=True)
    mono, channels = mono_with_cancellation_guard(audio)
    rate_model = model.audio_cfg["sample_rate"]
    waveform = torchaudio.functional.resample(torch.from_numpy(mono).unsqueeze(0), rate, rate_model)
    length = model.audio_cfg["input_frames"]
    segments = slice_padded_array(waveform.numpy(), length, length)
    segments = torch.from_numpy(segments.astype("float32")).unsqueeze(1)
    with torch.inference_mode():
        predictions, _ = model.inference_file(bsz=1, audio_segments=segments)
    starts = [length * index / rate_model for index in range(len(segments))]
    note_channels, errors = [], Counter()
    for channel in range(model.task_manager.num_decoding_channels):
        arrays = [batch[:, channel, :] for batch in predictions]
        zipped, _, decode_errors = model.task_manager.detokenize_list_batches(arrays, starts, return_events=True)
        notes, merge_errors = merge_zipped_note_events_and_ties_to_notes(zipped)
        note_channels.append(notes)
        errors.update(decode_errors)
        errors.update(merge_errors)
    notes, sanitation = serialize_notes(mix_notes(note_channels), info.duration)
    if sha256(path) != source_hash:
        raise RuntimeError("Input audio changed during YourMT3 inference; refusing stale output")
    audit = {"source_sha256": source_hash, "duration_seconds": info.duration,
             "channel_preprocessing": channels, "segment_count": len(segments),
             "segment_samples": length, "sample_rate": rate_model, "batch_size": 1,
             "decoding_errors": dict(errors), "note_count": len(notes),
             "counts_by_instrument": dict(Counter(note["instrument"] for note in notes)), **sanitation}
    return notes, audit
