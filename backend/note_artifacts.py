"""Bounded, source-linked evidence before notation changes event timing.

These are the final events passed to notation, after instrument post-processing;
they are neither ground truth nor unfiltered network predictions. Drum model
output remains separately available in drums.raw.mid / drums.transcription.json.
"""
import hashlib
from datetime import datetime, timezone
from importlib import metadata
import json
import math
from numbers import Real
from pathlib import Path
import re
from uuid import uuid4

from .config import INSTRUMENTS, MAX_AUDIO_SECONDS

SCHEMA_VERSION = 1
MAX_EVENTS = 30_000
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
AMPLITUDE_NOTE = "Relative event strength / velocity, not a calibrated probability or confidence in pitch, drum class, onset, or correctness."


def audio_sha256(path: Path) -> str:
    """Hash the exact input bytes in bounded memory, not an absolute path."""
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _label(value, maximum=100):
    # Deliberately omit free text, paths, URLs and arbitrary runtime diagnostics.
    return value if isinstance(value, str) and len(value) <= maximum and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", value) else None


def safe_provenance(description):
    result = {key: value for key in ("engine", "profile", "mapping", "decoder", "device", "postprocessing", "preprocessing")
              if (value := _label(description.get(key))) is not None}
    channels = description.get("channel_preprocessing")
    if isinstance(channels, dict) and channels.get("method") == "preserve-stereo-cancellation-v1":
        count, selected, used = (channels.get(k) for k in ("input_channels", "selected_channel", "used_channel_fallback"))
        if (type(count) is int and 1 <= count <= 32 and type(used) is bool
                and ((used and count == 2 and type(selected) is int and 0 <= selected < count)
                     or (not used and selected is None))):
            result["channel_preprocessing"] = {"method": channels["method"], "input_channels": count,
                                               "used_channel_fallback": used, "selected_channel": selected}
    if type(description.get("context_passes")) is int and description["context_passes"] in (1, 3):
        result["context_passes"] = description["context_passes"]
    conditioning = description.get("conditioning")
    if isinstance(conditioning, dict) and _label(conditioning.get("normalization")) in {"none", "peak"}:
        result["normalization"] = conditioning["normalization"]
    model = description.get("model")
    if isinstance(model, dict):
        model_info = {key: value for key in ("revision", "source_revision", "variant")
                      if (value := _label(model.get(key))) is not None}
        repo = model.get("repo")
        if isinstance(repo, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}/[A-Za-z0-9_.-]{1,80}", repo):
            model_info["repo"] = repo
        hashes = model.get("sha256", {})
        if isinstance(hashes, dict):
            model_info["sha256"] = {name: hashes[name] for name in ("config.json", "adt_config.yaml", "model.safetensors")
                                    if isinstance(hashes.get(name), str) and re.fullmatch(r"[a-f0-9]{64}", hashes[name])}
        if model_info:
            result["model"] = model_info
    # These are host package versions, not a claim that a particular checkpoint
    # is verified. ADT checkpoint hashes come only from its worker manifest above.
    packages = {}
    for package in ("numpy", "librosa", "basic-pitch", "onnxruntime", "soundfile"):
        try:
            version = _label(metadata.version(package))
        except metadata.PackageNotFoundError:
            continue
        if version:
            packages[package] = version
    result["host_packages"] = packages
    return result


def write_note_artifact(folder, events, *, instrument, duration, source, description):
    """Validate and stage one auditable input-to-notation JSON; never truncate.

    Malformed or excessive output fails before any existing score is replaced.
    Returns the bounded native-Python events also used to generate that score.
    """
    if instrument not in INSTRUMENTS:
        raise ValueError("지원하지 않는 채보 악기예요.")
    if isinstance(duration, bool) or not isinstance(duration, Real) or not math.isfinite(duration) or not 0 < duration <= MAX_AUDIO_SECONDS:
        raise ValueError("채보 원본 음원 길이가 유효하지 않아요.")
    source = Path(source)
    if source.name not in {f"{instrument}.wav", "original.wav"}:
        raise ValueError("채보 원본 파일 정보를 확인해주세요.")
    bounded = []
    for event in events:
        if len(bounded) >= MAX_EVENTS:
            raise ValueError("원시 채보 결과가 30,000개 음표 제한을 초과해요.")
        if not isinstance(event, (tuple, list)) or len(event) != 4:
            raise ValueError("원시 채보 음표 형식이 올바르지 않아요.")
        if any(isinstance(v, bool) or not isinstance(v, Real) or not math.isfinite(v) for v in event):
            raise ValueError("원시 채보 음표는 유한한 숫자여야 해요.")
        start, end, pitch, amplitude = map(float, event)
        if not 0 <= start < end <= duration or not pitch.is_integer() or not 0 <= pitch <= 127 or not 0 < amplitude <= 1:
            raise ValueError("원시 채보 음표가 음원·음정·강도 범위를 벗어나요.")
        bounded.append([start, end, int(pitch), amplitude])
    document = {
        "schema": "akbo.pre-quantization-notes", "schema_version": SCHEMA_VERSION,
        "artifact_id": uuid4().hex, "created_at": datetime.now(timezone.utc).isoformat(),
        "instrument": instrument, "duration": float(duration), "event_count": len(bounded),
        "source": {"file": source.name, "kind": "original" if source.name == "original.wav" else "stem",
                   "instrument": None if source.name == "original.wav" else instrument, "sha256": audio_sha256(source)},
        "provenance": safe_provenance(description),
        "semantics": {"stage": "input-to-notation-after-instrument-postprocessing", "time_unit": "seconds",
                      "time_origin": "source-audio-start-before-score-offset", "pitch": "MIDI note; GM percussion for drums",
                      "amplitude": AMPLITUDE_NOTE, "confidence_available": False,
                      "ground_truth": False, "reflects_manual_score_edits": False},
        "event_fields": ["start", "end", "pitch", "amplitude"], "events": bounded,
    }
    payload = json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_ARTIFACT_BYTES:
        raise ValueError("원시 채보 결과 파일이 크기 제한을 초과해요.")
    (Path(folder) / f"{instrument}.notes.json").write_bytes(payload)
    return bounded
