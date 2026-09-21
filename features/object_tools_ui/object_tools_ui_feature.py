"""ObjectToolsUIFeature — Unified Component Architecture implementation for the object_tools_ui
domain."""

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade
from . import ui_weight_transfer

_TAB_KEY = "LAYER"

_MERGED_ROW_DOMAIN_IDS = frozenset({"weight_transfer", "weight_export", "weight_import"})
_ROW_ANCHOR_DOMAIN_ID = "weight_export"


# ==============================================================================
# ObjectToolsUIFeature — UnifiedFeatureExtension
# ==============================================================================

class ObjectToolsUIFeature(UnifiedFeatureExtension):
    """Structural extension owning the LAYER-tab ("Object mode UI") section dispatch loop."""

    # ── Configuration (class attributes) ───────────────────────────────────

    domain_id = "object_tools_ui"
    actions = []
    section_title = "Object Tools"
    draw_tab = ""

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        return {"status": "CANCELLED"}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        """Draw the whole LAYER tab: the first non-collapsible viewer spec
        (``deform_layer_viewer``, unmigrated), then."""
        extensions = UnifiedRegistry.get_by_tab(_TAB_KEY)

        for ext in extensions:
            if not ext.is_collapsible():
                ext.draw_section_for_tab(layout, context, _TAB_KEY)
                break

        for ext in extensions:
            if not ext.is_collapsible():
                continue
            domain_id = ext.get_id()
            if domain_id in _MERGED_ROW_DOMAIN_IDS:
                if domain_id != _ROW_ANCHOR_DOMAIN_ID:
                    # Already drawn as part of the combined Import/Export
                    # row below.
                    continue
                layout.separator(factor=0.2)
                ui_weight_transfer.draw_import_export_section(layout, context)
                continue
            layout.separator(factor=0.2)
            UnifiedRegistry.draw_collapsible_section(layout, context, ext, _TAB_KEY)

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
    UnifiedRegistry.register(ObjectToolsUIFeature())


def unregister():
    """Unregister the feature from UnifiedRegistry."""
    UnifiedRegistry.unregister("object_tools_ui")
