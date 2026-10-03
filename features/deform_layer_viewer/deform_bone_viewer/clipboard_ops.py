
import bpy
from ....core.facade import CoreFacade
from ....interface.utils.op_exec import run_domain_via_unified


class SUPERSKIN_OT_deform_copy_bone_weight(bpy.types.Operator):
    bl_idname = "superskin.deform_copy_bone_weight"
    bl_label = "Copy Bone Weight"
    bl_description = "Copy the active bone's weights on the whole mesh"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_system_activated()

    def execute(self, context):
        return run_domain_via_unified(context, "deform_layer_viewer", "copy_bone_plane")


class SUPERSKIN_OT_deform_cut_bone_weight(bpy.types.Operator):
    bl_idname = "superskin.deform_cut_bone_weight"
    bl_label = "Cut Bone Weight"
    bl_description = "Copy the active bone's weights on the whole mesh, then clear them"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_system_activated()

    def execute(self, context):
        return run_domain_via_unified(context, "deform_layer_viewer", "cut_bone_plane")


class SUPERSKIN_OT_deform_paste_bone_weight_add(bpy.types.Operator):
    bl_idname = "superskin.deform_paste_bone_weight_add"
    bl_label = "Paste to Bone Weight (Add)"
    bl_description = "Add the copied weights to the active bone (up to 1.0)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_system_activated()

    def execute(self, context):
        return run_domain_via_unified(context, "deform_layer_viewer", "paste_bone_plane_add")


class SUPERSKIN_OT_deform_paste_bone_weight_subtract(bpy.types.Operator):
    bl_idname = "superskin.deform_paste_bone_weight_subtract"
    bl_label = "Paste to Bone Weight (Subtract)"
    bl_description = "Subtract the copied weights from the active bone (down to 0.0)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_system_activated()

    def execute(self, context):
        return run_domain_via_unified(context, "deform_layer_viewer", "paste_bone_plane_subtract")


class SUPERSKIN_OT_deform_paste_bone_weight_replace(bpy.types.Operator):
    bl_idname = "superskin.deform_paste_bone_weight_replace"
    bl_label = "Paste to Bone Weight (Replace)"
    bl_description = "Replace the active bone's weights with the copied weights"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_system_activated()

    def execute(self, context):
        return run_domain_via_unified(context, "deform_layer_viewer", "paste_bone_plane_replace")


class SUPERSKIN_OT_deform_copy_layer_weight(bpy.types.Operator):
    bl_idname = "superskin.deform_copy_layer_weight"
    bl_label = "Copy Layer Weight"
    bl_description = "Copy the active layer's mask on the whole mesh"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_system_activated()

    def execute(self, context):
        return run_domain_via_unified(context, "deform_layer_viewer", "copy_layer_plane")


class SUPERSKIN_OT_deform_cut_layer_weight(bpy.types.Operator):
    bl_idname = "superskin.deform_cut_layer_weight"
    bl_label = "Cut Layer Weight"
    bl_description = "Copy the active layer's mask on the whole mesh, then clear it"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_system_activated()

    def execute(self, context):
        return run_domain_via_unified(context, "deform_layer_viewer", "cut_layer_plane")


class SUPERSKIN_OT_deform_paste_layer_weight_add(bpy.types.Operator):
    bl_idname = "superskin.deform_paste_layer_weight_add"
    bl_label = "Paste to Layer Weight (Add)"
    bl_description = "Add the copied weights to the active layer's mask (up to 1.0)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_system_activated()

    def execute(self, context):
        return run_domain_via_unified(context, "deform_layer_viewer", "paste_layer_plane_add")


class SUPERSKIN_OT_deform_paste_layer_weight_subtract(bpy.types.Operator):
    bl_idname = "superskin.deform_paste_layer_weight_subtract"
    bl_label = "Paste to Layer Weight (Subtract)"
    bl_description = "Subtract the copied weights from the active layer's mask (down to 0.0)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_system_activated()

    def execute(self, context):
        return run_domain_via_unified(context, "deform_layer_viewer", "paste_layer_plane_subtract")


class SUPERSKIN_OT_deform_paste_layer_weight_replace(bpy.types.Operator):
    bl_idname = "superskin.deform_paste_layer_weight_replace"
    bl_label = "Paste to Layer Weight (Replace)"
    bl_description = "Replace the active layer's mask with the copied weights"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_system_activated()

    def execute(self, context):
        return run_domain_via_unified(context, "deform_layer_viewer", "paste_layer_plane_replace")



_classes = (
    SUPERSKIN_OT_deform_copy_bone_weight,
    SUPERSKIN_OT_deform_cut_bone_weight,
    SUPERSKIN_OT_deform_paste_bone_weight_add,
    SUPERSKIN_OT_deform_paste_bone_weight_subtract,
    SUPERSKIN_OT_deform_paste_bone_weight_replace,
    SUPERSKIN_OT_deform_copy_layer_weight,
    SUPERSKIN_OT_deform_cut_layer_weight,
    SUPERSKIN_OT_deform_paste_layer_weight_add,
    SUPERSKIN_OT_deform_paste_layer_weight_subtract,
    SUPERSKIN_OT_deform_paste_layer_weight_replace,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
