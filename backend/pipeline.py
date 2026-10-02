import logging
import threading
from pathlib import Path

import numpy as np
import soundfile as sf

from . import demo, store
from .config import INSTRUMENTS, LABELS
from .media import download_youtube, normalize
from .score import transcribe
from .editing import generate_score, notation_metadata
from .separator import ENGINE, Cancelled, check_cancel, save_audio, separate_sequential, waveform

logger = logging.getLogger(__name__)


def stem_update(job_id: str, inst: str, **values):
    with store.LOCK:
        job = store.get(job_id)
        for stem in job["stems"]:
            if stem["id"] == inst:
                stem.update(values)
        store.save(job)


def fail(job_id: str, error: Exception):
    with store.LOCK:
        job = store.get(job_id)
        for stem in job["stems"]:
            if stem["status"] == "running":
                stem["status"] = "pending"
            if stem["score_status"] == "running":
                stem.update(score_status="error", score_error="채보가 중단됐어요. 다시 시도해주세요.")
        store.save(job)
    if isinstance(error, Cancelled):
        store.update(job_id, status="cancelled", message="작업을 중단했어요. 완료된 파일은 계속 사용할 수 있어요.")
        return
    logger.exception("Job %s failed", job_id)
    if isinstance(error, ValueError):
        message = str(error)
    elif isinstance(error, ImportError):
        message = "서버에 처리 엔진의 필수 패키지가 없어요. 서버 설정을 확인해주세요."
    elif "out of memory" in str(error).lower():
        message = "GPU 메모리가 부족해요. SAM_CHUNK_SECONDS를 낮추거나 작은 모델로 다시 시도해주세요."
    else:
        message = "처리 엔진에서 오류가 발생했어요. 모델 접근 권한과 서버 로그를 확인해주세요."
    store.update(job_id, status="error", error=message, message="작업을 완료하지 못했어요")


def run_separation(job_id: str, event: threading.Event, source: Path | None = None, url: str | None = None, analysis_only=False):
    try:
        folder = store.directory(job_id)
        store.update(job_id, status="running", stage="preparing", progress=2, message="음악 파일을 준비하고 있어요")
        check_cancel(event)
        if url:
            source, title = download_youtube(url, folder)
            store.update(job_id, title=title)
        check_cancel(event)
        if source is None:
            raise ValueError("음악 파일이 없어요.")
        duration = normalize(source, folder / "original.wav")
        if analysis_only:
            check_cancel(event)
            store.update(job_id, duration=duration, original_url=store.asset_url(job_id, "original.wav"),
                         status="separated", stage="done", progress=100,
                         message="원본을 준비했어요. 악기 분리는 실행하지 않았으며 BPM·가사 분석을 사용할 수 있어요.")
            return
        store.update(job_id, duration=duration, original_url=store.asset_url(job_id, "original.wav"), progress=7, stage="separating", message="SAM Audio 모델을 준비하고 있어요")
        audio, _ = sf.read(folder / "original.wav", dtype="float32")
        original_rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))

        def emit(inst, target, index, fraction):
            check_cancel(event)
            store.update(job_id, progress=round(8 + (index + fraction) / 6 * 89), message=f"{LABELS[inst]} 소리를 분리하고 있어요", active_instrument=inst)
            if target is None:
                stem_update(job_id, inst, status="running")
                return
            save_audio(folder / f"{inst}.wav", target)
            rms = float(np.sqrt(np.mean(target.astype(np.float64) ** 2)))
            stem_update(job_id, inst, status="ready", audio_url=store.asset_url(job_id, f"{inst}.wav"), waveform=waveform(target), energy_ratio=round(rms / max(original_rms, 1e-8), 4), quiet=rms < max(0.0001, original_rms * 0.01))

        residual = separate_sequential(audio, ENGINE.extract, emit, event)
        save_audio(folder / "residual.wav", residual)
        check_cancel(event)
        store.update(job_id, status="separated", progress=100, stage="done", active_instrument=None, residual_url=store.asset_url(job_id, "residual.wav"), message="악기 분리가 끝났어요. 원하는 악기를 듣고 악보를 만들어보세요.")
    except Exception as error:
        fail(job_id, error)


