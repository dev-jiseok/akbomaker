"""Local, opt-in Audiveris jobs. Recognition is a review draft, never a score truth.

Run one web worker: jobs/process cancellation are coordinated in this process.
No engine download, license acceptance, shell interpolation or remote upload.
"""
import asyncio
import hashlib
import io
import json
import logging
import math
import os
import re
import selectors
import shutil
import signal
import subprocess
import sys
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from . import score_import, store

UPLOAD_LIMIT = 25 * 1024 * 1024
MAX_PAGES = 4
MAX_PIXELS = 24_000_000
MAX_JOB_BYTES = 512 * 1024 * 1024
MAX_JOB_ENTRIES = 1024
LOCK = threading.RLock()
ACTIVE: dict[str, threading.Event] = {}
UPLOADING = 0
POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="score-omr")
router = APIRouter(prefix="/api/score-omr")
BASE_WARNINGS = [
    "자동 인식은 검토용 초안입니다. 원본과 음표·쉼표·박자·반복·가사를 비교한 뒤 적용하세요.",
    "TAB의 줄·프렛 숫자는 인식하지 않습니다. 오선에서 새로 제안한 TAB는 원본 운지가 아닙니다.",
    "가사 인식은 엔진의 설치된 OCR 언어에 따라 누락되거나 틀릴 수 있습니다.",
    "복잡한 표기는 기존 음표 편집기가 거절할 수 있습니다. 이때 원본 표기 유지 미리보기를 사용하세요.",
]


class Cancelled(Exception):
    pass


def executable():
    value = os.getenv("AKBO_OMR_EXECUTABLE", "")
    path = Path(value)
    return str(path) if value and path.is_absolute() and path.is_file() and os.access(path, os.X_OK) else None


def ocr_languages():
    value = os.getenv("AKBO_OMR_LANGUAGES", "eng")
    if not re.fullmatch(r"[a-z]{3}(?:\+[a-z]{3}){0,3}", value):
        raise ValueError("AKBO_OMR_LANGUAGES는 eng 또는 eng+kor 같은 언어 코드로 설정해주세요.")
    return value


@router.get("/status")
def status():
    issues = []
    if os.name != "posix":
        issues.append("현재 인식 작업 제어는 macOS/Linux 서버에서 지원합니다.")
    if not executable():
        issues.append("서버에 Audiveris를 설치하고 AKBO_OMR_EXECUTABLE 절대 경로를 설정해주세요. 자동 설치하지 않습니다.")
    if not shutil.which("pdfinfo") or not shutil.which("pdftoppm"):
        issues.append("PDF 검증·미리보기에 필요한 Poppler(pdfinfo, pdftoppm)가 없어요.")
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        issues.append("이미지 검증에 필요한 Pillow가 없어요. 서버 의존성을 설치해주세요.")
    try:
        ocr_languages()
    except ValueError as error:
        issues.append(str(error))
    tab_issues = []
    if os.name != "posix":
        tab_issues.append("현재 PDF 작업 제어는 macOS/Linux 서버에서 지원합니다.")
    if not shutil.which("pdfinfo") or not shutil.which("pdftoppm"):
        tab_issues.append("PDF 검증·미리보기에 Poppler가 필요합니다.")
    try:
        import pdfplumber  # noqa: F401
    except ImportError:
        tab_issues.append("PDF TAB 좌표 읽기에 pdfplumber가 필요합니다.")
    return {"available": not issues, "engine": "Audiveris", "issues": issues,
            "tab_review_available": not tab_issues, "tab_review_issues": tab_issues,
            "limits": {"upload_mb": 25, "max_pages": MAX_PAGES}, "tablature_supported": False}


def directory(job_id):
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise HTTPException(404, "악보 인식 작업을 찾을 수 없어요.")
    return store.DATA_DIR / "score-omr" / job_id


def save(job):
    with LOCK:
        folder = directory(job["id"])
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "state.tmp"
        path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        path.replace(folder / "state.json")
    return job


