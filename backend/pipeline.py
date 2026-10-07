import logging
import tempfile
import threading
from pathlib import Path

import numpy as np
import soundfile as sf

from . import demo, store
from .config import INSTRUMENTS, LABELS
from .media import download_youtube, normalize
from .score import transcribe
from .editing import generate_score, notation_metadata
from .note_artifacts import write_note_artifact
from .transcription import engine_description, transcribe_drums
from .errors import processing_error
from .separator import ENGINE, Cancelled, check_cancel, save_audio, separate_sequential, separate_independent, waveform

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
    stage = {"preparing": "음원 준비", "separating": "악기 분리", "transcribing": "채보"}.get(job.get("stage"), "음악 처리")
    message = processing_error(error, stage, job_id)
    store.update(job_id, status="error", error=message, message="작업을 완료하지 못했어요")


def run_separation(job_id: str, event: threading.Event, source: Path | None = None, url: str | None = None, analysis_only=False, separation_strategy="sequential"):
    try:
        if separation_strategy not in {"sequential", "independent"} or analysis_only and separation_strategy != "sequential":
            raise ValueError("악기 분리 방식을 확인해주세요.")
        folder = store.directory(job_id)
        store.update(job_id, status="running", stage="preparing", progress=2, message="음악 파일을 준비하고 있어요")
        check_cancel(event)
        if url:
            source, title = download_youtube(url, folder)
            store.update(job_id, title=title)
        check_cancel(event)
        if source is None:
            raise ValueError("음악 파일이 없어요.")
        audio_preprocessing = {}
        duration = normalize(source, folder / "original.wav", details=audio_preprocessing)
        store.update(job_id, audio_preprocessing=audio_preprocessing)
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

        independent = separation_strategy == "independent"
        store.update(job_id, separation_strategy=separation_strategy, separation={
            "strategy": separation_strategy,
            "input_mode": "original-per-instrument" if independent else "previous-residual",
            "instrument_order": list(INSTRUMENTS), "accuracy_evaluated": False,
            "stems_may_overlap": independent, "additive_residual": not independent,
        })
        if independent:
            separate_independent(audio, ENGINE.extract, emit, event)
            residual_url = None
        else:
            residual = separate_sequential(audio, ENGINE.extract, emit, event)
            save_audio(folder / "residual.wav", residual)
            residual_url = store.asset_url(job_id, "residual.wav")
        check_cancel(event)
        store.update(job_id, status="separated", progress=100, stage="done", active_instrument=None, residual_url=residual_url, message="악기 분리가 끝났어요. 원하는 악기를 듣고 악보를 만들어보세요.")
    except Exception as error:
        fail(job_id, error)
    finally:
        # The queue has one worker: all six stems finish (or fail/cancel) before
        # releasing weights and allocator cache, and the next job cannot race us.
        ENGINE.offload()


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
            document = generate_score(events[inst], inst, demo.TITLE, demo.BPM, demo.DURATION, folder,
                                      transcription={"engine": "known-composition", "profile": "sample", "raw_events": False,
                                                     "warning": "자작 샘플의 작곡 데이터입니다. 실제 채보 모델의 정확도 검증 결과가 아니에요."})
            stem_update(job_id, inst, status="ready", score_status="ready", audio_url=store.asset_url(job_id, f"{inst}.wav"), waveform=waveform(samples), score_url=store.asset_url(job_id, f"{inst}.musicxml"), midi_url=store.asset_url(job_id, f"{inst}.mid"), note_count=len(document["notes"]), score_bpm=demo.BPM, score_revision=document["revision"], score_edited=False, score_layout=document["layout"], score_title=document["title"], **notation_metadata(document))
            store.update(job_id, progress=round((index + 1) / 6 * 100), stage="separating")
        store.update(job_id, status="completed", stage="done", progress=100, duration=demo.DURATION, bpm=demo.BPM, original_url=store.asset_url(job_id, "original.wav"), message="샘플 작업실이 준비됐어요")
    except Exception as error:
        fail(job_id, error)


