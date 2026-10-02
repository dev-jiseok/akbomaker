"""Local CPU Whisper candidates. Never silently substitute lyrics for music."""
import importlib.util
import math
import os
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import DATA_DIR
from .editing import xml_text

MODEL_NAME = os.getenv("LYRICS_MODEL", "base")
MODEL_DIR = DATA_DIR / "models" / "whisper"


def status():
    return {"available": importlib.util.find_spec("faster_whisper") is not None,
            "model": MODEL_NAME, "device": "cpu", "compute_type": "int8"}


@lru_cache(maxsize=1)
def model():
    from faster_whisper import WhisperModel
    return WhisperModel(MODEL_NAME, device="cpu", compute_type="int8", cpu_threads=4,
                        download_root=str(MODEL_DIR))


class Cue(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=80, pattern=r"^[\w-]+$")
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(min_length=1, max_length=80)
    _xml_text = field_validator("text")(xml_text)


class CandidateEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_revision: str = Field(max_length=80)
    cues: list[Cue] = Field(max_length=2000)


def recognize(path: Path, language, event, emit):
    from .separator import check_cancel
    check_cancel(event)
    segments, info = model().transcribe(str(path), language=None if language == "auto" else language,
                                       word_timestamps=True, vad_filter=True, beam_size=5,
                                       condition_on_previous_text=False, hallucination_silence_threshold=2)
    cues = []
    for segment in segments:
        check_cancel(event)
        emit(segment.end)
        if segment.no_speech_prob > .75:
            continue
        for word in segment.words or []:
            text = word.word.strip()
            if not text or not math.isfinite(word.start) or not math.isfinite(word.end) or word.end <= word.start:
                continue
            start, end = round(max(0, word.start), 3), round(word.end, 3)
            if end <= start:
                continue
            cues.append({"id": uuid4().hex, "start": start, "end": end, "text": xml_text(text[:80])})
            if len(cues) > 2000:
                raise ValueError("인식 결과가 너무 길어요. 짧은 음원으로 나누어주세요.")
    check_cancel(event)
    if not cues:
        raise ValueError("가사를 찾지 못했어요. 무보컬 음원이거나 노래 인식이 어려울 수 있어요.")
    return {"revision": uuid4().hex, "cues": cues, "language": info.language,
            "warning": "자동 인식 초안입니다. 노래·반주·코러스에서는 단어와 시간이 틀릴 수 있으니 듣고 확인해주세요."}


def timed_lyrics(cues, document):
    """Merge words landing on one grid cell instead of inventing later onsets."""
    bpm = document.get("timing_bpm", document["bpm"])
    offset = document.get("audio_offset", 0)
    grouped, skipped = {}, 0
    for cue in sorted(cues, key=lambda c: c["start"]):
        tick = round((cue["start"] - offset) * bpm / 60 * 4)
        if tick < 0 or tick >= document["ticks"]:
            skipped += 1
            continue
        text = cue["text"].strip()
        grouped[tick] = (grouped.get(tick, "") + " " + text).strip()
        if len(grouped[tick]) > 80:
            raise ValueError("한 칸에 가사가 너무 많이 모였어요. 가사 시간을 나누어 수정해주세요.")
    return [{"id": uuid4().hex, "start": start, "text": text} for start, text in grouped.items()], skipped