def read(job_id):
    with LOCK:
        path = directory(job_id) / "state.json"
        if not path.is_file():
            raise HTTPException(404, "악보 인식 작업을 찾을 수 없어요.")
        return json.loads(path.read_text(encoding="utf-8"))


def update(job_id, **changes):
    with LOCK:
        return save({**read(job_id), **changes})


@router.get("/{job_id}")
def get(job_id: str):
    with LOCK:
        job = read(job_id)
        if job["status"] in {"queued", "running"} and job_id not in ACTIVE:
            job = update(job_id, status="error", error="서버 재시작으로 인식이 중단됐어요. 원본은 보관되며 다시 업로드할 수 있어요.", message="인식 중단")
        return job


def parse_pages(value):
    if not re.fullmatch(r"[0-9,\- ]{1,80}", value):
        raise ValueError("페이지는 1 또는 1-3,5 형식으로 입력해주세요.")
    pages = []
    for group in value.split(","):
        if not re.fullmatch(r"\s*\d{1,4}(?:\s*-\s*\d{1,4})?\s*", group):
            raise ValueError("페이지 범위를 확인해주세요.")
        bounds = [int(item) for item in group.split("-")]
        start, end = bounds[0], bounds[-1]
        if start < 1 or end < start or end > 9999 or end - start >= MAX_PAGES:
            raise ValueError("한 번에 최대 4페이지를 선택해주세요.")
        pages.extend(range(start, end + 1))
    result = sorted(set(pages))
    if len(result) > MAX_PAGES:
        raise ValueError("한 번에 최대 4페이지를 선택해주세요.")
    return result


def inspect_upload(data, filename):
    ext = Path(filename).suffix.lower()
    if not data:
        raise ValueError("빈 파일은 업로드할 수 없어요.")
    if ext == ".pdf" and data.startswith(b"%PDF-"):
        return ".pdf"
    if ext not in {".png", ".jpg", ".jpeg"}:
        raise ValueError("PDF·PNG·JPG·JPEG 악보 파일을 선택해주세요.")
    from PIL import Image
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in {"PNG", "JPEG"} or image.width * image.height > MAX_PIXELS or getattr(image, "n_frames", 1) != 1:
                    raise ValueError("단일 PNG/JPEG 이미지를 최대 2,400만 픽셀까지 지원합니다.")
                if (image.format == "PNG") != (ext == ".png"):
                    raise ValueError("이미지 확장자와 실제 형식이 다릅니다.")
                image.verify()
    except (OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError("이미지 파일이 손상됐거나 너무 큽니다.") from None
    return ext


def stop_process(process):
    # POSIX session covers the Java subprocess of an Audiveris launcher too.
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=2)
        if os.name == "posix":
            # The launcher may have already exited while a child still lives.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait(timeout=5)
    except ProcessLookupError:
        pass


def run_command(command, event, cwd, timeout=30, capture=False):
    """Bound time and retained output. No pipe deadlock or unbounded engine logs."""
    process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, start_new_session=os.name == "posix",
                               env={**os.environ, "LC_ALL": "C"})
    output = bytearray()

    selector = selectors.DefaultSelector()
    deadline = time.monotonic() + timeout
    next_budget_check = 0
    try:
        selector.register(process.stdout, selectors.EVENT_READ)
        while selector.get_map() or process.poll() is None:
            if event.is_set():
                raise Cancelled()
            if time.monotonic() >= deadline:
                raise ValueError("악보 인식 제한 시간을 초과했어요. 페이지를 나누어 다시 시도해주세요.")
            if time.monotonic() >= next_budget_check:
                check_disk_budget(cwd)
                next_budget_check = time.monotonic() + .5
            for key, _ in selector.select(timeout=.1):
                chunk = os.read(key.fd, 4096)
                if not chunk:
                    selector.unregister(key.fileobj)
                elif len(output) < 65536:
                    output.extend(chunk[:65536 - len(output)])
        if event.is_set():
            raise Cancelled()
        if process.returncode:
            raise ValueError("인식 도구가 파일을 처리하지 못했어요. 원본·페이지 선택·서버 엔진 설정을 확인해주세요.")
        return bytes(output) if capture else b""
    finally:
        stop_process(process)
        selector.close()
        process.stdout.close()


