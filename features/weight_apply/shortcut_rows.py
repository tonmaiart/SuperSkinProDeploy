
from ...interface.utils import session_shortcuts
from .brush_tool.brush_tool import SuperSkinWeightBrushTool
from .tool_visibility import tool_keymap_name
from .vertex_tool.circle_select import CIRCLE_SELECT_ENABLED
from .vertex_tool.select_tool import SuperSkinWeightSelectTool

_SESSION_KEYMAP = session_shortcuts.KEYMAP_NAME


def _row(tab, group, label, *targets):
    return {"tab": tab, "group": group, "label": label, "targets": list(targets), "key": ""}


def _fixed(tab, group, label, key):
    return {"tab": tab, "group": group, "label": label, "targets": [], "key": key}


def shortcut_rows():
    brush = tool_keymap_name(SuperSkinWeightBrushTool)
    select = tool_keymap_name(SuperSkinWeightSelectTool)
    rows = [
        _row('GENERAL', "Edit Weight", "Switch Brush / Vertex Tool",
             (_SESSION_KEYMAP, "superskin.toggle_weight_brush_tool", 0)),
        _row('GENERAL', "Edit Weight", "Repeat Last Weight Apply",
             (_SESSION_KEYMAP, "superskin.repeat_last_weight_apply", 0)),

        _fixed('BRUSH', "Paint", "Paint: Add", "LMB"),
        _fixed('BRUSH', "Paint", "Paint: Smooth", "Shift+LMB"),
        _fixed('BRUSH', "Paint", "Paint: Scale", "Ctrl+LMB"),
        _fixed('BRUSH', "Paint", "Paint: Sharpen", "Alt+LMB"),
        _row('BRUSH', "Brush", "Change Brush Size", (brush, "superskin.weight_brush_adjust_radius", 0)),
        _row('BRUSH', "Brush", "Toggle Projected", (brush, "superskin.toggle_brush_projected", 0)),
        _fixed('BRUSH', "Common", "Brush Options", "RMB"),

        dict(_row('VERTEX', "Selection", "Select Linked", (select, "superskin.weight_select_linked", 0)),
             key="Double Click", locked=True),
        _row('VERTEX', "Selection", "Select Linked from Selection",
             (select, "superskin.weight_select_linked_selected", 0)),
        _row('VERTEX', "Selection", "Select All", (select, "superskin.weight_select_all", 0)),
        _row('VERTEX', "Selection", "Deselect All", (select, "superskin.weight_select_none", 0)),
        _row('VERTEX', "Selection", "Invert Selection", (select, "superskin.weight_select_invert", 0)),
        _row('VERTEX', "Selection", "Grow Selection", (_SESSION_KEYMAP, "superskin.grow_selection", 0)),
        _row('VERTEX', "Selection", "Shrink Selection", (_SESSION_KEYMAP, "superskin.shrink_selection", 0)),
        _row('VERTEX', "Selection", "Grow Selection (Numpad)", (select, "superskin.grow_selection", 0)),
        _row('VERTEX', "Selection", "Shrink Selection (Numpad)", (select, "superskin.shrink_selection", 0)),
        _row('VERTEX', "Hide", "Hide Selected Vertices", (_SESSION_KEYMAP, "superskin.weight_hide_selected", 0)),
        _row('VERTEX', "Hide", "Hide Unselected Vertices", (_SESSION_KEYMAP, "superskin.weight_hide_unselected", 0)),
        _row('VERTEX', "Hide", "Reveal Hidden Vertices", (_SESSION_KEYMAP, "superskin.weight_reveal", 0)),
        _row('VERTEX', "Edit Weight", "Add / Scale Weight",
             (select, "superskin.weight_gesture", 0), (_SESSION_KEYMAP, "superskin.weight_gesture", 0)),
        _row('VERTEX', "Edit Weight", "Smooth / Sharpen Weight",
             (select, "superskin.weight_gesture", 1), (_SESSION_KEYMAP, "superskin.weight_gesture", 1)),
        _fixed('VERTEX', "Common", "Vertex Options", "RMB"),
    ]
    if CIRCLE_SELECT_ENABLED:
        rows.append(_row('VERTEX', "Select Tools", "Drag-Select Remove (Circle Brush)",
                         (_SESSION_KEYMAP, "superskin.weight_select_circle", 0)))
    return rows
