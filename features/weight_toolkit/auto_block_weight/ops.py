
import json

import bpy
from ....core.facade import CoreFacade
from ....interface.utils.op_exec import run_domain_via_unified
from . import logic, soften_logic


class MESH_OT_auto_assign_closest_unlocked_bone(bpy.types.Operator):
    bl_idname = "mesh.auto_assign_closest_unlocked_bone"
    bl_label = "Block Weight"
    bl_description = (
        "Give each selected vertex full weight from its nearest unlocked bone"
        "\n\u2022 Lock bones that should not receive weight"
        "\n\u2022 Face Rings and Distance From are in the Adjust Last Operation panel"
    )
    bl_options = {'REGISTER', 'UNDO'}

    face_rings: bpy.props.BoolProperty(
        name="Face Rings",
        description="Split lip and eyelid bones by angle around the mouth or eye, following its edge loops",
        default=False,
    )
    distance_from: bpy.props.EnumProperty(
        name="Distance From",
        description="What each bone's distance to a vertex is measured against",
        items=(
            ('BONE', "Bone", "The whole bone, head to tail"),
            ('POINT', "Point", "Only the bone head, ignoring its length (for small detail bones)"),
        ),
        default='BONE',
    )
    pool_bones: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        if getattr(context.scene, "superskin_is_mask_mode", False):
            self.report({'WARNING'}, "Auto Assign is not available while the Mask row is active")
            return {'CANCELLED'}

        if not self.pool_bones:
            try:
                self.pool_bones = json.dumps(sorted(logic.resolve_pool(CoreFacade(context))))
            except ValueError:
                pass
        pool = json.loads(self.pool_bones) if self.pool_bones else None
        soften_logic.set_options(0.0, 0, self.face_rings, self.distance_from == 'POINT', pool)
        return run_domain_via_unified(context, "weight_toolkit", "auto")


def register():
    bpy.utils.register_class(MESH_OT_auto_assign_closest_unlocked_bone)


def unregister():
    bpy.utils.unregister_class(MESH_OT_auto_assign_closest_unlocked_bone)