def check_disk_budget(folder):
    count = total = 0
    for parent, dirs, files in os.walk(folder, followlinks=False):
        count += len(dirs) + len(files)
        if count > MAX_JOB_ENTRIES:
            raise ValueError("인식 작업의 파일 수 제한을 초과했어요. 페이지를 나누어주세요.")
        for name in [*dirs, *files]:
            path = Path(parent) / name
            if path.is_symlink():
                raise ValueError("안전하지 않은 인식 결과 경로입니다.")
        for name in files:
            try:
                total += (Path(parent) / name).stat().st_size
            except FileNotFoundError:  # Engine can replace its own temporary files.
                continue
            if total > MAX_JOB_BYTES:
                raise ValueError("인식 작업의 임시 파일 용량(512MB)을 초과했어요. 페이지를 나누어주세요.")


def preview_source(job, event):
    from PIL import Image, ImageOps
    folder = directory(job["id"])
    source = folder / job["source_name"]
    urls = []
    if source.suffix == ".pdf":
        metadata = run_command([shutil.which("pdfinfo"), str(source)], event, folder, capture=True).decode("utf-8", errors="replace")
        count = re.search(r"^Pages:\s+(\d+)", metadata, re.M)
        if not count or re.search(r"^Encrypted:\s+yes", metadata, re.M):
            raise ValueError("암호화되지 않은 정상 PDF를 선택해주세요.")
        if int(count[1]) > 100:
            raise ValueError("PDF는 총 100페이지 이하 파일로 나누어주세요.")
        if max(job["pages"]) > int(count[1]):
            raise ValueError(f"PDF는 {int(count[1])}페이지입니다. 선택한 페이지를 확인해주세요.")
        for page in job["pages"]:
            boxes = run_command([shutil.which("pdfinfo"), "-f", str(page), "-l", str(page), "-box", str(source)], event, folder, capture=True).decode("utf-8", errors="replace")
            validate_pdf_boxes(boxes)
            prefix = folder / f"preview-{page}"
            run_command([shutil.which("pdftoppm"), "-f", str(page), "-l", str(page), "-singlefile", "-scale-to", "2400", "-png", str(source), str(prefix)], event, folder)
            urls.append(f'/api/score-omr/{job["id"]}/files/{prefix.name}.png')
    else:
        if job["pages"] != [1]:
            raise ValueError("이미지는 1페이지만 선택해주세요.")
        with Image.open(source) as original:
            normalized = ImageOps.exif_transpose(original).convert("RGB")
            # Normalize orientation before recognition, preserve original upload separately.
            normalized.save(folder / "input.png")
            normalized.thumbnail((2400, 2400))
            normalized.save(folder / "preview-1.png")
        urls.append(f'/api/score-omr/{job["id"]}/files/preview-1.png')
    update(job["id"], preview_urls=urls, progress=20, message="원본 미리보기를 만들었어요. 음표를 인식하고 있어요.")
    return source if source.suffix == ".pdf" else folder / "input.png"


