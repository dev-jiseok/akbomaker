from types import SimpleNamespace
from unittest.mock import Mock
import sys

import pytest

from backend import gpu, separator
from backend.errors import processing_error


@pytest.fixture
def devices(monkeypatch):
    monkeypatch.delenv("SAM_MIN_FREE_GB", raising=False)
    monkeypatch.delenv("SAM_MAX_GPU_UTILIZATION", raising=False)
    cuda = SimpleNamespace(
        device_count=lambda: 3,
        get_device_properties=lambda index: SimpleNamespace(uuid=["c", "a", "b"][index]),
    )
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=cuda))
    query = Mock(return_value=SimpleNamespace(stdout=
        "GPU-a, 24000, 100\nGPU-b, 22000, 0\nGPU-c, 23000, 0\nGPU-hidden, 48000, 0\n"))
    monkeypatch.setattr(gpu.subprocess, "run", query)
    return query


def test_selection_respects_visible_order_and_excludes_busy_gpu(devices):
    assert gpu.select_device("cuda") == "cuda:0"
    devices.return_value.stdout = "GPU-a, 24000, 0\nGPU-b, 22000, 0\nGPU-c, 23000, 80\n"
    assert gpu.select_device("auto") == "cuda:1"


def test_no_capacity_explains_failure_in_job(devices):
    devices.return_value.stdout = "GPU-a, 1000, 0\nGPU-b, 24000, 100\nGPU-c, 2000, 0\n"
    with pytest.raises(gpu.NoAvailableGPU) as caught:
        gpu.select_device("cuda")
    assert "사용 가능한 GPU가 없어요" in processing_error(caught.value, "분리", "job1")


@pytest.mark.parametrize("device", ["cpu", "cuda:1"])
def test_explicit_device_does_not_query_other_gpus(devices, device):
    assert gpu.select_device(device) == device
    devices.assert_not_called()


def test_model_selects_again_only_after_offload(monkeypatch):
    engine = separator.SAMSeparator()
    engine.model = Mock()
    engine._move_model = Mock()
    selection = Mock(side_effect=["cuda:2", "cuda:1"])
    monkeypatch.setattr(separator, "select_device", selection)
    # Avoid real CUDA cache operations in this lifecycle test.
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=SimpleNamespace(is_initialized=lambda: False)))
    engine.load()
    engine.load()
    assert engine.device == "cuda:2"
    assert selection.call_count == 1
    engine.offload()
    engine.load()
    assert engine.device == "cuda:1"
    assert engine._move_model.call_args_list[1].args == ("cpu",)
