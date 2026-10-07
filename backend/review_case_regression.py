"""Offline same-input transcription regression, not separation evaluation."""
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat

from .evaluation import compare_events
from .note_artifacts import MAX_ARTIFACT_BYTES
from .review_case_evaluation import evaluate_review_case, MAX_CASE_BYTES
from .transcription_review import _recognized, MAX_SOURCE_BYTES

MAX_EXPORT_BYTES = 8 * 1024 * 1024


class ReviewCaseRegressionError(ValueError):
    pass


def _stamp(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _path(value):
    path = Path(value).absolute()
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ReviewCaseRegressionError("심볼릭 링크 경로는 평가 입력·출력으로 사용하지 않아요.")
    return path


def _read(path, maximum, *, retain=True):
    """Bounded, no-follow regular-file read, recording identity and exact bytes."""
    path = _path(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum:
        raise ReviewCaseRegressionError("평가 파일은 크기 제한 안의 비어 있지 않은 일반 파일이어야 해요.")
    digest = hashlib.sha256()
    chunks, total = [], 0
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if _stamp(os.fstat(stream.fileno())) != _stamp(before):
            raise ReviewCaseRegressionError("평가 입력이 읽기 전에 변경됐어요.")
        while chunk := stream.read(1024 * 1024):
            total += len(chunk)
            if total > maximum:
                raise ReviewCaseRegressionError("평가 입력이 크기 제한을 초과했어요.")
            digest.update(chunk)
            if retain:
                chunks.append(chunk)
        if total != before.st_size or _stamp(os.fstat(stream.fileno())) != _stamp(before):
            raise ReviewCaseRegressionError("평가 입력이 읽는 도중 변경됐어요.")
    if _stamp(path.lstat()) != _stamp(before):
        raise ReviewCaseRegressionError("평가 입력이 읽는 도중 교체됐어요.")
    return (b"".join(chunks) if retain else None), {"sha256": digest.hexdigest(), "stamp": _stamp(before)}


def _json(path, maximum):
    raw, receipt = _read(path, maximum)

    def unique(pairs):
        output = {}
        for key, value in pairs:
            if key in output:
                raise ValueError("duplicate JSON key")
            output[key] = value
        return output

    def finite(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("nonfinite JSON number")
        return number

    def invalid(value):
        raise ValueError("nonfinite JSON number")

    try:
        document = json.loads(raw, object_pairs_hook=unique, parse_float=finite, parse_constant=invalid)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ReviewCaseRegressionError("JSON에 중복 필드·유효하지 않은 숫자나 문법이 있어요.") from error
    if not isinstance(document, dict):
        raise ReviewCaseRegressionError("평가 JSON은 객체여야 해요.")
    return document, receipt


def _code_hashes():
    root = Path(__file__).resolve().parent.parent
    names = ("backend/evaluation.py", "backend/review_case_evaluation.py", "backend/review_case_regression.py",
             "backend/transcription_review.py", "backend/note_artifacts.py", "backend/config.py",
             "scripts/evaluate-review-case.py")
    return {name: _read(root / name, MAX_CASE_BYTES, retain=False)[1]["sha256"] for name in names}


def _verify_audio(path, expected_duration):
    """Check the local WAV header without decoding, inferring or retaining audio."""
    import soundfile as sf
    path = _path(path)
    expected = path.lstat()
    if not stat.S_ISREG(expected.st_mode) or not 0 < expected.st_size <= MAX_SOURCE_BYTES:
        raise ReviewCaseRegressionError("음원은 크기 제한 안의 일반 WAV 파일이어야 해요.")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        with os.fdopen(descriptor, "rb") as stream:
            if _stamp(os.fstat(stream.fileno())) != _stamp(expected):
                raise ReviewCaseRegressionError("음원 파일이 변경됐어요.")
            with sf.SoundFile(stream) as sound:
                if (sound.format not in ("WAV", "WAVEX", "RF64") or not 0 < sound.samplerate <= 192000
                        or not 1 <= sound.channels <= 2 or sound.frames <= 0
                        or abs(sound.frames / sound.samplerate - expected_duration) > .05):
                    raise ReviewCaseRegressionError("입력은 검수 당시 길이와 일치하는 모노/스테레오 WAV여야 해요.")
            if _stamp(os.fstat(stream.fileno())) != _stamp(expected):
                raise ReviewCaseRegressionError("음원 파일이 읽는 동안 변경됐어요.")
    except (RuntimeError, ValueError, OSError) as error:
        if isinstance(error, ReviewCaseRegressionError):
            raise
        raise ReviewCaseRegressionError("입력 파일을 WAV로 확인할 수 없어요.") from error
    if _stamp(path.lstat()) != _stamp(expected):
        raise ReviewCaseRegressionError("음원 파일이 읽는 동안 교체됐어요.")


def evaluate_regression(case_path, prediction_path, audio_path, *, expected_code_hashes=None):
    """Read fixed evidence and candidate events, without inference or file writes.

    Only identical source bytes are allowed. Changed SAM stems require a future
    independently attested alignment/lineage workflow, not a bypass here.
    """
    code = _code_hashes()
    if expected_code_hashes is not None and code != expected_code_hashes:
        raise ReviewCaseRegressionError("평가 모듈을 불러오는 동안 코드가 변경됐어요. 결과를 저장하지 않습니다.")
    exported, case_receipt = _json(case_path, MAX_EXPORT_BYTES)
    required = {"schema", "schema_version", "audio_included", "reference_status", "case", "report", "warnings"}
    if (exported.get("schema") != "akbo.review-case-export" or type(exported.get("schema_version")) is not int
            or exported["schema_version"] != 1 or exported.get("audio_included") is not False
            or exported.get("reference_status") != "reviewer-confirmed" or not required <= exported.keys()
            or not exported.keys() <= required | {"freshness_at_export", "exported_at"}):
        raise ReviewCaseRegressionError("검수 완료된 구간 검토 내보내기 파일이 필요해요.")
    case = exported.get("case")
    baseline_report = evaluate_review_case(case)
    if baseline_report is None:
        raise ReviewCaseRegressionError("초안은 정답 기준으로 평가할 수 없어요. 별도로 검수를 완료해주세요.")
    # Ignore any metrics supplied in the export: always recompute from frozen
    # notes and explicitly confirmed manual reference.
    snapshot, inst = case["snapshot"], case["instrument"]
    document, candidate_receipt = _json(prediction_path, MAX_ARTIFACT_BYTES)
    events, source, provenance = _recognized(document, inst, snapshot["duration"])
    if any(source[key] != snapshot["source"][key] for key in ("kind", "file", "sha256")):
        raise ReviewCaseRegressionError("후보 채보의 입력 음원이 고정 사례와 달라요. 변경된 SAM 분리음은 이 동일입력 평가에서 비교하지 않습니다.")
    artifact_id = document.get("artifact_id")
    if artifact_id is not None and (not isinstance(artifact_id, str) or not re.fullmatch(r"[a-f0-9]{32}", artifact_id)):
        raise ReviewCaseRegressionError("후보 음표 기록의 식별자가 올바르지 않아요.")
    _, audio_receipt = _read(audio_path, MAX_SOURCE_BYTES, retain=False)
    if audio_receipt["sha256"] != source["sha256"]:
        raise ReviewCaseRegressionError("실제 제공된 WAV 해시가 검수 사례·후보 채보 입력과 다릅니다.")
    _verify_audio(audio_path, snapshot["duration"])
    window = snapshot["window"]
    owned = lambda rows: [[event["start"], event["end"], event["pitch"], event["amplitude"]] for event in rows
                          if window["start"] <= event["start"] < window["end"]]
    reference = owned(case["reference"]["events"])
    estimated = owned(events)
    candidate = compare_events(reference, estimated, tolerance=.05, include_errors=True,
                               duration_tolerance_ratio=None if inst == "drums" else .2)
    if inst == "drums":
        candidate.pop("median_offset_error_ms", None)
    candidate.update(reference_present=bool(reference), reference_kind="reviewer-confirmed",
                     offset_evaluated=inst != "drums", manually_edited_estimate=False)
    baseline = deepcopy(baseline_report["metrics"]["recognized"])
    deltas = {key: candidate[key] - baseline[key] for key in ("matched_notes", "missing_notes", "extra_notes", "estimated_notes")}
    deltas.update({key: round(candidate[key] - baseline[key], 4) if reference else None
                   for key in ("precision", "recall", "f1")})
    if inst != "drums":
        deltas["duration_f1"] = (round(candidate["duration_diagnostics"]["f1"] - baseline["duration_diagnostics"]["f1"], 4)
                                 if reference else None)
    # Re-read exact input bytes and code after computing, so a mid-run edit
    # cannot be attributed to the initial receipt or silently accepted.
    for path, maximum, initial in ((case_path, MAX_EXPORT_BYTES, case_receipt),
                                   (prediction_path, MAX_ARTIFACT_BYTES, candidate_receipt),
                                   (audio_path, MAX_SOURCE_BYTES, audio_receipt)):
        if _read(path, maximum, retain=False)[1] != initial:
            raise ReviewCaseRegressionError("평가 도중 입력 파일이 변경됐어요. 결과를 저장하지 않습니다.")
    if _code_hashes() != code:
        raise ReviewCaseRegressionError("평가 도중 코드가 변경됐어요. 결과를 저장하지 않습니다.")
    warnings = baseline_report["warnings"] + [
        "동일한 입력 WAV 바이트의 후처리 후·박자 격자 적용 전 음표만 비교합니다. 분리 방식 변경의 효과는 평가하지 않습니다.",
        "차이는 후보 - 고정 인식 결과입니다. 선택한 검수 구간의 진단이며 우승 모델·곡 전체 성능·배포 권장을 뜻하지 않아요.",
        "음원은 파일 해시와 WAV 헤더만 검사했으며 이 명령에서 추론하거나 음질·가사·운지·오디오 정렬을 새로 검증하지 않았어요.",
    ]
    return {"schema": "akbo.review-case-regression", "schema_version": 1, "status": "complete",
            "scope": "selected-reviewed-source-time-onsets", "reference_kind": "reviewer-confirmed",
            "independent_ground_truth": False, "same_input_only": True, "inference_executed": False,
            "instrument": inst, "window": deepcopy(window), "reference_present": bool(reference),
            "case_id": case["id"], "case_revision": case["revision"],
            "hashes": {"case_export_sha256": case_receipt["sha256"], "snapshot_sha256": case["snapshot_id"],
                       "reference_sha256": case["confirmation"]["reference_sha256"],
                       "baseline_notes_file_sha256": snapshot["provenance"]["notes_file_sha256"],
                       "candidate_notes_file_sha256": candidate_receipt["sha256"],
                       "source_audio_sha256": audio_receipt["sha256"], "code_sha256": code},
            "baseline": {"layer": "recognized", "metrics": baseline},
            "candidate": {"stage": "input-to-notation-after-instrument-postprocessing", "engine": provenance["engine"],
                          "artifact_id": artifact_id, "total_events": len(events), "metrics": candidate},
            "deltas": deltas, "warnings": warnings}


def write_new_report(path, report):
    """Exclusive creation: existing files and symlinks are never overwritten."""
    path = _path(path)
    payload = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8") + b"\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
