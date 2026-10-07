"""Read-only, bounded comparison of saved transcription evidence and scores.

The hash verifies only the exact input named in the note artifact. It does not
verify the ancestry of other stems, prove accuracy, or run any audio model.
"""
import hashlib
import json
import math
import os
import re
import stat

from . import store
from .config import INSTRUMENTS, MAX_AUDIO_SECONDS, SAMPLE_RATE
from .note_artifacts import MAX_ARTIFACT_BYTES, MAX_EVENTS

MAX_SCORE_BYTES = 16 * 1024 * 1024
MAX_SOURCE_BYTES = (MAX_AUDIO_SECONDS + 1) * SAMPLE_RATE * 8 + 1024 * 1024
MAX_WINDOW_EVENTS = 1500
BUSY = {"queued", "running", "transcribing", "analyzing"}


class ReviewError(ValueError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def _invalid():
    return ReviewError(409, "저장된 채보 검토 데이터가 올바르지 않아요. 다시 채보한 뒤 확인해주세요.")


def _changed():
    return ReviewError(409, "검토 중 음원이나 악보가 변경됐어요. 작업이 끝나면 다시 열어주세요.")


def _number(value, minimum, maximum):
    if type(value) not in (int, float):
        raise _invalid()
    try:
        valid = math.isfinite(value) and minimum <= value <= maximum
    except OverflowError:
        valid = False
    if not valid:
        raise _invalid()
    return value


def _integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise _invalid()
    return value


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[\w-]{1,80}", value):
        raise _invalid()
    return value


def _stamp(result):
    return (result.st_dev, result.st_ino, result.st_size, result.st_mtime_ns, result.st_ctime_ns)


def _file_stamp(path, maximum, *, missing=None):
    try:
        result = path.lstat()
    except FileNotFoundError:
        if missing is None:
            return None
        raise ReviewError(*missing) from None
    except OSError:
        raise _invalid() from None
    if not stat.S_ISREG(result.st_mode) or not 0 < result.st_size <= maximum:
        raise _invalid()
    return _stamp(result)


def _open_verified(path, expected):
    """Never follow a last-component symlink or read a device/FIFO."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise _changed() from None
    stream = os.fdopen(descriptor, "rb")
    actual = os.fstat(stream.fileno())
    if not stat.S_ISREG(actual.st_mode) or _stamp(actual) != expected:
        stream.close()
        raise _changed()
    return stream


def _json_file(path, maximum, missing):
    fingerprint = _file_stamp(path, maximum, missing=missing)
    with _open_verified(path, fingerprint) as stream:
        data = stream.read(maximum + 1)
        if len(data) > maximum or _stamp(os.fstat(stream.fileno())) != fingerprint:
            raise _changed()

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("non-finite JSON number")
        return result

    def invalid_constant(value):
        raise ValueError("non-finite JSON number")

    try:
        document = json.loads(data, object_pairs_hook=unique, parse_float=finite_float,
                              parse_constant=invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise _invalid() from None
    if not isinstance(document, dict):
        raise _invalid()
    return document, fingerprint, hashlib.sha256(data).hexdigest()


def _audio_sha256(path, expected):
    digest = hashlib.sha256()
    total = 0
    with _open_verified(path, expected) as stream:
        while chunk := stream.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_SOURCE_BYTES:
                raise _changed()
            digest.update(chunk)
        if total != expected[2] or _stamp(os.fstat(stream.fileno())) != expected:
            raise _changed()
    return digest.hexdigest()


def _available(job, inst, active_jobs):
    if inst not in INSTRUMENTS:
        raise ReviewError(404, "지원하지 않는 악기예요.")
    if job["id"] in active_jobs or job.get("status") in BUSY:
        raise ReviewError(409, "음원·악보 처리가 끝나면 채보 검토를 열어주세요.")
    if job.get("demo") or job.get("source_type") == "musicxml":
        raise ReviewError(404, "이 프로젝트에는 실제 음원 채보 검토 데이터가 없어요.")
    stem = next((item for item in job.get("stems", []) if item.get("id") == inst), None)
    info = stem.get("score_transcription") if stem else None
    if not stem or not isinstance(info, dict) or info.get("raw_events") is not True:
        raise ReviewError(404, "채보 당시 음표 데이터가 없어요. 다시 채보하면 검토할 수 있어요.")
    # A failed multi-file publication cannot prove that the retained evidence
    # and score are one generation. Keep the old score viewable, but fail closed
    # here until a complete successful transcription is available.
    if stem.get("score_status") != "ready":
        raise ReviewError(409, "이 악보의 채보가 정상 완료되지 않아 검토 데이터를 확인할 수 없어요. 다시 채보해주세요.")
    return stem


def _window(duration, start, seconds):
    try:
        _number(start, 0, MAX_AUDIO_SECONDS)
        _number(seconds, .25, 30)
        if start >= duration:
            raise _invalid()
    except ReviewError:
        raise ReviewError(422, "검토 시작 위치는 곡 안으로, 길이는 0.25~30초로 선택해주세요.") from None
    # Query parameters and JSON bodies may spell the same time as 0.0 or 0;
    # normalize both so snapshot digests do not depend on HTTP encoding.
    return {"start": float(start), "end": float(min(duration, start + seconds))}


def _recognized(document, inst, duration):
    if (document.get("schema") != "akbo.pre-quantization-notes"
            or type(document.get("schema_version")) is not int or document["schema_version"] != 1
            or document.get("instrument") != inst
            or _number(document.get("duration"), 0, MAX_AUDIO_SECONDS) != duration
            or document.get("event_fields") != ["start", "end", "pitch", "amplitude"]):
        raise _invalid()
    semantics = document.get("semantics")
    if (not isinstance(semantics, dict)
            or semantics.get("stage") != "input-to-notation-after-instrument-postprocessing"
            or semantics.get("reflects_manual_score_edits") is not False
            or semantics.get("time_unit") != "seconds"
            or semantics.get("time_origin") != "source-audio-start-before-score-offset"
            or semantics.get("ground_truth") is not False or semantics.get("confidence_available") is not False):
        raise _invalid()
    source = document.get("source")
    if not isinstance(source, dict):
        raise _invalid()
    kind = source.get("kind")
    expected_name = "original.wav" if kind == "original" else f"{inst}.wav" if kind == "stem" else None
    if (expected_name is None or source.get("file") != expected_name
            or source.get("instrument") != (inst if kind == "stem" else None)
            or not isinstance(source.get("sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", source["sha256"])):
        raise _invalid()
    provenance = document.get("provenance")
    if (not isinstance(provenance, dict) or not isinstance(provenance.get("engine"), str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,99}", provenance["engine"])
            or provenance["engine"] == "known-composition"):
        raise _invalid()
    raw_events = document.get("events")
    if not isinstance(raw_events, list) or len(raw_events) > MAX_EVENTS:
        raise _invalid()
    if _integer(document.get("event_count"), 0, MAX_EVENTS) != len(raw_events):
        raise _invalid()
    events = []
    for index, raw in enumerate(raw_events):
        if not isinstance(raw, list) or len(raw) != 4:
            raise _invalid()
        start, end = _number(raw[0], 0, duration), _number(raw[1], 0, duration)
        pitch, amplitude = _integer(raw[2], 0, 127), _number(raw[3], 0, 1)
        if start >= end or amplitude == 0:
            raise _invalid()
        events.append({"id": f"raw-{index}", "start": start, "end": end, "pitch": pitch, "amplitude": amplitude})
    return events, {"kind": kind, "file": expected_name, "sha256": source["sha256"]}, provenance


def _score(document, inst, duration, source, provenance, *, automatic=False):
    if (type(document.get("version")) is not int or document["version"] != 1
            or document.get("instrument") != inst or type(document.get("edited")) is not bool
            or automatic and document["edited"]):
        raise _invalid()
    revision = _identifier(document.get("revision"))
    bpm = _number(document.get("bpm"), 40, 240)
    timing_bpm = _number(document.get("timing_bpm", bpm), 40, 240)
    offset = _number(document.get("audio_offset", 0), 0, duration)
    ticks = _integer(document.get("ticks"), 1, 14400)
    info = document.get("transcription")
    if (not isinstance(info, dict) or info.get("raw_events") is not True
            or info.get("engine") != provenance["engine"]
            or info.get("source") != source["kind"]):
        raise ReviewError(409, "악보와 채보 당시 데이터의 출처가 달라 검토할 수 없어요. 다시 채보해주세요.")
    for key in ("profile", "mapping", "decoder", "context_passes"):
        if key in provenance and info.get(key) != provenance[key]:
            raise _invalid()
    notes = document.get("notes")
    if not isinstance(notes, list) or len(notes) > MAX_EVENTS:
        raise _invalid()
    events, seen = [], set()
    for note in notes:
        if not isinstance(note, dict):
            raise _invalid()
        identifier = _identifier(note.get("id"))
        start = _integer(note.get("start"), 0, ticks)
        length = _integer(note.get("length"), 1, 16384)
        pitch = _integer(note.get("pitch"), 0, 127)
        velocity = _integer(note.get("velocity"), 1, 127)
        if identifier in seen or start + length > ticks:
            raise _invalid()
        seen.add(identifier)
        # Saved ticks are sixteenth notes. Display BPM is not a time warp.
        events.append({"id": identifier, "start": offset + start / 4 * 60 / timing_bpm,
                       "end": offset + (start + length) / 4 * 60 / timing_bpm,
                       "pitch": pitch, "amplitude": velocity / 127})
    return events, {"revision": revision, "edited": document["edited"], "bpm": bpm,
                    "timing_bpm": timing_bpm, "audio_offset": offset}


def _layer(events, window):
    selected = [event for event in events if event["start"] < window["end"] and event["end"] > window["start"]]
    if len(selected) > MAX_WINDOW_EVENTS:
        raise ReviewError(422, "이 구간의 음표가 1,500개를 초과해요. 더 짧은 구간을 선택해주세요.")
    return {"events": selected, "total": len(events), "in_window": len(selected)}


def canonical_sha256(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def frozen_snapshot(document):
    """Exclude live playback URLs and the digest itself from immutable evidence."""
    return {key: value for key, value in document.items() if key not in {"audio", "snapshot_id"}}


def _frozen_provenance(document, file_hashes, automatic_revision):
    saved = document["provenance"]
    inference = {}
    for key in ("engine", "profile", "mapping", "decoder", "device", "normalization"):
        value = saved.get(key)
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,99}", value):
            inference[key] = value
    if type(saved.get("context_passes")) is int and saved["context_passes"] in (1, 3):
        inference["context_passes"] = saved["context_passes"]
    model = saved.get("model")
    if isinstance(model, dict):
        safe = {key: value for key in ("revision", "source_revision", "variant")
                if isinstance(value := model.get(key), str)
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,99}", value)}
        if isinstance(model.get("repo"), str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}/[A-Za-z0-9_.-]{1,80}", model["repo"]):
            safe["repo"] = model["repo"]
        hashes = model.get("sha256")
        if isinstance(hashes, dict):
            safe["sha256"] = {key: value for key in ("config.json", "adt_config.yaml", "model.safetensors")
                              if isinstance(value := hashes.get(key), str) and re.fullmatch(r"[a-f0-9]{64}", value)}
        if safe:
            inference["model"] = safe
    packages = saved.get("host_packages")
    if isinstance(packages, dict):
        inference["host_packages"] = {key: value for key in ("numpy", "librosa", "basic-pitch", "onnxruntime", "soundfile")
                                      if isinstance(value := packages.get(key), str)
                                      and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,99}", value)}
    artifact_id = document.get("artifact_id")
    if artifact_id is not None and (not isinstance(artifact_id, str) or not re.fullmatch(r"[a-f0-9]{32}", artifact_id)):
        raise _invalid()
    return {"notes_file_sha256": file_hashes[0], "automatic_file_sha256": file_hashes[1],
            "current_file_sha256": file_hashes[2], "raw_artifact_id": artifact_id,
            "automatic_revision": automatic_revision, "inference": inference}


def inspect_review(job_id, inst, start, seconds, *, task_lock, active_jobs, get_job, on_verified=None):
    """Snapshot under task/store gates, hash without locks, then recheck inputs.

    A changing generation is rejected rather than returning layers from different
    runs. Reading never migrates legacy XML or creates/replaces any saved files.
    """
    with task_lock, store.LOCK:
        job = get_job(job_id)
        stem = _available(job, inst, active_jobs)
        duration = _number(job.get("duration"), .000001, MAX_AUDIO_SECONDS)
        window = _window(duration, start, seconds)
        folder = store.directory(job_id)
        if folder.is_symlink() or not folder.is_dir():
            raise _invalid()
        fingerprints = {}
        documents, file_hashes = [], []
        for suffix, maximum, missing in (
            ("notes", MAX_ARTIFACT_BYTES, (404, "채보 당시 음표 파일이 없어요. 다시 채보해주세요.")),
            ("auto", MAX_SCORE_BYTES, (409, "자동 생성 당시 악보가 없어 비교할 수 없어요. 다시 채보해주세요.")),
            ("score", MAX_SCORE_BYTES, (404, "검토할 현재 악보 파일이 없어요.")),
        ):
            path = folder / f"{inst}.{suffix}.json"
            document, fingerprint, file_hash = _json_file(path, maximum, missing)
            documents.append(document)
            file_hashes.append(file_hash)
            fingerprints[path] = (fingerprint, maximum)
        recognized, source, provenance = _recognized(documents[0], inst, duration)
        automatic, _ = _score(documents[1], inst, duration, source, provenance, automatic=True)
        current, score = _score(documents[2], inst, duration, source, provenance)
        if (stem.get("score_revision") != score["revision"]
                or stem["score_transcription"] != documents[2]["transcription"]):
            raise _changed()
        if not score["edited"] and documents[1] != documents[2]:
            raise _changed()
        for name in {"original.wav", f"{inst}.wav"}:
            path = folder / name
            fingerprints[path] = (_file_stamp(path, MAX_SOURCE_BYTES), MAX_SOURCE_BYTES)
        source_path = folder / source["file"]
        source_stamp = fingerprints[source_path][0]
        if source_stamp is None:
            raise ReviewError(409, "채보에 사용한 음원이 없어 출처를 확인할 수 없어요.")
        audio = {"original_url": store.asset_url(job_id, "original.wav") if fingerprints[folder / "original.wav"][0] else None,
                 "stem_url": store.asset_url(job_id, f"{inst}.wav") if fingerprints[folder / f"{inst}.wav"][0] else None,
                 "input_url": store.asset_url(job_id, source["file"])}
        layers = {"recognized": _layer(recognized, window), "automatic": _layer(automatic, window),
                  "current": _layer(current, window)}
        saved_provenance = _frozen_provenance(documents[0], file_hashes, documents[1]["revision"])

    if _audio_sha256(source_path, source_stamp) != source["sha256"]:
        raise ReviewError(409, "채보 당시와 현재 음원의 해시가 달라요. 현재 음원으로 다시 채보해주세요.")

    warnings = [
        "인식·자동·현재 음표는 정답 악보가 아니에요. 단계 사이의 차이를 오인식이나 정확도 점수로 판단하지 마세요.",
        "인식 음표는 악기별 후처리 후, 격자 보정·악보 시작 오프셋 적용 전 기록이에요. 자동은 마지막 채보 직후, 현재는 저장된 수정본이에요.",
        "음표 강도는 확신도·정확도가 아니에요. 악보 강도는 저장된 MIDI velocity를 127로 나눈 값이에요.",
        "해시는 채보에 사용한 입력 파일 하나만 확인해요. 다른 분리 음원과 원본의 생성 관계까지 검증한 것은 아니에요.",
    ]
    if any(event["end"] > duration for event in automatic + current):
        warnings.append("악보 끝마디의 음표는 실제 음원 끝보다 길 수 있어요. 저장된 길이를 그대로 표시해요.")
    result = {"schema": "akbo.transcription-review", "schema_version": 1, "instrument": inst,
              "duration": duration, "window": window, "source": {**source, "verified": True},
              "audio": audio, "score": score, "layers": layers, "warnings": warnings,
              "provenance": saved_provenance}
    result["snapshot_id"] = canonical_sha256(frozen_snapshot(result))
    with task_lock, store.LOCK:
        latest = get_job(job_id)
        _available(latest, inst, active_jobs)
        if latest != job or folder.is_symlink():
            raise _changed()
        for path, (fingerprint, maximum) in fingerprints.items():
            if _file_stamp(path, maximum) != fingerprint:
                raise _changed()
        # Persistence callers commit only inside this final generation gate;
        # otherwise another transcription could start between inspect and save.
        return on_verified(result) if on_verified is not None else result
