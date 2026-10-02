"""Real bundled-model smoke test; skipped in lightweight CI without Basic Pitch."""
import importlib.util

import numpy as np
import pytest
import soundfile as sf

from backend.config import SAMPLE_RATE
from backend.score import transcribe


@pytest.mark.skipif(importlib.util.find_spec("basic_pitch") is None, reason="Basic Pitch ML extras not installed")
def test_real_basic_pitch_detects_a440(tmp_path):
    t = np.arange(3 * SAMPLE_RATE) / SAMPLE_RATE
    envelope = np.minimum(t / 0.03, 1) * np.minimum((3 - t) / 0.05, 1)
    audio = (0.3 * np.sin(2 * np.pi * 440 * t) + 0.04 * np.sin(2 * np.pi * 880 * t)) * envelope
    path = tmp_path / "a440.wav"
    sf.write(path, audio, SAMPLE_RATE)
    notes = transcribe(path, "piano")
    assert notes, "Real model did not return any notes"
    assert any(pitch == 69 for start, end, pitch, amp in notes)
    assert all(end > start and 0 <= pitch <= 127 for start, end, pitch, amp in notes)
