import asyncio
import re
import threading
import zipfile
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from uuid import uuid4

from . import store
from .config import INSTRUMENTS, MAX_AUDIO_SECONDS, MAX_UPLOAD_BYTES, ROOT
from .media import EXTENSIONS, youtube_url
from .pipeline import run_demo, run_separation, run_transcription, run_cpu_analysis, stem_update
from .editing import ScoreEdit, load_document, persist, validate_edit, notation_metadata
from .separator import engine_status
from . import lyrics

POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="audio-worker")
EVENTS: dict[str, threading.Event] = {}
TASK_LOCK = threading.RLock()


@asynccontextmanager
async def lifespan(app):
    store.recover()
    yield
    for event in list(EVENTS.values()):
        event.set()


app = FastAPI(title="Akbo Maker", lifespan=lifespan)


class UploadSizeLimit:
    """Limit the wire body before Starlette spools multipart uploads to disk."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") != "/api/jobs" or scope.get("method") != "POST":
            return await self.app(scope, receive, send)
        limit = MAX_UPLOAD_BYTES + 1024 * 1024  # multipart overhead
        headers = dict(scope.get("headers", []))
        try:
            too_large = int(headers.get(b"content-length", b"0")) > limit
        except ValueError:
            return await JSONResponse({"detail": "올바르지 않은 업로드 요청이에요."}, status_code=400)(scope, receive, send)
        if too_large:
            return await JSONResponse({"detail": "파일이 업로드 크기 제한을 초과해요."}, status_code=413)(scope, receive, send)
        total = 0

        async def limited_receive():
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > limit:
                    raise HTTPException(413, "파일이 업로드 크기 제한을 초과해요.")
            return message

        return await self.app(scope, limited_receive, send)


app.add_middleware(UploadSizeLimit)


def valid_job(job_id: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(404, "작업을 찾을 수 없어요.")
    try:
        return store.get(job_id)
    except KeyError:
        raise HTTPException(404, "작업을 찾을 수 없어요.")


def reserve(job_id: str) -> threading.Event:
    with TASK_LOCK:
        if len(EVENTS) >= 3:
            raise HTTPException(429, "현재 처리할 작업이 많아요. 잠시 뒤 다시 시도해주세요.")
        if job_id in EVENTS:
            raise HTTPException(409, "이미 처리 중인 작업이에요.")
        event = threading.Event()
        EVENTS[job_id] = event
        return event


def submit(job_id: str, function, event, *args):
    def run():
        try:
            function(job_id, event, *args)
        finally:
            with TASK_LOCK:
                EVENTS.pop(job_id, None)
    POOL.submit(run)


@app.get("/api/health")
def health():
    return {"ok": True, "engine": engine_status(), "lyrics": lyrics.status(), "limits": {"max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024), "max_audio_seconds": MAX_AUDIO_SECONDS}, "demo_available": True}


@app.post("/api/jobs", status_code=202)
async def create_job(file: UploadFile | None = File(default=None), url: str | None = Form(default=None), analysis_only: bool = Form(default=False)):
    if bool(file) == bool(url):
        raise HTTPException(422, "음악 파일 또는 유튜브 링크 중 하나를 선택해주세요.")
    if url:
        try:
            url = youtube_url(url)
        except ValueError as error:
            raise HTTPException(422, str(error))
    extension = Path(file.filename or "").suffix.lower() if file else None
    if file and extension not in EXTENSIONS:
        raise HTTPException(415, "지원하지 않는 파일 형식이에요. MP3, WAV, FLAC, M4A, MP4 등을 사용해주세요.")
    engine = await asyncio.to_thread(engine_status)
    if not analysis_only and not engine["available"]:
        raise HTTPException(503, " ".join(engine["issues"]))
    with TASK_LOCK:
        if len(EVENTS) >= 3:
            raise HTTPException(429, "현재 처리할 작업이 많아요. 잠시 뒤 다시 시도해주세요.")
        job = store.create(Path(file.filename or "음악").stem if file else "YouTube 음악", "upload" if file else "youtube")
        job = store.update(job["id"], analysis_only=analysis_only)
        event = threading.Event()
        EVENTS[job["id"]] = event
    source = store.directory(job["id"]) / f"source{extension}" if file else None
    try:
        if file:
            total = 0
            with source.open("wb") as output:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise HTTPException(413, "파일이 업로드 크기 제한을 초과해요.")
                    output.write(chunk)
            if total == 0:
                raise HTTPException(422, "빈 파일은 업로드할 수 없어요.")
        submit(job["id"], run_separation, event, source, url, analysis_only)
        return job
    except BaseException as error:
        with TASK_LOCK:
            EVENTS.pop(job["id"], None)
        store.update(job["id"], status="error", error="업로드를 완료하지 못했어요.")
        raise error
    finally:
        if file:
            await file.close()


@app.post("/api/demo", status_code=202)
def create_demo():
    with TASK_LOCK:
        if len(EVENTS) >= 3:
            raise HTTPException(429, "잠시 뒤 샘플을 다시 열어주세요.")
        job = store.create("Sunday, softly", "sample", demo=True)
        event = threading.Event()
        EVENTS[job["id"]] = event
    submit(job["id"], run_demo, event)
    return job


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return valid_job(job_id)


@app.post("/api/jobs/{job_id}/cancel")
def cancel(job_id: str):
    job = valid_job(job_id)
    with TASK_LOCK:
        event = EVENTS.get(job_id)
        if event:
            event.set()
            return store.update(job_id, message="현재 구간이 끝나면 작업을 중단할게요.")
    return job


class TranscriptionRequest(BaseModel):
    instruments: list[str] = Field(default_factory=lambda: list(INSTRUMENTS), min_length=1, max_length=6)
    bpm: int = Field(default=120, ge=40, le=240)
    overwrite_edits: bool = False
    audio_offset: float = Field(default=0, ge=0, le=600, allow_inf_nan=False)


@app.post("/api/jobs/{job_id}/transcribe", status_code=202)
def request_transcription(job_id: str, body: TranscriptionRequest):
    with TASK_LOCK:
        return start_transcription(job_id, body)


def start_transcription(job_id: str, body: TranscriptionRequest):
    job = valid_job(job_id)
    if job["status"] not in {"separated", "completed", "cancelled", "error"}:
        raise HTTPException(409, "먼저 악기 분리가 완료되어야 해요.")
    if body.audio_offset >= (job.get("duration") or 0):
        raise HTTPException(422, "첫 박 위치는 곡 길이 안에 있어야 해요.")
    instruments = list(dict.fromkeys(body.instruments))
    if any(inst not in INSTRUMENTS for inst in instruments):
        raise HTTPException(422, "지원하지 않는 악기가 포함되어 있어요.")
    ready = {stem["id"] for stem in job["stems"] if stem["status"] == "ready"}
    if not set(instruments).issubset(ready):
        raise HTTPException(409, "아직 분리되지 않은 악기가 포함되어 있어요.")
    if not body.overwrite_edits and any(s.get("score_edited") and s["id"] in instruments for s in job["stems"]):
        raise HTTPException(409, "직접 수정한 악보가 있어요. 다시 채보하면 수정본을 대체하므로 먼저 확인해주세요.")
    if not job["demo"] and any(inst != "drums" for inst in instruments) and not engine_status()["transcription_available"]:
        raise HTTPException(503, "서버에 Basic Pitch 채보 엔진을 설치해주세요.")
    event = reserve(job_id)
    job = store.update(job_id, status="transcribing", stage="transcribing", progress=0, error=None)
    submit(job_id, run_transcription, event, instruments, body.bpm, body.audio_offset)
    return job


def score_stem(job_id, inst):
    job = valid_job(job_id)
    stem = next((s for s in job["stems"] if s["id"] == inst), None)
    if not stem or not stem.get("score_url"):
        raise HTTPException(404, "먼저 악보를 만들어주세요.")
    return job, stem


class RecognitionRequest(BaseModel):
    model_config = {"extra": "forbid"}
    source: str = Field(default="vocal", pattern=r"^(original|vocal)$")
    language: str = Field(default="auto", pattern=r"^(auto|ko|en|ja|zh)$")
    replace_candidate: bool = False


def begin_analysis(job_id, kind, source="original", language="auto"):
    job = valid_job(job_id)
    if job_id in EVENTS or job["status"] in {"queued", "running", "transcribing", "analyzing"}:
        raise HTTPException(409, "현재 작업이 끝나면 분석해주세요.")
    if not job.get("duration") or not (store.directory(job_id) / f"{source}.wav").is_file():
        raise HTTPException(409, "분석할 음원이 아직 준비되지 않았어요.")
    event = reserve(job_id)
    previous = job["status"]
    result = store.update(job_id, status="analyzing", stage=kind, progress=1, analysis_error=None,
                          analysis_previous_status=previous,
                          message="BPM·박 위치를 분석하고 있어요" if kind == "beats" else "CPU 가사 모델을 준비하고 있어요. 첫 실행에는 다운로드가 필요해요.")
    submit(job_id, run_cpu_analysis, event, kind, previous, source, language)
    return result


@app.post("/api/jobs/{job_id}/analyze-beats", status_code=202)
def request_beats(job_id: str):
    with TASK_LOCK:
        return begin_analysis(job_id, "beats")


@app.post("/api/jobs/{job_id}/lyrics/recognize", status_code=202)
def request_lyrics(job_id: str, body: RecognitionRequest):
    with TASK_LOCK:
        job = valid_job(job_id)
        if job["demo"]:
            raise HTTPException(422, "합성 샘플에는 실제 노랫말이 없어요. 실제 음원으로 가사를 인식해주세요.")
        if job.get("lyric_candidate") and not body.replace_candidate:
            raise HTTPException(409, "기존 가사 초안이 있어요. 새 인식으로 대체할지 확인해주세요.")
        if not lyrics.status()["available"]:
            raise HTTPException(503, "CPU 가사 인식 패키지를 설치해주세요: backend/requirements-asr.txt")
        return begin_analysis(job_id, "lyrics", body.source, body.language)


@app.put("/api/jobs/{job_id}/lyrics/candidate")
def save_candidate(job_id: str, body: lyrics.CandidateEdit):
    with TASK_LOCK, store.LOCK:
        job = valid_job(job_id)
        if job_id in EVENTS:
            raise HTTPException(409, "분석이 끝나면 초안을 저장해주세요.")
        current = job.get("lyric_candidate", {})
        if body.base_revision != current.get("revision", ""):
            raise HTTPException(409, "다른 화면에서 가사 초안이 변경됐어요. 최신 초안을 다시 불러와주세요.")
        ids = set()
        for cue in body.cues:
            if cue.id in ids or cue.end <= cue.start or cue.end > (job.get("duration") or 0) + .001 or not cue.text.strip():
                raise HTTPException(422, "가사 ID, 시작·끝 시간, 곡 범위와 내용을 확인해주세요.")
            ids.add(cue.id)
        candidate = {**current, "revision": uuid4().hex, "cues": sorted([c.model_dump() for c in body.cues], key=lambda c: c["start"])}
        return store.update(job_id, lyric_candidate=candidate)


class ApplyLyricsRequest(BaseModel):
    model_config = {"extra": "forbid"}
    base_revision: str = Field(max_length=80)
    score_revisions: dict[str, str] = Field(default_factory=dict, max_length=6)


@app.post("/api/jobs/{job_id}/lyrics/apply")
def apply_candidate(job_id: str, body: ApplyLyricsRequest):
    with TASK_LOCK, store.LOCK:
        job = valid_job(job_id)
        if job_id in EVENTS:
            raise HTTPException(409, "분석이 끝나면 가사를 적용해주세요.")
        candidate = job.get("lyric_candidate", {})
        if candidate.get("revision") != body.base_revision:
            raise HTTPException(409, "가사 초안이 변경됐어요. 최신 초안을 확인해주세요.")
        documents, skipped = [], 0
        targets = [s for s in job["stems"] if s.get("score_url") and s.get("score_status") == "ready"]
        if set(body.score_revisions) != {s["id"] for s in targets}:
            raise HTTPException(409, "대상 악보가 변경됐어요. 다시 확인하고 적용해주세요.")
        for stem in targets:
            doc = load_document(store.directory(job_id), stem["id"], job["duration"])
            if doc["revision"] != body.score_revisions[stem["id"]]:
                raise HTTPException(409, "다른 화면에서 악보가 수정됐어요. 최신 악보를 확인해주세요.")
            try:
                items, outside = lyrics.timed_lyrics(candidate["cues"], doc)
                skipped += outside
                edit = ScoreEdit(base_revision=doc["revision"], title=doc["title"], bpm=doc["bpm"], notes=doc["notes"],
                                 annotations=doc["annotations"], layout=doc["layout"], tab=doc.get("tab"), lyrics=items)
                validated = validate_edit(edit, doc)
                # A shared lyric update is not a note/auto-fingering edit.
                # Preserve the exact stored note data, including legacy fields.
                documents.append({**validated, "notes": doc["notes"]})
            except ValueError as error:
                raise HTTPException(422, str(error))
        folder = store.directory(job_id)
        with tempfile.TemporaryDirectory(prefix="guide-", dir=folder) as temporary:
            staging = Path(temporary)
            for doc in documents:
                persist(doc, staging)
            for file in staging.iterdir():
                file.replace(folder / file.name)
        for doc in documents:
            stem_update(job_id, doc["instrument"], score_revision=doc["revision"], score_edited=True, **notation_metadata(doc))
        return {"job": store.update(job_id, lyric_guide=candidate), "applied": len(documents), "skipped": skipped}


@app.post("/api/jobs/{job_id}/scores/{inst}/new")
def new_empty_score(job_id: str, inst: str, body: TranscriptionRequest):
    with TASK_LOCK, store.LOCK:
        job = valid_job(job_id)
        if inst not in INSTRUMENTS:
            raise HTTPException(404, "지원하지 않는 악기예요.")
        if job_id in EVENTS or not job.get("duration"):
            raise HTTPException(409, "음원 준비가 끝나면 악보를 만들어주세요.")
        stem = next(s for s in job["stems"] if s["id"] == inst)
        if stem.get("score_url"):
            raise HTTPException(409, "이미 악보가 있어요. 직접 수정에서 편집해주세요.")
        if body.audio_offset >= job["duration"]:
            raise HTTPException(422, "첫 박 위치가 곡 범위를 벗어났어요.")
        from .editing import generate_score
        doc = generate_score([], inst, job["title"], body.bpm, job["duration"], store.directory(job_id),
                             body.audio_offset, job.get("lyric_guide", {}).get("cues"))
        doc["edited"] = True
        persist(doc, store.directory(job_id))
        stem_update(job_id, inst, score_status="ready", score_url=store.asset_url(job_id, f"{inst}.musicxml"),
                    midi_url=store.asset_url(job_id, f"{inst}.mid"), score_bpm=body.bpm, score_revision=doc["revision"],
                    score_title=doc["title"], score_layout=doc["layout"], note_count=0,
                    score_edited=True, score_warning="직접 입력할 빈 악보입니다. 자동 채보·악기 분리 결과가 아니에요.", **notation_metadata(doc))
        return store.get(job_id)


@app.get("/api/jobs/{job_id}/scores/{inst}")
def get_score_document(job_id: str, inst: str, original: bool = False):
    with store.LOCK:
        job, _ = score_stem(job_id, inst)
        try:
            return load_document(store.directory(job_id), inst, job["duration"], original=original)
        except FileNotFoundError:
            raise HTTPException(404, "편집할 악보를 찾을 수 없어요.")


@app.put("/api/jobs/{job_id}/scores/{inst}")
def save_score_document(job_id: str, inst: str, body: ScoreEdit):
    # Serializes edits with transcription; stale browser tabs cannot overwrite.
    with TASK_LOCK:
        if job_id in EVENTS:
            raise HTTPException(409, "작업이 끝나면 악보를 저장해주세요.")
        with store.LOCK:
            job, _ = score_stem(job_id, inst)
            current = load_document(store.directory(job_id), inst, job["duration"])
            if body.base_revision != current["revision"]:
                raise HTTPException(409, "다른 화면에서 악보가 변경됐어요. 편집본을 JSON으로 보관하고 최신 악보를 다시 열어주세요.")
            try:
                document = validate_edit(body, current)
            except ValueError as error:
                raise HTTPException(422, str(error))
            persist(document, store.directory(job_id))
            stem_update(job_id, inst, score_revision=document["revision"], score_bpm=document["bpm"],
                        score_title=document["title"], score_layout=document["layout"], score_edited=True,
                        note_count=len(document["notes"]), score_status="ready", score_error=None, **notation_metadata(document))
            return {"document": document, "job": store.get(job_id)}


@app.post("/api/jobs/{job_id}/scores/{inst}/preview")
def preview_score_document(job_id: str, inst: str, body: ScoreEdit):
    with store.LOCK:
        job, _ = score_stem(job_id, inst)
        current = load_document(store.directory(job_id), inst, job["duration"])
    try:
        document = validate_edit(body, current)
    except ValueError as error:
        raise HTTPException(422, str(error))
    with tempfile.TemporaryDirectory(prefix="akbo-preview-") as temporary:
        folder = Path(temporary)
        persist(document, folder)
        return {"musicxml": (folder / f"{inst}.musicxml").read_text()}


class CopyLyricsRequest(BaseModel):
    model_config = {"extra": "forbid"}
    base_revision: str = Field(max_length=80)


@app.post("/api/jobs/{job_id}/scores/{inst}/copy-lyrics")
def copy_score_lyrics(job_id: str, inst: str, body: CopyLyricsRequest):
    with TASK_LOCK, store.LOCK:
        if job_id in EVENTS:
            raise HTTPException(409, "작업이 끝나면 가사를 복사해주세요.")
        job, _ = score_stem(job_id, inst)
        folder = store.directory(job_id)
        source = load_document(folder, inst, job["duration"])
        if source["revision"] != body.base_revision:
            raise HTTPException(409, "원본 악보가 변경됐어요. 최신 악보를 다시 열어주세요.")
        if not source.get("lyrics"):
            raise HTTPException(422, "먼저 가사를 저장해주세요.")
        documents = []
        for stem in job["stems"]:
            if stem["id"] == inst or stem.get("score_status") != "ready" or not stem.get("score_url"):
                continue
            target = load_document(folder, stem["id"], job["duration"])
            target_bpm = target.get("timing_bpm", target["bpm"])
            source_bpm = source.get("timing_bpm", source["bpm"])
            items = [{**l, "id": uuid4().hex, "start": round((l["start"] / source_bpm / 4 * 60 + source.get("audio_offset", 0) - target.get("audio_offset", 0)) * target_bpm / 60 * 4)} for l in source["lyrics"]]
            if any(l["start"] < 0 or l["start"] >= target["ticks"] for l in items) or len({l["start"] for l in items}) != len(items):
                raise HTTPException(409, "대상 악보의 시간 격자가 달라 가사를 안전하게 복사할 수 없어요.")
            edit = ScoreEdit(base_revision=target["revision"], title=target["title"], bpm=target["bpm"],
                             notes=target["notes"], annotations=target["annotations"], layout=target["layout"],
                             tab=target.get("tab"), lyrics=items)
            documents.append(validate_edit(edit, target))
        # Generate every target first: a failed export never overwrites any target.
        with tempfile.TemporaryDirectory(prefix="lyrics-", dir=folder) as temporary:
            staging = Path(temporary)
            for document in documents:
                persist(document, staging)
            for file in staging.iterdir():
                file.replace(folder / file.name)
        for document in documents:
            stem_update(job_id, document["instrument"], score_revision=document["revision"], score_edited=True,
                        score_title=document["title"], score_layout=document["layout"], **notation_metadata(document))
        return {"job": store.get(job_id), "copied": len(documents)}


def allowed_files() -> set[str]:
    return {"original.wav", "residual.wav", *[f"{inst}.{extension}" for inst in INSTRUMENTS for extension in ("wav", "mid", "musicxml")]}


@app.get("/api/jobs/{job_id}/files/{name}")
def download(job_id: str, name: str, download: bool = False):
    valid_job(job_id)
    if name not in allowed_files():
        raise HTTPException(404, "파일을 찾을 수 없어요.")
    path = store.directory(job_id) / name
    if not path.is_file():
        raise HTTPException(404, "파일이 아직 준비되지 않았어요.")
    mime = {".wav": "audio/wav", ".mid": "audio/midi", ".musicxml": "application/vnd.recordare.musicxml+xml"}[path.suffix]
    return FileResponse(path, media_type=mime, filename=name if download else None)


@app.get("/api/jobs/{job_id}/archive")
def archive(job_id: str):
    job = valid_job(job_id)
    if job["status"] in {"queued", "running", "transcribing", "analyzing"}:
        raise HTTPException(409, "처리가 끝나면 전체 파일을 받을 수 있어요.")
    folder = store.directory(job_id)
    temporary = folder / f"archive-{uuid4().hex}.tmp"
    target = folder / "archive.zip"
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in sorted(allowed_files()):
            path = store.directory(job_id) / name
            if path.is_file():
                bundle.write(path, name)
        bundle.writestr("README.txt", "Akbo Maker\n" + ("Original synthesized demo. Not SAM Audio inference.\n" if job["demo"] else "SAM Audio sequential source separation.\n") + "Scores are automatic drafts, quantized to a 1/16-note grid in 4/4. Check pitches and rhythm.\nDrum scores are experimental spectral-onset estimates.\n")
    temporary.replace(target)
    return FileResponse(target, media_type="application/zip", filename=f"akbo-{job_id[:8]}.zip")


# Built frontend can be served by the same API origin on a GPU host.
dist = ROOT / "dist"
if dist.is_dir():
    app.mount("/", StaticFiles(directory=dist, html=True), name="web")
