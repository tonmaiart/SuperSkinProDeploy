
import bpy

from .brush_logic import raycast_under_cursor, screen_mode_radius_px
from . import brush_draw

_RADIUS_DRAG_SENSITIVITY_SCREEN = 1.0 / 400.0

_RADIUS_DRAG_SENSITIVITY_SURFACE = 1.0 / 800.0


class SUPERSKIN_OT_weight_brush_adjust_radius(bpy.types.Operator):
    """Drag to change the brush size"""
    bl_idname = "superskin.weight_brush_adjust_radius"
    bl_label = "Adjust Weight Brush Radius"
    bl_options = {'INTERNAL'}

    _adjusting_active = False

    def invoke(self, context, event):
        from .brush_ops import get_brush_prefs, SCREEN_RADIUS_MAX, SURFACE_RADIUS_MAX
        self._prefs = get_brush_prefs()
        self._start_radius = self._prefs.brush_radius
        self._initial_x = event.mouse_x
        self._anchor_region = (event.mouse_region_x, event.mouse_region_y)
        self._anchor_hit = self._raycast_anchor(context, event)
        self._px_per_unit = self._anchor_px_per_unit(context)
        self._max_radius = (
            SCREEN_RADIUS_MAX if self._prefs.brush_projection == 'SCREEN' else SURFACE_RADIUS_MAX
        )
        SUPERSKIN_OT_weight_brush_adjust_radius._adjusting_active = True
        context.window_manager.modal_handler_add(self)
        self._update()
        return {'RUNNING_MODAL'}

    def _raycast_anchor(self, context, event):
        if self._prefs.brush_projection == 'SCREEN':
            return None
        obj = context.active_object
        if obj is None or obj.type != 'MESH' or context.mode != 'PAINT_WEIGHT':
            return None
        from .brush_hover import _get_bvh_cached
        _bm, bvh = _get_bvh_cached(context, obj)
        hit, _face_index, hit_world, hit_normal = raycast_under_cursor(context, event, obj, bvh)
        return (hit_world, hit_normal) if hit else None

    def _anchor_px_per_unit(self, context):
        if self._anchor_hit is None or context.region is None or context.region_data is None:
            return None
        from bpy_extras.view3d_utils import location_3d_to_region_2d
        from mathutils import Vector
        rv3d = context.region_data
        hit_world = self._anchor_hit[0]
        right = rv3d.view_matrix.inverted().to_3x3() @ Vector((1.0, 0.0, 0.0))
        p0 = location_3d_to_region_2d(context.region, rv3d, hit_world)
        p1 = location_3d_to_region_2d(context.region, rv3d, hit_world + right)
        if p0 is None or p1 is None:
            return None
        px = (p1 - p0).length
        return px if px > 1e-6 else None

    def _update(self):
        from .brush_ops import get_falloff_start_fraction
        p = self._prefs
        label = ""
        falloff_start = get_falloff_start_fraction(p)

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
            if self._prefs.brush_projection == 'SURFACE':
                sensitivity = (
                    1.0 / self._px_per_unit if self._px_per_unit
                    else _RADIUS_DRAG_SENSITIVITY_SURFACE
                )
            else:
                sensitivity = _RADIUS_DRAG_SENSITIVITY_SCREEN
            new_radius = self._start_radius + delta * sensitivity
            self._prefs.brush_radius = min(self._max_radius, abs(new_radius))
            try:
                self._update()
            except Exception as exc:
                import traceback
                from ....core.facade import CoreFacade
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

        return {'PASS_THROUGH'}

    def cancel(self, context):
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
