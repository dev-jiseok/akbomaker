"""Durable, explicitly reviewed PDF TAB -> MusicXML projects.

The evidence file is immutable. User timing/corrections live in a separate
revisioned draft, and a new score gets its own copies of every source artifact.
No audio model or unreviewed spacing-to-rhythm inference is used.
"""
import hashlib
import json
import sys
import threading
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import score_omr, source_projects, tab_review

router = APIRouter(prefix="/api/score-omr")
MAX_STATE_BYTES = 2 * 1024 * 1024
RHYTHM_SLOT = threading.BoundedSemaphore(1)


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _evidence(job_id):
    job = score_omr.get(job_id)
    if job["status"] in {"queued", "running"}:
        raise HTTPException(409, "PDF 좌표 분석이 끝난 뒤 검토를 시작해주세요.")
    evidence = job.get("source_coordinates")
    if not evidence or not evidence.get("tab_staffs") or job.get("source_name") != "source.pdf":
        raise HTTPException(422, "이 작업에 검토할 PDF TAB 숫자 자료가 없어요.")
    folder = score_omr.directory(job_id)
    def checked(name, maximum, digest):
        path = folder / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
            raise HTTPException(409, "원본 검토 자료를 읽을 수 없어요.")
        content = path.read_bytes()
        if _hash(content) != digest:
            raise HTTPException(409, "원본 검토 자료가 변경됐어요. 이전 초안을 다른 원본에 적용하지 않습니다.")
        return content
    original = checked("source.pdf", score_omr.UPLOAD_LIMIT, job["source_sha256"])
    coordinates = checked("source-coordinates.json", MAX_STATE_BYTES, evidence["sha256"])
    try:
        analysis = json.loads(coordinates)
        if (type(analysis.get("schema_version")) is not int or analysis["schema_version"] != 1
                or analysis.get("source_sha256") != job["source_sha256"]
                or analysis.get("editable_musicxml") is not False or analysis.get("rhythm_known") is not False
                or analysis.get("requires_review") is not True
                or [p["page"] for p in analysis["pages"]] != job["pages"]):
            raise ValueError()
    except (ValueError, KeyError, TypeError, AttributeError):
        raise HTTPException(409, "PDF 좌표 자료의 출처와 범위를 확인할 수 없어요.") from None
    return job, analysis, original, coordinates


def _write(job_id, state):
    content = _bytes(state)
    if len(content) > MAX_STATE_BYTES:
        raise HTTPException(413, "TAB 검토 초안이 너무 커요. 보표를 나누어주세요.")
    folder = score_omr.directory(job_id)
    path, temporary = folder / "tab-review.json", folder / "tab-review.tmp"
    if path.is_symlink() or temporary.is_symlink():
        raise HTTPException(409, "안전하지 않은 TAB 초안 저장 경로예요.")
    temporary.write_bytes(content)
    temporary.replace(path)


def _state(job):
    path = score_omr.directory(job["id"]) / "tab-review.json"
    if path.is_symlink():
        raise HTTPException(409, "안전하지 않은 TAB 초안 경로예요.")
    if not path.exists():
        state = {"revision": uuid4().hex, "source_sha256": job["source_sha256"],
                 "coordinate_sha256": job["source_coordinates"]["sha256"], "draft": None,
                 "draft_sha256": _hash(_bytes(None))}
        _write(job["id"], state)
        return state
    try:
        if not path.is_file() or path.stat().st_size > MAX_STATE_BYTES:
            raise ValueError()
        state = json.loads(path.read_bytes())
        if (state["source_sha256"] != job["source_sha256"]
                or state["coordinate_sha256"] != job["source_coordinates"]["sha256"]
                or state["draft_sha256"] != _hash(_bytes(state["draft"]))):
            raise ValueError()
        Revision(base_revision=state["revision"])
        if state["draft"] is not None:
            tab_review.ReviewDraft.model_validate(state["draft"])
    except (ValueError, TypeError, KeyError):
        raise HTTPException(409, "TAB 초안의 무결성 확인에 실패했어요. 원본은 유지됩니다.") from None
    return state


def _document(job, analysis, state):
    return {**{key: state[key] for key in ("revision", "source_sha256", "coordinate_sha256", "draft")},
            "source_url": job["source_url"], "preview_urls": job["preview_urls"], "analysis": analysis}


