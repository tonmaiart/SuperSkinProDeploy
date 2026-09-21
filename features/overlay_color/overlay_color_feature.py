"""OverlayColorFeature — Unified Component Architecture implementation for the overlay_color
domain."""

import bpy

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade
from . import multi_color_draw
from . import multi_color_mask_draw

_LEGACY_RAMP_TEXTURE_NAMES = (".SSP_VGColor_EditRamp", ".SSP_VGColor_MaskRamp")


def _purge_legacy_ramp_textures() -> None:
    try:
        for name in _LEGACY_RAMP_TEXTURE_NAMES:
            tex = bpy.data.textures.get(name)
            if tex is not None:
                bpy.data.textures.remove(tex)
    except Exception:
        pass


# ==============================================================================
# OverlayColorFeature — UnifiedFeatureExtension
# ==============================================================================

class OverlayColorFeature(UnifiedFeatureExtension):
    """Unified extension owning SuperSkinPro's overlay color modes."""

    # ── Configuration (class attributes) ──────────────────────────────────

    domain_id = "overlay_color"
    actions = [
        "start_multi_color", "stop_multi_color", "toggle_multi_color",
        "start_multi_color_mask", "stop_multi_color_mask", "toggle_multi_color_mask",
    ]
    keymaps = [
        {
            "key": "Alt+3", "label": "Weight Overlay Mode (Edit Mode)", "mode": "Cycle",
            "source_label": "Weight Overlay Mode",
        },
    ]

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        try:
            if action == "toggle_multi_color":
                multi_color_draw.cycle()
                core_facade.invalidate_and_redraw()
            elif action == "start_multi_color":
                multi_color_draw.start()
                core_facade.invalidate_and_redraw()
            elif action == "stop_multi_color":
                multi_color_draw.stop()
                core_facade.invalidate_and_redraw()
            elif action == "toggle_multi_color_mask":
                multi_color_mask_draw.toggle()
                core_facade.invalidate_and_redraw()
            elif action == "start_multi_color_mask":
                multi_color_mask_draw.start()
                core_facade.invalidate_and_redraw()
            elif action == "stop_multi_color_mask":
                multi_color_mask_draw.stop()
                core_facade.invalidate_and_redraw()
            else:
                return {"status": "CANCELLED", "message": f"Unknown action: {action}"}
        except Exception as e:
            return {"status": "CANCELLED", "message": str(e)}
        return {"status": "FINISHED"}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        """No-op -- this domain has no settings-UI presence (no ``draw_tab``, see the class-
        attribute comment above), so this is never actually called by the PREFERENCE-tab loop."""
        pass

    def get_keymap_items(self) -> list:
        """Expose Alt+3's ``(km, kmi, label)`` to the in-panel shortcut editor
        (``interface/utils/keymap_editor.py``)."""
        from . import keymap as _keymap
        return _keymap.get_registered_keymap_items()

    # ── JSON persistence ──────────────────────────────────────────────────

    def populate(self, data: dict) -> None:
        """No-op -- the weight/mask ramps are hardcoded constants, not a
        persisted setting (see ``native_sync.py``)."""
        pass

    def serialize_into(self, full_dict: dict) -> None:
        """No-op -- nothing in this domain is persisted anymore."""
        pass


# ==============================================================================
# Registration (called from __init__.py)
# ==============================================================================

def register():
    """Purge any leftover legacy ramp-texture data, register the extension."""
    _purge_legacy_ramp_textures()
    UnifiedRegistry.register(OverlayColorFeature())


def unregister():
    UnifiedRegistry.unregister("overlay_color")
