"""OverlayColorFeature — Unified Component Architecture implementation for
the overlay_color domain.

Merges what used to be two separate domains, `vgcolor` (weight/mask ramp
customization) and `multi_color_preview` (Alt+3 rainbow per-bone preview) —
both are "SuperSkinPro draws its own color on top of Blender's native
viewport instead of leaving it alone" mechanisms, and both needed to
coordinate to avoid visually stacking (see `native_sync.py`'s "Yields to
Multi Color Preview" note), which is much simpler to do inside one domain
than across two with the project's "Zero Cross-Imports" rule between
features. Renamed from `vgcolor` to `overlay_color` to reflect that
broader identity — it was never really about "VG color" specifically.

Owns:
  - Two hardcoded color ramps (`native_sync._EDIT_RAMP_STOPS` for normal
    weight editing, `native_sync._MASK_RAMP_STOPS` for mask editing),
    pushed into Blender's native weight-color pipeline by `native_sync.py`
    while the addon's own "Edit Layer Weight" mode is active. Not
    user-editable -- no settings UI, no JSON persistence.
  - Multi Color Preview (`multi_color_draw.py`), the Alt+3 toggle that
    blends all bone colors per vertex via a temp mesh color attribute +
    native Solid/Attribute shading.
  - Action dispatch: `start_multi_color` / `stop_multi_color` /
    `toggle_multi_color` (multi_color_draw.py).
"""

import bpy

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade
from . import multi_color_draw

# Names of the hidden ramp-editing Texture datablocks a pre-hardcoded-ramp
# install may still have lying around (see git history for `_ramp_io.py`'s
# former `get_or_create_ramp_texture()`) -- purged once on register() so
# upgrading users don't carry that orphan data forward indefinitely.
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
    actions = ["start_multi_color", "stop_multi_color", "toggle_multi_color"]
    # No section_title / draw_tab: this domain has no settings-UI presence
    # at all -- the "Overlay Color" box with its "Multi Color Preview: hold
    # Alt+3 to toggle" pointer line was removed as unnecessary (the Alt+3
    # keymap works on its own without a UI reminder). Leaving draw_tab
    # unset (defaults to "") excludes this extension from
    # UnifiedRegistry.get_by_tab('PREFERENCE')'s loop entirely -- same
    # mechanism widget_preferences.py already uses to keep addon_updater/
    # activate out of that popover. Action dispatch (Alt+3 -> toggle_multi_color)
    # and the shortcut-overlay HUD entry below are unaffected -- neither
    # depends on draw_tab.
    keymaps = [
        {"key": "Alt+3", "label": "Multi Color Preview", "mode": "Toggle"},
    ]
    # No defaults_path: the weight/mask ramps are hardcoded constants in
    # native_sync.py now, not a persisted setting -- see that module's
    # docstring. supports_dev_override / supports_reset_to_default stay at
    # their ABC defaults (False / True respectively) but are moot either
    # way since there's nothing left for either System Actions button to
    # touch on this domain.

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        try:
            if action == "toggle_multi_color":
                multi_color_draw.toggle()
                core_facade.invalidate_and_redraw()
            elif action == "start_multi_color":
                multi_color_draw.start()
                core_facade.invalidate_and_redraw()
            elif action == "stop_multi_color":
                multi_color_draw.stop()
                core_facade.invalidate_and_redraw()
            else:
                return {"status": "CANCELLED", "message": f"Unknown action: {action}"}
        except Exception as e:
            return {"status": "CANCELLED", "message": str(e)}
        return {"status": "FINISHED"}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        """No-op -- this domain has no settings-UI presence (no ``draw_tab``,
        see the class-attribute comment above), so this is never actually
        called by the PREFERENCE-tab loop. Still required: ``draw_section``
        is ``@abstractmethod`` on ``UnifiedFeatureExtension``."""
        pass

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
