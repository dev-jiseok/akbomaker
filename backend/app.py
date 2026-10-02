import asyncio
import re
import threading
import zipfile
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
from .pipeline import run_demo, run_separation, run_transcription
from .separator import engine_status

POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="audio-worker")
EVENTS: dict[str, threading.Event] = {}
TASK_LOCK = threading.Lock()


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
    return {"ok": True, "engine": engine_status(), "limits": {"max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024), "max_audio_seconds": MAX_AUDIO_SECONDS}, "demo_available": True}


@app.post("/api/jobs", status_code=202)
async def create_job(file: UploadFile | None = File(default=None), url: str | None = Form(default=None)):
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
    if not engine["available"]:
        raise HTTPException(503, " ".join(engine["issues"]))
    with TASK_LOCK:
        if len(EVENTS) >= 3:
            raise HTTPException(429, "현재 처리할 작업이 많아요. 잠시 뒤 다시 시도해주세요.")
        job = store.create(Path(file.filename or "음악").stem if file else "YouTube 음악", "upload" if file else "youtube")
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
        submit(job["id"], run_separation, event, source, url)
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


@app.post("/api/jobs/{job_id}/transcribe", status_code=202)
def request_transcription(job_id: str, body: TranscriptionRequest):
    job = valid_job(job_id)
    if job["status"] not in {"separated", "completed", "cancelled", "error"}:
        raise HTTPException(409, "먼저 악기 분리가 완료되어야 해요.")
    instruments = list(dict.fromkeys(body.instruments))
    if any(inst not in INSTRUMENTS for inst in instruments):
        raise HTTPException(422, "지원하지 않는 악기가 포함되어 있어요.")
    ready = {stem["id"] for stem in job["stems"] if stem["status"] == "ready"}
    if not set(instruments).issubset(ready):
        raise HTTPException(409, "아직 분리되지 않은 악기가 포함되어 있어요.")
    if not job["demo"] and any(inst != "drums" for inst in instruments) and not engine_status()["transcription_available"]:
        raise HTTPException(503, "서버에 Basic Pitch 채보 엔진을 설치해주세요.")
    event = reserve(job_id)
    job = store.update(job_id, status="transcribing", stage="transcribing", progress=0, error=None)
    submit(job_id, run_transcription, event, instruments, body.bpm)
    return job


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
    if job["status"] in {"queued", "running", "transcribing"}:
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
