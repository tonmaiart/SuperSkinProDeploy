"""SkinToolsUIFeature — Unified Component Architecture implementation for the skin_tools_ui domain."""

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade
from . import (
    ui_weight_apply, ui_mirror, ui_action_grid,
)

_TAB_KEY = "SKINNING"

_DRAW_BODY_BY_DOMAIN = {
    "weight_apply": ui_weight_apply.draw_section,
}

_MERGED_GRID_DOMAIN_IDS = frozenset({"auto_block", "mirror", "clipboard", "in_mesh_transfer"})
_GRID_ANCHOR_DOMAIN_ID = "auto_block"


# ==============================================================================
# SkinToolsUIFeature — UnifiedFeatureExtension
# ==============================================================================

class SkinToolsUIFeature(UnifiedFeatureExtension):
    """Structural extension owning the SKINNING-tab ("Edit weight mode UI") section dispatch loop."""

    # ── Configuration (class attributes) ───────────────────────────────────

    domain_id = "skin_tools_ui"
    actions = []
    section_title = "Skin Tools"
    draw_tab = ""

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        return {"status": "CANCELLED"}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        """Draw the whole SKINNING tab: the first non-collapsible viewer spec
        (``deform_layer_viewer``, unmigrated)."""
        extensions = UnifiedRegistry.get_by_tab(_TAB_KEY)

        for ext in extensions:
            if not ext.is_collapsible():
                ext.draw_section_for_tab(layout, context, _TAB_KEY)
                break

        for ext in extensions:
            if not ext.is_collapsible():
                continue
            domain_id = ext.get_id()
            if domain_id in _MERGED_GRID_DOMAIN_IDS:
                if domain_id != _GRID_ANCHOR_DOMAIN_ID:
                    # Already drawn as part of the combined grid below.
                    continue
                layout.separator(factor=0.2)
                ui_action_grid.draw_section(layout, context)
                continue
            layout.separator(factor=0.2)
            UnifiedRegistry.draw_collapsible_section(
                layout, context, ext, _TAB_KEY,
                draw_body=_DRAW_BODY_BY_DOMAIN.get(domain_id),
            )

    # ── JSON persistence ──────────────────────────────────────────────────

    def populate(self, data: dict) -> None:
        pass

    def serialize_into(self, full_dict: dict) -> None:
        pass


# ==============================================================================
# Registration (called from __init__.py)
# ==============================================================================

def register():
    """Register the feature with UnifiedRegistry."""
    UnifiedRegistry.register(SkinToolsUIFeature())


def unregister():
    """Unregister the feature from UnifiedRegistry."""
    UnifiedRegistry.unregister("skin_tools_ui")
