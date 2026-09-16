"""InMeshTransferFeature — Unified Component Architecture for intra-mesh
closest-surface-point weight/mask blending.

Owns:
  - Action dispatch: "mark_source", "transfer"
  - UI layout: moved to features/skin_tools_ui/ui_in_mesh_transfer.py, then
    folded into features/skin_tools_ui/ui_action_grid.py's combined grid
    (see docs/domains/skin_tools_ui.md) — draw_section() here is a stub. The
    moved code reads `marked_source_count_for_mesh()` via this domain's new
    `public_api.py` instead of importing `logic.py` directly, since it no
    longer lives inside this package.
  - JSON persistence: no-op (this domain has no user-editable prefs)
"""

import os

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade
from . import logic

_DEFAULTS_PATH = os.path.join(os.path.dirname(__file__), "default_config.json")


# ==============================================================================
# InMeshTransferFeature — UnifiedFeatureExtension
# ==============================================================================

class InMeshTransferFeature(UnifiedFeatureExtension):
    """Unified extension for the In-Mesh Transfer domain."""

    # ── Configuration (class attributes) ───────────────────────────────────

    domain_id = "in_mesh_transfer"
    actions = ["mark_source", "transfer"]
    section_title = "In-Mesh Transfer"
    draw_tab = "SKINNING"
    defaults_path = _DEFAULTS_PATH
    locked_expanded = True

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        core_facade.debug_log("feature_domains", f"in_mesh_transfer.execute() action={action!r}")
        try:
            if action == "mark_source":
                count = logic.mark_source(core_facade)
                if count is None:
                    core_facade.show_toast("ยกเลิกการทำเครื่องหมาย Source แล้ว")
                else:
                    core_facade.show_toast(f"ทำเครื่องหมาย Source แล้ว {count} vertex")
                return {"status": "FINISHED"}
            if action == "transfer":
                logic.transfer(core_facade)
                return {"status": "FINISHED"}
            return {"status": "CANCELLED", "message": f"Unknown action: {action}"}
        except ValueError as e:
            core_facade.debug_log(
                "feature_domains", f"in_mesh_transfer.execute() action={action!r} raised {e!r}",
            )
            return {"status": "CANCELLED", "message": str(e)}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        """UI moved to features/skin_tools_ui/ui_action_grid.py — see
        docs/domains/skin_tools_ui.md."""
        pass

    # ── JSON persistence ──────────────────────────────────────────────────

    def populate(self, data: dict) -> None:
        pass

    def serialize_into(self, full_dict: dict) -> None:
        pass


# ==============================================================================
# Registration (called from __init__.py)
# ==============================================================================

def register():
    """Register with UnifiedRegistry."""
    UnifiedRegistry.register(InMeshTransferFeature())


def unregister():
    """Unregister from UnifiedRegistry."""
    UnifiedRegistry.unregister("in_mesh_transfer")
