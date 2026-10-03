from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from backend import app as api, startup


@pytest.fixture
def engines(monkeypatch, tmp_path):
    monkeypatch.delenv("AKBO_ALLOW_DEGRADED", raising=False)
    monkeypatch.setattr(startup.separator, "engine_status", lambda: {
        "issues": [], "transcription_available": True,
    })
    monkeypatch.setattr(startup.lyrics, "status", lambda: {"available": True})
    monkeypatch.setattr(startup, "DATA_DIR", tmp_path)
    mocks = {}
    for name, owner, attribute in [
        ("runtime", startup, "check_runtime"),
        ("sam", startup.separator.ENGINE, "load"),
        ("inference", startup.separator.ENGINE, "warmup"),
        ("pitch", startup.score, "transcription_model"),
        ("whisper", startup.lyrics, "model"),
    ]:
        mocks[name] = Mock()
        monkeypatch.setattr(owner, attribute, mocks[name])
    monkeypatch.setattr(api.store, "DATA_DIR", tmp_path)
    return mocks


def test_missing_configuration_prevents_api_startup(engines, monkeypatch):
    monkeypatch.setattr(startup.separator, "engine_status", lambda: {
        "issues": ["HF_TOKEN을 설정해주세요"], "transcription_available": True,
    })
    with pytest.raises(RuntimeError, match="HF_TOKEN"):
        with TestClient(api.app):
            pytest.fail("API must not accept requests before engines are ready")
    for initialize in engines.values():
        initialize.assert_not_called()


@pytest.mark.parametrize("engine", ["runtime", "sam", "inference", "pitch", "whisper"])
def test_failed_engine_initialization_prevents_api_startup(engines, engine):
    engines[engine].side_effect = RuntimeError("model initialization failed")
    with pytest.raises(RuntimeError, match="서버 시작 중단.*model initialization failed"):
        with TestClient(api.app):
            pytest.fail("Failed initialization must prevent startup")


def test_all_engines_load_before_api_accepts_requests(engines):
    with TestClient(api.app) as client:
        assert api.app.state.engines_ready is True
        for initialize in engines.values():
            initialize.assert_called_once()
        assert client.get("/docs").status_code == 200
        assert client.get("/api/health").json()["ready"] is True


def test_degraded_mode_requires_explicit_opt_in(engines, monkeypatch):
    monkeypatch.setenv("AKBO_ALLOW_DEGRADED", "1")
    assert startup.prepare_engines() is False
    for initialize in engines.values():
        initialize.assert_not_called()
