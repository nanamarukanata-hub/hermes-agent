"""Non-destructive kernel execution and GPU smoke test for an authorized Colab session."""
import json

try:
    import torch
    device = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    print("HERMES_KERNEL_EXEC_OK", json.dumps({
        "python_executed": True,
        "cuda_available": torch.cuda.is_available(),
        "gpu": device,
    }))
except ImportError:
    print("HERMES_KERNEL_EXEC_OK", json.dumps({
        "python_executed": True, "cuda_available": False,
        "gpu": None, "torch_missing": True,
    }))
