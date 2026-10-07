import asyncio
import re
import threading
import zipfile
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from .frontend import FrontendFiles
from pydantic import BaseModel, Field
from uuid import uuid4

from . import store
from .config import INSTRUMENTS, MAX_AUDIO_SECONDS, MAX_UPLOAD_BYTES, ROOT
from .media import EXTENSIONS, youtube_url
from .pipeline import run_demo, run_separation, run_transcription, run_cpu_analysis, stem_update
from .editing import Meter, ScoreEdit, load_document, persist, validate_edit, notation_metadata
from .rhythm import normalize_meters, measure_map
from .separator import engine_status
from .drum_worker import worker_status
from . import lyrics
from . import score_import
from . import score_omr
from . import source_projects
from . import tab_review_projects
from .score_preservation import restyle_musicxml
from . import transcription_review
from . import review_cases
from .startup import prepare_engines

POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="audio-worker")
EVENTS: dict[str, threading.Event] = {}
TASK_LOCK = threading.RLock()


@asynccontextmanager
async def lifespan(app):
    app.state.engines_ready = await asyncio.to_thread(prepare_engines)
    store.recover()
    yield
    for event in list(EVENTS.values()):
        event.set()
    score_omr.shutdown()


app = FastAPI(title="Akbo Maker", lifespan=lifespan)
app.include_router(score_omr.router)
app.include_router(source_projects.router)
app.include_router(tab_review_projects.router)


