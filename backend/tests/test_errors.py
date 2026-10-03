import pytest

from backend.errors import processing_error


def test_runtime_error_has_stage_and_log_reference_without_raw_secrets():
    message = processing_error(RuntimeError("Authorization: Bearer secret-token"), "악기 분리", "job-123")
    assert "악기 분리" in message and "job-123" in message
    assert "RuntimeError" in message
    assert "secret-token" not in message


@pytest.mark.parametrize("error,expected", [
    (RuntimeError("CUDA out of memory"), "GPU 메모리"),
    (ModuleNotFoundError("sam_audio"), "필수 엔진 패키지"),
    (PermissionError("denied"), "권한"),
    (TimeoutError("download"), "연결"),
])
def test_runtime_failure_explains_recovery(error, expected):
    assert expected in processing_error(error, "음악 처리", "job-123")
