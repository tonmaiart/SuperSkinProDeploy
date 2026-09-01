"""Auto Block Weight operator — auto assign closest unlocked bone."""

import bpy
from ...interface.utils.op_exec import run_domain_via_unified


class MESH_OT_auto_assign_closest_unlocked_bone(bpy.types.Operator):
    """Advanced Weight Assigning — Loop-Axis Aligned"""
    bl_idname = "mesh.auto_assign_closest_unlocked_bone"
    bl_label = "Auto Assign Weight (Loop-Axis Aligned)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        if getattr(context.scene, "superskin_is_mask_mode", False):
            self.report({'WARNING'}, "Auto Assign is not available while the Mask row is active")
            return {'CANCELLED'}

        return run_domain_via_unified(context, "auto_block", "auto")


def _menu_func(self, context):
    self.layout.operator(
        MESH_OT_auto_assign_closest_unlocked_bone.bl_idname, icon='BONE_DATA'
    )


def register():
    bpy.utils.register_class(MESH_OT_auto_assign_closest_unlocked_bone)
    bpy.types.VIEW3D_MT_paint_weight.append(_menu_func)


def unregister():
    bpy.types.VIEW3D_MT_paint_weight.remove(_menu_func)
    bpy.utils.unregister_class(MESH_OT_auto_assign_closest_unlocked_bone)
