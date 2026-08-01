"""BonePickerFeature — Unified Component Architecture implementation for the bone picker domain.

Collapses the old BonePickerDomain (action dispatch) and prefs.py (PropertyGroup,
draw, persistence) into a single UnifiedFeatureExtension subclass.

Owns:
  - SSPrefBonePicker PropertyGroup (registered on WindowManager) -- now just
    ``overall_size``; every other appearance setting (colors, wedge width,
    line widths, pivot ratio, fill opacity, head circle size) was moved to
    hardcoded constants in ``deform_overlay.py`` and is no longer
    user-configurable.
  - Action dispatch: "start_bone_picker", "stop_bone_picker", "clear_multi_selection"
  - UI layout: draw_section() -- just the Overall Size slider now
  - JSON persistence: populate() / serialize_into() (``overall_size`` only)

``overall_size`` is adjustable only via the Settings UI slider
(``draw_section()`` below) now. There used to also be an Alt+2+Scroll
shortcut (``SUPERSKIN_OT_bone_overlay_size_step``) -- removed, since its
Blender-native "Adjust Last Operation" redo panel got clobbered on every
Alt+2 release (``object.mw_pick_bone`` needs its own ``'UNDO'`` for real
bone-pick undo support, and that undo push becomes the new top of the
undo stack, invalidating the resize operator's redo panel regardless of
``'REGISTER'``), making it an unreliable way to adjust the value. The
Settings UI slider is the only control left.
"""

import bpy
import os

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade
from . import ops

_DEFAULTS_PATH = os.path.join(os.path.dirname(__file__), "default_config.json")


# ==============================================================================
# Property Groups
# ==============================================================================

def _on_changed(self, context):
    if not getattr(context, 'active_object', None):
        return
    from ...core.facade import CoreFacade
    try:
        CoreFacade(context).invalidate_color_only()
    except ValueError:
        pass  # Not activated -- nothing to redraw, the sidebar panel is hidden anyway.
    CoreFacade.save_prefs()


class SSPrefBonePicker(bpy.types.PropertyGroup):
    """The one remaining live/adjustable setting for the static deform
    skeleton overlay -- every other appearance value is now a hardcoded
    constant in deform_overlay.py, with no UI and no JSON key."""

    overall_size: bpy.props.FloatProperty(
        name="Overall Size",
        description="Global size multiplier applied to the whole bone shape",
        min=0.1, max=5.0, default=1.0, step=10,
        update=_on_changed,
    )


# ==============================================================================
# BonePickerFeature — UnifiedFeatureExtension
# ==============================================================================

class BonePickerFeature(UnifiedFeatureExtension):
    """Unified extension for the Bone Picker domain."""

    # ── Configuration (class attributes) ──────────────────────────────────

    domain_id = "bone_picker"
    actions = ["start_bone_picker", "stop_bone_picker", "clear_multi_selection"]
    section_title = "Bone Picker"
    draw_tab = "PREFERENCE"
    json_path = ("customize", "bone_picker")
    defaults_path = _DEFAULTS_PATH
    keymaps = [
        {
            "key": "Alt+2", "label": "Bone Picker", "mode": "Hold",
            "is_active": lambda: ops.is_active(),
            "sub_keymaps": [
                {"key": "Left Click", "label": "Append Bone Selection"},
                {"key": "Middle Click", "label": "Remove Bone Selection"},
                {"key": "Right Click", "label": "Cancel Bone Picker"},
                {"key": "Release", "label": "Confirm Bone Selection"},
            ],
        },
    ]
    # Every field already live-saves straight to user.json via _on_changed
    # above -- opted out of both System Actions dev-only workflows: promoting
    # live values to the shipped default_config.json ("Save As Default") and
    # discarding live values back to it ("Reset to Default"). defaults_path
    # stays set since load() still needs it to seed first-run values merged
    # with any saved user.json customization.
    supports_dev_override = False
    supports_reset_to_default = False

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        try:
            if action == "start_bone_picker":
                pass
            elif action == "stop_bone_picker":
                pass
            elif action == "clear_multi_selection":
                obj = core_facade.get_obj()
                storage = getattr(obj, "superskin_storage", None)
                if storage:
                    storage.selected_names = ""
                    storage.selection_history = ""
                    storage.last_clicked_index = -1
                core_facade.show_toast("CLEAN ALL MULTI SELECTION", 1.0)
            else:
                return {"status": "CANCELLED", "message": f"Unknown action: {action}"}
        except Exception as e:
            return {"status": "CANCELLED", "message": str(e)}
        return {"status": "FINISHED"}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        """Just the Overall Size slider -- every other appearance setting
        (colors, wedge width, line widths, pivot ratio, fill opacity, head
        circle size) is hardcoded in deform_overlay.py with no UI at all.
        This is the only control left for overall_size -- there used to
        also be an Alt+2+Scroll shortcut, removed because its Blender-
        native "Adjust Last Operation" redo panel got clobbered on every
        release (object.mw_pick_bone's own undo push, needed for real
        bone-pick undo support, becomes the new top of the undo stack and
        invalidates the resize operator's redo panel regardless of
        'REGISTER'), making it an unreliable way to adjust the value."""
        bp = context.window_manager.superskin_bone_picker_prefs
        layout.use_property_decorate = False
        layout.prop(bp, "overall_size", slider=True)

    # ── JSON persistence ──────────────────────────────────────────────────

    def populate(self, data: dict) -> None:
        """Write section data dict into the live WindowManager property."""
        bp = bpy.context.window_manager.superskin_bone_picker_prefs
        if "overall_size" in data:
            bp.overall_size = float(data["overall_size"])

    def serialize_into(self, full_dict: dict) -> None:
        """Write current values into full_dict at the correct JSON path."""
        bp = bpy.context.window_manager.superskin_bone_picker_prefs
        full_dict.setdefault("customize", {})["bone_picker"] = {
            "overall_size": bp.overall_size,
        }


# ==============================================================================
# Registration (called from __init__.py)
# ==============================================================================

def register():
    """Register PropertyGroup on WindowManager and the extension with UnifiedRegistry."""
    if hasattr(bpy.types, SSPrefBonePicker.__name__):
        bpy.utils.unregister_class(SSPrefBonePicker)
    bpy.utils.register_class(SSPrefBonePicker)
    bpy.types.WindowManager.superskin_bone_picker_prefs = bpy.props.PointerProperty(
        type=SSPrefBonePicker, options={'SKIP_SAVE'},
    )
    UnifiedRegistry.register(BonePickerFeature())


def unregister():
    """Unregister PropertyGroup and the extension."""
    UnifiedRegistry.unregister("bone_picker")
    try:
        del bpy.types.WindowManager.superskin_bone_picker_prefs
    except Exception:
        pass
    bpy.utils.unregister_class(SSPrefBonePicker)
