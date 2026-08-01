"""environment_collector -- static runtime environment facts for a support
report bundle (add-on version, Blender version, OS, GPU).

No bpy.context, bpy.ops, or handler registration (INV-3). ``bpy.app`` and
``gpu.platform.*`` reads are fine -- neither touches bpy.context; both are
plain attribute/state reads available for the lifetime of the Blender
process, unlike anything under ``bpy.context`` which needs a live UI call.
"""
from __future__ import annotations

import os
import platform


def _addon_root() -> str:
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    subsystems_dir = os.path.dirname(pkg_dir)
    return os.path.dirname(subsystems_dir)


def _read_addon_version() -> str:
    """Parse ``version`` out of ``blender_manifest.toml``.

    Deliberately self-contained rather than reusing
    ``interface/panel_main.py``'s own ``_read_addon_version()`` --
    ``core_subsystems/`` never imports from ``interface/`` (see this
    package's import invariants in ``core_subsystems/__init__.py``), so
    this small duplication is the cost of keeping that boundary intact for
    a one-line read.
    """
    manifest_path = os.path.join(_addon_root(), "blender_manifest.toml")
    try:
        import tomllib
        with open(manifest_path, "rb") as fh:
            manifest = tomllib.load(fh)
        return str(manifest.get("version", "unknown"))
    except Exception:
        return "unknown"


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
        "addon_version": _read_addon_version(),
        "blender_version": _get_blender_version(),
        "os": _get_os_info(),
        "gpu": _get_gpu_info(),
    }