def source_coordinates(job, source, event):
    """Keep original PDF TAB numbers as evidence, never manufacture rhythm.

    Disposable parser with resource limits where supported, plus a parent
    wall-clock timeout. Failure of this optional evidence pass does not block OMR.
    """
    if source.suffix != ".pdf":
        return None
    folder = directory(job["id"])
    output = folder / "source-coordinates.json"
    run_command([sys.executable, str(Path(__file__).with_name("pdf_score_analysis.py")),
                 "--input", str(source), "--output", str(output),
                 "--pages", ",".join(map(str, job["pages"])),
                 "--instrument", "drums" if job["notation"] == "drums" else "auto"],
                event, folder, timeout=45)
    if output.is_symlink() or not output.is_file() or output.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("PDF 원본 좌표 검토 자료를 읽을 수 없어요.")
    payload = output.read_bytes()
    analysis = json.loads(payload)
    if (not isinstance(analysis, dict)
            or analysis.get("source_sha256") != job["source_sha256"]
            or type(analysis.get("schema_version")) is not int or analysis["schema_version"] != 1
            or analysis.get("editable_musicxml") is not False or analysis.get("rhythm_known") is not False
            or analysis.get("requires_review") is not True
            or not isinstance(analysis.get("pages"), list)
            or any(not isinstance(page, dict) or type(page.get("page")) is not int for page in analysis["pages"])
            or [page["page"] for page in analysis["pages"]] != job["pages"]):
        raise ValueError("PDF 원본 좌표 자료의 출처와 범위를 확인할 수 없어요.")
    if any(not isinstance(page.get("staffs"), list) for page in analysis["pages"]):
        raise ValueError("PDF 원본 보표 좌표 형식을 확인할 수 없어요.")
    staffs = [staff for page in analysis["pages"] for staff in page["staffs"]]
    if any(not isinstance(staff, dict) or staff.get("kind") not in ("tab", "staff", "ambiguous")
           or not isinstance(staff.get("digits"), list) for staff in staffs):
        raise ValueError("PDF 원본 보표 좌표 형식을 확인할 수 없어요.")
    digits = [digit for staff in staffs for digit in staff["digits"]]
    if any(not isinstance(digit, dict) or type(digit.get("accepted")) is not bool
           or digit.get("requires_review") is not True for digit in digits):
        raise ValueError("PDF 원본 숫자 좌표의 검토 상태를 확인할 수 없어요.")
    return {"download_url": f'/api/score-omr/{job["id"]}/files/{output.name}',
            "sha256": hashlib.sha256(payload).hexdigest(), "pages": job["pages"],
            "tab_staffs": sum(staff["kind"] == "tab" for staff in staffs),
            "digit_candidates": len(digits), "matched_digits": sum(digit["accepted"] for digit in digits),
            "rhythm_known": False, "editable_musicxml": False}


def validate_pdf_boxes(metadata):
    boxes = re.findall(r"^(?:Page\s+\d+\s+)?(?:MediaBox|CropBox):\s+([^\n]+)", metadata, re.M)
    if len(boxes) < 2:
        raise ValueError("PDF 페이지 크기를 안전하게 확인할 수 없어요.")
    for box in boxes:
        try:
            x0, y0, x1, y1 = map(float, box.split())
        except ValueError:
            raise ValueError("PDF 페이지 크기가 올바르지 않아요.") from None
        width, height = x1 - x0, y1 - y0
        if not all(math.isfinite(v) for v in (x0, y0, x1, y1, width, height)) or not (0 < width <= 1440 and 0 < height <= 1440) or width * height * (300 / 72) ** 2 > MAX_PIXELS:
            raise ValueError("PDF 페이지 면적이 너무 커요. A4/A3 크기로 나누어주세요.")


