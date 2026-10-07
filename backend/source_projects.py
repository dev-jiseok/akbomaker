"""Durable original-notation projects; never round-trip through ScoreDocument.

The immutable upload and the initial sanitized XML stay separate from working
XML. One atomic state file holds edits, layout and bounded undo/redo history.
Job metadata is a projection; an old grid editor cannot rewrite these scores.
"""
import asyncio
import copy
import hashlib
import io
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from . import score_import, score_integrity, score_omr, source_score, store
from .config import INSTRUMENTS
from .score_preservation import LAYOUT_WARNING, restyle_working_xml
from .source_fidelity import verify_layout

router = APIRouter(prefix="/api/source-scores")
HISTORY_LIMIT = 12
HISTORY_BYTES = 24 * 1024 * 1024
STATE_BYTES = 48 * 1024 * 1024
SOURCE_WARNING = "스타일만 바꿀 때 음악 내용은 유지합니다. 수정 기능을 사용하면 선택한 음표와 필요한 연결 표기를 함께 반영합니다. PDF 인식 누락을 자동으로 복원하지는 않습니다."
ATTACHMENT_LIMITS = {"reference.pdf": 25 * 1024 * 1024, "source-coordinates.json": 2 * 1024 * 1024,
                     "reviewed-tab.json": 2 * 1024 * 1024}


def _digest(data):
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def _directory(job_id):
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise HTTPException(404, "원본 유지 악보를 찾을 수 없어요.")
    return store.directory(job_id)


def _read(job_id):
    path = _directory(job_id) / "source-score.json"
    if path.is_symlink() or not path.is_file():
        raise HTTPException(404, "원본 유지 악보를 찾을 수 없어요.")
    if path.stat().st_size > STATE_BYTES:
        raise HTTPException(409, "악보 저장 상태가 너무 커요. 원본을 보관하고 관리자에게 확인해주세요.")
    state = json.loads(path.read_text(encoding="utf-8"))
    if (state.get("id") != job_id or _digest(state["xml"]) != state["working_sha256"]
            or _digest(state["initial_xml"]) != state["initial_sha256"]):
        raise HTTPException(409, "저장된 악보의 무결성 확인에 실패했어요. 원본은 덮어쓰지 않습니다.")
    return state


def _write_state(state, *, folder=None):
    payload = json.dumps(state, ensure_ascii=False).encode("utf-8")
    if len(payload) > STATE_BYTES:
        raise ValueError("편집 기록의 저장 용량을 초과했어요. 악보를 파트별로 나누어주세요.")
    folder = folder if folder is not None else _directory(state["id"])
    temporary = folder / "source-score.tmp"
    if temporary.is_symlink() or (folder / "source-score.json").is_symlink():
        raise ValueError("안전하지 않은 악보 저장 경로예요.")
    temporary.write_bytes(payload)
    temporary.replace(folder / "source-score.json")


def _styled(state, *, original=False):
    xml = state["initial_xml"] if original else state["xml"]
    return restyle_working_xml(xml.encode("utf-8"), state["part_id"],
                               state["layout"]["preset"], state["layout"]["measures_per_line"])


def project(job_id, state=None):
    """Return up-to-date public job metadata from the authoritative state."""
    with store.LOCK:
        state = state or _read(job_id)
        try:
            job = store.get(job_id)
        except KeyError:
            raise HTTPException(404, "원본 유지 프로젝트를 찾을 수 없어요.") from None
        job["score_preserved"] = {"instrument": state["instrument"], "part_id": state["part_id"], "revision": state["revision"]}
        for stem in job["stems"]:
            if stem["id"] == state["instrument"]:
                stem.update(score_revision=state["revision"], score_layout=state["layout"],
                            score_edited=state["xml"] != state["initial_xml"],
                            note_count=len(source_score.describe(state["xml"], state["part_id"], state["instrument"])["notes"]))
        return job


def _document(state):
    info = source_score.describe(state["xml"], state["part_id"], state["instrument"])
    styled, layout_warnings = _styled(state)
    content_check = verify_layout(state["xml"], state["initial_xml"], styled, state["part_id"])
    base = f'/api/source-scores/{state["id"]}/files/'
    return {"id": state["id"], "revision": state["revision"], "title": state["title"],
            "instrument": state["instrument"], "part_id": state["part_id"], "layout": state["layout"],
            "content_check": content_check,
            "integrity_report": score_integrity.audit(state["xml"], state["part_id"], state["instrument"]),
            "xml": styled.decode("utf-8"), "notes": info["notes"], "drum_options": info["drum_options"],
            "warnings": list(dict.fromkeys([SOURCE_WARNING, *state["warnings"], *info.get("warnings", []),
                                              *[w for w in layout_warnings if w != LAYOUT_WARNING]])),
            "source_url": base + state["source_name"], "current_url": base + "current.musicxml",
            "original_url": base + "original.musicxml", "source_sha256": state["source_sha256"],
            "working_sha256": state["working_sha256"],
            "attachments": [{"name": name, "url": base + name, "sha256": digest}
                            for name, digest in state.get("attachments", {}).items()],
            "history": {"undo": bool(state["undo"]), "redo": bool(state["redo"])}}


