"""Pure, bounded scoring/export for frozen, manually reviewed source-time clips.

Hashes bind the recorded evidence, not its truth or the ancestry of SAM stems.
No file, network, audio, model or score-writing operation is performed here.
"""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
import re

from .config import INSTRUMENTS
from .evaluation import compare_events

LAYERS = ("recognized", "automatic", "current")
MAX_CASE_BYTES = 2 * 1024 * 1024
MAX_EVENTS = 1500
MAX_SCORE_SECONDS = 6000
TIME_ORIGIN = "source-audio-start-before-score-offset"


class ReviewCaseEvaluationError(ValueError):
    """Malformed or unconfirmed evidence must never silently become a score."""


def _fail(message):
    raise ReviewCaseEvaluationError(message)


def _object(value, required, optional=()):
    if (not isinstance(value, dict) or not set(required) <= value.keys()
            or not value.keys() <= set(required) | set(optional)):
        _fail("검토 자료의 필드 구성이 올바르지 않아요.")
    return value


def _text(value, maximum, *, nonempty=False):
    if (not isinstance(value, str) or len(value) > maximum
            or nonempty and not value.strip() or any(ord(char) < 32 and char not in "\n\t\r" for char in value)):
        _fail("검토 자료의 설명이나 식별자가 올바르지 않아요.")
    return value


def _pattern(value, pattern):
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        _fail("검토 자료의 식별자 또는 해시가 올바르지 않아요.")
    return value


def _id(value):
    return _pattern(value, r"[\w-]{1,80}")


def _hash(value):
    return _pattern(value, r"[a-f0-9]{64}")


def _number(value, minimum, maximum, *, integer=False):
    if type(value) not in ((int,) if integer else (int, float)):
        _fail("검토 자료는 유한한 숫자와 정수 음정을 사용해야 해요.")
    try:
        valid = math.isfinite(value) and minimum <= value <= maximum
    except OverflowError:
        valid = False
    if not valid:
        _fail("검토 자료의 숫자가 허용 범위를 벗어났어요.")
    return value


def _timestamp(value):
    _text(value, 80, nonempty=True)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        _fail("검토 시각은 ISO 형식이어야 해요.")
    if parsed.tzinfo is None:
        _fail("검토 시각에는 시간대가 필요해요.")


def _canonical_bytes(value):
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError):
        _fail("검토 자료를 안전한 JSON으로 표현할 수 없어요.")
    if len(encoded) > MAX_CASE_BYTES:
        _fail("검토 자료가 2MB 제한을 초과했어요.")
    return encoded


def canonical_sha256(value):
    """Canonical content digest shared with the case storage layer."""
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _inference(value):
    _object(value, ("engine",), ("profile", "mapping", "decoder", "device", "context_passes",
                               "normalization", "model", "host_packages"))
    for key in ("engine", "profile", "mapping", "decoder", "device"):
        if key in value:
            _pattern(value[key], r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,99}")
    if value["engine"] == "known-composition":
        _fail("샘플 작곡 음표는 실제 채보 평가 자료가 아니에요.")
    if "context_passes" in value and (type(value["context_passes"]) is not int or value["context_passes"] not in (1, 3)):
        _fail("채보 분석 횟수가 올바르지 않아요.")
    if "normalization" in value and value["normalization"] not in ("none", "peak"):
        _fail("채보 음량 처리 정보가 올바르지 않아요.")
    if "host_packages" in value:
        packages = value["host_packages"]
        if not isinstance(packages, dict) or len(packages) > 32:
            _fail("채보 패키지 정보가 올바르지 않아요.")
        for key, item in packages.items():
            _pattern(key, r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,99}")
            _pattern(item, r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,99}")
    if "model" in value:
        model = _object(value["model"], (), ("revision", "source_revision", "variant", "repo", "sha256"))
        for key in ("revision", "source_revision", "variant"):
            if key in model:
                _pattern(model[key], r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,99}")
        if "repo" in model:
            _pattern(model["repo"], r"[A-Za-z0-9_.-]{1,80}/[A-Za-z0-9_.-]{1,80}")
        if "sha256" in model:
            hashes = _object(model["sha256"], (), ("config.json", "adt_config.yaml", "model.safetensors"))
            for digest in hashes.values():
                _hash(digest)


