"""Weight Brush -- interactive Hardness adjustment (Shift+F)."""

import bpy

from .brush_logic import raycast_under_cursor, screen_mode_radius_px
from . import brush_draw

_HARDNESS_DRAG_SENSITIVITY = 1.0 / 400.0


class SUPERSKIN_OT_weight_brush_adjust_hardness(bpy.types.Operator):
    """Shift+F while the Weight Brush tool is active: hold+drag to adjust Hardness."""
    bl_idname = "superskin.weight_brush_adjust_hardness"
    bl_label = "Adjust Weight Brush Hardness"
    bl_options = {'INTERNAL'}

    _adjusting_active = False

    def invoke(self, context, event):
        from .brush_ops import get_brush_prefs
        self._prefs = get_brush_prefs()
        self._start_hardness = self._prefs.brush_hardness
        self._initial_x = event.mouse_x
        self._anchor_region = (event.mouse_region_x, event.mouse_region_y)
        self._anchor_hit = self._raycast_anchor(context, event)
        SUPERSKIN_OT_weight_brush_adjust_hardness._adjusting_active = True
        context.window_manager.modal_handler_add(self)
        self._update()
        return {'RUNNING_MODAL'}

    def _raycast_anchor(self, context, event):
        """Hit under the cursor at invoke time; the preview stays pinned there while the cursor moves freely."""
        if self._prefs.brush_projection == 'SCREEN':
            return None
        obj = context.active_object
        if obj is None or obj.type != 'MESH' or context.mode != 'PAINT_WEIGHT':
            return None
        from .brush_hover import _get_bvh_cached
        _bm, bvh = _get_bvh_cached(context, obj)
        hit, _face_index, hit_world, hit_normal = raycast_under_cursor(context, event, obj, bvh)
        return (hit_world, hit_normal) if hit else None

    def _update(self):
        """Draw the live-adjusted Hardness through the SAME brush_draw entry points the real
        cursor uses, so the previewed falloff ring is pixel-identical to what a real dab would use."""
        from .brush_ops import get_falloff_start_fraction
        p = self._prefs
        falloff_start = get_falloff_start_fraction(p)
        label = ""

        if p.brush_projection == 'SCREEN':
            radius_px = screen_mode_radius_px(p.brush_radius)
            brush_draw.show_screen(self._anchor_region, radius_px, radius_px * falloff_start, label)
            return

        if self._anchor_hit is None:
            brush_draw.hide()
            return

        hit_world, hit_normal = self._anchor_hit
        brush_draw.show_surface(
            hit_world, hit_normal, p.brush_radius, p.brush_radius * falloff_start, label,
        )

    def modal(self, context, event):
        if event.type == 'MOUSEMOVE':
            delta = event.mouse_x - self._initial_x
            new_hardness = self._start_hardness + delta * _HARDNESS_DRAG_SENSITIVITY
            self._prefs.brush_hardness = max(0.0, min(1.0, new_hardness))
            try:
                self._update()
            except Exception as exc:
                import traceback
                from ....core.facade import CoreFacade
                CoreFacade.debug_log(
                    "feature_domains",
                    f"weight_brush_adjust_hardness: _update() failed: {exc!r}\n{traceback.format_exc()}",
                )
            return {'RUNNING_MODAL'}

        if event.type in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
            return self._finish(context, cancelled=False)

        if event.type in {'RIGHTMOUSE', 'ESC'}:
            self._prefs.brush_hardness = self._start_hardness
            return self._finish(context, cancelled=True)

        return {'PASS_THROUGH'}

    def cancel(self, context):
        """Blender calls this (not modal()) when it force-terminates this operator outside
        modal()'s own confirm/cancel branches."""
        SUPERSKIN_OT_weight_brush_adjust_hardness._adjusting_active = False
        brush_draw.hide()

    def _finish(self, context, cancelled):
        SUPERSKIN_OT_weight_brush_adjust_hardness._adjusting_active = False
        brush_draw.hide()
        try:
            from ....core.facade import CoreFacade
            CoreFacade.save_prefs()
        except Exception:
            pass
        return {'CANCELLED'} if cancelled else {'FINISHED'}


def register():
    bpy.utils.register_class(SUPERSKIN_OT_weight_brush_adjust_hardness)


def unregister():
    try:
        bpy.utils.unregister_class(SUPERSKIN_OT_weight_brush_adjust_hardness)
    except Exception:
        pass