def _snapshot(state):
    return {"xml": state["xml"], "layout": copy.deepcopy(state["layout"])}


def _trim_history(state):
    for key in ("undo", "redo"):
        state[key] = state[key][-HISTORY_LIMIT:]
    while sum(len(entry["xml"].encode("utf-8")) for key in ("undo", "redo") for entry in state[key]) > HISTORY_BYTES:
        # Undo contains older snapshots, redo the nearest future at its end.
        key = "undo" if len(state["undo"]) >= len(state["redo"]) else "redo"
        state[key].pop(0)


def _change(job_id, revision, transform):
    with store.LOCK:
        state = _read(job_id)
        if revision != state["revision"]:
            raise HTTPException(409, "다른 화면에서 악보가 변경됐어요. 입력 내용을 보관하고 최신 악보를 다시 불러와주세요.")
        updated = copy.deepcopy(state)
        transform(updated)
        if _snapshot(updated) == _snapshot(state):
            return {"document": _document(state), "job": project(job_id, state)}
        updated["working_sha256"] = _digest(updated["xml"])
        updated["revision"] = uuid4().hex
        _trim_history(updated)
        # Validate and derive the complete response before committing anything.
        doc = _document(updated)
        job = project(job_id, updated)
        _write_state(updated)
        return {"document": doc, "job": job}


def _error(error):
    return HTTPException(422, str(error)[:500])


def _same_tab_provenance(existing, incoming):
    """Warning copy may evolve; every identity and future data field must match."""
    required = {"id", "source_sha256", "coordinate_sha256", "review_sha256", "method"}
    if (not isinstance(existing, dict) or not isinstance(incoming, dict)
            or not required.issubset(existing) or not required.issubset(incoming)):
        return False
    return ({key: value for key, value in existing.items() if key != "warnings"}
            == {key: value for key, value in incoming.items() if key != "warnings"})


def _create(data, filename, part_id, instrument, preset, measures_per_line, provenance, *,
            creation_id=None, tab_review=None, attachments=None):
    if instrument not in INSTRUMENTS:
        raise ValueError("가져올 악기를 선택해주세요.")
    prepared = source_score.prepare(data, filename, part_id, instrument)
    info = source_score.describe(prepared["xml"], part_id, instrument)
    layout = {"preset": preset, "measures_per_line": measures_per_line, "show_numbers": True}
    # Exercise the renderer input validation before creating a project folder.
    restyle_working_xml(prepared["xml"].encode("utf-8"), part_id, preset, measures_per_line)
    attachments = attachments or {}
    if any(name not in ATTACHMENT_LIMITS or not isinstance(content, bytes)
           or len(content) > ATTACHMENT_LIMITS[name] for name, content in attachments.items()):
        raise ValueError("원본 대조 자료의 형식 또는 크기가 올바르지 않아요.")
    with store.LOCK:
        if creation_id:
            try:
                existing = store.get(creation_id)
            except KeyError:
                pass
            else:
                if existing.get("status") == "completed" and _same_tab_provenance(existing.get("score_tab_review"), tab_review):
                    return project(creation_id)
                raise HTTPException(409, "이 변환 요청의 프로젝트가 이미 있거나 저장이 중단됐어요. 기존 자료는 덮어쓰지 않습니다.")
        job = store.create(prepared["title"], "musicxml", **({"job_id": creation_id, "persist": False} if creation_id else {}))
        source_name = "source" + Path(filename).suffix.lower()
        state = {"id": job["id"], "version": 1, "revision": uuid4().hex,
                 "title": prepared["title"], "instrument": instrument, "part_id": part_id,
                 "xml": prepared["xml"], "initial_xml": prepared["xml"],
                 "working_sha256": _digest(prepared["xml"]), "initial_sha256": _digest(prepared["xml"]),
                 "source_sha256": _digest(data), "source_name": source_name,
                 "layout": layout, "undo": [], "redo": [],
                 "attachments": {name: _digest(content) for name, content in attachments.items()},
                 "warnings": [*prepared["warnings"], *(provenance["warnings"] if provenance else []),
                              *(tab_review.get("warnings", []) if tab_review else [])]}
        staging = None
        try:
            if creation_id:
                # A deterministic ID is never reserved by a half-written job.
                # Stage on the same filesystem, validate, then publish the
                # complete directory atomically while holding the store lock.
                store.DATA_DIR.mkdir(parents=True, exist_ok=True)
                staging = Path(tempfile.mkdtemp(prefix=f".source-{creation_id}-", dir=store.DATA_DIR))
            folder = staging if staging is not None else _directory(job["id"])
            (folder / source_name).write_bytes(data)
            for name, content in attachments.items():
                (folder / name).write_bytes(content)
            if staging is not None:
                _write_state(state, folder=folder)
            else:
                _write_state(state)
            for stem in job["stems"]:
                if stem["id"] == instrument:
                    stem.update(score_status="ready", score_url=f'/api/source-scores/{job["id"]}/files/current.musicxml',
                                score_source_url=f'/api/source-scores/{job["id"]}/files/{source_name}',
                                score_title=prepared["title"], score_revision=state["revision"], score_layout=layout,
                                score_audio_offset=0, note_count=len(info["notes"]), score_edited=False)
            job.update(status="completed", stage="done", progress=100, duration=0,
                       message="원본 표기를 유지한 악보입니다. 스타일과 선택한 음표를 편집할 수 있어요.",
                       score_preserved={"instrument": instrument, "part_id": part_id, "revision": state["revision"]})
            if provenance:
                job["score_omr"] = provenance
            if tab_review:
                job["score_tab_review"] = tab_review
            if staging is not None:
                _document(state)
                (folder / "job.json").write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
                destination = _directory(job["id"])
                if destination.exists() or destination.is_symlink():
                    raise HTTPException(409, "이 변환 요청의 프로젝트가 이미 있어요. 기존 자료는 덮어쓰지 않습니다.")
                staging.rename(destination)
                staging = None
            else:
                store.save(job)
            return project(job["id"], state)
        except Exception:
            if not creation_id:
                store.update(job["id"], status="error", error="원본 유지 프로젝트를 저장하지 못했어요. 원본 파일은 변경하지 않았습니다.")
            raise
        finally:
            # Only this invocation's fresh staging directory is disposable.
            # Completed projects and previously existing paths are never removed.
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)


