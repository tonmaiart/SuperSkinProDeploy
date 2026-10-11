
import bpy

from ....interface.utils.op_exec import run_domain_via_unified


class OBJECT_OT_ssp_copy_weight_single(bpy.types.Operator):
    bl_idname = "object.ssp_copy_weight_single"
    bl_label = "Copy Vertex Influence"
    bl_description = "Copy the weights of the one selected vertex"
    bl_options = {'REGISTER'}

    def execute(self, context):
        return run_domain_via_unified(context, "weight_toolkit", "copy_single")


class OBJECT_OT_ssp_paste_weight_replace(bpy.types.Operator):
    bl_idname = "object.ssp_paste_weight_replace"
    bl_label = "Paste Weight (Replace)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        return run_domain_via_unified(context, "weight_toolkit", "paste_replace")


_classes = (
    OBJECT_OT_ssp_copy_weight_single,
    OBJECT_OT_ssp_paste_weight_replace,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
