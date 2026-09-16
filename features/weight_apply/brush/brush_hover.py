"""Weight Brush -- persistent hover cursor.

Shows the brush radius/falloff circle at the cursor whenever the mouse is
over a 3D viewport in Edit Mesh mode WHILE the "Weight Brush" WorkSpaceTool
is the active tool -- even before the first click. Distinct from
`brush_draw.py`'s circle during an actual `SUPERSKIN_OT_weight_brush`
stroke (`../brush_ops.py`): that one only exists while LMB is held; this
one runs continuously while the tool is selected, independent of any
stroke (steps aside, see `_stroke_active` below, while one is in progress).

## Design history (three attempts; this is the third)

**Attempt #1** invoked this operator ONCE, globally, via
`bpy.app.timers.register()` at addon-register time, with its `modal()`
driven by a self-added `TIMER` event at a fixed interval. Abandoned after
two confirmed bugs (real exported `adhoc:weight_brush_hover` debug logs):
`context.area`/`context.region` are never populated on a `TIMER` event for
an operator invoked with no real UI event behind it, and on a real user's
machine the `TIMER` ticks themselves stopped being delivered entirely after
a mode-switch bounce elsewhere in the addon silently dropped this modal off
the window's handler stack.

**Attempt #2** hooked a `MOUSEMOVE` entry into the Weight Brush
`WorkSpaceTool`'s own `bl_keymap` (`../brush_tool.py`), invoking a
persistent modal operator (`modal_handler_add()`) exactly like
`bone_picker/ops.py`'s own picker modal. This sidestepped both of attempt
#1's bugs (a real `MOUSEMOVE` event always carries correct
`context.area`/`context.region`, and if anything ever dropped this modal
from the stack, the very next `MOUSEMOVE` while the tool was still active
would simply re-`invoke()` a fresh instance). It worked, for a while --
but see `docs/bug-history/0038` for the full multi-pass diagnosis of what
turned out to be wrong with it: **the mere existence of a bare
`MOUSEMOVE`/`ANY` entry inside a `WorkSpaceTool`'s own `bl_keymap` --
independent of anything this operator's own `modal()` did with those
events -- silently broke ALL panel-content interaction anywhere in the 3D
View editor (every N-panel tab's own widgets, not just this addon's; only
tab-*switching* kept working) for as long as the Weight Brush tool stayed
selected, the moment that keymap entry fired even once.** This was
confirmed by elimination (an exhaustive codebase search found no
addon-side state that explained it) and by direct experiment (removing
only that one keymap binding, nothing else, fixed the symptom outright).
This appears to be a genuine Blender engine behavior specific to binding
`MOUSEMOVE` inside a *tool's own* keymap -- nothing else in this whole
addon does that, and no other domain here has ever exhibited this symptom.

**Attempt #3 (current)** moves the `MOUSEMOVE`/`ANY` binding to a plain
`'Mesh'`-mode addon keymap instead (`keymap.py` in this same package) --
the same registration style every other custom keymap in this codebase
already uses (`bone_picker/keymap.py`,
`overlay_color/keymap.py`, `weight_apply/keymap.py`'s own Alt-drag gesture)
-- and, as a structural consequence, this
operator is no longer a persistent modal at all: it never calls
`modal_handler_add()`. `invoke()` does one self-contained update (raycast,
gate checks, `brush_draw.show_*()`/`hide()`) and always returns
`{'PASS_THROUGH'}` immediately, exactly once per `MOUSEMOVE` event that
reaches it. It cannot linger (there is no lifecycle left to linger in), and
it cannot suppress anything else's event handling, since it never claims an
event and never occupies a slot on the window's modal handler stack.

Because a `'Mesh'`-mode keymap applies throughout Edit Mesh mode regardless
of which editor/area the mouse is currently over (unlike attempt #2's tool
keymap, naturally scoped to the 3D viewport by construction), this
operator explicitly gates on `_is_weight_brush_tool_active()`,
`context.area.type == 'VIEW_3D'`, and the viewport's own `'WINDOW'` region
(`_mouse_in_window_region()`) before doing anything else -- see
`keymap.py`'s own docstring for the registration side of this.
"""