def run_demo(job_id: str, event: threading.Event):
    try:
        store.update(job_id, status="running", stage="preparing", progress=5, message="직접 만든 샘플 음악을 준비하고 있어요")
        stems, events = demo.generate()
        folder = store.directory(job_id)
        save_audio(folder / "original.wav", sum(stems.values()))
        for index, inst in enumerate(INSTRUMENTS):
            check_cancel(event)
            samples = stems[inst]
            save_audio(folder / f"{inst}.wav", samples)
            document = generate_score(events[inst], inst, demo.TITLE, demo.BPM, demo.DURATION, folder)
            stem_update(job_id, inst, status="ready", score_status="ready", audio_url=store.asset_url(job_id, f"{inst}.wav"), waveform=waveform(samples), score_url=store.asset_url(job_id, f"{inst}.musicxml"), midi_url=store.asset_url(job_id, f"{inst}.mid"), note_count=len(document["notes"]), score_bpm=demo.BPM, score_revision=document["revision"], score_edited=False, score_layout=document["layout"], score_title=document["title"], **notation_metadata(document))
            store.update(job_id, progress=round((index + 1) / 6 * 100), stage="separating")
        store.update(job_id, status="completed", stage="done", progress=100, duration=demo.DURATION, bpm=demo.BPM, original_url=store.asset_url(job_id, "original.wav"), message="샘플 작업실이 준비됐어요")
    except Exception as error:
        fail(job_id, error)


def run_transcription(job_id: str, event: threading.Event, instruments: list[str], bpm: int, audio_offset=0):
    try:
        job = store.get(job_id)
        folder = store.directory(job_id)
        store.update(job_id, status="transcribing", stage="transcribing", progress=0, bpm=bpm, error=None)
        for index, inst in enumerate(instruments):
            check_cancel(event)
            stem_update(job_id, inst, score_status="running", score_error=None)
            store.update(job_id, message=f"{LABELS[inst]} 음원을 악보로 옮기고 있어요", progress=round(index / len(instruments) * 100))
            try:
                # Demo uses known composition events and never calls inference.
                events = demo.generate()[1][inst] if job["demo"] else transcribe(folder / f"{inst}.wav", inst)
                check_cancel(event)
                document = generate_score(events, inst, job["title"], bpm, job["duration"], folder, audio_offset,
                                          job.get("lyric_guide", {}).get("cues"))
                stem_update(job_id, inst, score_status="ready", score_url=store.asset_url(job_id, f"{inst}.musicxml"), midi_url=store.asset_url(job_id, f"{inst}.mid"), note_count=len(document["notes"]), score_bpm=bpm, score_revision=document["revision"], score_edited=False, score_layout=document["layout"], score_title=document["title"], score_warning="드럼은 온셋·주파수 기반 리듬 초안이에요. 킥·스네어·하이햇 구분을 확인해주세요." if inst == "drums" else "16분음표 기준으로 정리한 자동 채보 초안이에요. 음정·리듬을 확인해주세요.", **notation_metadata(document))
            except Cancelled:
                raise
            except Exception:
                logger.exception("Transcription failed for %s / %s", job_id, inst)
                stem_update(job_id, inst, score_status="error", score_error="채보에 실패했어요. 음원은 보존되어 있으니 다시 시도할 수 있어요.")
        check_cancel(event)
        store.update(job_id, status="completed", stage="done", progress=100, message="채보가 끝났어요. 악보를 확인하고 편한 크기로 조절해보세요.")
    except Exception as error:
        fail(job_id, error)


def run_cpu_analysis(job_id, event, kind, previous_status, source="original", language="auto"):
    try:
        folder = store.directory(job_id)
        check_cancel(event)
        if kind == "beats":
            from .analysis import analyze_beats
            result = analyze_beats(folder / "original.wav")
            check_cancel(event)
            store.update(job_id, rhythm_analysis=result)
        else:
            from .lyrics import recognize
            duration = store.get(job_id)["duration"]
            def emit(seconds):
                store.update(job_id, progress=min(95, round(5 + seconds / duration * 90)), message="가사를 인식하고 단어 시간을 맞추고 있어요")
            result = recognize(folder / f"{source}.wav", language, event, emit)
            result["cues"] = [{**c, "end": min(c["end"], duration)} for c in result["cues"] if c["start"] < duration]
            result["source"] = source
            if not result["cues"]:
                raise ValueError("곡 안에 배치할 가사를 찾지 못했어요.")
            store.update(job_id, lyric_candidate=result)
        store.update(job_id, status=previous_status, stage="done", progress=100,
                     analysis_previous_status=None, analysis_error=None, message="분석 초안을 준비했어요. 확인 후 적용해주세요.")
    except Exception as error:
        logger.exception("CPU analysis %s failed for %s", kind, job_id)
        message = str(error) if isinstance(error, ValueError) else "분석에 실패했어요. 패키지·모델 다운로드·서버 로그를 확인해주세요. 기존 악보는 보존했어요."
        store.update(job_id, status=previous_status, stage="done", progress=100,
                     analysis_previous_status=None,
                     analysis_error=None if isinstance(error, Cancelled) else message,
                     message="분석을 중단했어요. 기존 악보는 보존했어요." if isinstance(error, Cancelled) else message)
