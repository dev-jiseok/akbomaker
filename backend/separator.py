"""Official SAM Audio adapter; one inference per instrument per overlapping chunk."""
import importlib.util
import os
import shutil
import threading
from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf

from .config import INSTRUMENTS, PROMPTS, SAMPLE_RATE


class Cancelled(Exception):
    pass


def check_cancel(event: threading.Event) -> None:
    if event.is_set():
        raise Cancelled()


def engine_status() -> dict:
    issues = []
    installed = importlib.util.find_spec("sam_audio") is not None
    if not installed:
        issues.append("SAM Audio가 설치된 GPU 서버를 연결해주세요.")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        issues.append("서버에 FFmpeg 설치가 필요해요.")
    device = os.getenv("SAM_DEVICE", "cuda")
    if installed:
        import torch
        if device.startswith("cuda") and not torch.cuda.is_available():
            issues.append("CUDA GPU를 사용할 수 없어요.")
        model = os.getenv("SAM_MODEL", "facebook/sam-audio-base")
        if not Path(model).is_dir():
            from huggingface_hub import get_token
            if not get_token():
                issues.append("승인된 Hugging Face 계정의 HF_TOKEN을 설정해주세요.")
    return {"available": not issues, "model": os.getenv("SAM_MODEL", "facebook/sam-audio-base"), "device": device, "issues": issues, "transcription_available": importlib.util.find_spec("basic_pitch") is not None}


def waveform(audio: np.ndarray, count: int = 96) -> list[float]:
    if len(audio) == 0:
        return [0] * count
    values = np.array([np.sqrt(np.mean(chunk.astype(np.float64) ** 2)) if len(chunk) else 0 for chunk in np.array_split(audio, count)])
    peak = values.max()
    return np.round(values / peak if peak else values, 4).tolist()


def chunk_starts(length: int, chunk: int, overlap: int) -> list[int]:
    if length <= chunk:
        return [0]
    starts = list(range(0, length - chunk + 1, chunk - overlap))
    if starts[-1] + chunk < length:
        starts.append(length - chunk)
    return starts


class SAMSeparator:
    def __init__(self):
        self.model = None
        self.processor = None
        self.device = os.getenv("SAM_DEVICE", "cuda")

    def load(self):
        if self.model is not None:
            return
        from sam_audio import SAMAudio, SAMAudioProcessor
        name = os.getenv("SAM_MODEL", "facebook/sam-audio-base")
        # No reranking/span prediction: unused auxiliary models exhaust VRAM.
        model = SAMAudio.from_pretrained(
            name, visual_ranker=None, text_ranker=None, span_predictor=None,
        ).eval().to(self.device)
        processor = SAMAudioProcessor.from_pretrained(name)
        if processor.audio_sampling_rate != SAMPLE_RATE:
            raise RuntimeError("SAM Audio 모델의 샘플레이트가 예상과 다릅니다.")
        self.model, self.processor = model, processor

    def warmup(self):
        # Exercise the actual processor/inference/output contract before serving.
        self.extract(np.zeros(4 * SAMPLE_RATE, dtype=np.float32), "vocal",
                     threading.Event(), lambda _: None)

    def extract(self, audio: np.ndarray, inst: str, event: threading.Event, progress: Callable[[float], None]) -> np.ndarray:
        import torch
        self.load()
        chunk_size = int(float(os.getenv("SAM_CHUNK_SECONDS", "20")) * SAMPLE_RATE)
        chunk_size = max(4 * SAMPLE_RATE, chunk_size)
        overlap = min(2 * SAMPLE_RATE, chunk_size // 4)
        starts = chunk_starts(len(audio), chunk_size, overlap)
        target = np.zeros_like(audio)
        weights = np.zeros_like(audio)
        for index, start in enumerate(starts):
            check_cancel(event)
            part = audio[start:start + chunk_size]
            batch = self.processor(audios=[torch.from_numpy(part).unsqueeze(0)], descriptions=[PROMPTS[inst]]).to(self.device)
            with torch.inference_mode():
                result = self.model.separate(batch, predict_spans=False, reranking_candidates=1)
            # SAM returns one variable-length waveform per batch item.
            separated = result.target[0].detach().float().cpu().numpy().reshape(-1)[:len(part)]
            if len(separated) < len(part) or not np.isfinite(separated).all():
                raise RuntimeError("SAM Audio returned an invalid waveform")
            weight = np.ones(len(part), dtype=np.float32)
            fade = min(overlap, len(part))
            if index > 0:
                weight[:fade] = np.linspace(0.001, 1, fade)
            if index < len(starts) - 1:
                weight[-fade:] = np.linspace(1, 0.001, fade)
            target[start:start + len(part)] += separated * weight
            weights[start:start + len(part)] += weight
            progress((index + 1) / len(starts))
        return target / np.maximum(weights, 1e-8)


def separate_sequential(audio: np.ndarray, extract: Callable, emit: Callable, event: threading.Event) -> np.ndarray:
    residual = audio.copy()
    for index, inst in enumerate(INSTRUMENTS):
        check_cancel(event)
        target = extract(residual, inst, event, lambda fraction: emit(inst, None, index, fraction))
        if target.shape != residual.shape or not np.isfinite(target).all():
            raise ValueError("분리된 음원의 길이나 샘플 값이 올바르지 않아요.")
        # Reuse the exact extracted target: sum(stems) + residual == input.
        residual = residual - target
        emit(inst, target, index, 1.0)
    return residual


ENGINE = SAMSeparator()


def save_audio(path: Path, samples: np.ndarray):
    # Floating point WAV preserves the sum and avoids clipping intermediate stems.
    sf.write(path, samples, SAMPLE_RATE, subtype="FLOAT")
