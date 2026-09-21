"""Pure I/O functions for SuperSkinPro preferences — no bpy, no PropertyGroup logic.

Path resolution mirrors core_subsystems/rust_loader.py's approach (resolve from addon root).
"""

import json
import os
import tempfile


def _addon_root() -> str:
    """Return the absolute path to the addon root (parent of core_subsystems/)."""
    preferences_dir = os.path.dirname(os.path.abspath(__file__))  # .../core_subsystems/preferences
    subsystems_dir = os.path.dirname(preferences_dir)              # .../core_subsystems
    return os.path.dirname(subsystems_dir)                          # .../<addon_root>


def default_json_path() -> str:
    """Resolve the path to the factory default preferences JSON file.

    Walks up from this file's directory to the addon root, then appends
    ``prefs/default_prefs.json``.
    """
    return os.path.join(_addon_root(), "prefs", "default_prefs.json")


def user_json_path() -> str:
    """Resolve the path to the per-machine user preferences JSON file.

    Uses ``bpy.utils.user_resource('CONFIG', ...)`` so the file lives outside
    the addon folder and survives addon updates / reinstalls.
    """
    import bpy
    config_dir = bpy.utils.user_resource('CONFIG', path="SuperSkinPro", create=True)
    return os.path.join(config_dir, "user_prefs.json")


def load_json_safe(path: str) -> dict:
    """Load and parse a JSON file, returning ``{}`` on any failure (missing, corrupt, etc).

    Never raises — callers can always assume a dict is returned.
    """
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def save_json(path: str, data: dict) -> None:
    """Atomically write *data* as JSON to *path*.

    Writes to a temp file first, then ``os.replace`` over the real file to
    avoid corruption if the process is killed mid-write.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        # Best-effort cleanup — don't leave a stale temp file
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge two dicts.

    Keys present in *override* take precedence over *base*.
    Keys missing from *override* fall back to *base*.
    Supports the case where an older user.json doesn't yet have a key added
    in a later version.
    """
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = deep_merge(result[key], val)
        else:
            result[key] = val
    return result
