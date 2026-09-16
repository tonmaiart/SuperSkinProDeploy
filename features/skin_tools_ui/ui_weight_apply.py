"""Weight Apply — SKINNING-tab widget code.

Moved from ``features/weight_apply/ui.py`` and the N-panel-only half of
``features/weight_apply/brush/brush_ui.py`` (``draw_tool_select_buttons()``/
``_draw_brush_settings()``) (2026-09-14) — the
``weight_apply`` domain keeps its ``execute()``/CoreFacade logic,
``SSPrefWeightApply``/``SSPrefWeightBrush`` PropertyGroups, viewport draw
handlers/keymaps, and the brush-primitive functions that
``brush/brush_tool.py``'s viewport-header settings also depends on
(``draw_icon_button_row()``, ``draw_projection_button()``,
``draw_hardness_button()``) in its own package — see
``weight_apply/brush/brush_ui.py``'s own docstring for why those stayed put
instead of moving here too. This file's own Hardness row diverged from that
shared code (2026-09-15) — it calls ``draw_hardness_buttons()`` (plural,
three separate buttons) instead, see ``_draw_brush_settings()`` below.
PropertyGroup access reads
``context.window_manager.superskin_weight_apply_prefs`` /
``superskin_weight_brush_prefs`` directly (plain WindowManager attributes,
no import needed). Everything else this code needs from `weight_apply`'s
own package is reached through the domain's ``public_api.py``. See
``docs/domains/weight_apply.md`` and ``docs/domains/skin_tools_ui.md``.

All four Add/Scale/Smooth/Sharpen row buttons invoke the SAME
`superskin.weight_gesture` operator (`weight_apply/ops.py`) that the
Alt-drag gesture uses -- a plain click is just this operator's other entry
point (`execute()` via `EXEC_DEFAULT`, with `resolved_action`/`intensity`
pre-set here instead of resolved from a drag). This is what gives a button
click the same native "Adjust Last Operation" redo panel (F9, bottom-left)
the gesture already had, instead of a separate non-adjustable popup.
"""

from ...interface.utils.icons import get_tool_brush_icon_id, get_tool_lasso_icon_id
from ...interface.utils.mode_edit_toggle import draw_edit_mask_button
from ..weight_apply.public_api import (
    BRUSH_ENABLED, ACTION_TO_GESTURE_PAIR,
    get_active_weight_tool_idname, WEIGHT_BRUSH_TOOL_IDNAME,
    draw_projection_button, draw_hardness_buttons,
    BRUSH_ICON_BUTTON_SCALE_X,
)


# Slight height bump for the Brush/Lasso, "Edit Mask", Hardness and
# Projection button rows only (2026-09-16, per explicit user request) --
# NOT the shared `brush_ui._ROW_SCALE_Y` (1.8x, still used as-is by
# `brush_tool.py`'s viewport-header row); this is a smaller, local-only
# bump kept separate so the two surfaces can keep diverging independently.
_TOOL_BUTTON_SCALE_Y = 1.15


def _icon_kwargs(icon_id: int, fallback: str) -> dict:
    """`{"icon_value": ...}` for a loaded custom icon, or a built-in
    `{"icon": fallback}` if it failed to load -- mirrors
    `weight_apply/brush/brush_ui.py`'s own `_icon_kwargs()` (kept private to
    that module, so duplicated here rather than exposed publicly for such a
    small, pure helper)."""
    return {"icon_value": icon_id} if icon_id else {"icon": fallback}


# =========================================================================
#  Add/Scale/Smooth/Sharpen section (moved from weight_apply/ui.py)
# =========================================================================

