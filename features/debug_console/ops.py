"""Standalone operators for the Debug Console domain.

These deliberately do NOT go through SUPERSKIN_OT_execute_action /
UnifiedRegistry action dispatch, since that path always constructs
CoreFacade(context), which raises if SuperSkinPro is not Pro-activated or
there is no active mesh object -- exactly the situations where a debug tool
is most needed. Only CoreFacade's @classmethod surface is used here, which
requires neither. See README.md "Why no dispatch actions".
"""

import bpy

from ...core.facade import CoreFacade
from .debug_console_feature import get_visible_entries


class SUPERSKIN_OT_copy_debug_log(bpy.types.Operator):
    """Copy the currently filtered (visible-categories + search) debug log to the clipboard as plain text."""
    bl_idname = "superskin.copy_debug_log"
    bl_label = "Copy Debug Log"
    bl_options = {'REGISTER'}

    def execute(self, context):
        entries = get_visible_entries(context)
        if not entries:
            self.report({'INFO'}, "No log entries to copy")
            return {'CANCELLED'}

        report = "\n".join(
            f"[{e['timestamp']}][{e['category'].upper()}] {e['message']}"
            for e in entries
        )
        context.window_manager.clipboard = report
        self.report({'INFO'}, f"Copied {len(entries)} log entries to clipboard")
        return {'FINISHED'}


class SUPERSKIN_OT_clear_debug_log(bpy.types.Operator):
    """Discard all buffered debug log entries."""
    bl_idname = "superskin.clear_debug_log"
    bl_label = "Clear Debug Log"
    bl_options = {'REGISTER'}

    def execute(self, context):
        CoreFacade.clear_debug_logs()
        context.area.tag_redraw()
        return {'FINISHED'}


_classes = [
    SUPERSKIN_OT_copy_debug_log,
    SUPERSKIN_OT_clear_debug_log,
]


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
