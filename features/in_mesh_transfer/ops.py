"""In-Mesh Transfer operators — thin shells routed through UnifiedRegistry."""

import bpy

from ...core.facade import CoreFacade
from ...interface.utils.op_exec import run_domain_via_unified


class MESH_OT_ssp_inmesh_mark_source(bpy.types.Operator):
    """Mark the currently selected vertices as the Source region for In-Mesh Transfer"""
    bl_idname = "mesh.ssp_inmesh_mark_source"
    bl_label = "Mark Source"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (CoreFacade.is_system_activated() and
                context.active_object is not None and
                context.active_object.type == 'MESH')

    def execute(self, context):
        return run_domain_via_unified(context, "in_mesh_transfer", "mark_source")


class MESH_OT_ssp_inmesh_transfer(bpy.types.Operator):
    """Blend the marked Source region's weight/mask onto the selected Target region, same active Layer"""
    bl_idname = "mesh.ssp_inmesh_transfer"
    bl_label = "Transfer"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (CoreFacade.is_system_activated() and
                context.active_object is not None and
                context.active_object.type == 'MESH')

    def execute(self, context):
        return run_domain_via_unified(context, "in_mesh_transfer", "transfer")


def register():
    bpy.utils.register_class(MESH_OT_ssp_inmesh_mark_source)
    bpy.utils.register_class(MESH_OT_ssp_inmesh_transfer)


def unregister():
    bpy.utils.unregister_class(MESH_OT_ssp_inmesh_transfer)
    bpy.utils.unregister_class(MESH_OT_ssp_inmesh_mark_source)
