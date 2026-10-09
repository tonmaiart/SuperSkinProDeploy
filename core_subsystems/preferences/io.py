
import json
import os
import tempfile


def _addon_root() -> str:
    preferences_dir = os.path.dirname(os.path.abspath(__file__))
    subsystems_dir = os.path.dirname(preferences_dir)
    return os.path.dirname(subsystems_dir)


def default_json_path() -> str:
    return os.path.join(_addon_root(), "prefs", "default_prefs.json")


def user_json_path() -> str:
    import bpy
    config_dir = bpy.utils.user_resource('CONFIG', path="SuperSkinPro", create=True)
    return os.path.join(config_dir, "user_prefs.json")


def load_json_safe(path: str) -> dict:
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
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = deep_merge(result[key], val)
        else:
            result[key] = val
    return result
