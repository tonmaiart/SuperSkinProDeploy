
import json

import bpy
from bpy_extras.io_utils import ExportHelper, ImportHelper

from ..core.facade import CoreFacade
from .ops_shortcut_editor import HEADER_SPLIT
from .utils import themes


def _commit(context):
    CoreFacade.save_prefs()
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()


def draw_themes_header(layout, context):
    header = layout.split(factor=HEADER_SPLIT)
    header.row()
    actions = header.row(align=True)
    actions.operator("superskin.import_theme", text="Import", icon='IMPORT')
    actions.operator("superskin.export_theme", text="Export", icon='EXPORT')
    reset_all = actions.row(align=True)
    reset_all.enabled = not themes.theme_is_default()
    reset_all.operator("superskin.reset_theme", text="Reset All", icon='LOOP_BACK')


class SUPERSKIN_OT_reset_theme(bpy.types.Operator):
    """Restore every SuperSkinPro color to its default"""
    bl_idname = "superskin.reset_theme"
    bl_label = "Reset Theme"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(
            self, event,
            title="Reset all colors?",
            message="This restores every SuperSkinPro color on the Themes tab to its default.",
            confirm_text="Reset All",
        )

    def execute(self, context):
        themes.reset_theme()
        _commit(context)
        return {'FINISHED'}


class SUPERSKIN_OT_export_theme(bpy.types.Operator, ExportHelper):
    """Save every SuperSkinPro color to a JSON file"""
    bl_idname = "superskin.export_theme"
    bl_label = "Export Theme"
    bl_options = {'INTERNAL'}

    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        try:
            with open(self.filepath, "w", encoding="utf-8") as fh:
                json.dump(themes.export_theme(), fh, indent=2)
        except OSError as e:
            self.report({'ERROR'}, f"Could not write theme file: {e}")
            return {'CANCELLED'}
        self.report({'INFO'}, "Exported theme")
        return {'FINISHED'}


class SUPERSKIN_OT_import_theme(bpy.types.Operator, ImportHelper):
    """Load SuperSkinPro colors from a JSON file"""
    bl_idname = "superskin.import_theme"
    bl_label = "Import Theme"
    bl_options = {'INTERNAL'}

    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        try:
            with open(self.filepath, encoding="utf-8") as fh:
                themes.import_theme(json.load(fh))
        except (OSError, ValueError) as e:
            self.report({'ERROR'}, f"Could not import theme: {e}")
            return {'CANCELLED'}
        _commit(context)
        self.report({'INFO'}, "Imported theme")
        return {'FINISHED'}


_classes = (SUPERSKIN_OT_reset_theme, SUPERSKIN_OT_export_theme, SUPERSKIN_OT_import_theme)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
