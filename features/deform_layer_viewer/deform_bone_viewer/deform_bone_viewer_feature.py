
import os
import bpy

from ....interface.registry.register_api import UnifiedFeatureExtension
from ....core.facade import CoreFacade
from . import ui


_DEFAULTS_PATH = os.path.join(os.path.dirname(__file__), "default_config.json")



class DeformBoneViewerFeature(UnifiedFeatureExtension):
    """Non-collapsible viewer extension for the Deform Bone List in the SKINNING tab."""


    domain_id = "deform_bone_viewer"
    actions = [
        "copy_bone_plane", "cut_bone_plane", "paste_bone_plane_add", "paste_bone_plane_subtract", "paste_bone_plane_replace",
        "copy_layer_plane", "cut_layer_plane", "paste_layer_plane_add", "paste_layer_plane_subtract", "paste_layer_plane_replace",
        "move_weight_by_name",
    ]
    section_title = "Deform Bones List"
    draw_tab = "SKINNING"
    link = "https://tonmaiart.github.io/superskinpro-docs/bone_list/"
    collapsible = True
    priority = 0
    expanded_by_default = True
    locked_expanded = True
    show_section_label = False


    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        from . import clipboard_logic
        try:
            if action == "copy_bone_plane":
                clipboard_logic.bone_weight_clipboard.copy(core_facade)
            elif action == "cut_bone_plane":
                clipboard_logic.bone_weight_clipboard.cut(core_facade)
            elif action == "paste_bone_plane_add":
                clipboard_logic.bone_weight_clipboard.paste(core_facade, mode='ADD')
            elif action == "paste_bone_plane_subtract":
                clipboard_logic.bone_weight_clipboard.paste(core_facade, mode='SUBTRACT')
            elif action == "paste_bone_plane_replace":
                clipboard_logic.bone_weight_clipboard.paste(core_facade, mode='REPLACE')
            elif action == "copy_layer_plane":
                clipboard_logic.layer_weight_clipboard.copy(core_facade)
            elif action == "cut_layer_plane":
                clipboard_logic.layer_weight_clipboard.cut(core_facade)
            elif action == "paste_layer_plane_add":
                clipboard_logic.layer_weight_clipboard.paste(core_facade, mode='ADD')
            elif action == "paste_layer_plane_subtract":
                clipboard_logic.layer_weight_clipboard.paste(core_facade, mode='SUBTRACT')
            elif action == "paste_layer_plane_replace":
                clipboard_logic.layer_weight_clipboard.paste(core_facade, mode='REPLACE')
            elif action == "move_weight_by_name":
                from . import move_weight_logic
                prefs = context.window_manager.superskin_move_weight_prefs
                pair_count, vert_count = move_weight_logic.move_weight_by_name(
                    core_facade, prefs.search, prefs.replace, prefs.factor)
                return {"status": "FINISHED",
                        "message": f"Moved weight for {pair_count} bone pair(s) on {vert_count} vertices"}
            else:
                return {"status": "CANCELLED", "message": f"Unknown action: {action}"}
        except ValueError as e:
            return {"status": "CANCELLED", "message": str(e)}
        return {"status": "FINISHED"}

    def get_keymap_items(self) -> list:
        from . import keymap as _keymap
        return _keymap.get_registered_keymap_items()


    def draw_section(self, layout, context) -> None:
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            layout.label(text="No mesh active", icon='ERROR')
            return

        ui.draw_influence_list_system(layout, context, rows=7)


    def populate(self, data: dict) -> None:
        pass

    def serialize_into(self, full_dict: dict) -> None:
        pass


