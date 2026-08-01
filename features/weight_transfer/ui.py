import bpy


class SUPERSKIN_PT_weight_transfer_options(bpy.types.Panel):
    """Popover content for the Weight Transfer domain's settings (Layer
    Output, Insert Method, Transfer Method, Auto Assign Modifier on Import),
    opened from the gear icon next to "Transfer Weight to Selected Mesh".
    Defined here (not weight_transfer_feature.py) so weight_transfer_feature
    -- which already imports this module -- can register it via
    `ui.SUPERSKIN_PT_weight_transfer_options` without this module needing an
    import back the other way."""
    bl_idname = "SUPERSKIN_PT_weight_transfer_options"
    bl_label = "Weight Transfer Options"
    bl_space_type = 'VIEW_3D'
    # HEADER, not UI -- UI is the N-panel sidebar region itself, so a Panel
    # registered against it (with no bl_category restricting which tab it
    # docks under) gets auto-listed as its own separate docked panel (under
    # a default "Misc" tab) in ADDITION to being invocable via
    # layout.popover(). See features/mirror/README.md /
    # features/debug_console/README.md for the bug this avoids.
    bl_region_type = 'HEADER'

    def draw(self, context):
        layout = self.layout
        prefs = context.window_manager.superskin_weight_transfer_prefs

        # ── SECTION: TRANSFER / IMPORT (shared by object.mw_copy_skin_weight_maya
        # and superskin.import_weight_json — both run the same transfer_core engine)
        box_shared = layout.box()
        box_shared.label(text="Transfer / Import", icon='FILE_REFRESH')

        col = box_shared.column(align=True)
        row = col.split(factor=0.4, align=True)
        row.label(text="Layer Output:")
        row.prop(prefs, "layer_output", text="")

        row = col.split(factor=0.4, align=True)
        row.label(text="Insert Method:")
        row.prop(prefs, "insert_method", text="")

        row = col.split(factor=0.4, align=True)
        row.label(text="Transfer Method:")
        row.prop(prefs, "transfer_method", text="")

        layout.separator(factor=0.6)

        # ── SECTION: TRANSFER (object.mw_copy_skin_weight_maya only)
        box_transfer = layout.box()
        box_transfer.label(text="Transfer", icon='MOD_DATA_TRANSFER')
        box_transfer.prop(prefs, "use_selected_source_verts", toggle=False)
        box_transfer.prop(prefs, "use_selected_target_verts", toggle=False)

        row_pose = box_transfer.split(factor=0.4, align=True)
        row_pose.label(text="Pose Reference:")
        row_pose.prop(prefs, "pose_mode", text="")

        layout.separator(factor=0.6)

        # ── SECTION: IMPORT (superskin.import_weight_json only)
        box_import = layout.box()
        box_import.label(text="Import", icon='IMPORT')
        box_import.prop(prefs, "auto_assign_armature", toggle=False)


def draw_section(layout, context):
    row = layout.row(align=True)
    row.scale_y = 1.2
    row.operator("object.mw_copy_skin_weight_maya", text="Transfer")
    row.operator("superskin.export_weight_json", text="Export")
    row.operator("superskin.import_weight_json", text="Import")
    row.popover(SUPERSKIN_PT_weight_transfer_options.bl_idname, text="", icon='PREFERENCES')