def draw_section(layout, context) -> None:
    p = context.window_manager.superskin_weight_apply_prefs

    # "Apply Tools" layout -- two-column split, 30% left / 70% right
    # (2026-09-15, per explicit user request; was a plain 50/50 split
    # before). Left column: the brush tool-select button sharing a row
    # with "Edit Mask" (moved here from deform_layer_viewer's own row --
    # see that domain's `_draw_combined()`, which now skips it on the
    # SKINNING tab so it isn't drawn twice), then -- while Weight Brush is
    # active -- Hardness, Radius and Projection each on their own row, in
    # that order (see `_draw_tool_column()`'s own docstring for the
    # 2026-09-16 compaction pass and the later same-day reorder/height
    # bump). Right column keeps the Add/Scale/Smooth/Sharpen rows and the
    # "Smooth Affected Only" checkbox exactly as before.
    layout_apply_tools = layout.split(factor=0.4)
    col1 = layout_apply_tools.column()
    col2 = layout_apply_tools.column(align=True)

    _draw_tool_column(col1, context)

    _draw_op_row(col2, "add", "Add", p, "add_val")
    col2.separator(factor=0.6)
    _draw_op_row(col2, "scale", "Scale", p, "scale_val")
    col2.separator(factor=0.6)
    _draw_op_row(col2, "smooth", "Smooth", p, "smooth_val")
    col2.separator(factor=0.6)
    _draw_op_row(col2, "sharpen", "Sharpen", p, "sharpen_val")

    col2.separator(factor=1.0)
    opts = col2.row(align=True)
    opts.alignment = 'RIGHT'
    opts.prop(p, "smooth_affected_only", text="Smooth Affected Only", toggle=False)


def _draw_tool_column(col, context) -> None:
    """Left column of the "Apply Tools" layout: the brush/lasso tool-select
    button and "Edit Mask" share ONE row, then -- only while Weight Brush is
    the active tool -- Hardness, Radius and Projection each on their own row
    below, in that order (`_draw_brush_settings()`). The whole column is
    wrapped in a `box()` so it reads as one grouped control cluster against
    the right column's plain rows.

    **Compacted (2026-09-16, per explicit user request to shrink this box's
    height by roughly half):** no more bottom filler row padding the box out
    to approximately match the right column's height -- the box now just
    sizes to its own content, so it reads noticeably shorter than the right
    column whenever brush settings are hidden or when Lasso Select is the
    active tool. `tool_row` also dropped the shared `_ROW_SCALE_Y` (1.8x)
    bump it used to take from `brush_ui.py` -- that constant is still used
    as-is by `brush_tool.py`'s viewport-header settings row, this is a
    local-only reduction. See `_draw_brush_settings()` below for the
    Projection/Hardness reshuffle that followed.

    **Button height nudged back up slightly (2026-09-16, same day, per a
    LATER explicit user request):** `tool_row` (Brush/Lasso toggle + "Edit
    Mask") carries a small `scale_y` bump again -- not the old full `1.8x`
    `_ROW_SCALE_Y`, just `_TOOL_BUTTON_SCALE_Y` (see module constant below),
    enough to make the buttons easier to hit without giving back most of the
    height this box just shed.

    **`tool_row`'s own top/bottom margin removed (2026-09-16, SAME DAY, per
    a STILL LATER explicit user request):** the `separator(factor=0.2)` that
    used to sit between the box's top edge and `tool_row`, and the
    `separator(factor=0.3)` that used to sit between `tool_row` and
    `_draw_brush_settings()`, are both gone -- `tool_row` now sits flush
    against the box's top edge and flush against the settings block below
    it. The box's own bottom-edge `separator(factor=0.2)` is unaffected (it
    is the box's own closing padding, not specific to `tool_row`)."""
    col = col.box()

    tool_row = col.row(align=True)
    tool_row.scale_y = _TOOL_BUTTON_SCALE_Y
    if BRUSH_ENABLED:
        tool_sub = tool_row.row(align=True)
        tool_sub.scale_x = BRUSH_ICON_BUTTON_SCALE_X
        draw_tool_select_buttons(tool_sub, context)
    draw_edit_mask_button(
        tool_row, context,
        enter_edit_idname="superskin.enter_layer_edit",
        edit_mask_idname="superskin.toggle_mask_mode",
        edit_mask_text="Mask",
    )

    brush_settings_shown = (
        BRUSH_ENABLED and get_active_weight_tool_idname(context) == WEIGHT_BRUSH_TOOL_IDNAME
    )
    if brush_settings_shown:
        _draw_brush_settings(col, context)

    col.separator(factor=0.2)