def collect_results(job_id, output, *, notation=None):
    from .audiveris_compat import normalize_export
    folder = directory(job_id)
    check_disk_budget(output)
    candidates = sorted(p for p in output.rglob("*") if p.suffix.lower() in {".mxl", ".musicxml", ".xml"})
    if len(candidates) > 16:
        raise ValueError("인식 결과가 너무 많아요. 페이지를 나누어주세요.")
    results = []
    for path in candidates:
        if path.is_symlink() or not path.resolve().is_relative_to(output.resolve()) or not path.is_file():
            raise ValueError("안전하지 않은 인식 결과 경로입니다.")
        if path.stat().st_size > score_import.UPLOAD_LIMIT:
            raise ValueError("인식된 악보가 편집기 크기 제한(2MB)을 초과해요. 페이지를 나누어주세요.")
        data = path.read_bytes()
        listing = score_import.inspect(data, path.name)
        _, root = score_import.unpack(data, path.name)
        if not any(p["measures"] for p in listing["parts"]) or root.find(".//note") is None:
            raise ValueError("인식 결과에 음표·쉼표가 없습니다. 빈 악보를 완료된 결과로 적용하지 않았어요.")
        result_id = uuid4().hex
        raw_name = result_id + path.suffix.lower()
        (folder / raw_name).write_bytes(data)
        normalized, corrections = normalize_export(data, path.name)
        quality_warnings = []
        if notation == "drums":
            pitched = len(root.findall(".//note/pitch"))
            missing_instrument = sum(1 for note in root.findall(".//note") if note.find("unpitched") is not None and note.find("instrument") is None)
            if pitched:
                quality_warnings.append(f"드럼으로 요청했지만 {pitched}개 음표가 일반 음정으로 인식됐어요. 음자리표·보표 인식 오류일 수 있으며 올바른 드럼 악보로 가져올 수 없습니다. 원본 표기 유지 미리보기로 대조해주세요.")
            if missing_instrument:
                quality_warnings.append(f"타격 종류가 없는 드럼 음표 {missing_instrument}개가 있어요. 임의로 킥·하이햇을 배정하지 않습니다.")
        raw_url = f"/api/score-omr/{job_id}/files/{raw_name}"
        name, filename, prepared = raw_name, path.name[:180], data
        if normalized is not None:
            name, filename, prepared = result_id + ".normalized.musicxml", path.stem[:140] + ".normalized.musicxml", normalized
            (folder / name).write_bytes(prepared)
        results.append({"id": result_id, "filename": filename, "download_url": f"/api/score-omr/{job_id}/files/{name}",
                        "sha256": hashlib.sha256(prepared).hexdigest(), "raw_download_url": raw_url,
                        "raw_sha256": hashlib.sha256(data).hexdigest(), "normalizations": corrections, "quality_warnings": quality_warnings})
    if not results:
        raise ValueError("인식 가능한 오선 악보를 찾지 못했어요. TAB 전용 악보는 지원하지 않습니다.")
    return results


def recognition_warnings(output):
    """Expose explicit engine rhythm diagnostics; never infer correctness from exit 0."""
    text = output.decode("utf-8", errors="replace")
    measures = set(re.findall(r"MeasureStack#(\d+) no correct rhythm", text))
    measures.update(re.findall(r"Measure\{#(\d+)\}[^\n]*too long", text))
    if not measures:
        return []
    numbers = ", ".join(sorted(measures, key=int)[:40])
    return [f"엔진이 리듬 불일치를 보고했어요(마디 {numbers}). 인식 완료와 별개로 원본의 박자·쉼표·음표 길이를 직접 검수해야 합니다."]