def _validate_draft(job, analysis, draft):
    value = draft.model_dump(mode="json")
    if (value["source_sha256"] != job["source_sha256"]
            or value["coordinate_sha256"] != job["source_coordinates"]["sha256"]):
        raise HTTPException(409, "초안과 원본 TAB 자료가 달라요. 최신 자료를 다시 열어주세요.")
    staffs = {staff["id"]: staff for page in analysis["pages"] for staff in page["staffs"] if staff["kind"] == "tab"}
    if len(value["staff_ids"]) != len(set(value["staff_ids"])) or any(ident not in staffs for ident in value["staff_ids"]):
        raise HTTPException(422, "선택한 TAB 보표를 확인해주세요.")
    ids = set()
    for row in value["rows"]:
        if row["id"] in ids or row["staff_id"] not in value["staff_ids"]:
            raise HTTPException(422, "TAB 검토 항목이 중복되거나 선택한 보표 밖에 있어요.")
        ids.add(row["id"])
        if not row["id"].startswith("manual-") and row["id"] not in {
                f'{row["staff_id"]}:{digit["id"]}' for digit in staffs[row["staff_id"]]["digits"]}:
            raise HTTPException(422, "원본에 없는 TAB 숫자 참조입니다. 빠진 음은 직접 추가로 입력해주세요.")
    return value


class Revision(BaseModel):
    model_config = {"strict": True, "extra": "forbid"}
    base_revision: str = Field(pattern=r"^[a-f0-9]{32}$")


class SaveRequest(Revision):
    draft: tab_review.ReviewDraft


class ImportRequest(SaveRequest):
    confirmed: bool


class RhythmRequest(Revision):
    staff_id: str = Field(pattern=r"^p[0-9]+s[0-9]+$", max_length=40)
    beats: int = Field(ge=1, le=12)
    beat_type: int
    meter_confirmed: bool


@router.post("/{job_id}/tab-review/rhythm-suggestions")
def rhythm_suggestions(job_id: str, body: RhythmRequest):
    if not body.meter_confirmed or body.beat_type not in {2, 4, 8, 16}:
        raise HTTPException(422, "원본 박자표를 확인한 뒤 리듬 후보를 요청해주세요.")
    with score_omr.LOCK:
        job, analysis, _, _ = _evidence(job_id)
        state = _state(job)
        if body.base_revision != state["revision"]:
            raise HTTPException(409, "검수 초안이 변경됐어요. 최신 초안을 불러와주세요.")
        if not any(staff.get("id") == body.staff_id and staff.get("kind") == "tab"
                   for page in analysis["pages"] for staff in page["staffs"]):
            raise HTTPException(422, "원본에 있는 TAB 보표를 선택해주세요.")
    if not RHYTHM_SLOT.acquire(blocking=False):
        raise HTTPException(429, "다른 PDF 리듬을 분석 중이에요. 잠시 뒤 다시 시도해주세요.")
    folder = score_omr.directory(job_id)
    output = folder / f"rhythm-proposal-{uuid4().hex}.json"
    try:
        score_omr.run_command([sys.executable, str(Path(__file__).with_name("pdf_tab_rhythm_runtime.py")),
            "--source", str(folder / "source.pdf"), "--coordinates", str(folder / "source-coordinates.json"),
            "--source-sha", job["source_sha256"], "--coordinates-sha", job["source_coordinates"]["sha256"],
            "--staff", body.staff_id, "--beats", str(body.beats), "--beat-type", str(body.beat_type),
            "--output", str(output)], threading.Event(), folder, timeout=12)
        if output.is_symlink() or not output.is_file() or output.stat().st_size > MAX_STATE_BYTES:
            raise ValueError("리듬 후보를 안전하게 읽지 못했어요.")
        result = json.loads(output.read_bytes())
        if (not isinstance(result, dict) or type(result.get("schema_version")) is not int
                or result["schema_version"] != 1 or result.get("method") != "paired-vector-staff-rhythm"
                or result.get("source_sha256") != job["source_sha256"]
                or result.get("coordinate_sha256") != job["source_coordinates"]["sha256"]
                or result.get("staff_id") != body.staff_id or result.get("requires_review") is not True
                or not isinstance(result.get("meter"), dict)
                or result["meter"].get("source") != "caller-confirmed"
                or result.get("meter", {}).get("beats") != body.beats
                or result.get("meter", {}).get("beat_type") != body.beat_type
                or not isinstance(result.get("measures"), list)
                or any(not isinstance(m, dict) or m.get("staff_id") != body.staff_id
                       or m.get("status") not in {"suggested", "unresolved"}
                       or not isinstance(m.get("rows"), list) or not isinstance(m.get("unresolved"), list)
                       for m in result["measures"])):
            raise ValueError("원본과 리듬 후보의 출처를 확인할 수 없어요.")
        with score_omr.LOCK:
            current, _, _, _ = _evidence(job_id)
            if _state(current)["revision"] != body.base_revision:
                raise HTTPException(409, "분석 중 검수 초안이 바뀌었어요. 최신 초안에서 다시 요청해주세요.")
        return {**result, "base_revision": body.base_revision}
    except (ValueError, KeyError, TypeError) as error:
        raise HTTPException(422, str(error)[:500]) from None
    finally:
        try:
            if output.is_file() or output.is_symlink():
                output.unlink()
        finally:
            RHYTHM_SLOT.release()


