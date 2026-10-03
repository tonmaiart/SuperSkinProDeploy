
import bpy

from ....core.facade import CoreFacade
from ....interface.utils.op_exec import run_domain_via_unified


class MESH_OT_ssp_inmesh_mark_source(bpy.types.Operator):
    """Mark the selected vertices as the source to copy weights from"""
    bl_idname = "mesh.ssp_inmesh_mark_source"
    bl_label = "Mark Source"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (CoreFacade.is_system_activated() and
                context.active_object is not None and
                context.active_object.type == 'MESH')

    def execute(self, context):
        return run_domain_via_unified(context, "weight_toolkit", "mark_source")


class MESH_OT_ssp_inmesh_transfer(bpy.types.Operator):
    """Copy weights from the marked source onto the selected vertices"""
    bl_idname = "mesh.ssp_inmesh_transfer"
    bl_label = "Transfer"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (CoreFacade.is_system_activated() and
                context.active_object is not None and
                context.active_object.type == 'MESH')

    def execute(self, context):
        return run_domain_via_unified(context, "weight_toolkit", "transfer")


def register():
    bpy.utils.register_class(MESH_OT_ssp_inmesh_mark_source)
    bpy.utils.register_class(MESH_OT_ssp_inmesh_transfer)


def unregister():
    bpy.utils.unregister_class(MESH_OT_ssp_inmesh_transfer)
    bpy.utils.unregister_class(MESH_OT_ssp_inmesh_mark_source)
