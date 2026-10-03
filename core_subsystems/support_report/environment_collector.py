from __future__ import annotations

import platform

from ... import ADDON_VERSION


def _get_blender_version() -> str:
    import bpy
    return bpy.app.version_string


def _get_gpu_info() -> dict:
    try:
        import gpu
        return {
            "vendor": gpu.platform.vendor_get(),
            "renderer": gpu.platform.renderer_get(),
            "version": gpu.platform.version_get(),
        }
    except Exception as exc:
        return {"error": f"GPU info unavailable: {exc!r}"}


def _get_os_info() -> dict:
    return {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
    }


def collect() -> dict:
    return {
        "addon_version": ADDON_VERSION,
        "blender_version": _get_blender_version(),
        "os": _get_os_info(),
        "gpu": _get_gpu_info(),
    }