def _events(events, window, maximum_end, *, onset_owned=False):
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        _fail("구간 음표는 최대 1,500개까지 기록할 수 있어요.")
    identifiers = set()
    for event in events:
        _object(event, ("id", "start", "end", "pitch", "amplitude"))
        identifier = _id(event["id"])
        if identifier in identifiers:
            _fail("같은 음표 목록에서 식별자가 중복됐어요.")
        identifiers.add(identifier)
        start = _number(event["start"], 0, maximum_end)
        end = _number(event["end"], 0, maximum_end)
        _number(event["pitch"], 0, 127, integer=True)
        amplitude = _number(event["amplitude"], 0, 1)
        if start >= end or amplitude <= 0:
            _fail("음표에는 양수 길이와 강도가 필요해요.")
        if start >= window["end"] or end <= window["start"]:
            _fail("구간과 겹치지 않는 음표가 포함됐어요.")
        if onset_owned and start < window["start"]:
            _fail("검수 기준 음표는 선택 구간 안에서 시작해야 해요. 이전 지속음은 새 타격으로 세지 않아요.")
    return identifiers


def validate_review_case(case):
    """Validate hashes, bounded frozen evidence and explicit review confirmation.

    Does not check current project freshness; storage/API performs that check.
    Distinct unison/duplicate-time notes remain separate events, never deduped.
    """
    _canonical_bytes(case)
    _object(case, ("schema", "schema_version", "id", "job_id", "instrument", "revision", "created_at",
                   "updated_at", "status", "snapshot_id", "snapshot", "reference", "annotations", "confirmation"))
    if case["schema"] != "akbo.review-case" or type(case["schema_version"]) is not int or case["schema_version"] != 1:
        _fail("지원하지 않는 구간 검토 자료 형식이에요.")
    for key in ("id", "job_id", "revision"):
        _pattern(case[key], r"[a-f0-9]{32}")
    for key in ("created_at", "updated_at"):
        _timestamp(case[key])
    inst = case["instrument"]
    if inst not in INSTRUMENTS or case["status"] not in ("draft", "reviewed"):
        _fail("악기 또는 검수 상태가 올바르지 않아요.")
    snapshot = _object(case["snapshot"], ("schema", "schema_version", "instrument", "duration", "window",
                                              "source", "score", "layers", "warnings", "provenance"))
    if (snapshot["schema"] != "akbo.transcription-review" or type(snapshot["schema_version"]) is not int
            or snapshot["schema_version"] != 1 or snapshot["instrument"] != inst):
        _fail("고정된 검토 자료와 악기 정보가 다릅니다.")
    duration = _number(snapshot["duration"], .000001, 600)
    window = _object(snapshot["window"], ("start", "end"))
    start, end = _number(window["start"], 0, duration), _number(window["end"], 0, duration)
    if start >= end or end - start > 30 + 1e-9:
        _fail("검토 구간은 0초보다 길고 최대 30초여야 해요.")
    source = _object(snapshot["source"], ("kind", "file", "sha256", "verified"))
    expected_file = "original.wav" if source["kind"] == "original" else f"{inst}.wav" if source["kind"] == "stem" else None
    if expected_file is None or source["file"] != expected_file or source["verified"] is not True:
        _fail("검토 자료의 채보 입력 출처가 확인되지 않았어요.")
    _hash(source["sha256"])
    score = _object(snapshot["score"], ("revision", "edited", "bpm", "timing_bpm", "audio_offset"))
    _id(score["revision"])
    if type(score["edited"]) is not bool:
        _fail("악보 수정 상태가 올바르지 않아요.")
    for key in ("bpm", "timing_bpm"):
        _number(score[key], 40, 240)
    _number(score["audio_offset"], 0, duration)
    layers = _object(snapshot["layers"], LAYERS)
    identifiers = {}
    for name in LAYERS:
        layer = _object(layers[name], ("events", "total", "in_window"))
        identifiers[name] = _events(layer["events"], window, duration if name == "recognized" else MAX_SCORE_SECONDS)
        total = _number(layer["total"], 0, 30000, integer=True)
        count = _number(layer["in_window"], 0, MAX_EVENTS, integer=True)
        if count != len(layer["events"]) or total < count:
            _fail("고정된 음표 개수와 목록이 일치하지 않아요.")
    warnings = snapshot["warnings"]
    if not isinstance(warnings, list) or len(warnings) > 32:
        _fail("검토 자료의 경고 목록이 올바르지 않아요.")
    for warning in warnings:
        _text(warning, 2000)
    provenance = _object(snapshot["provenance"], ("notes_file_sha256", "automatic_file_sha256", "current_file_sha256",
                                                      "raw_artifact_id", "automatic_revision", "inference"))
    for key in ("notes_file_sha256", "automatic_file_sha256", "current_file_sha256"):
        _hash(provenance[key])
    if provenance["raw_artifact_id"] is not None:
        _pattern(provenance["raw_artifact_id"], r"[a-f0-9]{32}")
    _id(provenance["automatic_revision"])
    _inference(provenance["inference"])
    _hash(case["snapshot_id"])
    if canonical_sha256(snapshot) != case["snapshot_id"]:
        _fail("고정된 검토 자료의 내용과 해시가 다릅니다.")
    reference = _object(case["reference"], ("events", "reviewer", "basis", "coverage_complete"), ("seed_layer",))
    _events(reference["events"], window, duration, onset_owned=True)
    _text(reference["reviewer"], 80)
    _text(reference["basis"], 2000)
    if type(reference["coverage_complete"]) is not bool or reference.get("seed_layer") not in (*LAYERS, None):
        _fail("검수 범위 또는 기준 음표의 출처가 올바르지 않아요.")
    annotations = case["annotations"]
    if not isinstance(annotations, list) or len(annotations) > 300:
        _fail("검토 메모는 최대 300개까지 기록할 수 있어요.")
    annotation_ids = set()
    for annotation in annotations:
        _object(annotation, ("id", "kind", "layer", "event_id", "start", "end", "note"))
        identifier = _id(annotation["id"])
        if identifier in annotation_ids:
            _fail("검토 메모 식별자가 중복됐어요.")
        annotation_ids.add(identifier)
        layer = annotation["layer"]
        if annotation["kind"] not in ("missed", "extra", "pitch", "onset", "offset", "other") or layer not in (*LAYERS, None):
            _fail("검토 메모의 종류나 비교 단계가 올바르지 않아요.")
        event_id = annotation["event_id"]
        if event_id is not None and (not isinstance(event_id, str) or layer is None or event_id not in identifiers[layer]):
            _fail("검토 메모가 고정된 음표와 연결되지 않았어요.")
        annotation_start = _number(annotation["start"], start, end)
        annotation_end = _number(annotation["end"], annotation_start, duration)
        if annotation_start >= end or annotation_end < annotation_start:
            _fail("검토 메모 위치가 구간 밖에 있어요.")
        _text(annotation["note"], 1000)
    confirmation = case["confirmation"]
    if case["status"] == "draft":
        if confirmation is not None:
            _fail("초안에는 완료된 검수 확인을 붙일 수 없어요.")
        return
    if reference["coverage_complete"] is not True or not reference["reviewer"].strip() or not reference["basis"].strip():
        _fail("검수 완료에는 전체 구간 확인, 검수자와 근거가 필요해요.")
    _object(confirmation, ("revision", "confirmed_at", "reference_sha256"))
    _timestamp(confirmation["confirmed_at"])
    _hash(confirmation["reference_sha256"])
    if confirmation["revision"] != case["revision"] or confirmation["reference_sha256"] != canonical_sha256(reference):
        _fail("현재 검수 내용과 완료 확인이 일치하지 않아요. 다시 검수해주세요.")


