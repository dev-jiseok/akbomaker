"""Fail before accepting requests if a required engine cannot initialize."""
import importlib
import logging
import os
import tempfile

from . import lyrics, score, separator
from .config import DATA_DIR

logger = logging.getLogger("uvicorn.error")


def prepare_engines():
    """Load once in the serving process; do not download models per request."""
    if os.getenv("AKBO_ALLOW_DEGRADED") == "1":
        logger.warning("AKBO_ALLOW_DEGRADED=1: demo/development mode; engine readiness checks skipped.")
        return False

    try:
        status = separator.engine_status()
        issues = list(status["issues"])
        if not status["transcription_available"]:
            issues.append("Basic Pitch 채보 패키지가 없어요.")
        if not lyrics.status()["available"]:
            issues.append("Whisper 가사 인식 패키지가 없어요.")
        if issues:
            raise RuntimeError(" ".join(issues))
    except Exception as error:
        raise RuntimeError(
            "서버 시작 중단: 필수 엔진 설정을 확인해주세요. ./start.sh로 의존성을 설치하고, "
            "SAM 모델 접근 승인을 받은 Hugging Face 계정의 HF_TOKEN을 .env에 설정해주세요. "
            f"원인: {error}"
        ) from error

    steps = [
        ("저장소 쓰기 권한", check_storage),
        ("CUDA / TorchCodec", check_runtime),
        ("SAM Audio 모델 다운로드·GPU 초기화", separator.ENGINE.load),
        ("SAM Audio 실제 분리 준비 검사", separator.ENGINE.warmup),
        ("Basic Pitch ONNX 모델 초기화", score.transcription_model),
        ("Whisper 모델 다운로드·CPU 초기화", lyrics.model),
    ]
    try:
        for label, initialize in steps:
            logger.info("시작 준비: %s", label)
            try:
                initialize()
            except Exception as error:
                raise RuntimeError(f"서버 시작 중단 — {label} 실패: {error}") from error
    finally:
        separator.ENGINE.offload()
    logger.info("SAM 모델을 CPU로 이동하고 유휴 GPU 메모리를 반환했습니다.")
    logger.info("필수 엔진 준비 완료. 업로드 요청을 받을 수 있습니다.")
    return True


def check_storage():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=DATA_DIR) as output:
        output.write(b"ready")
        output.flush()


def check_runtime():
    torch = importlib.import_module("torch")
    importlib.import_module("torchcodec")
    device = separator.ENGINE.device
    # An allocation and kernel catch invalid device indices and incompatible drivers.
    value = torch.ones(1, device=device) + 1
    if value.item() != 2:
        raise RuntimeError(f"{device} 연산 검사에 실패했어요.")
