"""dev_records_service -- resolves gitignored dev-tool output folders.

Consumed exclusively by core/ (via CoreFacade -- see core/facade/README.md's
"Dev Output Folders" section). Must not be imported from features/ directly
(Import Invariant #1).

No bpy.context, bpy.ops, or handler registration (INV-3) -- pure os.path,
addon-root resolution only. Stateless: every call recomputes the addon root
and re-runs os.makedirs, so this module carries no cross-reload identity
concerns the way profiler_service.py's class-level buffers do -- any two
generations of DevRecordsService behave identically for the same input.
"""
from __future__ import annotations

import os


class DevRecordsService:
    """Resolves ``<addon_root>/<name>/`` for developer-only export features.

    Centralizes the addon-root walk that individual export features (the
    profiler, the debug console, ...) would otherwise each reimplement --
    callers only ever supply a bare folder name, never a path, and never
    need to know where the addon root is or how to reach it. Every name
    this resolves a directory for is expected to have a matching entry in
    the repo's ``.gitignore`` (these are working-tree-only dev artifacts,
    never shipped or committed).
    """

    @classmethod
    def get_dir(cls, name: str) -> str:
        """Return the absolute path to ``<addon_root>/<name>/``, creating it
        if it doesn't exist yet.

        Args:
            name: Bare folder name (e.g. ``"profiler_records"``,
                ``"debug_console_records"``) -- never a path or anything
                containing a path separator.
        """
        if not name or os.sep in name or (os.altsep and os.altsep in name):
            raise ValueError(
                f"DevRecordsService.get_dir(name) expects a bare folder name, got {name!r}"
            )
        target_dir = os.path.join(cls._addon_root(), name)
        os.makedirs(target_dir, exist_ok=True)
        return target_dir

    @staticmethod
    def _addon_root() -> str:
        """Walk up from this file to the addon root: dev_records/ -> core_subsystems/ -> root."""
        pkg_dir = os.path.dirname(os.path.abspath(__file__))
        subsystems_dir = os.path.dirname(pkg_dir)
        return os.path.dirname(subsystems_dir)
