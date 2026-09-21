"""Standalone operator for the Support Report domain."""

import os

import bpy

from ...core.facade import CoreFacade


def _collect_rig_context(context):
    """Active-object-derived facts only — counts, never names."""
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
    """Write a sanitized diagnostic report (environment + log history) to a timestamped JSON
    file under a per-user support_reports/ folder (outside the addon's own installed directory."""
    bl_idname = "superskin.export_support_report"
    bl_label = "Export Diagnostic Report"
    bl_options = {'REGISTER'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(
            self, event,
            title="Export diagnostic report?",
            message="Bundles a sanitized environment + log snapshot to a file and copies its path to the clipboard.",
        )

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
