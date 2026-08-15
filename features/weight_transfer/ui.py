import bpy

from . import state_ops


class SUPERSKIN_UL_wt_entries(bpy.types.UIList):
    """One row per SSWeightTransferEntryItem in the unified Source/Target
    list: the mesh's name, an `is_source` radio toggle (RADIOBUT_ON/OFF —
    at most one row across the list may have this set, enforced by
    weight_transfer_feature.py's _on_is_source_changed()), and the row's own
    "Use Selected Vertices Only" toggle. Plain UIList (no SuperSkinListMixin)
    — that mixin's selection-adapter machinery is built for bone/layer lists
    and doesn't apply to a plain object reference list."""
    bl_idname = "SUPERSKIN_UL_wt_entries"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        if item.object:
            row.label(text=item.object.name, icon='MESH_DATA')
        else:
            row.label(text="(missing mesh)", icon='ERROR')
        source_icon = 'RADIOBUT_ON' if item.is_source else 'RADIOBUT_OFF'
        row.prop(item, "is_source", text="", icon=source_icon, toggle=True)
        toggle_icon = 'VERTEXSEL' if item.use_selected_verts else 'OBJECT_DATA'
        row.prop(item, "use_selected_verts", text="", icon=toggle_icon, toggle=True)


def _draw_transfer_tab(layout, context, prefs, state):
    # Reverse half of the list<->viewport selection sync: selecting a mesh
    # in the 3D viewport highlights its row here. Runs once per redraw of
    # this tab (Blender redraws context-sensitive panels on selection
    # change) — see state_ops.sync_active_entry_from_viewport()'s docstring
    # for why this direction is guarded against bouncing back into the
    # list-row-click -> viewport-selection direction.
    state_ops.sync_active_entry_from_viewport(context)

    # ── SOURCE + TARGETS (one unified list, no header label) ────────────
    box = layout.box()
    row = box.row()
    row.template_list(
        "SUPERSKIN_UL_wt_entries", "", state, "entries", state, "active_entry_index", rows=4,
    )
    col = row.column(align=True)
    col.operator("superskin.wt_add_entry", text="", icon='ADD')
    col.operator("superskin.wt_remove_entry", text="", icon='REMOVE')

    layout.separator(factor=0.6)

    # ── SETTINGS (shared with the Import tab) ──────────────────────────
    box_settings = layout.box()
    col = box_settings.column(align=True)
    row = col.split(factor=0.4, align=True)
    row.label(text="Target Layer Data:")
    row.prop(prefs, "insert_method", text="")
    row = col.split(factor=0.4, align=True)
    row.label(text="Method:")
    row.prop(prefs, "transfer_method", text="")
    row_pose = box_settings.split(factor=0.4, align=True)
    row_pose.label(text="Pose Reference:")
    row_pose.prop(prefs, "pose_mode", text="")

    layout.separator(factor=0.6)

    layout.operator("object.mw_copy_skin_weight_maya", text="Transfer", icon='FILE_REFRESH')


def _draw_export_tab(layout, context):
    layout.operator("superskin.export_weight_json", text="Export Weights to JSON...", icon='EXPORT')


def _draw_import_tab(layout, context, prefs):
    box_settings = layout.box()
    col = box_settings.column(align=True)
    row = col.split(factor=0.4, align=True)
    row.label(text="Target Layer Data:")
    row.prop(prefs, "insert_method", text="")
    row = col.split(factor=0.4, align=True)
    row.label(text="Method:")
    row.prop(prefs, "transfer_method", text="")

    layout.separator(factor=0.6)

    layout.operator("superskin.import_weight_json", text="Import Layer to Selected Mesh...", icon='IMPORT')


def draw_transfer_section(layout, context):
    """Entry point for the "Weight Transfer" tool_socket dropdown item.

    Split 2026-08-07 from a single inline Transfer/Export/Import tab bar
    into three independent dropdown entries (see weight_transfer_feature.py's
    WeightTransferFeature/WeightExportFeature/WeightImportFeature) — each
    entry now draws only its own tab's content directly, no tab bar."""
    prefs = context.window_manager.superskin_weight_transfer_prefs
    state = context.scene.superskin_weight_transfer_state
    _draw_transfer_tab(layout, context, prefs, state)


def draw_export_section(layout, context):
    """Entry point for the "Weight Export" tool_socket dropdown item."""
    _draw_export_tab(layout, context)


def draw_import_section(layout, context):
    """Entry point for the "Weight Import" tool_socket dropdown item."""
    prefs = context.window_manager.superskin_weight_transfer_prefs
    _draw_import_tab(layout, context, prefs)