def run_transcription(job_id: str, event: threading.Event, instruments: list[str], bpm: int, audio_offset=0, meters=None, profile="instrument", drum_engine="auto", drum_source="stem", pitched_engine="standard"):
    try:
        job = store.get(job_id)
        folder = store.directory(job_id)
        store.update(job_id, status="transcribing", stage="transcribing", progress=0, bpm=bpm, error=None)
        failed_instruments = []
        for index, inst in enumerate(instruments):
            check_cancel(event)
            stem_update(job_id, inst, score_status="running", score_error=None)
            store.update(job_id, message=f"{LABELS[inst]} 음원을 악보로 옮기고 있어요", progress=round(index / len(instruments) * 100))
            try:
                with tempfile.TemporaryDirectory(prefix="transcription-", dir=folder) as temporary:
                    staging = Path(temporary)
                    # Demo uses known composition events and never calls inference.
                    if job["demo"]:
                        events = demo.generate()[1][inst]
                        description = {"engine": "known-composition", "profile": "sample", "raw_events": False, "warning": "자작 샘플의 작곡 데이터입니다. 실제 채보 모델의 정확도 검증 결과가 아니에요."}
                    elif inst == "drums":
                        source = folder / ("original.wav" if drum_source == "original" else "drums.wav")
                        events, description = transcribe_drums(source, job["duration"], engine=drum_engine, artifacts=staging)
                        description["source"] = drum_source
                        description["raw_midi"] = (staging / "drums.raw.mid").is_file()
                    else:
                        source = folder / f"{inst}.wav"
                        description = engine_description(inst, profile)
                        if pitched_engine == "adaptive":
                            events = transcribe(source, inst, profile, engine="adaptive", details=description, artifacts=staging)
                        elif inst in {"vocal", "bass"} and profile == "instrument":
                            events = transcribe(source, inst, details=description)
                        else:
                            events = transcribe(source, inst) if profile == "instrument" else transcribe(source, inst, profile)
                        description["source"] = "stem"
                    if not job["demo"]:
                        events = write_note_artifact(staging, events, instrument=inst, duration=job["duration"],
                                                     source=source, description=description)
                        description["raw_events"] = True
                    check_cancel(event)
                    document = generate_score(events, inst, job["title"], bpm, job["duration"], staging, audio_offset,
                                              job.get("lyric_guide", {}).get("cues"), meters,
                                              transcription={**description, "timing_reviewed": False})
                    # Inference, event validation, and every derived score must
                    # succeed before replacing the previous result or evidence.
                    check_cancel(event)
                    for artifact in staging.iterdir():
                        artifact.replace(folder / artifact.name)
                stem_update(job_id, inst, score_status="ready", score_url=store.asset_url(job_id, f"{inst}.musicxml"), midi_url=store.asset_url(job_id, f"{inst}.mid"), note_count=len(document["notes"]), score_bpm=bpm, score_revision=document["revision"], score_edited=False, score_layout=document["layout"], score_title=document["title"], score_warning=description["warning"], **notation_metadata(document))
            except Cancelled:
                raise
            except Exception as error:
                logger.exception("Transcription failed for %s / %s", job_id, inst)
                failed_instruments.append(LABELS[inst])
                stem_update(job_id, inst, score_status="error", score_error=processing_error(error, f"{LABELS[inst]} 채보", job_id))
        check_cancel(event)
        message = (f"{', '.join(failed_instruments)} 채보에 실패했어요. 기존 악보가 있다면 보존했어요. 악기별 오류를 확인해주세요."
                   if failed_instruments else "채보가 끝났어요. 악보를 확인하고 편한 크기로 조절해보세요.")
        store.update(job_id, status="completed", stage="done", progress=100, message=message)
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
        message = processing_error(error, "박자 분석" if kind == "beats" else "가사 인식", job_id)
        store.update(job_id, status=previous_status, stage="done", progress=100,
                     analysis_previous_status=None,
                     analysis_error=None if isinstance(error, Cancelled) else message,
                     message="분석을 중단했어요. 기존 악보는 보존했어요." if isinstance(error, Cancelled) else message)
