"""environment_collector -- static runtime environment facts for a support
report bundle (add-on version, Blender version, OS, GPU).

No bpy.context, bpy.ops, or handler registration (INV-3). ``bpy.app`` and
``gpu.platform.*`` reads are fine -- neither touches bpy.context; both are
plain attribute/state reads available for the lifetime of the Blender
process, unlike anything under ``bpy.context`` which needs a live UI call.
"""
from __future__ import annotations

import platform

from ... import ADDON_VERSION


def _get_blender_version() -> str:
    import bpy
    return bpy.app.version_string


def _get_gpu_info() -> dict:
    """Best-effort GPU vendor/renderer/driver-version strings.

    Not guaranteed to contain an actual driver version number on every
    platform/vendor (notably macOS/Metal-backed contexts) -- treat as
    diagnostic best-effort, never a required field.
    """
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
    """Return a static environment snapshot: addon/Blender version, OS, GPU.

    Contains no user-identifying data (no file paths, no object/scene
    names) -- safe to include in a bundle sent to a third party as-is.
    """
    return {
        "addon_version": ADDON_VERSION,
        "blender_version": _get_blender_version(),
        "os": _get_os_info(),
        "gpu": _get_gpu_info(),
    }