def _warnings(case):
    result = [
        "이 결과는 사용자가 확인한 선택 구간 기준과의 일치도이며, 독립된 정답이나 곡 전체 정확도가 아닙니다.",
        "현재 저장된 악보는 수동 수정본일 수 있어요. 현재 악보와 기준의 일치도를 자동 채보 모델 성능으로 해석하지 마세요.",
        "음원 시작 기준 초 단위 시작점이 선택 구간 [시작, 끝) 안에 있는 음표만 평가하며 이전부터 이어진 지속음은 제외합니다.",
        "분리 음원의 음질·SDR, 가사·TAB 운지·악보 가독성은 이 점수로 평가하지 않습니다. 단계별 우승 방식도 자동 선정하지 않아요.",
        "파일 및 내용 해시는 기록의 동일성을 묶는 값이며 실제 SAM 실행 이력 전체나 검수 내용의 정답 여부를 증명하지 않아요.",
    ]
    if case["reference"].get("seed_layer"):
        result.append("기준 음표는 비교 단계에서 복사해 검수한 자료예요. 같은 출력을 복사했다는 이유로 독립된 정답이 되지 않습니다.")
    if case["snapshot"]["provenance"]["raw_artifact_id"] is None:
        result.append("이전 채보 기록에는 artifact ID가 없으며 파일 SHA-256으로 연결했습니다.")
    return result


