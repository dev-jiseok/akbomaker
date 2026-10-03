"""Opt-in real SAM smoke test. Never download models in ordinary CI."""
import os
import threading

import numpy as np
import pytest
import soundfile as sf

from backend import demo
from backend.config import INSTRUMENTS, SAMPLE_RATE
from backend.separator import SAMSeparator, engine_status, save_audio, separate_sequential


@pytest.mark.skipif(os.getenv("RUN_SAM_GPU_TESTS") != "1", reason="SAM model approval and CUDA GPU deferred; opt in explicitly")
def test_real_cuda_sam_sequential_roundtrip(tmp_path):
    status = engine_status()
    assert status["device"] == "auto" or status["device"].startswith("cuda"), "Run the real GPU test on a CUDA host, not the CPU demo."
    assert status["available"], "; ".join(status["issues"])
    # Original synthesized audio, not copyrighted song content. This verifies
    # integration, shape, serialization and summation, not separation quality.
    stems, _ = demo.generate()
    original = sum(stems.values())[:4 * SAMPLE_RATE]
    engine = SAMSeparator()
    targets = {}

    def emit(inst, audio, index, progress):
        if audio is not None:
            targets[inst] = audio.copy()
            save_audio(tmp_path / f"{inst}.wav", audio)

    residual = separate_sequential(original, engine.extract, emit, threading.Event())
    assert list(targets) == list(INSTRUMENTS)
    assert all(t.shape == original.shape and np.isfinite(t).all() for t in targets.values())
    assert np.isfinite(residual).all()
    np.testing.assert_allclose(sum(targets.values()) + residual, original, atol=5e-5, rtol=1e-4)
    for inst in INSTRUMENTS:
        samples, sr = sf.read(tmp_path / f"{inst}.wav", dtype="float32")
        assert sr == SAMPLE_RATE and samples.shape == original.shape
        np.testing.assert_array_equal(samples, targets[inst])
    # Idle releases tensor storage and allocator reservations. The same CPU
    # model must serve another job without downloading/reconstructing weights.
    import torch
    loaded_model = engine.model
    engine.offload()
    assert not engine.gpu_resident
    assert all(p.device.type == "cpu" for p in engine.model.parameters())
    # cuBLAS keeps a small workspace even after all model tensors are on CPU.
    assert torch.cuda.memory_allocated(engine.device) < 32 * 1024 * 1024
    idle_reserved = torch.cuda.memory_reserved(engine.device)
    assert idle_reserved < 64 * 1024 * 1024
    resumed = engine.extract(original, "vocal", threading.Event(), lambda _: None)
    assert engine.model is loaded_model and engine.gpu_resident
    assert resumed.shape == original.shape and np.isfinite(resumed).all()
    engine.offload()
    assert torch.cuda.memory_reserved(engine.device) <= idle_reserved
