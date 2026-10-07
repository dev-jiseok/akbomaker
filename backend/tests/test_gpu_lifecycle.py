import threading
from unittest.mock import Mock

import pytest
import numpy as np
import soundfile as sf

from backend import pipeline, store
from backend.separator import Cancelled


@pytest.mark.parametrize("failure", [RuntimeError("CUDA out of memory"), Cancelled(), None])
def test_failed_or_cancelled_job_always_releases_gpu(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    engine = Mock()
    if failure is None:
        engine.extract.side_effect = lambda audio, *args: audio * 0.1
    else:
        engine.extract.side_effect = failure
    monkeypatch.setattr(pipeline, "ENGINE", engine)
    def normalize(source, target, *, details=None):
        sf.write(target, np.ones(4800, dtype=np.float32) * 0.1, 48000)
        return 0.1
    monkeypatch.setattr(pipeline, "normalize", normalize)
    job = store.create("lifecycle test", "upload")
    pipeline.run_separation(job["id"], threading.Event(), tmp_path / "input.wav")
    engine.offload.assert_called_once()
    assert engine.extract.call_count == (6 if failure is None else 1)
    expected = "separated" if failure is None else "cancelled" if isinstance(failure, Cancelled) else "error"
    assert store.get(job["id"])["status"] == expected
