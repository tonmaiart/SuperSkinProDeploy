"""Standalone operator for the Support Report domain.

Deliberately does NOT go through SUPERSKIN_OT_execute_action /
UnifiedRegistry action dispatch, since that path always constructs
CoreFacade(context), which raises if SuperSkinPro is not Pro-activated or
there is no active mesh object — exactly the situations a user is most
likely to be reporting a problem from. Only CoreFacade's @classmethod
surface (export_support_report) is used here, which requires neither. See
README.md "Why no dispatch actions".
"""

import os

import bpy

from ...core.facade import CoreFacade


def _collect_rig_context(context):
    """Active-object-derived facts only — counts, never names.

    No object/mesh/armature name, no blend filepath — this domain's whole
    point is a report safe to hand to a third party, so nothing that could
    reveal a user's project/character naming ships here by default.
    Returns None when there's no active mesh (never invented).
    """
    obj = context.active_object
    if obj is None or obj.type != 'MESH':
        return None

    bone_count = 0
    for mod in obj.modifiers:
        if mod.type == 'ARMATURE' and mod.object and mod.object.data:
            bone_count = len(mod.object.data.bones)
            break

    return {
        "vertex_count": len(obj.data.vertices),
        "vertex_group_count": len(obj.vertex_groups),
        "bone_count": bone_count,
    }


class SUPERSKIN_OT_export_support_report(bpy.types.Operator):
    """Write a sanitized diagnostic report (environment + log history) to a
    timestamped JSON file under the addon's support_reports/ folder, copy
    the path to the clipboard, and open its containing folder."""
    bl_idname = "superskin.export_support_report"
    bl_label = "Export Diagnostic Report"
    bl_options = {'REGISTER'}

    def execute(self, context):
        rig_context = _collect_rig_context(context)
        path = CoreFacade.export_support_report(rig_context=rig_context)
        context.window_manager.clipboard = path

        try:
            bpy.ops.wm.path_open(filepath=os.path.dirname(path))
        except Exception:
            # Best-effort convenience only -- the path is already reported
            # below and copied to the clipboard regardless.
            pass

        self.report({'INFO'}, f"Diagnostic report created at {path} (path copied to clipboard)")
        return {'FINISHED'}


_classes = [
    SUPERSKIN_OT_export_support_report,
]


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
