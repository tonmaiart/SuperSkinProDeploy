"""Weight Apply — draws Add/Scale/Smooth/Sharpen section.

All four row buttons invoke the SAME `superskin.weight_gesture` operator
(ops.py) that the Alt-drag gesture uses -- a plain click is just this
operator's other entry point (`execute()` via `EXEC_DEFAULT`, with
`resolved_action`/`intensity` pre-set here instead of resolved from a
drag). This is what gives a button click the same native "Adjust Last
Operation" redo panel (F9, bottom-left) the gesture already had, instead of
a separate non-adjustable popup.
"""

from .ops import ACTION_TO_GESTURE_PAIR
from .brush import BRUSH_ENABLED
from .brush.brush_ui import draw_brush_row


def draw_section(layout):
    from .weight_apply_feature import get_prefs
    p = get_prefs()

    col = layout.column(align=True)

    _draw_op_row(col, "add", "Add", p, "add_val")
    col.separator(factor=0.6)
    _draw_op_row(col, "scale", "Scale", p, "scale_val")
    col.separator(factor=0.6)
    _draw_op_row(col, "smooth", "Smooth", p, "smooth_val")
    col.separator(factor=0.6)
    _draw_op_row(col, "sharpen", "Sharpen", p, "sharpen_val")

    col.separator(factor=1.0)
    opts = col.column(align=True)
    opts.prop(p, "smooth_affected_only", text="Smooth Affected Only", toggle=False)
    opts.prop(p, "smooth_across_surface", text="Smooth Across Surface", toggle=False)

    if BRUSH_ENABLED:
        draw_brush_row(col)


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
