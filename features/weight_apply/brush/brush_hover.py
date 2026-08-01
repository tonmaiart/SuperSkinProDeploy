"""Weight Brush -- persistent hover cursor.

Shows the brush radius/falloff circle at the cursor whenever the "Weight
Brush" WorkSpaceTool is the active tool and the mouse is over a 3D
viewport in Edit Mesh mode -- even before the first click. This is
DISTINCT from `brush_draw.py`'s circle during an actual
`SUPERSKIN_OT_weight_brush` stroke (`../brush_ops.py`): that one only
exists while LMB is held; this one runs continuously, for the life of the
Blender session, independent of any stroke.

Lifecycle: there is no direct Python API to force-cancel an arbitrary
running modal operator from outside it, so this uses the standard
"persistent background modal" pattern instead --

  - `register()` schedules `_start()` via `bpy.app.timers.register()`
    (a short delay so a valid window/context exists before the first
    `INVOKE_DEFAULT` call).
  - `_start()` invokes `SUPERSKIN_OT_weight_brush_hover` once; its
    `modal()` then runs forever via `{'PASS_THROUGH'}`, doing real work
    only on its own throttled `TIMER` ticks (`_HOVER_TICK_INTERVAL`, ~8Hz
    -- deliberately much lower than the paint operator's 60Hz, since this
    is a passive visual aid, not a stroke, and never calls
    `gather_brush_vertices()`/`gather_brush_vertices_screen()` at all --
    only a raycast for the hit point, no actual vertex gathering).
  - `unregister()` sets the module-level `_should_stop` flag; the running
    modal instance checks it on its own next tick and returns
    `{'FINISHED'}` there, tearing down its own timer and hiding the
    cursor -- this is what lets the operator class be safely unregistered
    afterward without the instance still technically "holding" it.

Steps aside (draws nothing) while `SUPERSKIN_OT_weight_brush._stroke_active`
is set, so this and the paint operator's own live cursor updates
(`brush_draw.py`) never fight over the same shared draw state.

Confidence note: `context.workspace.tools.from_space_view3d_mode()` (active-
tool detection, below) is the one piece of this file built on an API this
project hasn't exercised elsewhere and isn't independently verified against
a running Blender -- if the cursor never appears despite the tool being
selected, that call is the first thing to instrument with an ad hoc
`CoreFacade.debug_log("adhoc:weight_brush_hover", ...)` category (see the
project's Ad Hoc Debug Deck convention) to confirm what it's actually
returning.
"""

import bmesh
import bpy
from mathutils.bvhtree import BVHTree

from .brush_logic import build_bvh, raycast_under_cursor, world_to_screen, world_radius_to_screen_px
from . import brush_draw

# Deliberately lower than the paint operator's 60Hz (`brush_ops.py`'s
# `_BRUSH_APPLY_INTERVAL`) -- this only positions a display circle, no
# vertex gathering or weight writes, so it doesn't need stroke-grade
# responsiveness, and every tick rebuilds (or reuses, see `_get_bvh_cached()`)
# a BVH, which is real cost on a dense mesh.
_HOVER_TICK_INTERVAL = 1.0 / 8.0

_TOOL_IDNAME = "superskin.weight_brush_tool"

_should_stop = False

# Cache keyed on (object name, vert count, face count) -- a cheap proxy for
# "topology probably hasn't changed" that avoids rebuilding the BVH every
# tick for the common case (mouse moving, nothing being edited). A pure
# vertex-position edit (no count change) between ticks can leave this
# briefly stale; acceptable for a hover cursor (never used for actual
# painting, which always builds its own fresh BVH per stroke regardless).
_cache_key = None
_cache_bm = None
_cache_bvh = None


def _get_bvh_cached(obj):
    global _cache_key, _cache_bm, _cache_bvh
    bm = bmesh.from_edit_mesh(obj.data)
    key = (obj.name, len(bm.verts), len(bm.faces))
    if key != _cache_key:
        bm.faces.ensure_lookup_table()
        _cache_bvh = BVHTree.FromBMesh(bm)
        _cache_key = key
    _cache_bm = bm
    return bm, _cache_bvh


def _is_tool_active(context):
    try:
        tool = context.workspace.tools.from_space_view3d_mode(context.mode, create=False)
    except Exception:
        return False
    return tool is not None and tool.idname == _TOOL_IDNAME


class SUPERSKIN_OT_weight_brush_hover(bpy.types.Operator):
    """Background modal -- see this module's docstring."""
    bl_idname = "superskin.weight_brush_hover"
    bl_label = "Weight Brush Hover Cursor"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        self._timer = context.window_manager.event_timer_add(
            _HOVER_TICK_INTERVAL, window=context.window,
        )
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if _should_stop:
            context.window_manager.event_timer_remove(self._timer)
            brush_draw.hide()
            return {'FINISHED'}

        if event.type == 'TIMER':
            self._update(context, event)

        return {'PASS_THROUGH'}

    def _update(self, context, event):
        from .brush_ops import SUPERSKIN_OT_weight_brush, get_brush_prefs

        if SUPERSKIN_OT_weight_brush._stroke_active:
            return  # the paint operator's own brush_draw updates take priority

        area = context.area
        region = context.region
        obj = context.active_object
        if (area is None or area.type != 'VIEW_3D' or region is None or
                region.type != 'WINDOW' or context.mode != 'EDIT_MESH' or
                obj is None or obj.type != 'MESH'):
            brush_draw.hide()
            return

        if not _is_tool_active(context):
            brush_draw.hide()
            return

        bm, bvh = _get_bvh_cached(obj)
        hit, _face_index, hit_world = raycast_under_cursor(context, event, obj, bvh)
        if not hit:
            brush_draw.hide()
            return

        center_2d = world_to_screen(context, hit_world)
        if center_2d is None:
            brush_draw.hide()
            return

        p = get_brush_prefs()
        radius_px = world_radius_to_screen_px(context, hit_world, p.brush_radius)
        falloff_edge_px = radius_px * max(0.0, 1.0 - p.brush_falloff)
        brush_draw.show()
        brush_draw.update(
            (center_2d.x, center_2d.y), radius_px, falloff_edge_px,
            f"{p.brush_projection.title()}  R:{p.brush_radius:.1f}  F:{p.brush_falloff:.2f}",
        )


def _start(*_args):
    global _should_stop
    _should_stop = False
    bpy.ops.superskin.weight_brush_hover('INVOKE_DEFAULT')
    return None  # one-shot -- don't repeat this timer


def register():
    bpy.utils.register_class(SUPERSKIN_OT_weight_brush_hover)
    bpy.app.timers.register(_start, first_interval=0.2)


def unregister():
    global _should_stop
    _should_stop = True
    try:
        bpy.utils.unregister_class(SUPERSKIN_OT_weight_brush_hover)
    except Exception:
        pass
