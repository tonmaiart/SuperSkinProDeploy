"""Weight Transfer / Weight Export / Weight Import — LAYER-tab widget code.

Moved from ``features/weight_transfer/ui.py`` (2026-09-14) — the
``weight_transfer``/``weight_export``/``weight_import`` domains (three
``UnifiedFeatureExtension`` classes sharing one package) keep their
``execute()``/CoreFacade logic, ``SSPrefWeightTransfer``/
``SSWeightTransferEntryItem``/``SSWeightTransferState`` PropertyGroups, and
``state_ops.py``/``transfer_core``/``ops.py``/``io_ops.py`` in their own
package; only this drawing code (plus the ``SUPERSKIN_UL_wt_entries``
UIList, display-only) moved here. Reads the reverse list<->viewport
selection sync via the domain's new ``public_api.py``. See
``docs/domains/weight_transfer.md`` and ``docs/domains/skin_tools_ui.md``.

**Import/Export combined into one 2-column row (2026-09-15):** per explicit
user request, ``weight_import``/``weight_export`` no longer draw as two
separate collapsible sections -- ``draw_import_export_section()`` draws both
in one unlabeled row instead (Import left, with a gear-icon settings
popover; Export right, still a bare button since it has no configurable
settings). ``draw_export_section()``/``draw_import_section()`` and their
``_draw_export_tab()``/``_draw_import_tab()`` helpers are removed. See
``docs/domains/object_tools_ui.md``'s "Import / Export combined row" section.

**Transfer's inline settings box moved to a gear popover too (2026-09-15,
same day, second round):** per a follow-up explicit user request, the
Transfer button's own inline Method/Keep Old Layer Data box was replaced
with the same gear-icon popover pattern, reusing
``SUPERSKIN_PT_weight_transfer_options`` (the same class the Import button
already uses) rather than a second, duplicate popover class -- both buttons
read/write the identical ``SSPrefWeightTransfer`` fields.
"""

import bpy

from ..weight_transfer.public_api import sync_active_entry_from_viewport


# ==============================================================================
# UIList — unified Source/Target list
# ==============================================================================

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


# ==============================================================================
# Options popover — Method / Keep Old Layer Data, shared by Transfer AND
# the combined Import/Export row
# ==============================================================================

class SUPERSKIN_PT_weight_transfer_options(bpy.types.Panel):
    """Popover content shared by the "Transfer" button (Weight Transfer
    section) and the "Import" button (combined Import/Export row) — both
    read/write the same SSPrefWeightTransfer fields (Method / Keep Old
    Layer Data), so one popover class serves both gear icons instead of
    duplicating identical content. Used to be drawn inline as a settings
    box above each button; moved into a popover so the button row stays a
    single compact row, matching the Mirror section's
    SUPERSKIN_PT_mirror_options idiom (features/skin_tools_ui/ui_mirror.py).
    Export has no popover at all — it has nothing to configure (see
    docs/domains/weight_transfer.md's "No configurable export settings")."""
    bl_idname = "SUPERSKIN_PT_weight_transfer_options"
    bl_label = "Weight Transfer Options"
    bl_space_type = 'VIEW_3D'
    # HEADER, not UI -- see SUPERSKIN_PT_mirror_options's docstring
    # (features/skin_tools_ui/ui_mirror.py) for why a popover-only Panel
    # must use the HEADER region.
    bl_region_type = 'HEADER'
    bl_ui_units_x = 14

    def draw(self, context):
        layout = self.layout
        prefs = context.window_manager.superskin_weight_transfer_prefs

        col = layout.column(align=True)
        row = col.split(factor=0.4, align=True)
        row.label(text="Method:")
        row.prop(prefs, "transfer_method", text="")
        col.prop(prefs, "keep_old_layer_data")


# ==============================================================================
# Drawing entry points
# ==============================================================================

def _draw_transfer_tab(layout, context, state):
    # Reverse half of the list<->viewport selection sync: selecting a mesh
    # in the 3D viewport highlights its row here. Runs once per redraw of
    # this tab (Blender redraws context-sensitive panels on selection
    # change) — see state_ops.sync_active_entry_from_viewport()'s docstring
    # for why this direction is guarded against bouncing back into the
    # list-row-click -> viewport-selection direction.
    sync_active_entry_from_viewport(context)

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

    # ── TRANSFER + SETTINGS (gear popover, shared with the Import row) ──
    row = layout.row(align=True)
    row.operator("object.mw_copy_skin_weight_maya", text="Transfer", icon='FILE_REFRESH')
    row.popover(SUPERSKIN_PT_weight_transfer_options.bl_idname, text="", icon='PREFERENCES')


def draw_transfer_section(layout, context):
    """Entry point for the "Weight Transfer" section."""
    state = context.scene.superskin_weight_transfer_state
    _draw_transfer_tab(layout, context, state)


# ==============================================================================
# Import / Export — combined 2-column row
# ==============================================================================
# weight_import (left) / weight_export (right) no longer draw as two separate
# collapsible sections -- per explicit user request (2026-09-15), their
# buttons are combined into one unlabeled row split into two columns, same
# no-chrome pattern as skin_tools_ui's ui_action_grid.py combined grid. Import
# reuses SUPERSKIN_PT_weight_transfer_options (defined above, shared with the
# Transfer button) for its gear-icon settings popover -- Export still has
# nothing to configure (see docs/domains/weight_transfer.md's "No
# configurable export settings"), so it stays a bare button. See
# object_tools_ui_feature.py's _MERGED_ROW_DOMAIN_IDS/_ROW_ANCHOR_DOMAIN_ID.

def draw_import_export_section(layout, context):
    """Entry point for the combined Weight Import / Weight Export row --
    left column: Import button + gear-icon settings popover; right column:
    Export button. Drawn once, at weight_import's/weight_export's shared
    slot in the LAYER tab -- see object_tools_ui_feature.py."""
    split = layout.split(factor=0.5, align=True)

    import_cell = split.row(align=True)
    import_cell.operator("superskin.import_weight_json", text="Import", icon='IMPORT')
    import_cell.popover(SUPERSKIN_PT_weight_transfer_options.bl_idname, text="", icon='PREFERENCES')

    export_cell = split.row(align=True)
    export_cell.operator("superskin.export_weight_json", text="Export", icon='EXPORT')


# ==============================================================================
# Registration (called from features/object_tools_ui/__init__.py)
# ==============================================================================

_classes = (
    SUPERSKIN_UL_wt_entries,
    SUPERSKIN_PT_weight_transfer_options,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
