"""dev_records_service -- resolves dev-tool output folders OUTSIDE the addon
install tree.

Consumed exclusively by core/ (via CoreFacade -- see docs/core-interfaces/facade-api.md's
"Dev Output Folders" section). Must not be imported from features/ directly
(Import Invariant #1).

No bpy.context, bpy.ops, or handler registration (INV-3) -- ``bpy.utils.user_resource()``
is a pure path-resolution call, not a context/ops/handler API, so this still
complies. Stateless: every call re-resolves the directory and re-runs
os.makedirs, so this module carries no cross-reload identity concerns the
way profiler_service.py's class-level buffers do -- any two generations of
DevRecordsService behave identically for the same input.
"""
from __future__ import annotations

import os

import bpy

_ROOT_SUBDIR = "superskinpro_dev_output"


class DevRecordsService:
    """Resolves a writable, addon-external directory for developer-only
    export features (the profiler, the debug console, the support report).

    These folders used to live at ``<addon_root>/<name>/`` -- inside the
    addon's own installed tree. That was a problem for two reasons: (1) a
    Blender Extension's install directory can be read-only or get wiped/
    replaced on update/reinstall, so writing persistent dev output there is
    fragile, and (2) tooling that treats the addon directory as the sole
    "project" (e.g. an AI assistant's file-access sandbox) has no reason to
    read or write inside it, making these folders an awkward, easy-to-deny
    special case. Resolving to a per-user Blender data directory instead
    (``bpy.utils.user_resource('DATAFILES', ...)``, a sibling of the addon's
    own ``extensions/`` folder under the Blender version's config root)
    fixes both: it survives an addon reinstall, and it sits outside
    whatever sandbox is scoped to the addon's own working tree.

    Callers only ever supply a bare folder name, never a path, and never
    need to know where this resolves to.
    """

    @classmethod
    def get_dir(cls, name: str) -> str:
        """Return the absolute path to this addon's per-user dev-output
        directory for *name*, creating it if it doesn't exist yet.

        Args:
            name: Bare folder name (e.g. ``"profiler_records"``,
                ``"debug_console_records"``) -- never a path or anything
                containing a path separator.
        """
        if not name or os.sep in name or (os.altsep and os.altsep in name):
            raise ValueError(
                f"DevRecordsService.get_dir(name) expects a bare folder name, got {name!r}"
            )
        base = bpy.utils.user_resource('DATAFILES', path=_ROOT_SUBDIR, create=True)
        target_dir = os.path.join(base, name)
        os.makedirs(target_dir, exist_ok=True)
        return target_dir
