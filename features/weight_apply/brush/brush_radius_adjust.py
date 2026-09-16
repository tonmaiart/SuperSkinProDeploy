"""Weight Brush -- interactive Radius adjustment (F).

Replaces Blender's native `wm.radial_control` for Radius (Hardness has no
`wm.radial_control` binding at all -- it's a 3-preset EnumProperty,
Hard/Medium/Soft, picked from an attached 3-button toggle row in the
N-panel/viewport header, not dragged) because two things about pointing it
at `superskin_weight_brush_prefs.brush_radius` didn't fit this brush's own
scale/units:

  - `wm.radial_control`'s generic preview circle treats the controlled
    property as a literal on-screen pixel radius. This brush's
    `brush_radius` means something else entirely per projection mode --
    true world-space mesh units in Surface, a 0.0-1.0 fraction of a fixed
    pixel scale in Screen (`brush_logic.screen_mode_radius_px()`) -- so
    the native widget's circle came out far smaller than the actual brush
    footprint drawn during a real stroke/hover (`brush_draw.py`).
  - `wm.radial_control` maps raw drag DISTANCE (pixels) directly onto the
    property's numeric value with no independent sensitivity. Fine for a
    property whose natural range is already in the hundreds, but
    `brush_radius`'s 0.0-1.0 range meant a couple pixels of movement
    already saturated it -- felt like an all-or-nothing snap between 0
    and 1 rather than a smooth resize.

This operator fixes both: it calls the SAME `brush_draw.show_screen()`/
`show_surface()` the real cursor uses (so the preview is pixel-identical
to what painting will actually use), and it scales drag distance down
(`_RADIUS_DRAG_SENSITIVITY`) so the full 0.0-1.0 range spans a stretched,
comfortable few hundred pixels of travel instead of a handful -- the same
accumulate-onto-the-running-value-via-`cursor_warp` technique
`weight_apply/ops.py`'s own gesture drag already uses (never recompute
from an absolute offset -- that caps the reachable value at whatever a
single event's movement could cover).

No longer manually hides the OS cursor (`cursor_modal_set('NONE')`) --
`../brush_tool.py`'s `SuperSkinWeightBrushTool.bl_cursor` now covers that
at the tool level, engine-managed and per-region-aware, so this operator
only has to draw its own live preview circle. See `docs/bug-history/0038`
for why the hand-rolled version (mirrored from `brush_hover.py`, which had
the same thing) was fragile enough to remove outright.
"""

import bpy

from .brush_logic import raycast_under_cursor, screen_mode_radius_px
from . import brush_draw

# 400px of horizontal drag sweeps the full 0.0 -> 1.0 range -- stretches
# what used to be a near-instant 0/1 snap (wm.radial_control's raw 1:1
# pixel-to-value mapping) into a smooth, controllable resize. Starts from
# whatever brush_radius already is at invoke (added onto, never reset to
# 0), so a small correction to an already-tuned radius stays small.
_RADIUS_DRAG_SENSITIVITY_SCREEN = 1.0 / 400.0

# Surface projection interprets brush_radius as true world-space mesh
# units rather than a fixed pixel scale, so the same 400px sweep read as
# noticeably touchier in practice (real-world report). Halved sensitivity
# (800px sweeps the full range) to slow it down without touching Screen's
# already-tuned feel.
_RADIUS_DRAG_SENSITIVITY_SURFACE = 1.0 / 800.0


