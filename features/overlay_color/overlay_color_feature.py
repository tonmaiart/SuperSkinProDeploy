
import os

import bpy

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade
from . import multi_color_draw
from . import multi_color_mask_draw
from . import ramps

_DEFAULTS_PATH = os.path.join(os.path.dirname(__file__), "default_config.json")

_LEGACY_RAMP_TEXTURE_NAMES = (".SSP_VGColor_EditRamp", ".SSP_VGColor_MaskRamp")


def _purge_legacy_ramp_textures() -> None:
    try:
        for name in _LEGACY_RAMP_TEXTURE_NAMES:
            tex = bpy.data.textures.get(name)
            if tex is not None:
                bpy.data.textures.remove(tex)
    except Exception:
        pass


def _set_multi_color(enabled: bool) -> None:
    if enabled:
        multi_color_draw.start()
    else:
        multi_color_draw.stop()


def _in_mask_edit() -> bool:
    obj = bpy.context.active_object
    storage = getattr(obj, "superskin_storage", None) if obj else None
    return bool(storage and storage.active_is_mask)


def _get_wire_color(_self):
    return ramps.wire_color()


def _set_wire_color(_self, value):
    ramps.set_wire_color(value)


class SSPrefOverlayColor(bpy.types.PropertyGroup):
    wire_color: bpy.props.FloatVectorProperty(
        name="Wireframe",
        description="Color of the mesh wireframe while editing weights",
        subtype='COLOR', size=4, min=0.0, max=1.0, default=ramps.WIRE_COLOR_DEFAULT,
        get=_get_wire_color, set=_set_wire_color,
    )




class OverlayColorFeature(UnifiedFeatureExtension):
    """Unified extension owning SuperSkinPro's overlay color modes."""


    domain_id = "overlay_color"
    section_title = "Viewport Colors"
    draw_tab = "PREFERENCE"
    priority = 10
    json_path = ("customize", "overlay_color")
    defaults_path = _DEFAULTS_PATH
    actions = [
        "start_multi_color", "stop_multi_color", "toggle_multi_color",
        "start_multi_color_mask", "stop_multi_color_mask", "toggle_multi_color_mask",
    ]
    keymaps = [
        {
            "key": "Alt+3", "label": "Toggle Multi Color Display",
            "source_label": "Toggle Multi Color Display",
            "is_hidden": lambda: _in_mask_edit(),
        },
    ]


    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        try:
            if action == "toggle_multi_color":
                _set_multi_color(not multi_color_draw.is_enabled())
                core_facade.invalidate_and_redraw()
            elif action == "start_multi_color":
                _set_multi_color(True)
                core_facade.invalidate_and_redraw()
            elif action == "stop_multi_color":
                _set_multi_color(False)
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


    def draw_section(self, layout, context) -> None:
        from ...interface.template_ui import settings_row
        from .ui import draw_gradients
        draw_gradients(layout)
        col = layout.column()
        row = settings_row(col, "Wireframe")
        row.prop(context.window_manager.superskin_overlay_color_prefs, "wire_color", text="")
        if not ramps.wire_is_default():
            row.operator("superskin.reset_color_ramp", text="", icon='LOOP_BACK').ramp = 'wire'

    def get_keymap_items(self) -> list:
        from . import keymap as _keymap
        return _keymap.get_registered_keymap_items()


    def populate(self, data: dict) -> None:
        ramps.load(data)

    def serialize_into(self, full_dict: dict) -> None:
        full_dict.setdefault("customize", {})["overlay_color"] = ramps.dump()

    def get_theme(self):
        return ramps.dump()

    def set_theme(self, data: dict) -> None:
        ramps.load(data)

    def default_theme(self) -> dict:
        return ramps.default_dump()



def register():
    _purge_legacy_ramp_textures()
    if hasattr(bpy.types, SSPrefOverlayColor.__name__):
        bpy.utils.unregister_class(SSPrefOverlayColor)
    bpy.utils.register_class(SSPrefOverlayColor)
    bpy.types.WindowManager.superskin_overlay_color_prefs = bpy.props.PointerProperty(
        type=SSPrefOverlayColor, options={'SKIP_SAVE'},
    )
    UnifiedRegistry.register(OverlayColorFeature())


def unregister():
    UnifiedRegistry.unregister("overlay_color")
    if hasattr(bpy.types.WindowManager, "superskin_overlay_color_prefs"):
        del bpy.types.WindowManager.superskin_overlay_color_prefs
    bpy.utils.unregister_class(SSPrefOverlayColor)
