
import bpy

from ....interface.utils.op_exec import run_domain_via_unified


class MESH_OT_ssp_limit_total_select_exceeded(bpy.types.Operator):
    """Select vertices influenced by more bones than Max Influences"""
    bl_idname = "mesh.ssp_limit_total_select_exceeded"
    bl_label = "Select Exceeded Vertices"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (context.active_object is not None and
                context.active_object.type == 'MESH')

    def execute(self, context):
        return run_domain_via_unified(context, "weight_toolkit", "select_exceeded")


class MESH_OT_ssp_limit_total_apply(bpy.types.Operator):
    """Limit the selected vertices (or the whole mesh if none are selected) to Max Influences bones"""
    bl_idname = "mesh.ssp_limit_total_apply"
    bl_label = "Limit Total"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (context.active_object is not None and
                context.active_object.type == 'MESH')

    def execute(self, context):
        return run_domain_via_unified(context, "weight_toolkit", "limit_total")


def register():
    bpy.utils.register_class(MESH_OT_ssp_limit_total_select_exceeded)
    bpy.utils.register_class(MESH_OT_ssp_limit_total_apply)
    bpy.types.WindowManager.superskin_limit_total_max_influences = bpy.props.IntProperty(
        name="Max Influences", description="Bones kept per vertex when clamping",
        default=4, min=1, max=32, options={'SKIP_SAVE'},
    )


def unregister():
    try:
        del bpy.types.WindowManager.superskin_limit_total_max_influences
    except Exception:
        pass
    bpy.utils.unregister_class(MESH_OT_ssp_limit_total_apply)
    bpy.utils.unregister_class(MESH_OT_ssp_limit_total_select_exceeded)