@router.post("", status_code=201)
async def create(file: UploadFile = File(...), part_id: str = Form(..., max_length=80), instrument: str = Form(...),
                 preset: Literal["practice", "standard", "large"] = Form(default="practice"),
                 measures_per_line: int = Form(default=4), omr_id: str | None = Form(default=None, max_length=32),
                 omr_result_id: str | None = Form(default=None, max_length=32)):
    try:
        data = await file.read(score_import.UPLOAD_LIMIT + 1)
        if len(data) > score_import.UPLOAD_LIMIT:
            raise HTTPException(413, "악보 파일은 2MB 이하로 선택해주세요.")
        provenance = score_omr.provenance(data, omr_id, omr_result_id)
        return await asyncio.to_thread(_create, data, file.filename or "", part_id, instrument, preset, measures_per_line, provenance)
    except ValueError as error:
        raise _error(error) from None
    finally:
        await file.close()


@router.get("/{job_id}")
def get(job_id: str):
    with store.LOCK:
        try:
            return _document(_read(job_id))
        except ValueError as error:
            raise _error(error) from None


class Revision(BaseModel):
    model_config = {"extra": "forbid", "strict": True}
    base_revision: str = Field(pattern=r"^[a-f0-9]{32}$")


class EditRequest(Revision):
    patch: dict = Field(max_length=10)


class LayoutRequest(Revision):
    preset: Literal["practice", "standard", "large"]
    measures_per_line: Literal[2, 4]


class HistoryRequest(Revision):
    action: Literal["undo", "redo"]


@router.post("/{job_id}/edit")
def edit(job_id: str, body: EditRequest):
    def apply(state):
        xml = source_score.apply_edit(state["xml"], state["part_id"], state["instrument"], body.patch)
        if xml != state["xml"]:
            state["undo"].append(_snapshot(state))
            state["redo"] = []
            state["xml"] = xml
    try:
        return _change(job_id, body.base_revision, apply)
    except ValueError as error:
        raise _error(error) from None


@router.put("/{job_id}/layout")
def layout(job_id: str, body: LayoutRequest):
    def apply(state):
        updated = {"preset": body.preset, "measures_per_line": body.measures_per_line, "show_numbers": True}
        if updated != state["layout"]:
            state["undo"].append(_snapshot(state))
            state["redo"] = []
            state["layout"] = updated
    try:
        return _change(job_id, body.base_revision, apply)
    except ValueError as error:
        raise _error(error) from None


