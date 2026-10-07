"""Run an explicitly configured ADT environment without changing SAM's packages."""
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import shutil

import mido

from .config import ROOT
from .drum_mapping import DRUM_MAP, normalize_drums


def worker_status():
    """Cheap configuration check, NOT a claim that inference/quality passed."""
    configured = bool(os.getenv("AKBO_DRUM_WORKER"))
    ready = configured and all(Path(os.getenv(key, "")).expanduser().joinpath(suffix).is_file()
                               for key, suffix in [("AKBO_DRUM_WORKER", ""), ("AKBO_DRUM_SOURCE_DIR", "adt_transcriber.py"),
                                                   ("AKBO_DRUM_MODEL_DIR", "adt_config.yaml"), ("AKBO_DRUM_MODEL_DIR", "config.json"),
                                                   ("AKBO_DRUM_MODEL_DIR", "model.safetensors")])
    return {"configured": configured, "paths_ready": bool(ready), "device": os.getenv("AKBO_DRUM_DEVICE", "cpu"),
            "warning": "설정 파일 확인만 완료했습니다. 실제 실행·정확도 보증이 아닙니다." if ready else "별도 드럼 Python 환경·소스·체크포인트 설정이 필요해요."}


def read_drum_midi(path, duration, *, raw=False):
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("드럼 음원 길이를 확인해주세요.")
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("드럼 모델 결과 파일이 너무 커요.")
    midi = mido.MidiFile(path)
    active, events, seconds = {}, [], 0.
    for message in midi:
        seconds += message.time
        if message.type not in {"note_on", "note_off"}:
            continue
        if message.channel != 9 or not 35 <= message.note <= 81:
            raise ValueError("드럼 모델은 지원되는 GM 타악기 채널의 MIDI를 반환해야 해요.")
        key = (message.channel, message.note)
        if message.type == "note_on" and message.velocity > 0:
            if key in active:
                start, velocity = active[key]
                events.append((start, seconds, message.note, velocity / 127))
            active[key] = (seconds, message.velocity)
        elif key in active:
            start, velocity = active.pop(key)
            events.append((start, seconds, message.note, velocity / 127))
        if len(events) > 30_000:
            raise ValueError("드럼 모델 결과가 너무 복잡해요.")
    for (_, pitch), (start, velocity) in active.items():
        events.append((start, min(duration, start + .1), pitch, velocity / 127))
    from .transcription import clean_events
    events = clean_events(events, duration, minimum=.001)
    if raw:
        return events
    result, review = normalize_drums(events)
    if review["unsupported_count"]:
        raise ValueError("악보에서 지원하지 않는 타악기가 있어요. 원시 MIDI와 검토 데이터를 확인해주세요.")
    return result


def transcribe_external(path, duration, *, artifacts=None, details=None, normalization=None, decoding=None, context_passes=None):
    executable = Path(os.environ["AKBO_DRUM_WORKER"]).expanduser()
    source = Path(os.environ.get("AKBO_DRUM_SOURCE_DIR", "")).expanduser()
    model = Path(os.environ.get("AKBO_DRUM_MODEL_DIR", "")).expanduser()
    normalization = normalization or os.getenv("AKBO_DRUM_NORMALIZATION", "none")
    decoding = decoding or os.getenv("AKBO_DRUM_DECODING", "greedy")
    context_passes = str(os.getenv("AKBO_DRUM_CONTEXT_PASSES", "1") if context_passes is None else context_passes)
    if normalization not in {"none", "peak"} or decoding not in {"greedy", "constrained", "guarded"} or context_passes not in {"1", "3"}:
        raise ValueError("드럼 입력 보정·디코더 설정을 확인해주세요.")
    if not executable.is_file() or not (source / "adt_transcriber.py").is_file() or not (model / "adt_config.yaml").is_file():
        raise ValueError("드럼 모델의 별도 Python·소스·로컬 체크포인트 경로를 확인해주세요.")
    with tempfile.TemporaryDirectory(prefix="akbo-drum-") as temporary:
        output = Path(temporary) / "drums.mid"
        command = [str(executable), str(ROOT / "scripts" / "transcribe-drums.py"),
                   "--source", str(source.resolve()), "--model", str(model.resolve()),
                   "--audio", str(Path(path).resolve()), "--output", str(output),
                   "--device", os.getenv("AKBO_DRUM_DEVICE", "cpu"),
                   "--normalization", normalization, "--decoding", decoding, "--context-passes", context_passes]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=600 * int(context_passes), check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ValueError(f"드럼 전용 모델 실행에 실패했거나 {10 * int(context_passes)}분 제한을 초과했어요.") from error
        if result.returncode or not output.is_file():
            # Worker tracebacks can include paths or credentials. Keep detailed
            # diagnosis in the separately operated model environment.
            raise ValueError("드럼 전용 모델이 결과를 만들지 못했어요. 모델 환경에서 준비 검사를 실행해주세요.")
        raw_events = read_drum_midi(output, duration, raw=True)
        events, review = normalize_drums(raw_events)
        metadata_file = output.with_suffix(".json")
        metadata = json.loads(metadata_file.read_text()) if metadata_file.is_file() else {}
        if details is not None:
            details.update({"model": metadata.get("model"), "mapping": metadata.get("mapping"),
                            "device": metadata.get("device"), "elapsed_seconds": metadata.get("elapsed_seconds"),
                            "decoder": metadata.get("decoder"), "decode_review": metadata.get("decode_review", {}),
                            "conditioning": metadata.get("conditioning"),
                            "sampling_review": metadata.get("sampling_review", {}),
                            "recovery_review": metadata.get("recovery_review", {}),
                            "context_passes": metadata.get("context_passes", 1),
                            "consensus": {k: v for k, v in (metadata.get("consensus") or {}).items() if k not in {"candidates", "pass_events"}},
                            "review": {k: v for k, v in review.items() if k != "unsupported_events"}})
        elif review["unsupported_count"]:
            raise ValueError("미지원 타악기가 있어요. 검토 데이터를 보관하는 드럼 채보 경로를 사용해주세요.")
        if artifacts is not None:
            artifacts = Path(artifacts)
            artifacts.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(output, artifacts / "drums.raw.mid")
            (artifacts / "drums.transcription.json").write_text(json.dumps({"events": raw_events, "review": review, "runtime": metadata}, ensure_ascii=False, indent=2))
        return events
