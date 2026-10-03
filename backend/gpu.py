"""Choose among CUDA-visible GPUs without allocating on every device."""
import os
import subprocess


class NoAvailableGPU(RuntimeError):
    pass


def select_device(configured: str) -> str:
    if configured not in {"auto", "cuda"}:
        return configured
    import torch

    minimum = float(os.getenv("SAM_MIN_FREE_GB", "12")) * 1024
    maximum_utilization = float(os.getenv("SAM_MAX_GPU_UTILIZATION", "10"))
    if minimum <= 0 or not 0 <= maximum_utilization <= 100:
        raise ValueError("SAM_MIN_FREE_GB는 양수, SAM_MAX_GPU_UTILIZATION은 0~100이어야 합니다.")
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=uuid,memory.free,utilization.gpu",
         "--format=csv,noheader,nounits"],
        capture_output=True, text=True, check=True, timeout=10,
    )
    readings = {}
    for line in result.stdout.splitlines():
        uuid, free, utilization = (field.strip() for field in line.split(","))
        readings[uuid.removeprefix("GPU-")] = (float(free), float(utilization))
    candidates = []
    for index in range(torch.cuda.device_count()):
        # CUDA_VISIBLE_DEVICES may hide or reorder physical GPU indices.
        uuid = str(torch.cuda.get_device_properties(index).uuid).removeprefix("GPU-")
        free, utilization = readings.get(uuid, (0, 100))
        if free >= minimum and utilization <= maximum_utilization:
            candidates.append((free, -utilization, -index))
    if not candidates:
        raise NoAvailableGPU(
            f"사용 가능한 GPU가 없어요. 여유 메모리 {minimum / 1024:g} GiB 이상, "
            f"사용률 {maximum_utilization:g}% 이하인 GPU가 필요해요. 잠시 후 다시 시도해주세요."
        )
    return f"cuda:{-max(candidates)[2]}"
