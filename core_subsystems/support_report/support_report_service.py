"""support_report_service -- bundles a sanitized, user-safe diagnostic
report for the ``support_report`` feature domain's "Export Diagnostic
Report" button.

Consumed exclusively by core/ (via CoreFacade -- see core/facade/README.md's
"Support Report" section). Must not be imported from features/ directly
(Import Invariant #1).

No bpy.context (INV-3) -- ``rig_context`` (active-object-derived facts) is
supplied by the caller, which has actual bpy.context access; this module
never reads it directly. This also keeps the report generatable even when
SuperSkinPro isn't Pro-activated or there's no active mesh -- the same
"must work when something is already broken" reasoning ``profiler``/
``debug_console`` document for themselves.
"""
from __future__ import annotations

import json
import os
import re
import time

from ..debug_logging import DebugLogService
from ..dev_records import DevRecordsService

_RECORDS_DIRNAME = "support_reports"

# Matches a home-directory username segment on Windows/macOS/Linux so it can
# be redacted before the report ever leaves the machine -- log messages and
# any incidentally-embedded paths often carry the OS username.
_USER_PATH_RE = re.compile(r"(C:\\Users\\|/home/|/Users/)([^\\/]+)", re.IGNORECASE)


def _redact_paths(text: str) -> str:
    return _USER_PATH_RE.sub(lambda m: m.group(1) + "<redacted>", text)


def _sanitize(value):
    """Recursively redact home-directory usernames from every string found."""
    if isinstance(value, str):
        return _redact_paths(value)
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    return value


def _get_sanitized_logs() -> list[dict]:
    """Every buffered log entry except ad hoc (``adhoc:*``) dev-debug decks.

    Ad hoc categories are temporary, agent-authored diagnostic tags (see
    CLAUDE.md's "Ad Hoc Debug Deck Exception") -- noise for a user-facing
    support bundle, not meaningful history of what the user actually did.
    """
    return [
        entry for entry in DebugLogService.get_logs()
        if not entry["category"].startswith("adhoc:")
    ]


class SupportReportService:
    """Builds and writes the user-facing diagnostic report bundle."""

    @classmethod
    def build_report(cls, rig_context: dict | None = None) -> dict:
        """Assemble the full report dict: environment + rig context + logs.

        Args:
            rig_context: Optional caller-supplied active-object facts (e.g.
                vertex/bone counts). ``None`` when there's no active mesh --
                the section is simply omitted, never invented.
        """
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
        """Write ``build_report()``'s output to a timestamped JSON file
        under ``<addon_root>/support_reports/``.

        Returns:
            Absolute path to the written file.
        """
        report = cls.build_report(rig_context)
        records_dir = DevRecordsService.get_dir(_RECORDS_DIRNAME)
        filename = f"SSP_SupportReport_{time.strftime('%Y%m%d_%H%M%S')}.json"
        filepath = os.path.join(records_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        return filepath
