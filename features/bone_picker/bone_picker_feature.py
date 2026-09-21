"""BonePickerFeature — Unified Component Architecture implementation for the bone picker domain."""

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
        pass
    CoreFacade.save_prefs()


class SSPrefBonePicker(bpy.types.PropertyGroup):
    """The one remaining live/adjustable setting for the static deform skeleton overlay."""

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
            "source_label": "Pick Bone",
            "is_active": lambda: ops.is_active(),
            "sub_keymaps": [
                {"key": "Left Click", "label": "Append Bone Selection"},
                {"key": "Middle Click", "label": "Remove Bone Selection"},
                {"key": "Right Click", "label": "Cancel Bone Picker"},
                {"key": "Release", "label": "Confirm Bone Selection"},
            ],
        },
    ]
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
                    core_facade.clear_all_selected(obj)
                    storage.selection_history = ""
                    storage.last_clicked_index = -1
                core_facade.show_toast("CLEAN ALL MULTI SELECTION", 1.0)
            else:
                return {"status": "CANCELLED", "message": f"Unknown action: {action}"}
        except Exception as e:
            return {"status": "CANCELLED", "message": str(e)}
        return {"status": "FINISHED"}

    def get_keymap_items(self) -> list:
        """Expose Alt+2's ``(km, kmi, label)`` to the in-panel shortcut editor
        (``interface/utils/keymap_editor.py``)."""
        from . import keymap as _keymap
        return _keymap.get_registered_keymap_items()

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        """Just the Overall Size slider."""
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