def run(job_id, event):
    try:
        if event.is_set():
            raise Cancelled()
        job = update(job_id, status="running", progress=5, message="악보 파일과 선택한 페이지를 확인하고 있어요.")
        source = preview_source(job, event)
        if source.suffix == ".pdf":
            try:
                evidence = source_coordinates(job, source, event)
                job = update(job_id, source_coordinates=evidence)
            except Cancelled:
                raise
            except Exception:
                logging.getLogger(__name__).warning("PDF coordinate evidence unavailable for %s", job_id)
                job = update(job_id, warnings=[*job["warnings"], "PDF 원본 숫자·줄 좌표 보조 분석은 완료하지 못했습니다. 아래 MusicXML 인식 결과는 별도로 확인해주세요."])
        if job["notation"] == "tab":
            if not job.get("source_coordinates", {}).get("tab_staffs"):
                raise ValueError("읽을 수 있는 4/6줄 TAB 보표를 찾지 못했어요. 스캔·윤곽선 TAB는 아직 지원하지 않습니다.")
            with LOCK:
                if event.is_set():
                    raise Cancelled()
                update(job_id, status="ready", progress=100, results=[],
                       message="TAB 숫자·줄 검토 자료가 준비됐어요. 리듬과 누락을 원본에서 확인한 뒤 편집 악보로 만드세요.")
            return
        engine = executable()
        if not engine:
            raise ValueError("Audiveris 실행 설정을 확인해주세요.")
        folder = directory(job_id)
        output = folder / "output"
        output.mkdir()
        command = [engine, "-batch", "-export", "-output", str(output)]
        command += ["-constant", "org.audiveris.omr.image.ImageLoading.pdfResolution=300"]
        command += ["-constant", f"org.audiveris.omr.text.Language.defaultSpecification={ocr_languages()}"]
        if source.suffix == ".pdf":
            command += ["-sheets", *map(str, job["pages"])]
        for key, value in (("oneLineStaves", "true"), ("drumNotation", "true" if job["notation"] == "drums" else "false")):
            command += ["-constant", f"org.audiveris.omr.sheet.ProcessingSwitches.{key}={value}"]
        command += ["--", str(source)]
        update(job_id, engine_config={"pdf_dpi": 300, "ocr_languages": ocr_languages(), "drum_notation": job["notation"] == "drums",
                                    "executable_sha256": hashlib.sha256(Path(engine).read_bytes()).hexdigest()})
        try:
            timeout = min(3600, max(30, int(os.getenv("AKBO_OMR_TIMEOUT_SECONDS", "900"))))
        except ValueError:
            timeout = 900
        log = run_command(command, event, folder, timeout=timeout, capture=True)
        # Private operator diagnostic, intentionally not on the public asset allowlist.
        (folder / "engine.log").write_bytes(log)
        results = collect_results(job_id, output, notation=job["notation"])
        warnings = list(dict.fromkeys([*job["warnings"], *recognition_warnings(log), *[warning for result in results for warning in [*result["normalizations"], *result["quality_warnings"]]]]))
        with LOCK:
            if event.is_set():
                raise Cancelled()
            update(job_id, status="ready", progress=100, message="인식 초안이 준비됐어요. 원본과 비교하고 적용할 결과를 선택해주세요.", results=results, warnings=warnings)
    except Cancelled:
        update(job_id, status="cancelled", message="인식을 취소했어요. 원본은 보관됩니다.", results=[])
    except Exception as error:
        logging.getLogger(__name__).exception("OMR job %s failed", job_id)
        message = str(error)[:500] if isinstance(error, ValueError) else "악보 인식을 완료하지 못했어요. 서버 설정과 파일을 확인해주세요."
        update(job_id, status="error", message="인식 실패", error=message, results=[])
    finally:
        with LOCK:
            ACTIVE.pop(job_id, None)


@router.post("", status_code=202)
async def create(file: UploadFile = File(...), pages: str = Form(default="1", max_length=80), notation: str = Form(default="staff")):
    global UPLOADING
    reserved = False
    try:
        config = status()
        if notation not in {"staff", "drums", "tab"}:
            raise HTTPException(422, "일반 오선, 드럼 또는 PDF TAB를 선택해주세요.")
        if not config.get("tab_review_available" if notation == "tab" else "available"):
            raise HTTPException(503, " ".join(config.get("tab_review_issues" if notation == "tab" else "issues", [])))
        with LOCK:
            if len(ACTIVE) + UPLOADING >= 3:
                raise HTTPException(429, "인식 대기열이 가득 찼어요. 잠시 후 다시 시도해주세요.")
            UPLOADING += 1
            reserved = True
        try:
            selected = parse_pages(pages)
            data = await file.read(UPLOAD_LIMIT + 1)
            if len(data) > UPLOAD_LIMIT:
                raise HTTPException(413, "PDF·이미지는 25MB 이하로 선택해주세요.")
            # Image decoding/verification must not block other API requests.
            ext = await asyncio.to_thread(inspect_upload, data, file.filename or "")
            if notation == "tab" and ext != ".pdf":
                raise ValueError("TAB 숫자 검토는 실제 문자·선이 들어 있는 PDF만 지원합니다. 사진 인식은 아직 지원하지 않습니다.")
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        with LOCK:
            job_id = uuid4().hex
            folder = directory(job_id)
            folder.mkdir(parents=True)
            name = "source" + ext
            (folder / name).write_bytes(data)
            job = {"id": job_id, "status": "queued", "progress": 0, "message": "인식 순서를 기다리고 있어요.", "error": None,
                   "engine": "pdf-vector-tab-review" if notation == "tab" else "Audiveris", "notation": notation, "pages": selected, "source_name": name,
                   "source_sha256": hashlib.sha256(data).hexdigest(), "source_url": f"/api/score-omr/{job_id}/files/{name}",
                   "preview_urls": [], "results": [], "warnings": (["TAB 숫자·줄 후보를 읽은 자료이며 완성된 악보가 아닙니다. 리듬·튜닝·붙임줄·뮤트·누락된 음을 직접 확인해야 합니다."] if notation == "tab" else BASE_WARNINGS + (["드럼 음표의 위치·모양과 킥/스네어/하이햇 매핑을 반드시 확인하세요. 출판사마다 표기가 다릅니다."] if notation == "drums" else []))}
            save(job)
            event = threading.Event()
            ACTIVE[job_id] = event
            UPLOADING -= 1
            reserved = False
            try:
                POOL.submit(run, job_id, event)
            except Exception:
                ACTIVE.pop(job_id, None)
                update(job_id, status="error", error="인식 작업을 시작하지 못했어요.")
                raise
        return job
    finally:
        if reserved:
            with LOCK:
                UPLOADING -= 1
        await file.close()


