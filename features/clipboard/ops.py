# Copyright (c) 2026 Natchapon Srisuk. All rights reserved.
import bpy
from ...core.facade import CoreFacade
from ...interface.utils.op_exec import run_domain_via_unified

# ==============================================================================
# VERTEX OPERATORS (Smart Auto-Detect)
# ==============================================================================

class OBJECT_OT_ssp_copy_weight(bpy.types.Operator):
    bl_idname = "object.ssp_copy_weight"
    bl_label = "Copy Weight"
    bl_description = "Copy weight data based on active UI context automatically"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_system_activated()

    def execute(self, context):
        return run_domain_via_unified(context, "clipboard", "copy")


class OBJECT_OT_ssp_copy_weight_whole(bpy.types.Operator):
    bl_idname = "object.ssp_copy_weight_whole"
    bl_label = "Copy (Whole)"
    bl_description = "Copy the entire active Vertex Group/mask, ignoring the current selection (\"Plane Copy\")"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_system_activated()

    def execute(self, context):
        return run_domain_via_unified(context, "clipboard", "copy_whole")


class OBJECT_OT_ssp_copy_weight_single(bpy.types.Operator):
    bl_idname = "object.ssp_copy_weight_single"
    bl_label = "Copy Vertex Influence"
    bl_description = "Copy the influence of exactly one selected vertex (\"Vertex Influence Copy\")"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_system_activated()

    def execute(self, context):
        return run_domain_via_unified(context, "clipboard", "copy_single")


class OBJECT_OT_ssp_cut_weight(bpy.types.Operator):
    bl_idname = "object.ssp_cut_weight"
    bl_label = "Cut Weight"
    bl_description = "Cut weight data based on active UI context automatically"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_system_activated()

    def execute(self, context):
        return run_domain_via_unified(context, "clipboard", "cut")


class OBJECT_OT_ssp_paste_weight_add(bpy.types.Operator):
    bl_idname = "object.ssp_paste_weight_add"
    bl_label = "Paste Weight (Add)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_system_activated()

    def execute(self, context):
        return run_domain_via_unified(context, "clipboard", "paste_add")


class OBJECT_OT_ssp_paste_weight_subtract(bpy.types.Operator):
    bl_idname = "object.ssp_paste_weight_subtract"
    bl_label = "Paste Weight (Subtract)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_system_activated()

    def execute(self, context):
        return run_domain_via_unified(context, "clipboard", "paste_subtract")


class OBJECT_OT_ssp_paste_weight_replace(bpy.types.Operator):
    bl_idname = "object.ssp_paste_weight_replace"
    bl_label = "Paste Weight (Replace)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_system_activated()

    def execute(self, context):
        return run_domain_via_unified(context, "clipboard", "paste_replace")


class OBJECT_OT_ssp_paste_weight_plane(bpy.types.Operator):
    """Single "Paste" button for the Plane Copy tab -- reads the Add/
    Subtract/Replace mode from the tab's own enum (superskin_clipboard_prefs
    .plane_paste_mode) instead of needing three separate buttons."""
    bl_idname = "object.ssp_paste_weight_plane"
    bl_label = "Paste"
    bl_description = "Paste the Plane Copy clipboard using the Add/Subtract/Replace mode selected above"
    bl_options = {'REGISTER', 'UNDO'}

    _ACTION_BY_MODE = {"ADD": "paste_add", "SUBTRACT": "paste_subtract", "REPLACE": "paste_replace"}

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_system_activated()

    def execute(self, context):
        prefs = context.window_manager.superskin_clipboard_prefs
        action = self._ACTION_BY_MODE[prefs.plane_paste_mode]
        return run_domain_via_unified(context, "clipboard", action)


# ==============================================================================
# REGISTRATION
# ==============================================================================

_classes = (
    OBJECT_OT_ssp_copy_weight,
    OBJECT_OT_ssp_copy_weight_whole,
    OBJECT_OT_ssp_copy_weight_single,
    OBJECT_OT_ssp_cut_weight,
    OBJECT_OT_ssp_paste_weight_add,
    OBJECT_OT_ssp_paste_weight_subtract,
    OBJECT_OT_ssp_paste_weight_replace,
    OBJECT_OT_ssp_paste_weight_plane,
)

def register():
    for cls in _classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)