@router.post("/{job_id}/history")
def history(job_id: str, body: HistoryRequest):
    def apply(state):
        if not state[body.action]:
            raise HTTPException(409, "되돌리거나 다시 실행할 기록이 없어요.")
        target = state[body.action].pop()
        state["redo" if body.action == "undo" else "undo"].append(_snapshot(state))
        state.update(target)
    try:
        return _change(job_id, body.base_revision, apply)
    except ValueError as error:
        raise _error(error) from None


@router.get("/{job_id}/files/{name}")
def download(job_id: str, name: str):
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}
    with store.LOCK:
        state = _read(job_id)
        if name in state.get("attachments", {}) and name in ATTACHMENT_LIMITS:
            path = _directory(job_id) / name
            if (path.is_symlink() or not path.is_file() or path.stat().st_size > ATTACHMENT_LIMITS[name]
                    or _digest(path.read_bytes()) != state["attachments"][name]):
                raise HTTPException(409, "보관한 원본 대조 자료가 변경되어 다운로드를 중단했어요.")
            return FileResponse(path, filename=name, media_type="application/pdf" if name.endswith(".pdf") else "application/json", headers=headers)
        if name == state["source_name"]:
            path = _directory(job_id) / name
            if path.is_symlink() or not path.is_file() or _digest(path.read_bytes()) != state["source_sha256"]:
                raise HTTPException(409, "보관한 원본 파일이 변경되었거나 읽을 수 없어요.")
            return FileResponse(path, filename=name, media_type="application/octet-stream", headers=headers)
        if name == "notes.json":
            doc = _document(state)
            payload = {"schema_version": 1, "instrument": state["instrument"], "part_id": state["part_id"],
                       "revision": state["revision"], "time_unit": "quarter-note-fraction",
                       "source_sha256": state["source_sha256"], "content_check": doc["content_check"],
                       "notes": doc["notes"], "warnings": doc["warnings"], "integrity_report": doc["integrity_report"],
                       "description": "Selected-part note index, not a replacement for canonical MusicXML. PDF recognition may be incomplete."}
            headers["Content-Disposition"] = 'attachment; filename="notes.json"'
            return Response(json.dumps(payload, ensure_ascii=False), media_type="application/json", headers=headers)
        if name not in {"original.musicxml", "current.musicxml"}:
            raise HTTPException(404, "악보 파일을 찾을 수 없어요.")
        xml, _ = _styled(state, original=name == "original.musicxml")
        canonical = state["initial_xml"] if name == "original.musicxml" else state["xml"]
        verify_layout(canonical, state["initial_xml"], xml, state["part_id"])
        headers["Content-Disposition"] = f'attachment; filename="{name}"'
        return Response(xml, media_type="application/vnd.recordare.musicxml+xml", headers=headers)


def archive(job_id):
    with store.LOCK:
        state = _read(job_id)
        path = _directory(job_id) / state["source_name"]
        if path.is_symlink() or not path.is_file():
            raise HTTPException(409, "보관한 원본 파일을 읽을 수 없어요.")
        raw = path.read_bytes()
        if _digest(raw) != state["source_sha256"]:
            raise HTTPException(409, "보관한 원본 파일의 무결성 확인에 실패했어요.")
        current, _ = _styled(state)
        initial, _ = _styled(state, original=True)
        verify_layout(state["xml"], state["initial_xml"], current, state["part_id"])
        verify_layout(state["initial_xml"], state["initial_xml"], initial, state["part_id"])
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr(state["source_name"], raw)
            bundle.writestr("current.musicxml", current)
            bundle.writestr("original.musicxml", initial)
            bundle.writestr("working-all-parts.musicxml", state["xml"])
            for name, digest in state.get("attachments", {}).items():
                extra = _directory(job_id) / name
                if (name not in ATTACHMENT_LIMITS or extra.is_symlink() or not extra.is_file()
                        or extra.stat().st_size > ATTACHMENT_LIMITS[name]):
                    raise HTTPException(409, "원본 대조 자료를 읽을 수 없어요.")
                content = extra.read_bytes()
                if _digest(content) != digest:
                    raise HTTPException(409, "원본 대조 자료의 무결성 확인에 실패했어요.")
                bundle.writestr(name, content)
            bundle.writestr("README.txt", "Original-notation project. No audio transcription or 1/16 quantization.\n"
                            "source.* is the unchanged upload; original.musicxml is the initial selected part after safe preparation.\n"
                            "current.musicxml is the edited selected part; working-all-parts.musicxml retains all parts.\n"
                            "OMR can omit or misread notes. Styling is not recognition correction.\n"
                            f'Upload SHA256: {state["source_sha256"]}\nWorking SHA256: {state["working_sha256"]}\n')
        return Response(stream.getvalue(), media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="akbo-source-{job_id[:8]}.zip"',
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
