"""Make the pip-installed CUDA libraries loadable and check the GPU is there."""
from __future__ import annotations

import os
import sys
from pathlib import Path

_done = False


def register_cuda_dlls() -> None:
    """Windows: CTranslate2 loads cuBLAS by name, so put nvidia/*/bin on the DLL path."""
    global _done
    if _done or os.name != "nt":
        return
    for base in {Path(sys.prefix), Path(sys.base_prefix)}:
        for d in (base / "Lib" / "site-packages" / "nvidia").glob("*/bin"):
            os.add_dll_directory(str(d))
            os.environ["PATH"] = f"{d}{os.pathsep}{os.environ.get('PATH', '')}"
    _done = True


def cuda_device_count() -> int:
    register_cuda_dlls()
    import ctranslate2

    return ctranslate2.get_cuda_device_count()


def require_cuda() -> None:
    if cuda_device_count() < 1:
        raise RuntimeError(
            "No CUDA GPU found. Install a current NVIDIA driver, or run with --device cpu "
            "(much slower)."
        )
