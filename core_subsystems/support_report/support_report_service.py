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


def _get_sanitized_logs() -> list[dict]:
    return [
        entry for entry in DebugLogService.get_logs()
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