def evaluate_review_case(case):
    """Return reviewed-clip agreement, or None for a valid unreviewed draft."""
    validate_review_case(case)
    if case["status"] != "reviewed":
        return None
    window = case["snapshot"]["window"]

    def owned(events):
        return [[event["start"], event["end"], event["pitch"], event["amplitude"]] for event in events
                if window["start"] <= event["start"] < window["end"]]

    reference = owned(case["reference"]["events"])
    metrics = {}
    for name in LAYERS:
        events = case["snapshot"]["layers"][name]["events"]
        estimated = owned(events)
        result = compare_events(reference, estimated, tolerance=.05, include_errors=True,
                                duration_tolerance_ratio=None if case["instrument"] == "drums" else .2)
        if case["instrument"] == "drums":
            # Drum ends are notation placeholders, not ground-truth decay lengths.
            result.pop("median_offset_error_ms", None)
        result.update(reference_present=bool(reference), reference_kind="reviewer-confirmed",
                      offset_evaluated=case["instrument"] != "drums",
                      excluded_context_notes=len(events) - len(estimated),
                      manually_edited_estimate=name == "current" and case["snapshot"]["score"]["edited"],
                      confusion_kind="possible-drum-class" if case["instrument"] == "drums" else "possible-pitch-substitution")
        metrics[name] = result
    warnings = _warnings(case)
    if not reference:
        warnings.append("검수한 구간에 기준 음표가 없어요. 추가 음표 수를 확인하세요. 기준이 비어 있는 F1 0은 정확도 0% 또는 완벽한 무음 판정이 아닙니다.")
    return {"schema": "akbo.review-case-evaluation", "schema_version": 1, "status": "complete",
            "method": "reviewer-confirmed-source-onset-agreement-v1", "case_id": case["id"],
            "case_revision": case["revision"], "snapshot_id": case["snapshot_id"],
            "reference_sha256": case["confirmation"]["reference_sha256"], "instrument": case["instrument"],
            "window": deepcopy(window), "time_origin": TIME_ORIGIN,
            "reference_kind": "reviewer-confirmed", "independent_ground_truth": False,
            "scope": "selected-reviewed-source-time-onsets", "metrics": metrics, "warnings": warnings}


def export_review_case(case):
    """Return a detached JSON-safe package; source audio bytes/URLs are excluded."""
    report = evaluate_review_case(case)
    # Validation whitelists every field, including bounded safe provenance. There
    # is no audio payload, filesystem path or playback URL field in this schema.
    frozen = json.loads(_canonical_bytes(case))
    warnings = _warnings(case)
    if report is None:
        warnings.insert(0, "이 자료는 검수 전 초안이며 기준 음표 후보일 뿐이에요. 평가 점수를 제공하지 않습니다.")
    return {"schema": "akbo.review-case-export", "schema_version": 1, "audio_included": False,
            "reference_status": "reviewer-confirmed" if report is not None else "draft-candidate",
            "case": frozen, "report": report, "warnings": warnings}
