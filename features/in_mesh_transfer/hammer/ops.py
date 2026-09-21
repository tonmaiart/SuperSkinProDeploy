"""Hammer operator -- thin shell routed through UnifiedRegistry."""

import bpy

from ....core.facade import CoreFacade
from ....interface.utils.op_exec import run_domain_via_unified
from . import logic


class MESH_OT_ssp_inmesh_hammer(bpy.types.Operator):
    """Replace the selected vertices' weights with the average of their surrounding vertices"""
    bl_idname = "mesh.ssp_inmesh_hammer"
    bl_label = "Hammer"
    bl_options = {'REGISTER', 'UNDO'}

    blend: bpy.props.FloatProperty(
        name="Blend", description="0 keeps the current weights, 1 fully replaces them",
        default=1.0, min=0.0, max=1.0, subtype='FACTOR',
    )

    @classmethod
    def poll(cls, context):
        return (CoreFacade.is_system_activated() and
                context.active_object is not None and
                context.active_object.type == 'MESH')

    def execute(self, context):
        logic.set_blend(self.blend)
        return run_domain_via_unified(context, "in_mesh_transfer", "hammer")


def register():
    bpy.utils.register_class(MESH_OT_ssp_inmesh_hammer)


def unregister():
    bpy.utils.unregister_class(MESH_OT_ssp_inmesh_hammer)
