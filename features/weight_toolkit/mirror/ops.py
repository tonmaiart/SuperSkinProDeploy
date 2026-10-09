
import json

import bpy
from ....core.facade import CoreFacade
from .logic import generate_pairs, get_bone_centers, execute_mirror_pipeline
from ..weight_toolkit_feature import MirrorPreferencesService, _DEFAULTS_PATH



class OBJECT_OT_MirrorWeights(bpy.types.Operator):
    """Mirror weights to the other side of the mesh. While editing a layer, only selected vertices change. Select several layers to mirror them all at once"""
    bl_idname = "object.mirror_weights"
    bl_label = "Mirror Weights"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (context.active_object is not None and
                context.active_object.type == 'MESH')

    def execute(self, context):
        facade = CoreFacade(context)
        try:
            mirror_data = MirrorPreferencesService.get_mirror_data()
            do_mask = mirror_data in ('MASK', 'BOTH')
            do_layer = mirror_data in ('BONE', 'BOTH')

            if do_layer:
                sr_raw = MirrorPreferencesService.get_mirror_search_replace_pairs()
                managed = CoreFacade.managed_vg_names(facade.get_obj())
                pairs = generate_pairs(
                    vg_names=[vg.name for vg in facade.get_vertex_groups() if vg.name in managed],
                    bone_centers=get_bone_centers(facade),
                    sr_list=sr_raw,
                    axis=MirrorPreferencesService.get_mirror_axis(),
                    direction=MirrorPreferencesService.get_mirror_direction(),
                )
                if not pairs:
                    if not do_mask:
                        self.report({'WARNING'}, "No mirror pairs found.")
                        return {'CANCELLED'}
                    self.report(
                        {'WARNING'},
                        "No bone weight mirror pairs found — only the mask "
                        "was mirrored. Check that a matching Armature modifier "
                        "is present and bone names match a search/replace pair.",
                    )
        except ValueError:
            return {'CANCELLED'}

        context.scene.superskin_internal_transaction = True
        try:
            count = execute_mirror_pipeline(facade)
        except ValueError:
            return {'CANCELLED'}
        finally:
            context.scene.superskin_internal_transaction = False
        if count > 1:
            self.report({'INFO'}, f"Mirrored {count} layer(s)")
        return {'FINISHED'}



class SUPERSKIN_OT_add_mirror_sr(bpy.types.Operator):
    """Add a bone name pair, such as Left and Right"""
    bl_idname = "superskin.add_mirror_sr"
    bl_label = "Add Mirror Search/Replace Pair"
    bl_options = {'REGISTER'}

    def execute(self, context):
        mirror = context.window_manager.superskin_mirror_prefs
        sr_coll = mirror.search_replace_pairs
        sr_coll.add()
        mirror.search_replace_index = len(sr_coll) - 1
        CoreFacade.save_prefs()
        return {'FINISHED'}


class SUPERSKIN_OT_remove_mirror_sr(bpy.types.Operator):
    """Remove the selected bone name pair"""
    bl_idname = "superskin.remove_mirror_sr"
    bl_label = "Remove Mirror Search/Replace Pair"
    bl_options = {'REGISTER'}

    index: bpy.props.IntProperty(
        name="Index",
        description="Index of the pair to remove",
        default=0,
        min=0,
    )

    def execute(self, context):
        mirror = context.window_manager.superskin_mirror_prefs
        sr_coll = mirror.search_replace_pairs
        if 0 <= self.index < len(sr_coll):
            sr_coll.remove(self.index)
        mirror.search_replace_index = max(0, min(mirror.search_replace_index, len(sr_coll) - 1))
        CoreFacade.save_prefs()
        return {'FINISHED'}


class SUPERSKIN_OT_reset_mirror_sr(bpy.types.Operator):
    """Reset the bone name pairs to the default list"""
    bl_idname = "superskin.reset_mirror_sr"
    bl_label = "Reset Mapping Keywords"
    bl_options = {'REGISTER'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        with open(_DEFAULTS_PATH, encoding="utf-8") as f:
            defaults = json.load(f).get("search_replace_pairs", [])
        mirror = context.window_manager.superskin_mirror_prefs
        sr_coll = mirror.search_replace_pairs
        sr_coll.clear()
        for search_text, replace_text in defaults:
            item = sr_coll.add()
            item.search_text = search_text
            item.replace_text = replace_text
        mirror.search_replace_index = 0
        CoreFacade.save_prefs()
        return {'FINISHED'}



_classes = (
    OBJECT_OT_MirrorWeights,
    SUPERSKIN_OT_add_mirror_sr,
    SUPERSKIN_OT_remove_mirror_sr,
    SUPERSKIN_OT_reset_mirror_sr,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
