
import json
import os

import bpy

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade

_DEFAULTS_PATH = os.path.join(os.path.dirname(__file__), "default_config.json")


def _on_show_shortcut_hint_changed(self, context):
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
    CoreFacade.save_prefs()


def _read_overrides(prefs) -> dict:
    try:
        overrides = json.loads(prefs.hint_overrides)
    except ValueError:
        return {}
    return overrides if isinstance(overrides, dict) else {}


class SSPrefController(bpy.types.PropertyGroup):
    show_shortcut_hint: bpy.props.BoolProperty(
        name="Show Shortcut Hint",
        description="Show the shortcut list at the bottom-left of the viewport while editing weights",
        default=True,
        update=_on_show_shortcut_hint_changed,
    )
    hint_overrides: bpy.props.StringProperty(
        name="Shortcut Hint Rows",
        description="Shortcut rows shown or hidden in the shortcut hint, against their default",
        default="{}",
        options={'HIDDEN'},
    )



class ControllerFeature(UnifiedFeatureExtension):
    """Structural extension for the Controller domain (scene-mode gate, pie menu, utilities)."""


    domain_id = "controller"
    actions = []
    section_title = "Controller"
    draw_tab = ""
    json_path = ("customize", "controller")
    defaults_path = _DEFAULTS_PATH
    keymaps = [
        {"key": "Tab", "label": "Back to Object Mode", "source_label": "Back to Object Mode", "hidden": True},
    ]


    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        return {"status": "CANCELLED"}

    def get_keymap_items(self) -> list:
        from . import ops_scene_modes
        return ops_scene_modes.get_registered_keymap_items()

    def get_shortcut_rows(self) -> list:
        from .ops_scene_modes import EXIT_SESSION_LABEL
        from ...interface.utils.session_shortcuts import KEYMAP_NAME
        return [{"tab": 'GENERAL', "group": "Common", "label": EXIT_SESSION_LABEL,
                 "targets": [(KEYMAP_NAME, "superskin.exit_session_shortcut", 0)], "key": ""}]


    def draw_section(self, layout, context) -> None:
        pass


    def populate(self, data: dict) -> None:
        prefs = bpy.context.window_manager.superskin_controller_prefs
        prefs.show_shortcut_hint = bool(data.get("show_shortcut_hint", True))
        overrides = data.get("hint_overrides")
        valid = isinstance(overrides, dict)
        prefs.hint_overrides = json.dumps({str(k): bool(v) for k, v in overrides.items()} if valid else {})

    def serialize_into(self, full_dict: dict) -> None:
        prefs = bpy.context.window_manager.superskin_controller_prefs
        full_dict.setdefault("customize", {})["controller"] = {
            "show_shortcut_hint": prefs.show_shortcut_hint,
            "hint_overrides": _read_overrides(prefs),
        }



def register():
    if hasattr(bpy.types, SSPrefController.__name__):
        bpy.utils.unregister_class(SSPrefController)
    bpy.utils.register_class(SSPrefController)
    bpy.types.WindowManager.superskin_controller_prefs = bpy.props.PointerProperty(
        type=SSPrefController, options={'SKIP_SAVE'},
    )
    UnifiedRegistry.register(ControllerFeature())


def unregister():
    UnifiedRegistry.unregister("controller")
    try:
        del bpy.types.WindowManager.superskin_controller_prefs
    except Exception:
        pass
    bpy.utils.unregister_class(SSPrefController)