import bpy

from .brush_logic import raycast_under_cursor, screen_mode_radius_px, get_posed_vertex_coordinates
from . import brush_draw

_WEIGHT_BRUSH_TOOL_IDNAME = "superskin.weight_brush_tool"

# Cache keyed on (object name, vert count, face count) -- a cheap proxy for
# "topology probably hasn't changed" that avoids rebuilding the BVH on
# every single MOUSEMOVE for the common case (mouse moving, nothing being
# edited). A pure vertex-position edit (no count change) between ticks, OR
# a pose change (the armature re-posed without touching this mesh's own
# vertex/face count -- see `brush_logic.py`'s "Detects against the CURRENT
# POSE" for why the BVH is now built from POSED coordinates, not the
# bind-pose BMesh), can both leave this briefly stale; acceptable for a
# hover cursor (never used for actual painting, which always builds its
# own fresh BVH per stroke regardless) -- the same tradeoff this cache
# already made for vertex-position edits, just extended to cover pose too.
_cache_key = None
_cache_bm = None
_cache_bvh = None


def _get_bvh_cached(context, obj):
    global _cache_key, _cache_bm, _cache_bvh
    import bmesh
    from mathutils.bvhtree import BVHTree

    bm = bmesh.from_edit_mesh(obj.data)
    key = (obj.name, len(bm.verts), len(bm.faces))
    if key != _cache_key:
        bm.faces.ensure_lookup_table()
        posed_coords = get_posed_vertex_coordinates(context, obj)
        polygons = [[v.index for v in f.verts] for f in bm.faces]
        _cache_bvh = BVHTree.FromPolygons(posed_coords, polygons)
        _cache_key = key
    _cache_bm = bm
    return bm, _cache_bvh


def _mouse_in_window_region(context, event):
    """True only while the mouse is genuinely inside the 3D viewport's own
    drawable rectangle -- `mouse_region_x/y` are relative to `context.region`'s
    own origin, so this is a plain in-bounds test against that region's own
    size, not just a `region.type` check (a `'WINDOW'`-type region exists in
    most editors, not just `VIEW_3D` -- callers must check `context.area.type`
    separately)."""
    region = context.region
    if region is None or region.type != 'WINDOW':
        return False
    return 0 <= event.mouse_region_x <= region.width and 0 <= event.mouse_region_y <= region.height


def force_hide():
    """Public escape hatch for callers OUTSIDE this module -- specifically
    `features/controller/ops_scene_modes.py`'s `_exit_edit_mode()`, which
    must undo every viewport-state change this addon made on entering Edit
    Layer Weight on every exit path (Save Weights, Force Pose Mode, the
    auto-save guard) -- see CLAUDE.md's Viewport State Invariant. Hides the
    overlay immediately rather than waiting for the next `MOUSEMOVE` tick to
    notice `context.mode != 'EDIT_MESH'` on its own."""
    brush_draw.hide()


def _is_weight_brush_tool_active(context):
    try:
        tool = context.workspace.tools.from_space_view3d_mode(context.mode, create=False)
    except Exception:
        return False
    return bool(tool is not None and tool.idname == _WEIGHT_BRUSH_TOOL_IDNAME)


