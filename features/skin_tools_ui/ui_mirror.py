"""Mirror — SKINNING-tab widget code.

Moved from ``features/mirror/mirror_feature.py`` (``draw_section()``,
``SUPERSKIN_PT_mirror_options``, ``_draw_sr_body()``) and
``features/mirror/ops.py`` (``SUPERSKIN_UL_mirror_sr`` -- a UIList is
display-only, not an action, so it moved here alongside the rest of the
mirror UI rather than staying behind in `ops.py`, which keeps the real
`superskin.add_mirror_sr`/`superskin.remove_mirror_sr` operators). The
``mirror`` domain itself keeps ``SSPrefMirror``/``SSPrefMirrorSRItem``,
``MirrorPreferencesService``, and ``execute()`` in its own package. See
``docs/domains/mirror.md`` and ``docs/domains/skin_tools_ui.md``.

The "Mirror Weights" action button itself moved again (2026-09-14) into
``ui_action_grid.py``'s combined 2-column grid -- this module now only owns
registering ``SUPERSKIN_UL_mirror_sr``/``SUPERSKIN_PT_mirror_options``,
which ``ui_action_grid.py`` reuses for the gear-icon options popover next to
that button.
"""

import bpy


# ==============================================================================
# UIList — Search/Replace pair rows (moved from mirror/ops.py)
# ==============================================================================

class SUPERSKIN_UL_mirror_sr(bpy.types.UIList):
    bl_idname = "SUPERSKIN_UL_mirror_sr"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "search_text", text="")
        row.prop(item, "replace_text", text="")


def _draw_sr_body(box, context, mirror) -> None:
    sr_coll = mirror.search_replace_pairs
    idx = mirror.search_replace_index

    row = box.row()
    row.template_list(
        "SUPERSKIN_UL_mirror_sr", "",
        mirror, "search_replace_pairs",
        mirror, "search_replace_index",
        rows=4,
    )

    col_btns = row.column(align=True)
    # "Add" must stay enabled even when the list is empty -- it's the
    # only way to recover from an empty list through the UI. Only
    # "Remove" needs a valid selection, so its enabled state is scoped
    # to its own sub-layout rather than the shared column.
    col_btns.operator("superskin.add_mirror_sr", text="", icon='ADD')
    remove_col = col_btns.column(align=True)
    remove_col.enabled = 0 <= idx < len(sr_coll)
    rm = remove_col.operator("superskin.remove_mirror_sr", text="", icon='REMOVE')
    rm.index = idx


# ==============================================================================
# Options popover — direction, axis, mirror data, S/R mapping list
# ==============================================================================

class SUPERSKIN_PT_mirror_options(bpy.types.Panel):
    """Popover content for the Mirror domain's settings, opened from the
    gear icon next to "Mirror Weights". Used to be drawn inline in the
    section body; moved here so the button row stays a single compact row
    with the action first."""
    bl_idname = "SUPERSKIN_PT_mirror_options"
    bl_label = "Mirror Options"
    bl_space_type = 'VIEW_3D'
    # HEADER, not UI -- UI is the N-panel sidebar region itself, so a Panel
    # registered against it (with no bl_category restricting which tab it
    # docks under) gets auto-listed as its own separate docked panel (under
    # a default "Misc" tab) in ADDITION to being invocable via
    # layout.popover(). HEADER-region panels are only ever drawn when
    # explicitly invoked (popover()/menu), never auto-docked -- the same
    # convention Blender's own built-in popovers use (e.g.
    # VIEW3D_PT_shading_lighting).
    bl_region_type = 'HEADER'
    bl_ui_units_x = 16

    def draw(self, context):
        layout = self.layout
        mirror = context.window_manager.superskin_mirror_prefs

        col_opts = layout.column(align=True)
        row_axis = col_opts.split(factor=0.45, align=True)
        row_axis.label(text="Mirror Axis:")
        row_axis.prop(mirror, "mirror_axis", text="")
        col_opts.separator(factor=0.5)
        row_dir = col_opts.split(factor=0.45, align=True)
        row_dir.label(text="Mirror Direction:")
        row_dir.prop(mirror, "direction", text="")
        col_opts.separator(factor=0.5)
        row_data = col_opts.split(factor=0.45, align=True)
        row_data.label(text="Mirror Target Data:")
        row_data.prop(mirror, "mirror_data", text="")
        col_opts.separator(factor=0.5)
        col_opts.prop(mirror, "mirror_all_layers")
        layout.label(text="Mapping Keywords:")
        _draw_sr_body(layout.box(), context, mirror)


# ==============================================================================
# Registration (called from features/skin_tools_ui/__init__.py)
# ==============================================================================

_classes = (
    SUPERSKIN_UL_mirror_sr,
    SUPERSKIN_PT_mirror_options,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
