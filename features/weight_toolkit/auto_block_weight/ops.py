
import bpy
from ....interface.utils.op_exec import run_domain_via_unified
from . import soften_logic


class MESH_OT_auto_assign_closest_unlocked_bone(bpy.types.Operator):
    """Assign each selected vertex to its nearest unlocked bone"""
    bl_idname = "mesh.auto_assign_closest_unlocked_bone"
    bl_label = "Auto Assign Weight (Loop-Axis Aligned)"
    bl_options = {'REGISTER', 'UNDO'}

    smooth: bpy.props.FloatProperty(
        name="Smooth",
        description="How far weights blend between neighboring bones",
        default=0.0, min=0.0, max=soften_logic.MAX_SMOOTH, subtype='FACTOR',
    )
    repeat: bpy.props.IntProperty(
        name="Repeat",
        description="Extra smoothing inside the blended area",
        default=0, min=0, max=soften_logic.MAX_REPEAT, soft_max=20,
    )
    face_rings: bpy.props.BoolProperty(
        name="Face Rings",
        description="Split lip and eyelid bones by angle around the mouth or eye, following its edge loops",
        default=False,
    )

    def execute(self, context):
        if getattr(context.scene, "superskin_is_mask_mode", False):
            self.report({'WARNING'}, "Auto Assign is not available while the Mask row is active")
            return {'CANCELLED'}

        soften_logic.set_options(self.smooth, self.repeat, self.face_rings)
        return run_domain_via_unified(context, "weight_toolkit", "auto")


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