class UploadSizeLimit:
    """Limit the wire body before Starlette spools multipart uploads to disk."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        score_upload = path in {"/api/score-import", "/api/score-import/inspect", "/api/score-import/preserve-preview", "/api/source-scores"} or bool(re.fullmatch(r"/api/jobs/[a-f0-9]{32}/scores/[a-z]+/import-preview", path))
        source_update = bool(re.fullmatch(r"/api/source-scores/[a-f0-9]{32}/(?:edit|layout|history)", path))
        tab_update = bool(re.fullmatch(r"/api/score-omr/[a-f0-9]{32}/tab-review(?:/(?:import|rhythm-suggestions))?", path))
        omr_upload = path == "/api/score-omr"
        if scope["type"] != "http" or scope.get("method") not in {"POST", "PUT"} or (path != "/api/jobs" and not score_upload and not omr_upload and not source_update and not tab_update):
            return await self.app(scope, receive, send)
        limit = tab_review_projects.MAX_STATE_BYTES if tab_update else 32 * 1024 if source_update else (score_omr.UPLOAD_LIMIT if omr_upload else score_import.UPLOAD_LIMIT if score_upload else MAX_UPLOAD_BYTES) + 1024 * 1024
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
    return {"ok": True, "ready": getattr(app.state, "engines_ready", False), "engine": engine_status(), "drum_engine": worker_status(), "lyrics": lyrics.status(), "limits": {"max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024), "max_audio_seconds": MAX_AUDIO_SECONDS}, "demo_available": True}


@app.post("/api/jobs", status_code=202)
async def create_job(file: UploadFile | None = File(default=None), url: str | None = Form(default=None), analysis_only: bool = Form(default=False), separation_strategy: Literal["sequential", "independent"] = Form(default="sequential")):
    if analysis_only and separation_strategy != "sequential":
        raise HTTPException(422, "원본만 분석할 때는 악기 분리 방식을 지정할 수 없어요.")
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
        job = store.update(job["id"], analysis_only=analysis_only, separation_strategy=separation_strategy)
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
        submit(job["id"], run_separation, event, source, url, analysis_only, separation_strategy)
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
    job = valid_job(job_id)
    if job.get("score_preserved"):
        return source_projects.project(job_id)
    # Read-only compatibility for projects created before offset metadata existed.
    # Do not rewrite the user's score, revision, or job merely by viewing it.
    for stem in job["stems"]:
        if stem.get("score_url") and "score_audio_offset" not in stem:
            path = store.directory(job_id) / f'{stem["id"]}.score.json'
            if path.is_file():
                stem["score_audio_offset"] = load_document(store.directory(job_id), stem["id"], job["duration"])["audio_offset"]
    return job


async def read_score_upload(file):
    try:
        data = await file.read(score_import.UPLOAD_LIMIT + 1)
        if len(data) > score_import.UPLOAD_LIMIT:
            raise HTTPException(413, "악보 파일은 2MB 이하로 선택해주세요.")
        return data
    finally:
        await file.close()


def import_error(error):
    message = str(error) if isinstance(error, ValueError) else "MusicXML의 음정·시간·악기 정보를 읽을 수 없어요. 파일을 확인해주세요."
    # Validation details should not turn an uploaded document into an error dump.
    if isinstance(error, ValueError) and hasattr(error, "errors"):
        message = "이 악보의 길이·튜닝·음표·가사/메모가 현재 편집기 범위를 벗어나요."
    return HTTPException(422, message[:500])


@app.post("/api/score-import/inspect")
async def inspect_score_file(file: UploadFile = File(...)):
    data = await read_score_upload(file)
    try:
        return await asyncio.to_thread(score_import.inspect, data, file.filename or "")
    except (ValueError, KeyError, TypeError, OverflowError) as error:
        raise import_error(error) from None


@app.post("/api/score-import/preserve-preview")
async def preserve_score_preview(file: UploadFile = File(...), part_id: str = Form(..., max_length=80), preset: str = Form(default="practice"), measures_per_line: int = Form(default=4)):
    data = await read_score_upload(file)
    try:
        xml, warnings = await asyncio.to_thread(restyle_musicxml, data, file.filename or "", part_id, preset, measures_per_line)
        return {"xml": xml.decode("utf-8"), "warnings": warnings, "mode": "layout-only"}
    except (ValueError, KeyError, TypeError, OverflowError) as error:
        raise import_error(error) from None


def create_imported_project(data, filename, part_id, inst, provenance=None):
    if inst not in INSTRUMENTS:
        raise HTTPException(422, "가져올 악기를 선택해주세요.")
    try:
        doc, source, warnings = score_import.import_document(data, filename, part_id, inst)
    except (ValueError, KeyError, TypeError, OverflowError) as error:
        raise import_error(error) from None
    if provenance:
        warnings = [*warnings, *provenance["warnings"]]
    # Validate before creating a job. Invalid XML never creates partial projects.
    with TASK_LOCK, store.LOCK:
        job = store.create(doc["title"], "musicxml")
        folder = store.directory(job["id"])
        try:
            persist(doc, folder, automatic=True)
            (folder / f"{inst}.source.musicxml").write_bytes(source)
        except Exception:
            store.update(job["id"], status="error", error="가져온 악보를 저장하지 못했어요.")
            raise
        duration = doc["ticks"] / 4 * 60 / doc["bpm"]
        stem_update(job["id"], inst, score_status="ready", score_url=store.asset_url(job["id"], f"{inst}.musicxml"),
                    midi_url=store.asset_url(job["id"], f"{inst}.mid"), score_bpm=doc["bpm"], score_revision=doc["revision"],
                    score_title=doc["title"], score_layout=doc["layout"], note_count=len(doc["notes"]), score_edited=True,
                    score_source_url=store.asset_url(job["id"], f"{inst}.source.musicxml"),
                    score_warning=" · ".join(warnings), **notation_metadata(doc))
        return store.update(job["id"], status="completed", stage="done", progress=100, duration=duration, bpm=doc["bpm"],
                            message="악보를 가져왔어요. 직접 수정에서 연주·스타일을 편집하세요.", **({"score_omr": provenance} if provenance else {}))


@app.post("/api/score-import", status_code=201)
async def import_score_project(file: UploadFile = File(...), part_id: str = Form(..., max_length=80), instrument: str = Form(...), omr_id: str | None = Form(default=None, max_length=32), omr_result_id: str | None = Form(default=None, max_length=32)):
    data = await read_score_upload(file)
    provenance = score_omr.provenance(data, omr_id, omr_result_id)
    return await asyncio.to_thread(create_imported_project, data, file.filename or "", part_id, instrument, provenance)


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
    meter: Meter = Field(default_factory=lambda: Meter(measure=1, beats=4, beat_type=4))
    profile: Literal["instrument", "polyphonic"] = "instrument"
    drum_engine: Literal["auto", "neural", "spectral", "hybrid", "consensus"] = "auto"
    drum_source: Literal["stem", "original"] = "stem"
    pitched_engine: Literal["standard", "adaptive"] = "standard"


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
    # The frozen multi-instrument candidate regressed on guitar/piano controls.
    # Keep their low-level decoder research-only; never silently opt them in.
    if body.pitched_engine == "adaptive" and set(instruments) != {"synthesizer"}:
        raise HTTPException(422, "지속음 보강은 신디사이저에서만 선택해주세요. 기타·피아노는 검증 중이에요.")
    ready = {stem["id"] for stem in job["stems"] if stem["status"] == "ready"}
    # A user-supplied drum recording / original mix can be analyzed on CPU
    # without pretending SAM separation has run or marking other stems ready.
    if body.drum_source == "original" and (store.directory(job_id) / "original.wav").is_file():
        ready.add("drums")
    if not set(instruments).issubset(ready):
        raise HTTPException(409, "아직 분리되지 않은 악기가 포함되어 있어요.")
    if not body.overwrite_edits and any(s.get("score_edited") and s["id"] in instruments for s in job["stems"]):
        raise HTTPException(409, "직접 수정한 악보가 있어요. 다시 채보하면 수정본을 대체하므로 먼저 확인해주세요.")
    needs_basic_pitch = any(inst in {"guitar", "piano", "synthesizer"} or (inst in {"vocal", "bass"} and body.profile == "polyphonic") for inst in instruments)
    if not job["demo"] and needs_basic_pitch and not engine_status()["transcription_available"]:
        raise HTTPException(503, "서버에 Basic Pitch 채보 엔진을 설치해주세요.")
    if not job["demo"] and "drums" in instruments:
        worker = worker_status()
        if (body.drum_engine in {"neural", "hybrid", "consensus"} or (body.drum_engine == "auto" and worker["configured"])) and not worker["paths_ready"]:
            raise HTTPException(503, "드럼 전용 모델의 별도 실행 환경·체크포인트 설정을 확인해주세요.")
        if body.drum_source == "original" and not (store.directory(job_id) / "original.wav").is_file():
            raise HTTPException(409, "비교할 원본 음원이 없어요.")
    meters = generation_meters(body, job["duration"])
    event = reserve(job_id)
    job = store.update(job_id, status="transcribing", stage="transcribing", progress=0, error=None)
    submit(job_id, run_transcription, event, instruments, body.bpm, body.audio_offset, meters, body.profile, body.drum_engine, body.drum_source, body.pitched_engine)
    return job


def generation_meters(body, duration):
    try:
        if body.meter.measure != 1:
            raise ValueError("새 채보의 박자표는 첫 마디에 지정해주세요.")
        meters = normalize_meters([body.meter.model_dump()])
        measure_map(max(1, (duration - body.audio_offset) * body.bpm / 60 * 4 - 1e-9), meters, exact=False)
        return meters
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


def score_stem(job_id, inst):
    job = valid_job(job_id)
    if job.get("score_preserved"):
        raise HTTPException(409, "원본 유지 악보는 전용 편집기에서 수정해주세요. 격자 변환으로 원본 표기를 덮어쓰지 않습니다.")
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
        meters = generation_meters(body, job["duration"])
        doc = generate_score([], inst, job["title"], body.bpm, job["duration"], store.directory(job_id),
                             body.audio_offset, job.get("lyric_guide", {}).get("cues"), meters)
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


@app.get("/api/jobs/{job_id}/transcription-review/{inst}")
def get_transcription_review(job_id: str, inst: str, start: float = 0, seconds: float = 8):
    try:
        return transcription_review.inspect_review(job_id, inst, start, seconds,
                                                   task_lock=TASK_LOCK, active_jobs=EVENTS, get_job=valid_job)
    except transcription_review.ReviewError as error:
        raise HTTPException(error.status, str(error)) from None


@app.exception_handler(transcription_review.ReviewError)
async def review_case_error(request: Request, error: transcription_review.ReviewError):
    return JSONResponse({"detail": str(error)}, status_code=error.status)


def review_case_context():
    return {"task_lock": TASK_LOCK, "active_jobs": EVENTS, "get_job": valid_job}


@app.get("/api/jobs/{job_id}/review-cases/{inst}")
def list_review_cases(job_id: str, inst: str):
    return review_cases.list_cases(job_id, inst, **review_case_context())


@app.post("/api/jobs/{job_id}/review-cases/{inst}", status_code=201)
async def create_review_case(job_id: str, inst: str, request: Request):
    body = await review_cases.read_body(request)
    return await run_in_threadpool(review_cases.create_case, job_id, inst, body, **review_case_context())


@app.get("/api/jobs/{job_id}/review-cases/{inst}/{case_id}")
def get_review_case(job_id: str, inst: str, case_id: str):
    return review_cases.get_case(job_id, inst, case_id, **review_case_context())


@app.put("/api/jobs/{job_id}/review-cases/{inst}/{case_id}")
async def update_review_case(job_id: str, inst: str, case_id: str, request: Request):
    body = await review_cases.read_body(request)
    return await run_in_threadpool(review_cases.update_case, job_id, inst, case_id, body, **review_case_context())


@app.get("/api/jobs/{job_id}/review-cases/{inst}/{case_id}/export")
def export_review_case(job_id: str, inst: str, case_id: str):
    document = review_cases.export_case(job_id, inst, case_id, **review_case_context())
    return JSONResponse(document, headers={"Content-Disposition": f'attachment; filename="akbo-review-{inst}-{case_id[:8]}.json"'})


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


@app.post("/api/jobs/{job_id}/scores/{inst}/import-preview")
async def preview_imported_score(job_id: str, inst: str, file: UploadFile = File(...), part_id: str = Form(..., max_length=80), base_revision: str = Form(..., max_length=80), omr_id: str | None = Form(default=None, max_length=32), omr_result_id: str | None = Form(default=None, max_length=32)):
    data = await read_score_upload(file)
    provenance = score_omr.provenance(data, omr_id, omr_result_id)
    with TASK_LOCK, store.LOCK:
        if job_id in EVENTS:
            raise HTTPException(409, "현재 작업이 끝나면 악보를 가져와주세요.")
        job, _ = score_stem(job_id, inst)
        current = load_document(store.directory(job_id), inst, job["duration"])
        if current["revision"] != base_revision:
            raise HTTPException(409, "다른 화면에서 악보가 변경됐어요. 최신 악보를 다시 열어주세요.")
    try:
        doc, _, warnings = await asyncio.to_thread(score_import.import_document, data, file.filename or "", part_id, inst, current=current)
        return {"document": doc, "warnings": warnings + (provenance["warnings"] if provenance else []), "score_omr": provenance}
    except (ValueError, KeyError, TypeError, OverflowError) as error:
        raise import_error(error) from None


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
            documents.append({**validate_edit(edit, target), "notes": target["notes"]})
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
    return {"original.wav", "residual.wav", "drums.raw.mid", "drums.transcription.json",
            *[f"{inst}.transcription.json" for inst in ("guitar", "piano", "synthesizer")],
            *[f"{inst}.{extension}" for inst in INSTRUMENTS for extension in ("wav", "mid", "musicxml", "source.musicxml", "notes.json")]}


def current_artifact(job, name):
    # A later spectral run must not present a previous neural result as current.
    if name.endswith(".notes.json"):
        inst = name.removesuffix(".notes.json")
        return any(s["id"] == inst and (s.get("score_transcription") or {}).get("raw_events") is True for s in job["stems"])
    if name in {f"{inst}.transcription.json" for inst in ("guitar", "piano", "synthesizer")}:
        inst = name.removesuffix(".transcription.json")
        return any(s["id"] == inst and (s.get("score_transcription") or {}).get("pitched_review") is True for s in job["stems"])
    return name not in {"drums.raw.mid", "drums.transcription.json"} or any(s["id"] == "drums" and (s.get("score_transcription") or {}).get("raw_midi") for s in job["stems"])


@app.get("/api/jobs/{job_id}/files/{name}")
def download(job_id: str, name: str, download: bool = False):
    job = valid_job(job_id)
    if name not in allowed_files() or not current_artifact(job, name):
        raise HTTPException(404, "파일을 찾을 수 없어요.")
    path = store.directory(job_id) / name
    if not path.is_file():
        raise HTTPException(404, "파일이 아직 준비되지 않았어요.")
    mime = {".wav": "audio/wav", ".mid": "audio/midi", ".musicxml": "application/vnd.recordare.musicxml+xml", ".json": "application/json"}[path.suffix]
    return FileResponse(path, media_type=mime, filename=name if download or name.endswith(".source.musicxml") else None)


@app.get("/api/jobs/{job_id}/archive")
def archive(job_id: str):
    job = valid_job(job_id)
    if job.get("score_preserved"):
        return source_projects.archive(job_id)
    if job["status"] in {"queued", "running", "transcribing", "analyzing"}:
        raise HTTPException(409, "처리가 끝나면 전체 파일을 받을 수 있어요.")
    folder = store.directory(job_id)
    temporary = folder / f"archive-{uuid4().hex}.tmp"
    target = folder / "archive.zip"
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in sorted(allowed_files()):
            path = store.directory(job_id) / name
            if path.is_file() and current_artifact(job, name):
                bundle.write(path, name)
        source_note = "Imported MusicXML score. No audio separation or transcription.\n" if job["source_type"] == "musicxml" else "Original audio only. SAM inference not run.\n" if job.get("analysis_only") else "Original synthesized demo. Not SAM Audio inference.\n" if job["demo"] else "SAM Audio sequential source separation.\n"
        if not job["demo"] and not job.get("analysis_only") and job.get("separation_strategy") == "independent":
            source_note = "SAM Audio independent source separation (experimental). Each instrument was extracted from the original mix. Stems may overlap; there is no additive residual. Accuracy improvement is not established.\n"
        bundle.writestr("README.txt", "Akbo Maker\n" + source_note + "Scores use a 1/16-note grid with saved time signatures. BPM is quarter notes per minute. Check pitches and rhythm.\nDrum transcription is an experimental draft. drums.raw.mid (when present) preserves pre-quantization GM detections, including unsupported percussion. drums.transcription.json records the engine and review data.\n<instrument>.notes.json (when present) preserves source-linked events passed to notation, before time quantization and score offset. These are post-processed drafts, not ground truth, and do not include subsequent manual score edits. Amplitude is relative event strength, NOT confidence or accuracy.\nPitched <instrument>.transcription.json (when present) compares standard and experimental onset-relative decoding of the same model output. Activations are NOT confidence; overlapping octaves can be genuine notes. Only synthesizer currently exposes the experimental mode; guitar/piano candidates failed validation. These files describe transcription-time evidence, not later manual edits.\n")
    temporary.replace(target)
    return FileResponse(target, media_type="application/zip", filename=f"akbo-{job_id[:8]}.zip")


# Built frontend can be served by the same API origin on a GPU host.
dist = ROOT / "dist"
if dist.is_dir():
    app.mount("/", FrontendFiles(directory=dist, html=True), name="web")
