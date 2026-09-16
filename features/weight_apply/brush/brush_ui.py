"""Weight Brush drawing primitives -- Projection/Hardness cycle buttons,
shared by two call sites.

**2026-09-14: N-panel-only composition moved out.** This file used to also
own `draw_tool_select_buttons()`/`draw_tool_row()`/`_draw_brush_settings()`
-- the functions that assembled the N-panel's Brush/Lasso row -- called
from `../ui.py`'s `draw_section()`. Those moved to
`features/skin_tools_ui/ui_weight_apply.py` per the object_tools_ui/
skin_tools_ui migration (see docs/domains/skin_tools_ui.md). What's left
here (`draw_projection_button()`, `draw_hardness_button()`,
`draw_icon_button_row()`, `_ROW_SCALE_Y`, `_ICON_BUTTON_SCALE_X`) stayed
put because `brush_tool.py`'s WorkSpaceTool viewport-header settings
(`draw_settings()`, a DIFFERENT surface from the N-panel) also depends on
them directly within this same package -- moving them would have turned
that dependency into a `skin_tools_ui` → `weight_apply` viewport-tool
import, inverting the intended layering. Both `draw_settings()` and
`ui_weight_apply.py`'s N-panel row keep reading/writing the SAME
`SSPrefWeightBrush` prefs -- see `weight_apply/public_api.py` for how
`ui_weight_apply.py` reaches `draw_projection_button()`/the two scale
constants now that it lives outside this package.

**2026-09-15: Hardness diverges between the two surfaces.** Per two
successive explicit user requests the same day, `ui_weight_apply.py`'s
N-panel column stopped sharing Hardness's drawing code with
`brush_tool.py`'s viewport header: it now calls the new
`draw_hardness_buttons()` (plural, below) -- three separate attached
buttons dispatching the re-added `superskin.set_brush_hardness` operator --
instead of `draw_hardness_button()` (singular, the cycle button). The
viewport header keeps calling `draw_icon_button_row()`/
`draw_hardness_button()` unchanged, so the two surfaces are no longer in
lockstep on this one control specifically -- see
`docs/domains/weight_apply.md`'s "Hardness" section for the full history.

No Mode field anywhere in this UI -- which action a dab performs
(Add/Smooth/Scale/Sharpen) is read live from the held modifier key
(`brush_ops.py::_resolve_mode()`), not a stored setting. No Strength field
either -- a dab's intensity always comes from whichever of the
Add/Scale/Smooth/Sharpen sliders in the N-panel corresponds to the held
modifier (`brush_ops.py::_slider_intensity()`), not an independent
brush-only value.
"""

from ....interface.utils.icons import (
    get_brush_hard_icon_id, get_brush_medium_icon_id, get_brush_soft_icon_id,
    get_surface_projection_icon_id, get_screen_projection_icon_id,
)


def _icon_kwargs(icon_id: int, fallback: str) -> dict:
    """`{"icon_value": ...}` for a loaded custom icon, or a built-in
    `{"icon": fallback}` if it failed to load -- same graceful-fallback
    convention as `object_selector.py::_mesh_state_icon_kwargs()`."""
    return {"icon_value": icon_id} if icon_id else {"icon": fallback}


def draw_projection_button(row, p):
    """Single Surface/Screen cycle button, replacing the old two-item
    `brush_projection` dropdown per explicit request -- shared by the
    N-panel row below and `brush_tool.py`'s viewport header. Shows the
    CURRENT projection's own custom icon; clicking cycles to the other
    (`SUPERSKIN_OT_cycle_brush_projection`, `brush_ops.py`), whose own
    `description()` classmethod names what it will switch TO in the
    tooltip. Plain `operator()`, not `UILayout.prop_enum()` -- same
    custom-`icon_value` requirement as `draw_hardness_button()` below.
    Draws directly into whatever *row* it's given -- no scale of its own
    (see `draw_icon_button_row()` below for why).

    **Label + toggle color (2026-09-15, per explicit user request).** The
    button now shows the CURRENT projection's own name ("Surface"/"Screen")
    as its text instead of being icon-only, and is drawn `depress=True`
    while Screen is active (`depress=False` for Surface) so the toggle
    reads as a genuine two-state color change, not just an icon swap."""
    is_screen = p.brush_projection == 'SCREEN'
    if is_screen:
        icon_kwargs = _icon_kwargs(get_screen_projection_icon_id(), 'RESTRICT_VIEW_OFF')
    else:
        icon_kwargs = _icon_kwargs(get_surface_projection_icon_id(), 'SURFACE_DATA')
    row.operator(
        "superskin.cycle_brush_projection",
        text="Screen" if is_screen else "Surface",
        depress=is_screen,
        **icon_kwargs,
    )


