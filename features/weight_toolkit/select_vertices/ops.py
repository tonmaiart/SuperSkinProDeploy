
import bpy

from ....interface.utils.op_exec import run_domain_via_unified
from ....interface.utils.utils import _is_valid_mesh, _has_layer_system
from . import logic


_EXTEND_PROP = bpy.props.BoolProperty(
    name="Extend", description="Add to the current selection instead of replacing it",
    default=False, options={'SKIP_SAVE'},
)


def _poll_layer_mesh(context) -> bool:
    obj = context.active_object
    return obj is not None and obj.type == 'MESH' and _has_layer_system(obj)


class SUPERSKIN_OT_select_affected_by_selected_bones(bpy.types.Operator):
    bl_idname = "superskin.select_affected_by_selected_bones"
    bl_label = "Select Affected Vertices"
    bl_description = "Select the vertices influenced by any of the selected bones. Shift-click to add to the selection"
    bl_options = {'REGISTER', 'UNDO'}

    extend: _EXTEND_PROP

    @classmethod
    def poll(cls, context):
        return _is_valid_mesh(context.active_object)

    def invoke(self, context, event):
        self.extend = event.shift
        return self.execute(context)

    def execute(self, context):
        logic.set_extend(self.extend)
        return run_domain_via_unified(context, "weight_toolkit", "select_bone_affected", self)


class SUPERSKIN_OT_deselect_affected_by_selected_bones(bpy.types.Operator):
    bl_idname = "superskin.deselect_affected_by_selected_bones"
    bl_label = "Deselect Affected Vertices"
    bl_description = "Deselect the vertices influenced by any of the selected bones, keeping the rest of the selection"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _is_valid_mesh(context.active_object)

    def execute(self, context):
        return run_domain_via_unified(context, "weight_toolkit", "deselect_bone_affected", self)


class SUPERSKIN_OT_layer_select_affected_vertices(bpy.types.Operator):
    bl_idname = "superskin.layer_select_affected_vertices"
    bl_label = "Select Affected Vertices"
    bl_description = "Select all vertices covered by the active layer's mask. Shift-click to add to the selection"
    bl_options = {'REGISTER', 'UNDO'}

    extend: _EXTEND_PROP

    @classmethod
    def poll(cls, context):
        return _poll_layer_mesh(context)

    def invoke(self, context, event):
        self.extend = event.shift
        return self.execute(context)

    def execute(self, context):
        logic.set_extend(self.extend)
        return run_domain_via_unified(context, "weight_toolkit", "select_mask_affected", self)


class SUPERSKIN_OT_layer_deselect_affected_vertices(bpy.types.Operator):
    bl_idname = "superskin.layer_deselect_affected_vertices"
    bl_label = "Deselect Affected Vertices"
    bl_description = "Deselect the vertices covered by the active layer's mask, keeping the rest of the selection"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll_layer_mesh(context)

    def execute(self, context):
        return run_domain_via_unified(context, "weight_toolkit", "deselect_mask_affected", self)


_classes = (
    SUPERSKIN_OT_select_affected_by_selected_bones,
    SUPERSKIN_OT_deselect_affected_by_selected_bones,
    SUPERSKIN_OT_layer_select_affected_vertices,
    SUPERSKIN_OT_layer_deselect_affected_vertices,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
