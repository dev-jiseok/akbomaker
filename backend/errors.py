"""Actionable job errors without exposing credentials or internal tracebacks."""
import errno


def processing_error(error: Exception, stage: str, job_id: str) -> str:
    detail = str(error).lower()
    status = getattr(getattr(error, "response", None), "status_code", None)
    if status in {401, 403} or "gatedrepo" in type(error).__name__.lower():
        reason = "모델 접근이 거절됐어요. Hugging Face 모델 승인과 서버의 HF_TOKEN을 확인해주세요."
    elif "out of memory" in detail:
        reason = "GPU 메모리가 부족해요. SAM_CHUNK_SECONDS를 낮추거나 작은 모델을 사용해주세요."
    elif isinstance(error, ImportError):
        reason = "필수 엔진 패키지를 불러오지 못했어요. 서버에서 ./start.sh를 다시 실행해주세요."
    elif isinstance(error, OSError) and error.errno == errno.ENOSPC:
        reason = "서버 저장 공간이 부족해요. 공간을 확보한 뒤 다시 시도해주세요."
    elif isinstance(error, PermissionError):
        reason = "서버의 파일 읽기·쓰기 권한이 없어요. 저장소 권한을 확인해주세요."
    elif isinstance(error, (ConnectionError, TimeoutError)) or "connection" in type(error).__name__.lower() or "timeout" in type(error).__name__.lower():
        reason = "외부 서버 연결에 실패했거나 시간이 초과됐어요. 서버 네트워크와 모델 다운로드 상태를 확인해주세요."
    elif isinstance(error, ValueError):
        reason = str(error)
    else:
        reason = f"처리 엔진 오류({type(error).__name__})가 발생했어요. 아래 작업 번호로 서버 로그를 확인해주세요."
    return f"{stage} 실패: {reason} (작업 {job_id})"