def _update(context, event):
    """One self-contained update per `MOUSEMOVE` -- see this module's
    docstring for why there is no longer a persistent modal instance or
    lifecycle wrapped around this."""
    if not _is_weight_brush_tool_active(context):
        brush_draw.hide()
        return

    from .brush_ops import SUPERSKIN_OT_weight_brush, get_brush_prefs
    from .brush_radius_adjust import SUPERSKIN_OT_weight_brush_adjust_radius

    if SUPERSKIN_OT_weight_brush._stroke_active:
        return  # the paint operator's own brush_draw updates take priority

    if SUPERSKIN_OT_weight_brush_adjust_radius._adjusting_active:
        return  # the F-key radius-adjust modal's own brush_draw updates take priority

    if context.area is None or context.area.type != 'VIEW_3D' or not _mouse_in_window_region(context, event):
        # Because this operator is now driven by a plain 'Mesh'-mode
        # keymap (see keymap.py) rather than a WorkSpaceTool's own
        # bl_keymap, it fires on every MOUSEMOVE anywhere in the window
        # while in Edit Mesh mode -- including over completely unrelated
        # editors (Timeline, Outliner, Properties) and the 3D viewport's
        # own N-panel/Toolbar/header. This check is what scopes actual
        # drawing back down to just the 3D viewport's own paintable
        # rectangle.
        brush_draw.hide()
        return

    obj = context.active_object
    if context.mode != 'EDIT_MESH' or obj is None or obj.type != 'MESH':
        brush_draw.hide()
        return

    if context.region_data is None:
        brush_draw.hide()
        return

    p = get_brush_prefs()
    # No text label on the plain hover cursor -- per explicit request, the
    # info text should only appear while actually dabbing Smooth/Scale/
    # Sharpen (see `brush_ops.py::_dab()`), not on hover or a plain Add dab.
    label = ""

    if p.brush_projection == 'SCREEN':
        # Per explicit request: Screen projection's cursor is always
        # visible, positioned at the literal mouse position, and sized
        # in constant on-screen pixels regardless of zoom/distance --
        # no raycast needed at all, since Screen mode doesn't care what
        # (if anything) is actually under the cursor in 3D. Contrast
        # the SURFACE branch below.
        radius_px = screen_mode_radius_px(p.brush_radius)
        # No falloff-edge inner ring -- Hardness now scales intensity, not
        # spatial falloff (see brush_ops.py). Passing the full radius as
        # the "edge" value is what makes brush_draw's own
        # `edge < radius - epsilon` guard skip drawing it.
        brush_draw.show_screen(
            (event.mouse_region_x, event.mouse_region_y), radius_px, radius_px, label,
        )
        return

    # Surface projection: only ever shows while actually hovering the
    # mesh surface, oriented to that surface's normal, sized in true
    # world-space units -- per explicit request, the opposite of
    # Screen mode's "always visible" above.
    bm, bvh = _get_bvh_cached(context, obj)
    hit, _face_index, hit_world, hit_normal = raycast_under_cursor(context, event, obj, bvh)
    if not hit:
        brush_draw.hide()
        return

    brush_draw.show_surface(hit_world, hit_normal, p.brush_radius, p.brush_radius, label)


class SUPERSKIN_OT_weight_brush_hover(bpy.types.Operator):
    """Plain, non-modal, one-shot operator -- see this module's docstring
    for why. Bound to a plain `'Mesh'`-mode addon keymap (`keymap.py` in
    this same package), `MOUSEMOVE`/`ANY` -- deliberately NOT the Weight
    Brush WorkSpaceTool's own `bl_keymap` (see `docs/bug-history/0038`)."""
    bl_idname = "superskin.weight_brush_hover"
    bl_label = "Weight Brush Hover Cursor"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        try:
            _update(context, event)
        except Exception as exc:
            import traceback
            from ....core.facade import CoreFacade
            CoreFacade.debug_log(
                "adhoc:weight_brush_hover",
                f"EXCEPTION: {exc!r}\n{traceback.format_exc()}",
            )
        # Always PASS_THROUGH -- this operator never has any business
        # claiming a MOUSEMOVE event for itself; every other handler
        # (native UI, other editors, other keymap entries) must still see
        # it normally.
        return {'PASS_THROUGH'}


def register():
    bpy.utils.register_class(SUPERSKIN_OT_weight_brush_hover)


def unregister():
    try:
        bpy.utils.unregister_class(SUPERSKIN_OT_weight_brush_hover)
    except Exception:
        pass
    force_hide()