@router.get("/{job_id}/tab-review")
def get(job_id: str):
    with score_omr.LOCK:
        job, analysis, _, _ = _evidence(job_id)
        return _document(job, analysis, _state(job))


@router.put("/{job_id}/tab-review")
def save(job_id: str, body: SaveRequest):
    with score_omr.LOCK:
        job, analysis, _, _ = _evidence(job_id)
        state = _state(job)
        if body.base_revision != state["revision"]:
            raise HTTPException(409, "다른 화면에서 TAB 초안이 바뀌었어요. 현재 입력을 보관하고 다시 열어주세요.")
        draft = _validate_draft(job, analysis, body.draft)
        updated = {**state, "draft": draft, "draft_sha256": _hash(_bytes(draft)), "revision": uuid4().hex}
        updated.pop("import_base_revision", None)
        _write(job_id, updated)
        return _document(job, analysis, updated)


@router.post("/{job_id}/tab-review/import", status_code=201)
def import_score(job_id: str, body: ImportRequest):
    if not body.confirmed:
        raise HTTPException(422, "원본의 숫자·줄·리듬·튜닝·누락을 확인한 뒤 생성해주세요.")
    with score_omr.LOCK:
        job, analysis, original, coordinates = _evidence(job_id)
        state = _state(job)
        draft = _validate_draft(job, analysis, body.draft)
        digest = _hash(_bytes(draft))
        retry = state.get("import_base_revision") == body.base_revision and state["draft_sha256"] == digest
        if body.base_revision != state["revision"] and not retry:
            raise HTTPException(409, "검토 초안이 변경됐어요. 최신 초안을 다시 확인해주세요.")
        try:
            xml, summary = tab_review.build(analysis, draft)
        except ValueError as error:
            raise HTTPException(422, str(error)[:500]) from None
        project_id = _hash(f"tab-review:{job_id}:{digest}".encode())[:32]
        provenance = {"id": job_id, "source_sha256": job["source_sha256"],
                      "coordinate_sha256": job["source_coordinates"]["sha256"], "review_sha256": digest,
                      "method": "user-reviewed-pdf-tab", "warnings": summary.get("warnings", [])}
        review = _bytes({"schema_version": 1, "draft": draft, "summary": summary, "provenance": provenance})
        # Persist an incomplete or complete review before allocating a new score.
        # Same source+review content yields the same project ID on a lost-response retry.
        _write(job_id, {**state, "draft": draft, "draft_sha256": digest,
                       "revision": state["revision"] if retry else uuid4().hex,
                       "import_base_revision": body.base_revision})
        try:
            return source_projects._create(xml, "reviewed-tab.musicxml", "P1", draft["instrument"], "practice", 4, None,
                     creation_id=project_id, tab_review=provenance,
                     attachments={"reference.pdf": original, "source-coordinates.json": coordinates, "reviewed-tab.json": review})
        except ValueError as error:
            raise HTTPException(422, str(error)[:500]) from None
