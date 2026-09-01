# Copyright (c) 2026 Natchapon Srisuk. All rights reserved.
"""Deform Bones List clipboard operators -- thin shells dispatching into
DeformBoneViewerFeature's copy_bone_plane/paste_bone_plane_*/
copy_layer_plane/paste_layer_plane_* actions (see clipboard_logic.py).
Ported from the former Plane Copy tab in features/clipboard -- see
docs/domains/deform_bone_viewer.md.

Paste is one operator per mode (Add/Subtract/Replace) rather than one
operator plus a shared mode dropdown -- each is drawn as its own entry in
one of the two "Clipboard Bone Weight" / "Clipboard Layer Weight"
drop-down menus (ui.py's SUPERSKIN_MT_deform_bone_weight_clipboard /
SUPERSKIN_MT_deform_layer_weight_clipboard), matching the "More" overflow
menu already on this list's sidebar -- a menu item has nowhere to host a
persistent dropdown control, so the mode is baked into which item you
click instead.
"""

import bpy
from ...core.facade import CoreFacade
from ...interface.utils.op_exec import run_domain_via_unified


class SUPERSKIN_OT_deform_copy_bone_weight(bpy.types.Operator):
    bl_idname = "superskin.deform_copy_bone_weight"
    bl_label = "Copy Bone Weight"
    bl_description = "Copy the active Vertex Group's weight densely across the whole mesh, ignoring the current selection"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_system_activated()

    def execute(self, context):
        return run_domain_via_unified(context, "deform_bone_viewer", "copy_bone_plane")


class SUPERSKIN_OT_deform_paste_bone_weight_add(bpy.types.Operator):
    bl_idname = "superskin.deform_paste_bone_weight_add"
    bl_label = "Paste to Bone Weight (Add)"
    bl_description = "Merge the Clipboard Bone Weight clipboard onto the active Vertex Group, adding values up to a ceiling of 1.0"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_system_activated()

    def execute(self, context):
        return run_domain_via_unified(context, "deform_bone_viewer", "paste_bone_plane_add")


class SUPERSKIN_OT_deform_paste_bone_weight_subtract(bpy.types.Operator):
    bl_idname = "superskin.deform_paste_bone_weight_subtract"
    bl_label = "Paste to Bone Weight (Subtract)"
    bl_description = "Deduct the Clipboard Bone Weight clipboard from the active Vertex Group, flooring at 0.0"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_system_activated()

    def execute(self, context):
        return run_domain_via_unified(context, "deform_bone_viewer", "paste_bone_plane_subtract")


class SUPERSKIN_OT_deform_paste_bone_weight_replace(bpy.types.Operator):
    bl_idname = "superskin.deform_paste_bone_weight_replace"
    bl_label = "Paste to Bone Weight (Replace)"
    bl_description = "Overwrite the active Vertex Group with the Clipboard Bone Weight clipboard completely"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_system_activated()

    def execute(self, context):
        return run_domain_via_unified(context, "deform_bone_viewer", "paste_bone_plane_replace")


class SUPERSKIN_OT_deform_copy_layer_weight(bpy.types.Operator):
    bl_idname = "superskin.deform_copy_layer_weight"
    bl_label = "Copy Layer Weight"
    bl_description = "Copy the active layer's mask densely across the whole mesh, ignoring the current selection"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_system_activated()

    def execute(self, context):
        return run_domain_via_unified(context, "deform_bone_viewer", "copy_layer_plane")


class SUPERSKIN_OT_deform_paste_layer_weight_add(bpy.types.Operator):
    bl_idname = "superskin.deform_paste_layer_weight_add"
    bl_label = "Paste to Layer Weight (Add)"
    bl_description = "Merge the Clipboard Layer Weight clipboard onto the active layer's mask, adding values up to a ceiling of 1.0"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_system_activated()

    def execute(self, context):
        return run_domain_via_unified(context, "deform_bone_viewer", "paste_layer_plane_add")


class SUPERSKIN_OT_deform_paste_layer_weight_subtract(bpy.types.Operator):
    bl_idname = "superskin.deform_paste_layer_weight_subtract"
    bl_label = "Paste to Layer Weight (Subtract)"
    bl_description = "Deduct the Clipboard Layer Weight clipboard from the active layer's mask, flooring at 0.0"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_system_activated()

    def execute(self, context):
        return run_domain_via_unified(context, "deform_bone_viewer", "paste_layer_plane_subtract")


class SUPERSKIN_OT_deform_paste_layer_weight_replace(bpy.types.Operator):
    bl_idname = "superskin.deform_paste_layer_weight_replace"
    bl_label = "Paste to Layer Weight (Replace)"
    bl_description = "Overwrite the active layer's mask with the Clipboard Layer Weight clipboard completely"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_system_activated()

    def execute(self, context):
        return run_domain_via_unified(context, "deform_bone_viewer", "paste_layer_plane_replace")


# ==============================================================================
# REGISTRATION
# ==============================================================================

_classes = (
    SUPERSKIN_OT_deform_copy_bone_weight,
    SUPERSKIN_OT_deform_paste_bone_weight_add,
    SUPERSKIN_OT_deform_paste_bone_weight_subtract,
    SUPERSKIN_OT_deform_paste_bone_weight_replace,
    SUPERSKIN_OT_deform_copy_layer_weight,
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
