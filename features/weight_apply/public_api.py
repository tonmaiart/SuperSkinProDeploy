
from .brush_tool import BRUSH_ENABLED
from .brush_tool.brush_hover import force_hide as force_hide_brush_hover
from .brush_tool.brush_ops import clear_last_stroke_point, last_stroke_bone, last_stroke_point
from .brush_tool.brush_prep import warm_soon as warm_brush_soon
from .brush_tool.brush_ui import draw_brush_mode, draw_connected_only_icon, draw_falloff_buttons
from .evaluated_map import (
    displayed as displayed_vertices,
    ensure_index_map,
    remove_index_map,
)
from .mesh_flags import (
    clear_selection as clear_mesh_selection,
    has_session_flags,
    has_vertex_selection,
    session_selection_live,
    swap_in_session_flags,
    swap_out_session_flags,
    vertex_positions,
    vertex_select as read_vertex_select,
    write_vertex_select,
)
from .brush_tool.brush_tool import (
    _get_active_tool_idname as get_active_weight_tool_idname,
    _WEIGHT_BRUSH_TOOL_IDNAME as WEIGHT_BRUSH_TOOL_IDNAME,
)
from .mirror_apply import mirror_bone_names, mirror_scope
from .live_feed import (
    add_listener as add_live_write_listener,
    remove_listener as remove_live_write_listener,
)
from .session_display import (
    displayed_bone_name,
    patch_bone_colors as patch_display_bone_colors,
    set_preview_bone as preview_display_bone,
    refresh_now as refresh_session_display,
    set_bone_colors as set_display_bone_colors,
    set_ramps as set_display_ramps,
    set_wire_color as set_display_wire_color,
    show_native_surface,
)
from .ops import ACTION_TO_GESTURE_PAIR, remember_repeat
from .stop_values import SUPERSKIN_OT_weight_amount_skip
from .tool_visibility import (
    hide_weight_tools, last_weight_tool_idname, reset_stale_weight_tool, show_weight_tools,
)
from .vertex_tool.common import LASSO_TOOL_IDNAME, WEIGHT_OPTIONS_PANEL_IDNAME
from .vertex_tool.welded import weld_reps as seam_weld_reps

__all__ = [
    "displayed_vertices",
    "posed_surface",
    "vertex_positions",
    "ensure_index_map",
    "remove_index_map",
    "WEIGHT_OPTIONS_PANEL_IDNAME",
    "force_hide_brush_hover",
    "BRUSH_ENABLED",
    "draw_falloff_buttons",
    "draw_brush_mode",
    "draw_connected_only_icon",
    "last_stroke_point",
    "last_stroke_bone",
    "clear_last_stroke_point",
    "warm_brush_soon",
    "ACTION_TO_GESTURE_PAIR",
    "remember_repeat",
    "AMOUNT_SKIP_OP",
    "add_live_write_listener",
    "remove_live_write_listener",
    "set_display_ramps",
    "set_display_wire_color",
    "refresh_session_display",
    "show_native_surface",
    "set_display_bone_colors",
    "patch_display_bone_colors",
    "preview_display_bone",
    "displayed_bone_name",
    "get_active_weight_tool_idname",
    "WEIGHT_BRUSH_TOOL_IDNAME",
    "LASSO_TOOL_IDNAME",
    "show_weight_tools",
    "hide_weight_tools",
    "reset_stale_weight_tool",
    "last_weight_tool_idname",
    "read_vertex_select",
    "write_vertex_select",
    "clear_mesh_selection",
    "has_session_flags",
    "has_vertex_selection",
    "session_selection_live",
    "swap_in_session_flags",
    "swap_out_session_flags",
    "uses_edit_mode_selection",
    "seam_weld_reps",
    "mirror_scope",
    "mirror_bone_names",
]


def posed_surface(context, obj):
    from .brush_tool.brush_logic import build_bvh, posed_array
    _mesh, bvh, coords = build_bvh(context, obj)
    return bvh, posed_array(coords)


def uses_edit_mode_selection(context):
    return bool(context.scene.superskin_weight_apply_scene.use_edit_mode_selection)

AMOUNT_SKIP_OP = SUPERSKIN_OT_weight_amount_skip.bl_idname
