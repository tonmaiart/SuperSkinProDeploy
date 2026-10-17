
import bpy

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade
from . import multi_color_draw
from . import multi_color_mask_draw
from . import native_sync

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
        native_sync.release()
        multi_color_draw.start()
    else:
        multi_color_draw.stop()
    native_sync.sync_now()


def _in_mask_edit() -> bool:
    obj = bpy.context.active_object
    storage = getattr(obj, "superskin_storage", None) if obj else None
    return bool(storage and storage.active_is_mask)




class OverlayColorFeature(UnifiedFeatureExtension):
    """Unified extension owning SuperSkinPro's overlay color modes."""


    domain_id = "overlay_color"
    actions = [
        "start_multi_color", "stop_multi_color", "toggle_multi_color",
        "start_multi_color_mask", "stop_multi_color_mask", "toggle_multi_color_mask",
    ]
    keymaps = [
        {
            "key": "Alt+3", "label": "Toggle Multi Color Display",
            "source_label": "Weight Overlay Mode",
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
        pass

    def get_keymap_items(self) -> list:
        from . import keymap as _keymap
        return _keymap.get_registered_keymap_items()


    def populate(self, data: dict) -> None:
        pass

    def serialize_into(self, full_dict: dict) -> None:
        pass



def register():
    _purge_legacy_ramp_textures()
    UnifiedRegistry.register(OverlayColorFeature())


def unregister():
    UnifiedRegistry.unregister("overlay_color")
