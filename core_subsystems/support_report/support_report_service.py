from __future__ import annotations

import json
import os
import re
import time

from ..debug_logging import DebugLogService
from ..dev_records import DevRecordsService

_RECORDS_DIRNAME = "support_reports"

_USER_PATH_RE = re.compile(r"(C:\\Users\\|/home/|/Users/)([^\\/]+)", re.IGNORECASE)


def _redact_paths(text: str) -> str:
    return _USER_PATH_RE.sub(lambda m: m.group(1) + "<redacted>", text)


def _sanitize(value):
    if isinstance(value, str):
        return _redact_paths(value)
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    return value


_QUOTED_RE = re.compile(r"""(?<!\w)(['"])([^'"\n]+?)\1(?!\w)(?!\s*:)""")
_ENUM_RE = re.compile(r"[A-Z0-9_]+")
_NAME_TOKEN = "<name>"


def _project_names() -> set[str]:
    try:
        import bpy
        data = bpy.data
        names = set()
        for coll in (data.objects, data.meshes, data.armatures, data.collections,
                     data.materials, data.actions, data.shape_keys):
            names.update(idb.name for idb in coll)
        for obj in data.objects:
            names.update(vg.name for vg in obj.vertex_groups)
        for arm in data.armatures:
            names.update(b.name for b in arm.bones)
        for key in data.shape_keys:
            names.update(kb.name for kb in key.key_blocks)
    except Exception:
        return set()
    return {n for n in names if len(n) >= 2}


def _build_name_redactor(names: set[str]):
    if not names:
        return lambda text: text
    alternation = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    pattern = re.compile(rf"(?<!\w)(?:{alternation})(?!\w)")
    return lambda text: pattern.sub(_NAME_TOKEN, text)


def _redact_quoted(text: str) -> str:
    def repl(m):
        if _ENUM_RE.fullmatch(m.group(2)):
            return m.group(0)
        return m.group(1) + _NAME_TOKEN + m.group(1)
    return _QUOTED_RE.sub(repl, text)


def _get_sanitized_logs() -> list[dict]:
    redact_names = _build_name_redactor(_project_names())
    return [
        {**entry, "message": _redact_quoted(redact_names(str(entry["message"])))}
        for entry in DebugLogService.get_logs()
        if not entry["category"].startswith("adhoc:")
    ]


class SupportReportService:
    """Builds and writes the user-facing diagnostic report bundle."""

    @classmethod
    def build_report(cls, rig_context: dict | None = None) -> dict:
        from . import environment_collector

        report = {
            "_schema_version": 1,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "environment": environment_collector.collect(),
            "rig_context": rig_context,
            "logs": _get_sanitized_logs(),
        }
        return _sanitize(report)

    @classmethod
    def export_to_file(cls, rig_context: dict | None = None) -> str:
        report = cls.build_report(rig_context)
        records_dir = DevRecordsService.get_dir(_RECORDS_DIRNAME)
        filename = f"SSP_SupportReport_{time.strftime('%Y%m%d_%H%M%S')}.json"
        filepath = os.path.join(records_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        return filepath