class SUPERSKIN_OT_weight_brush_adjust_radius(bpy.types.Operator):
    """F while the Weight Brush tool is active: hold+drag to resize
    Radius, drawing the exact same cursor circle a real stroke/hover would
    show at that radius. LMB/Enter confirms; RMB/Esc reverts to the radius
    at invoke -- mirrors wm.radial_control's own confirm/cancel contract
    (tap F, drag, click to confirm; F's own release does nothing special)."""
    bl_idname = "superskin.weight_brush_adjust_radius"
    bl_label = "Adjust Weight Brush Radius"
    bl_options = {'INTERNAL'}

    # Checked by brush_hover.py so it steps aside while this modal owns
    # brush_draw -- same convention as SUPERSKIN_OT_weight_brush._stroke_active.
    _adjusting_active = False

    def invoke(self, context, event):
        from .brush_ops import get_brush_prefs
        self._prefs = get_brush_prefs()
        self._start_radius = self._prefs.brush_radius
        self._initial_x = event.mouse_x
        self._initial_y = event.mouse_y
        SUPERSKIN_OT_weight_brush_adjust_radius._adjusting_active = True
        context.window_manager.modal_handler_add(self)
        self._update(context, event)
        return {'RUNNING_MODAL'}

    def _update(self, context, event):
        """Draw the live-adjusted radius via the SAME brush_draw entry
        points the real cursor uses -- see this module's docstring for why
        that (not a generic radial-control circle) is the whole point."""
        p = self._prefs
        label = f"Weight Brush Radius: {p.brush_radius:.3f}"

        # No falloff-edge inner ring -- Hardness now scales intensity, not
        # spatial falloff (see brush_ops.py). Passing the full radius as
        # the "edge" value is what makes brush_draw's own
        # `edge < radius - epsilon` guard skip drawing it.
        if p.brush_projection == 'SCREEN':
            radius_px = screen_mode_radius_px(p.brush_radius)
            brush_draw.show_screen(
                (event.mouse_region_x, event.mouse_region_y), radius_px, radius_px, label,
            )
            return

        obj = context.active_object
        if obj is None or obj.type != 'MESH' or context.mode != 'EDIT_MESH':
            brush_draw.hide()
            return

        from .brush_hover import _get_bvh_cached
        _bm, bvh = _get_bvh_cached(context, obj)
        hit, _face_index, hit_world, hit_normal = raycast_under_cursor(context, event, obj, bvh)
        if not hit:
            brush_draw.hide()
            return

        brush_draw.show_surface(hit_world, hit_normal, p.brush_radius, p.brush_radius, label)

    def modal(self, context, event):
        if event.type == 'MOUSEMOVE':
            # `cursor_warp` below resets the mouse back to _initial_x every
            # frame (infinite-drag), so `delta` here is only the movement
            # since the last warp -- it must be ACCUMULATED onto the
            # running brush_radius, never used as an absolute offset (see
            # this module's docstring / weight_apply/ops.py's own gesture
            # for why that would cap the reachable value).
            delta = event.mouse_x - self._initial_x
            sensitivity = (
                _RADIUS_DRAG_SENSITIVITY_SURFACE
                if self._prefs.brush_projection == 'SURFACE'
                else _RADIUS_DRAG_SENSITIVITY_SCREEN
            )
            new_radius = self._prefs.brush_radius + delta * sensitivity
            self._prefs.brush_radius = max(0.0, min(1.0, new_radius))
            context.window.cursor_warp(self._initial_x, self._initial_y)
            try:
                self._update(context, event)
            except Exception as exc:
                import traceback
                from ....core.facade import CoreFacade
                # "feature_domains" (a real, registered category -- not an
                # "adhoc:" one) matches how `weight_apply/ops.py`'s own
                # background-worker exception logging is done permanently
                # elsewhere in this domain, since this guard is a permanent
                # safety net (an exception here must never abort/leave this
                # modal stuck), not temporary bug-chasing instrumentation.
                CoreFacade.debug_log(
                    "feature_domains",
                    f"weight_brush_adjust_radius: _update() failed: {exc!r}\n{traceback.format_exc()}",
                )
            return {'RUNNING_MODAL'}

        if event.type in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
            return self._finish(context, cancelled=False)

        if event.type in {'RIGHTMOUSE', 'ESC'}:
            self._prefs.brush_radius = self._start_radius
            return self._finish(context, cancelled=True)

        # Anything else (keyboard shortcuts, scroll, other mouse buttons,
        # clicks/drags meant for native Blender UI or the addon's own
        # panels) is NOT this operator's business -- PASS_THROUGH lets it
        # reach whatever would normally handle it. The earlier version of
        # this branch returned RUNNING_MODAL here (swallowing everything
        # not explicitly listed above), which stole the PRESS half of any
        # click anywhere in the window while this modal was running --
        # e.g. a slider drag never sees its own PRESS to start dragging, a
        # button never sees a matching PRESS+RELEASE pair to register a
        # click, and a Toolbar click to switch tools does nothing but
        # silently confirm/cancel this modal instead. See
        # `docs/bug-history/0036` for the same "stuck modal blocks
        # everything" failure class in a sibling operator.
        return {'PASS_THROUGH'}

    def cancel(self, context):
        """Blender calls this (not modal()) when it force-terminates this
        operator outside modal()'s own confirm/cancel branches -- e.g. a
        workspace/tool switch triggered some other way while this modal is
        still running. Without this override the preview circle would stay
        stuck showing its last position -- mirrors `brush_hover.py`'s own
        `cancel()` for the identical reason."""
        SUPERSKIN_OT_weight_brush_adjust_radius._adjusting_active = False
        brush_draw.hide()

    def _finish(self, context, cancelled):
        SUPERSKIN_OT_weight_brush_adjust_radius._adjusting_active = False
        brush_draw.hide()
        try:
            from ....core.facade import CoreFacade
            CoreFacade.save_prefs()
        except Exception:
            pass
        return {'CANCELLED'} if cancelled else {'FINISHED'}


def register():
    bpy.utils.register_class(SUPERSKIN_OT_weight_brush_adjust_radius)


def unregister():
    try:
        bpy.utils.unregister_class(SUPERSKIN_OT_weight_brush_adjust_radius)
    except Exception:
        pass