def _draw_op_row(col, action, label, p, val_prop):
    split = col.split(factor=0.25, align=True)
    split.scale_y = 1.2
    # `superskin.weight_gesture` also defines invoke()/modal() for the
    # Alt-drag gesture -- layout.operator() defaults to INVOKE_DEFAULT
    # context, which would call invoke() here too and start a modal drag
    # waiting for mouse movement instead of applying immediately. Forcing
    # EXEC_DEFAULT makes a plain click call execute() directly, applying
    # `intensity` right away, exactly like the old dedicated per-action
    # operators did.
    split.operator_context = 'EXEC_DEFAULT'
    op = split.operator("superskin.weight_gesture", text=label)
    op.action = ACTION_TO_GESTURE_PAIR[action]
    op.resolved_action = action
    op.intensity = getattr(p, val_prop)
    split.prop(p, val_prop, text="", slider=True)


# =========================================================================
#  Brush toolbar row (moved from weight_apply/brush/brush_ui.py)
# =========================================================================

def draw_tool_select_buttons(row, context):
    """Single Brush/Lasso cycle button -- lets the user switch the active
    3D-viewport Toolbar tool (Weight Brush <-> Lasso Select) straight from
    the N-panel instead of opening the Toolbar. Shows the CURRENTLY active
    tool's own icon; clicking switches to the OTHER one
    (`superskin.toggle_weight_brush_tool`, `weight_apply/brush/brush_tool.py`'s
    `SUPERSKIN_OT_toggle_weight_brush_tool` -- the same operator its Alt+1
    keymap already uses)."""
    active_idname = get_active_weight_tool_idname(context)
    if active_idname == WEIGHT_BRUSH_TOOL_IDNAME:
        icon_kwargs = _icon_kwargs(get_tool_brush_icon_id(), 'BRUSH_DATA')
    else:
        icon_kwargs = _icon_kwargs(get_tool_lasso_icon_id(), 'RESTRICT_SELECT_OFF')
    row.operator("superskin.toggle_weight_brush_tool", text="", **icon_kwargs)


def _draw_brush_settings(col, context):
    """Hardness, then Radius, then Projection, each on its own row
    (2026-09-16, per explicit user request moving Projection out from
    sharing Hardness's row to its OWN row below Radius -- previously
    Projection+Hardness shared one row with Radius below both; see
    `docs/domains/weight_apply.md`'s "Two-column layout"/"Hardness" notes
    for the fuller back-and-forth this settings block has been through).
    `draw_projection_button()` is the exact same primitive `brush_tool.py`'s
    viewport-header settings uses. Hardness still calls
    `draw_hardness_buttons()` (plural -- three separate Hard/Medium/Soft
    buttons, not the single cycle button the viewport header keeps using).
    `hardness_row` and `projection_row` both carry `_TOOL_BUTTON_SCALE_Y`
    (a slight height bump, same request); `radius_row` (a labeled slider,
    not icon buttons) is unaffected."""
    p = context.window_manager.superskin_weight_brush_prefs

    hardness_row = col.row(align=True)
    hardness_row.scale_x = BRUSH_ICON_BUTTON_SCALE_X
    hardness_row.scale_y = _TOOL_BUTTON_SCALE_Y
    draw_hardness_buttons(hardness_row, p)

    radius_row = col.row(align=True)
    radius_row.prop(p, "brush_radius", text="Radius", slider=True)

    projection_row = col.row(align=True)
    projection_row.scale_x = BRUSH_ICON_BUTTON_SCALE_X
    projection_row.scale_y = _TOOL_BUTTON_SCALE_Y
    draw_projection_button(projection_row, p)