# Icon buttons are also wider than a default button, per explicit request
# -- these are icon-only (no text to anchor a click target to), so a plain
# default-sized square reads much smaller/harder to hit at a glance than a
# labeled slider. WIDTH-only (`scale_x`) is applied to a nested `sub` row
# spanning the Projection button AND the three Hardness buttons
# (`draw_icon_button_row()` below) so all four end up the exact same
# width -- an earlier revision scaled only the Hardness trio in their own
# nested row, leaving Projection at the outer row's default scale and
# visibly smaller/narrower than its neighbors. HEIGHT (`scale_y`) is set
# once on the OUTER row instead (`_draw_brush_settings()`/`brush_tool.py::
# draw_settings()`), covering the Radius slider too -- an earlier revision
# put both scales on this nested `sub` row alone, leaving Radius at the
# row's default height and visibly shorter/thinner than the buttons next
# to it (reported directly from a screenshot).
_ICON_BUTTON_SCALE_X = 1.6
_ROW_SCALE_Y = 1.8


_HARDNESS_ICON_GETTERS = {
    'HARD': ('SHARPCURVE', get_brush_hard_icon_id),
    'MEDIUM': ('SPHERECURVE', get_brush_medium_icon_id),
    'SOFT': ('SMOOTHCURVE', get_brush_soft_icon_id),
}


def draw_hardness_button(row, p):
    """Single Hard/Medium/Soft cycle button, replacing the old three-button
    attached toggle row per explicit request -- shared by the N-panel row
    below and `brush_tool.py`'s viewport header. Mirrors
    `draw_projection_button()` above: shows the CURRENT preset's own custom
    icon; clicking cycles to the next preset
    (`SUPERSKIN_OT_cycle_brush_hardness`, `brush_ops.py`), whose own
    `description()` classmethod names what it will switch TO in the
    tooltip. Draws directly into whatever *row* it's given -- no scale of
    its own (see `draw_icon_button_row()` below for why)."""
    fallback_icon, icon_getter = _HARDNESS_ICON_GETTERS.get(
        p.brush_hardness, _HARDNESS_ICON_GETTERS['HARD'],
    )
    row.operator(
        "superskin.cycle_brush_hardness", text="",
        **_icon_kwargs(icon_getter(), fallback_icon),
    )


def draw_hardness_buttons(row, p):
    """Hard/Medium/Soft as three SEPARATE, attached buttons instead of one
    cycle button (2026-09-15, per explicit user request bringing back the
    original three-button row -- see `docs/domains/weight_apply.md`'s
    "Hardness" section for the full back-and-forth). Each button dispatches
    `superskin.set_brush_hardness` with its own `value`, `depress`d exactly
    when it matches the CURRENT preset -- same visual "pressed-in active
    item" convention `expand=True` gives an `EnumProperty` for free.  Used
    only by `ui_weight_apply.py`'s N-panel column now -- `brush_tool.py`'s
    viewport header still calls `draw_hardness_button()` (singular, the
    cycle button) above, unchanged. Draws directly into whatever *row* it's
    given, same convention as every other primitive in this file."""
    for ident, (fallback_icon, icon_getter) in _HARDNESS_ICON_GETTERS.items():
        op = row.operator(
            "superskin.set_brush_hardness", text="",
            depress=(p.brush_hardness == ident),
            **_icon_kwargs(icon_getter(), fallback_icon),
        )
        op.value = ident


def draw_icon_button_row(row, p):
    """Projection + Hardness as ONE evenly-WIDE, attached button strip -- a
    single nested `sub = row.row(align=True)` carrying `_ICON_BUTTON_
    SCALE_X`, with both `draw_projection_button()` and `draw_hardness_
    button()` drawing into that SAME `sub` (neither scales itself), so both
    icon buttons come out identically sized regardless of which row/layout
    calls this. Shared by the N-panel row (`_draw_brush_settings()`
    below) and `brush_tool.py`'s viewport header so both stay visually in
    lockstep. Height is NOT set here -- see `_ROW_SCALE_Y`'s comment above
    for why that lives on the OUTER row instead, one level up."""
    sub = row.row(align=True)
    sub.scale_x = _ICON_BUTTON_SCALE_X
    draw_projection_button(sub, p)
    draw_hardness_button(sub, p)