@router.post("/{job_id}/cancel")
def cancel(job_id: str):
    with LOCK:
        job = get(job_id)
        if job_id in ACTIVE and job["status"] in {"queued", "running"}:
            ACTIVE[job_id].set()
            return update(job_id, message="인식을 중단하고 있어요.")
        return job


@router.get("/{job_id}/files/{name}")
def asset(job_id: str, name: str):
    job = get(job_id)
    allowed = {job["source_name"], *[url.rsplit("/", 1)[-1] for url in job["preview_urls"]],
               *[r[key].rsplit("/", 1)[-1] for r in job["results"] for key in ("download_url", "raw_download_url") if r.get(key)]}
    evidence = job.get("source_coordinates")
    if evidence:
        allowed.add(evidence["download_url"].rsplit("/", 1)[-1])
    path = directory(job_id) / name
    if name not in allowed or path.is_symlink() or not path.is_file() or path.parent != directory(job_id):
        raise HTTPException(404, "파일을 찾을 수 없어요.")
    if evidence and name == "source-coordinates.json" and (path.stat().st_size > 2 * 1024 * 1024
            or hashlib.sha256(path.read_bytes()).hexdigest() != evidence["sha256"]):
        raise HTTPException(409, "원본 좌표 검토 파일이 변경되어 다운로드를 중단했어요.")
    preview = name.startswith("preview-")
    return FileResponse(path, media_type="image/png" if preview else "application/octet-stream", filename=None if preview else name,
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


def provenance(data, job_id, result_id):
    if not job_id and not result_id:
        return None
    if not job_id or not result_id:
        raise HTTPException(422, "인식 원본과 결과를 함께 선택해주세요.")
    job = get(job_id)
    result = next((r for r in job["results"] if r["id"] == result_id), None)
    if job["status"] != "ready" or not result or result["sha256"] != hashlib.sha256(data).hexdigest():
        raise HTTPException(409, "선택한 인식 결과와 업로드가 달라요. 인식 결과를 다시 선택해주세요.")
    return {"id": job_id, "result_id": result_id, "engine": job["engine"], "source_url": job["source_url"],
            "result_url": result["download_url"], "preview_urls": job["preview_urls"],
            "raw_result_url": result.get("raw_download_url", result["download_url"]), "raw_result_sha256": result.get("raw_sha256", result["sha256"]),
            "normalizations": result.get("normalizations", []),
            "source_sha256": job["source_sha256"], "result_sha256": result["sha256"], "warnings": job["warnings"]}


def shutdown():
    with LOCK:
        for event in ACTIVE.values():
            event.set()
