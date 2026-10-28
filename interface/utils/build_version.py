
import json
import os
import re
import subprocess
import sys


BUILD_INFO_FILE = "build_info.json"
_RELEASE_TAG_GLOB = "[0-9]*.[0-9]*.[0-9]*"
_GIT_TIMEOUT_S = 5.0

_cached = None


def manifest_version(root):
    try:
        with open(os.path.join(root, "blender_manifest.toml"), encoding="utf-8") as fh:
            match = re.search(r'^version\s*=\s*"([^"]+)"', fh.read(), re.MULTILINE)
    except OSError:
        return "0.0.0"
    return match.group(1) if match else "0.0.0"


def _git(root, *args):
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        out = subprocess.run(
            ["git", "-C", root, *args], capture_output=True, text=True,
            timeout=_GIT_TIMEOUT_S, **kwargs,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def git_dev_build(root):
    if not os.path.exists(os.path.join(root, ".git")):
        return None
    tag = _git(root, "describe", "--tags", "--abbrev=0", "--match", _RELEASE_TAG_GLOB)
    count = _git(root, "rev-list", "--count", f"{tag}..HEAD" if tag else "HEAD")
    return int(count) if count and count.isdigit() else None


def format_version(base, dev_build):
    if dev_build is None:
        return f"{base}.?"
    return f"{base}.{dev_build}" if dev_build > 0 else base


def resolve(root):
    base = manifest_version(root)
    dev_build = git_dev_build(root)
    if dev_build is None:
        try:
            with open(os.path.join(root, BUILD_INFO_FILE), encoding="utf-8") as fh:
                info = json.load(fh)
            if info.get("base") == base and isinstance(info.get("dev_build"), int):
                dev_build = info["dev_build"]
        except (OSError, ValueError, AttributeError):
            pass
    return base, dev_build


def addon_version():
    global _cached
    if _cached is None:
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        try:
            _cached = format_version(*resolve(root))
        except Exception:
            _cached = format_version(manifest_version(root), None)
    return _cached
