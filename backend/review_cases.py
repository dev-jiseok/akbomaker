"""Bounded review notebooks, separate from every score/transcription artifact.

A case freezes one verified inspection window. Only its draft and explicit
human confirmation change. Saving a case never changes a job, score, or model.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import os
import re
import stat
from uuid import uuid4

from . import store, transcription_review as inspection
from .config import INSTRUMENTS

MAX_BYTES = 2 * 1024 * 1024
MAX_CASES = 32
MAX_HISTORY = 3
CASE_ID = re.compile(r"[a-f0-9]{32}")
SHA256 = re.compile(r"[a-f0-9]{64}")
ReviewError = inspection.ReviewError


def _error(status=422):
    return ReviewError(status, "검토 사례의 형식·음표 범위·확인 상태를 확인해주세요.")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _bytes(value):
    try:
        data = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                          separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise _error() from None
    if len(data) > MAX_BYTES:
        raise ReviewError(413, "검토 사례는 요청·파일당 2MB 이하로 저장해주세요.")
    return data


async def read_body(request):
    """Stream-cap JSON before parsing; no unbounded FastAPI body/model load."""
    try:
        length = request.headers.get("content-length")
        declared = int(length) if length is not None else 0
    except ValueError:
        raise _error() from None
    if declared > MAX_BYTES:
        raise ReviewError(413, "검토 요청은 2MB 이하로 보내주세요.")
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_BYTES:
            raise ReviewError(413, "검토 요청은 2MB 이하로 보내주세요.")
        chunks.append(chunk)

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
            raise ValueError("nonfinite JSON value")
        return number

    def invalid(value):
        raise ValueError("nonfinite JSON value")

    try:
        body = json.loads(b"".join(chunks), object_pairs_hook=unique, parse_float=finite, parse_constant=invalid)
    except (ValueError, UnicodeError, RecursionError):
        raise _error() from None
    if not isinstance(body, dict):
        raise _error()
    return body


def _validate_case(document, status=409):
    from .review_case_evaluation import validate_review_case, ReviewCaseEvaluationError
    try:
        validate_review_case(document)
    except (ReviewCaseEvaluationError, ValueError, TypeError, KeyError):
        raise _error(status) from None


def _root(job_id, inst, *, get_job, create=False):
    get_job(job_id)  # Existing job authorization/ID validation, not a job write.
    if inst not in INSTRUMENTS:
        raise ReviewError(404, "지원하지 않는 악기예요.")
    folder = store.directory(job_id)
    try:
        if folder.is_symlink() or not folder.is_dir():
            raise _error(409)
        for name in ("review-cases", inst):
            folder = folder / name
            if not folder.exists() and not folder.is_symlink():
                if not create:
                    return None
                folder.mkdir()
            if not stat.S_ISDIR(folder.lstat().st_mode):
                raise _error(409)
    except OSError:
        raise ReviewError(409, "검토 사례 폴더를 안전하게 열 수 없어요.") from None
    return folder


def _case_files(root):
    if root is None:
        return []
    files = []
    scanned = 0
    for path in root.iterdir():
        scanned += 1
        if scanned > MAX_CASES * 3 + 4:
            raise ReviewError(409, "검토 사례 폴더의 파일 제한을 초과했어요.")
        if re.fullmatch(r"[a-f0-9]{32}\.json", path.name):
            files.append(path)
            if len(files) > MAX_CASES:
                raise ReviewError(409, "악기당 검토 사례는 최대 32개예요.")
        elif not re.fullmatch(r"(?:[a-f0-9]{32}\.history\.json|\.pending-[a-f0-9]{32}\.tmp)", path.name):
            raise _error(409)
    return files


def _read_case(path, job_id, inst):
    document, _, _ = inspection._json_file(path, MAX_BYTES, (404, "검토 사례를 찾을 수 없어요."))
    _validate_case(document)
    if document.get("job_id") != job_id or document.get("instrument") != inst or document.get("id") != path.stem:
        raise _error(409)
    return document


def _load(job_id, inst, case_id, *, get_job):
    if not isinstance(case_id, str) or not CASE_ID.fullmatch(case_id):
        raise ReviewError(404, "검토 사례를 찾을 수 없어요.")
    root = _root(job_id, inst, get_job=get_job)
    if root is None:
        raise ReviewError(404, "검토 사례를 찾을 수 없어요.")
    path = root / f"{case_id}.json"
    return _read_case(path, job_id, inst), path


def _atomic_write(path, document):
    data = _bytes(document)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise _error(409)
    inspection._file_stamp(path, MAX_BYTES)  # Reject symlink/device targets.
    temporary = path.parent / f".pending-{uuid4().hex}.tmp"
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError:
        raise ReviewError(409, "검토 사례 저장을 완료하지 못했어요. 저장 상태를 다시 확인해주세요.") from None
    finally:
        if temporary.exists():
            temporary.unlink()


def _freshness(document, *, task_lock, active_jobs, get_job):
    window = document["snapshot"]["window"]
    try:
        current = inspection.inspect_review(document["job_id"], document["instrument"], window["start"],
                                            max(.25, window["end"] - window["start"]), task_lock=task_lock,
                                            active_jobs=active_jobs, get_job=get_job)
    except ReviewError:
        return {"status": "unavailable", "message": "현재 채보와의 일치 여부를 확인할 수 없어요. 저장 당시 고정 사례는 그대로 열 수 있어요."}
    if current["snapshot_id"] != document["snapshot_id"]:
        return {"status": "stale", "message": "현재 음원·악보가 저장 당시와 달라요. 이 사례는 저장 당시의 고정된 결과예요."}
    return {"status": "current", "message": "확인 시점의 입력 음원·악보와 같은 사례예요. 정답이나 독립 검증을 뜻하지 않아요."}


def _response(document, *, task_lock, active_jobs, get_job, freshness=None):
    from .review_case_evaluation import evaluate_review_case
    return {**deepcopy(document), "report": evaluate_review_case(document),
            "freshness": freshness if freshness is not None else _freshness(document, task_lock=task_lock,
                                                                             active_jobs=active_jobs, get_job=get_job)}


def list_cases(job_id, inst, *, task_lock, active_jobs, get_job):
    with task_lock, store.LOCK:
        root = _root(job_id, inst, get_job=get_job)
        cases = []
        for path in _case_files(root):
            document = _read_case(path, job_id, inst)
            cases.append({key: document[key] for key in ("id", "instrument", "revision", "status", "created_at",
                                                         "updated_at", "snapshot_id")})
            cases[-1].update(window=document["snapshot"]["window"], reference_event_count=len(document["reference"]["events"]),
                             annotation_count=len(document["annotations"]))
        return {"cases": sorted(cases, key=lambda case: (case["created_at"], case["id"]), reverse=True)}


def create_case(job_id, inst, body, *, task_lock, active_jobs, get_job):
    if (not isinstance(body, dict) or set(body) != {"start", "seconds", "snapshot_id"}
            or not isinstance(body.get("snapshot_id"), str) or not SHA256.fullmatch(body["snapshot_id"])):
        raise _error()

    def commit(live):
        if body["snapshot_id"] != live["snapshot_id"]:
            raise ReviewError(409, "화면의 채보 결과가 변경됐어요. 구간을 다시 불러온 뒤 사례를 저장해주세요.")
        root = _root(job_id, inst, get_job=get_job, create=True)
        if len(_case_files(root)) >= MAX_CASES:
            raise ReviewError(409, "악기당 검토 사례는 최대 32개예요.")
        now = _now()
        document = {"schema": "akbo.review-case", "schema_version": 1, "id": uuid4().hex,
                    "job_id": job_id, "instrument": inst, "revision": uuid4().hex,
                    "created_at": now, "updated_at": now, "status": "draft", "snapshot_id": live["snapshot_id"],
                    "snapshot": deepcopy(inspection.frozen_snapshot(live)),
                    "reference": {"events": [], "reviewer": "", "basis": "", "coverage_complete": False, "seed_layer": None},
                    "annotations": [], "confirmation": None}
        _validate_case(document, 422)
        _atomic_write(root / f'{document["id"]}.json', document)
        return document

    document = inspection.inspect_review(job_id, inst, body["start"], body["seconds"], task_lock=task_lock,
                                         active_jobs=active_jobs, get_job=get_job, on_verified=commit)
    return _response(document, task_lock=task_lock, active_jobs=active_jobs, get_job=get_job,
                     freshness={"status": "current", "message": "검증한 구간을 고정 사례로 저장했어요. 정답 후보는 아직 비어 있어요."})


def get_case(job_id, inst, case_id, *, task_lock, active_jobs, get_job):
    with task_lock, store.LOCK:
        document, _ = _load(job_id, inst, case_id, get_job=get_job)
    return _response(document, task_lock=task_lock, active_jobs=active_jobs, get_job=get_job)


def _draft(body):
    if (not isinstance(body, dict) or set(body) != {"base_revision", "status", "reference", "annotations"}
            or not isinstance(body.get("base_revision"), str) or not CASE_ID.fullmatch(body["base_revision"])
            or body.get("status") not in ("draft", "reviewed") or not isinstance(body.get("reference"), dict)
            or not isinstance(body.get("annotations"), list)):
        raise _error()
    reference = deepcopy(body["reference"])
    if not {"events", "reviewer", "basis", "coverage_complete"} <= set(reference) <= {"events", "reviewer", "basis", "coverage_complete", "seed_layer"}:
        raise _error()
    reference.setdefault("seed_layer", None)
    return reference, deepcopy(body["annotations"])


def _save_history(path, previous):
    history_path = path.with_name(path.stem + ".history.json")
    prior = []
    if inspection._file_stamp(history_path, MAX_BYTES) is not None:
        history, _, _ = inspection._json_file(history_path, MAX_BYTES, (409, "이전 검토 이력을 찾을 수 없어요."))
        if (history.get("schema") != "akbo.review-case-history" or history.get("case_id") != previous["id"]
                or not isinstance(history.get("revisions"), list) or len(history["revisions"]) > MAX_HISTORY):
            raise _error(409)
        prior = history["revisions"]
    state = {key: previous[key] for key in ("revision", "updated_at", "status", "reference", "annotations", "confirmation")}
    prior = (prior + [state])[-MAX_HISTORY:]
    history = {"schema": "akbo.review-case-history", "schema_version": 1, "case_id": previous["id"], "revisions": prior}
    # Keep up to three previous drafts within one bounded history file. Frozen
    # evidence remains once in the case; old drafts are not inference inputs.
    while len(prior) > 1:
        try:
            _bytes(history)
            break
        except ReviewError as error:
            if error.status != 413:
                raise
            prior.pop(0)
    _atomic_write(history_path, history)


def update_case(job_id, inst, case_id, body, *, task_lock, active_jobs, get_job):
    reference, annotations = _draft(body)

    def commit(live=None):
        previous, path = _load(job_id, inst, case_id, get_job=get_job)
        if body["base_revision"] != previous["revision"]:
            raise ReviewError(409, "다른 화면에서 검토 사례가 변경됐어요. 최신 저장본을 다시 열어주세요.")
        changed = reference != previous["reference"] or annotations != previous["annotations"]
        if changed and body["status"] == "reviewed":
            raise ReviewError(422, "변경한 내용을 먼저 초안으로 저장한 뒤, 저장된 전체 구간을 별도로 확인해주세요.")
        if body["status"] == "reviewed" and previous["status"] == "draft":
            if live is None or live["snapshot_id"] != previous["snapshot_id"]:
                raise ReviewError(409, "현재 음원·악보가 고정 사례와 달라 완료 확인할 수 없어요. 현재 구간을 새 사례로 저장해주세요.")
        candidate = {**deepcopy(previous), "reference": reference, "annotations": annotations, "status": body["status"],
                     "revision": uuid4().hex, "updated_at": _now(), "confirmation": None}
        if body["status"] == "reviewed":
            candidate["confirmation"] = {"revision": candidate["revision"], "confirmed_at": candidate["updated_at"],
                                         "reference_sha256": inspection.canonical_sha256(reference)}
        _validate_case(candidate, 422)
        if not changed and body["status"] == previous["status"]:
            document = previous
        else:
            _bytes(candidate)  # Never update history if the new file cannot fit.
            _save_history(path, previous)
            _atomic_write(path, candidate)
            document = candidate
        return document

    with task_lock, store.LOCK:
        previous, _ = _load(job_id, inst, case_id, get_job=get_job)
        if body["base_revision"] != previous["revision"]:
            raise ReviewError(409, "다른 화면에서 검토 사례가 변경됐어요. 최신 저장본을 다시 열어주세요.")
        needs_confirmation = body["status"] == "reviewed" and previous["status"] == "draft"
        if not needs_confirmation:
            document = commit()
    if needs_confirmation:
        # Historical drafts remain editable, but a NEW review confirmation
        # requires the same currently verifiable audio/score snapshot. The final
        # callback rechecks case revision under both locks after hashing audio.
        window = previous["snapshot"]["window"]
        document = inspection.inspect_review(job_id, inst, window["start"], max(.25, window["end"] - window["start"]),
                                             task_lock=task_lock, active_jobs=active_jobs, get_job=get_job, on_verified=commit)
    return _response(document, task_lock=task_lock, active_jobs=active_jobs, get_job=get_job)


def export_case(job_id, inst, case_id, *, task_lock, active_jobs, get_job):
    from .review_case_evaluation import export_review_case
    with task_lock, store.LOCK:
        document, _ = _load(job_id, inst, case_id, get_job=get_job)
    result = export_review_case(document)
    result["freshness_at_export"] = _freshness(document, task_lock=task_lock, active_jobs=active_jobs, get_job=get_job)
    result["exported_at"] = _now()
    return result
