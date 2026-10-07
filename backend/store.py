"""Small single-worker durable store. Job IDs act as unguessable local project IDs."""
import json
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .config import DATA_DIR, INSTRUMENTS, LABELS

LOCK = threading.RLock()


def directory(job_id: str) -> Path:
    return DATA_DIR / job_id


def get(job_id: str) -> dict:
    with LOCK:
        path = directory(job_id) / "job.json"
        if not path.is_file():
            raise KeyError(job_id)
        return json.loads(path.read_text())


def save(job: dict) -> dict:
    with LOCK:
        folder = directory(job["id"])
        folder.mkdir(parents=True, exist_ok=True)
        temp = folder / "job.tmp"
        temp.write_text(json.dumps(job, ensure_ascii=False))
        temp.replace(folder / "job.json")
        return deepcopy(job)


def update(job_id: str, **values) -> dict:
    with LOCK:
        job = get(job_id)
        job.update(values)
        return save(job)


def create(title: str, source_type: str, demo: bool = False, *, job_id: str | None = None, persist: bool = True) -> dict:
    with LOCK:
        if job_id is not None:
            import re
            if not re.fullmatch(r"[a-f0-9]{32}", job_id):
                raise ValueError("올바르지 않은 프로젝트 ID입니다.")
            if directory(job_id).exists() or directory(job_id).is_symlink():
                raise ValueError("이미 존재하는 프로젝트를 덮어쓸 수 없습니다.")
        job = {
            "id": job_id or uuid4().hex, "title": title[:180], "source_type": source_type,
            "demo": demo, "status": "queued", "stage": "waiting", "progress": 0,
            "message": "작업을 준비하고 있어요", "error": None,
            "created_at": datetime.now(timezone.utc).isoformat(), "duration": None,
            "bpm": None, "original_url": None, "residual_url": None,
            "stems": [{"id": inst, "label": LABELS[inst], "status": "pending", "score_status": "pending", "waveform": []} for inst in INSTRUMENTS],
        }
        return save(job) if persist else job


def recover() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for file in DATA_DIR.glob("*/job.json"):
        try:
            job = json.loads(file.read_text())
            if job["status"] == "analyzing":
                update(job["id"], status=job.get("analysis_previous_status") or "separated", stage="done", progress=100,
                       analysis_previous_status=None, analysis_error="서버 재시작으로 분석이 중단됐어요. 기존 음원·악보·가사는 보존됐으며 다시 분석할 수 있습니다.")
            elif job["status"] in {"queued", "running", "transcribing"}:
                update(job["id"], status="error", error="서버가 재시작되어 작업이 중단됐어요. 새 작업으로 다시 시도해주세요.")
        except (ValueError, KeyError):
            continue


def asset_url(job_id: str, name: str) -> str:
    return f"/api/jobs/{job_id}/files/{name}"
