
import json

import bpy
from bpy_extras.io_utils import ExportHelper, ImportHelper

from ..core.facade import CoreFacade
from .template_ui import SECTION_GAP, settings_row, settings_section
from .utils import keymap_editor

HEADER_SPLIT = 0.55
_KEY_UNITS = 10.0
_RESET_GAP = 0.6
_EYE_GAP = 0.6


def _commit(context):
    context.window_manager.keyconfigs.update()
    context.preferences.is_dirty = True


def draw_shortcut_editor(layout, context):
    wm = context.window_manager
    rows = keymap_editor.shortcut_rows()
    keymap_editor.sync_linked_targets(context, rows)
    hint_defaults = keymap_editor.default_hint_labels()

    header = layout.split(factor=HEADER_SPLIT)
    tabs = header.row(align=True)
    for key, label in keymap_editor.SHORTCUT_TABS:
        tabs.prop_enum(wm, "superskin_shortcut_tab", key, text=label)
    actions = header.row(align=True)
    actions.operator("superskin.import_shortcuts", text="Import", icon='IMPORT')
    actions.operator("superskin.export_shortcuts", text="Export", icon='EXPORT')
    reset_all = actions.row(align=True)
    reset_all.enabled = any(keymap_editor.row_is_modified(context, row) for row in rows)
    reset_all.operator("superskin.reset_shortcut", text="Reset All", icon='LOOP_BACK').label = ""
    layout.separator(factor=0.4)

    drawn = []
    tab = wm.superskin_shortcut_tab
    for index, (group, group_rows) in enumerate(keymap_editor.grouped_rows(rows, tab)):
        if index:
            layout.separator(factor=SECTION_GAP)
        body = settings_section(layout, group, f"superskin_shortcuts_{tab.lower()}_{group.lower().replace(' ', '_')}")
        drawn.append((len(group_rows), body is not None))
        if body is None:
            continue
        col = body.column(align=True)
        for row in group_rows:
            _draw_row(col, context, row, hint_defaults)
    return drawn


def _draw_row(col, context, row, hint_defaults):
    values = settings_row(col, row["label"])
    shown = keymap_editor.is_shown_in_hint(context, row["label"], hint_defaults)
    values.operator("superskin.toggle_hint_row", text="", emboss=False,
                    icon='HIDE_OFF' if shown else 'HIDE_ON').label = row["label"]
    values.separator(factor=_EYE_GAP)
    right = values.row(align=True)
    keys = right.row(align=True)
    keys.ui_units_x = _KEY_UNITS
    editable = bool(row["targets"]) and not row.get("locked")
    kmi = keymap_editor.resolve_target(context, row["targets"][0]) if editable else None
    if kmi is None:
        keys.enabled = False
        keys.alignment = 'CENTER'
        keys.label(text=keymap_editor.fixed_key_text(row["key"]) or "Unavailable")
    else:
        keys.prop(kmi, "type", text="", full_event=True)
    right.separator(factor=_RESET_GAP)
    if row["targets"] and keymap_editor.row_is_modified(context, row):
        right.operator("superskin.reset_shortcut", text="", icon='LOOP_BACK').label = row["label"]
    else:
        right.label(text="", icon='BLANK1')


def _row_kmi(context, label):
    row = next((r for r in keymap_editor.shortcut_rows() if r["label"] == label), None)
    return keymap_editor.resolve_target(context, row["targets"][0]) if row and row["targets"] else None


class SUPERSKIN_OT_toggle_hint_row(bpy.types.Operator):
    """Show or hide this shortcut in the shortcut hint"""
    bl_idname = "superskin.toggle_hint_row"
    bl_label = "Show in Shortcut Hint"
    bl_options = {'INTERNAL'}

    label: bpy.props.StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        shown = keymap_editor.is_shown_in_hint(context, self.label)
        keymap_editor.set_shown_in_hint(context, self.label, not shown)
        CoreFacade.save_prefs()
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
        return {'FINISHED'}


class SUPERSKIN_OT_edit_shortcuts(bpy.types.Operator):
    """Edit SuperSkinPro shortcuts by mode, and export or import them"""
    bl_idname = "superskin.edit_shortcuts"
    bl_label = "Edit Shortcuts"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        return bpy.ops.superskin.open_settings('INVOKE_DEFAULT', tab='SHORTCUTS')


class SUPERSKIN_OT_reset_shortcut(bpy.types.Operator):
    """Restore the default shortcut"""
    bl_idname = "superskin.reset_shortcut"
    bl_label = "Reset Shortcut"
    bl_options = {'INTERNAL'}

    label: bpy.props.StringProperty(options={'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        return "Restore this shortcut's default" if properties.label else "Restore every shortcut's default"

    def invoke(self, context, event):
        if self.label:
            return self.execute(context)
        return context.window_manager.invoke_confirm(
            self, event,
            title="Reset all shortcuts?",
            message="This restores every SuperSkinPro shortcut in all three tabs to its default.",
            confirm_text="Reset All",
        )

    def execute(self, context):
        for row in keymap_editor.shortcut_rows():
            if not self.label or row["label"] == self.label:
                keymap_editor.reset_row(context, row)
        _commit(context)
        return {'FINISHED'}


class SUPERSKIN_OT_export_shortcuts(bpy.types.Operator, ExportHelper):
    """Save every SuperSkinPro shortcut to a JSON file"""
    bl_idname = "superskin.export_shortcuts"
    bl_label = "Export Shortcuts"
    bl_options = {'INTERNAL'}

    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        context.window_manager.keyconfigs.update()
        data = keymap_editor.export_shortcuts(context)
        try:
            with open(self.filepath, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
        except OSError as e:
            self.report({'ERROR'}, f"Could not write shortcut file: {e}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Exported {len(data['bindings'])} shortcuts")
        return {'FINISHED'}


class SUPERSKIN_OT_import_shortcuts(bpy.types.Operator, ImportHelper):
    """Load SuperSkinPro shortcuts from a JSON file"""
    bl_idname = "superskin.import_shortcuts"
    bl_label = "Import Shortcuts"
    bl_options = {'INTERNAL'}

    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        context.window_manager.keyconfigs.update()
        try:
            with open(self.filepath, encoding="utf-8") as fh:
                applied, skipped = keymap_editor.import_shortcuts(context, json.load(fh))
        except (OSError, ValueError) as e:
            self.report({'ERROR'}, f"Could not import shortcuts: {e}")
            return {'CANCELLED'}
        _commit(context)
        message = f"Imported {applied} shortcuts"
        if skipped:
            message += f", skipped {skipped}"
        self.report({'WARNING'} if skipped else {'INFO'}, message)
        return {'FINISHED'}


_classes = [
    SUPERSKIN_OT_edit_shortcuts,
    SUPERSKIN_OT_toggle_hint_row,
    SUPERSKIN_OT_reset_shortcut,
    SUPERSKIN_OT_export_shortcuts,
    SUPERSKIN_OT_import_shortcuts,
]


def register():
    bpy.types.WindowManager.superskin_shortcut_tab = bpy.props.EnumProperty(
        name="",
        items=[(key, label, "") for key, label in keymap_editor.SHORTCUT_TABS],
        default='GENERAL',
        options={'SKIP_SAVE'},
    )
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.WindowManager.superskin_shortcut_tab
