"""Own modal lasso vertex selection for Weight Paint Mode, independent of the native lasso."""

import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from . import pick

_MIN_POINT_DISTANCE = 3.0
_LINE_COLOR = (1.0, 1.0, 1.0, 0.9)
_CLOSE_COLOR = (1.0, 1.0, 1.0, 0.35)


def _points_inside(px, py, polygon):
    """Even-odd point-in-polygon test, vectorised over the points."""
    inside = np.zeros(px.shape, dtype=np.bool_)
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        crosses = (yi > py) != (yj > py)
        if crosses.any():
            dy = yj - yi
            x_cross = (xj - xi) * (py - yi) / (dy if dy != 0.0 else 1.0) + xi
            inside ^= crosses & (px < x_cross)
        j = i
    return inside


class SUPERSKIN_OT_weight_select_lasso(bpy.types.Operator):
    """Lasso-select vertices in Weight Paint Mode"""
    bl_idname = "superskin.weight_select_lasso"
    bl_label = "Lasso Select Vertices"
    bl_options = {'REGISTER', 'UNDO'}

    mode: bpy.props.EnumProperty(
        items=(('SET', "Set", ""), ('ADD', "Extend", ""),
               ('SUB', "Subtract", ""), ('AND', "Intersect", "")),
        default='SET',
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.type == 'MESH' and obj.mode == 'WEIGHT_PAINT'
                and context.region is not None and context.region_data is not None)

    def invoke(self, context, event):
        if event.shift and event.ctrl:
            self.mode = 'AND'
        elif event.shift:
            self.mode = 'ADD'
        elif event.ctrl:
            self.mode = 'SUB'
        else:
            self.mode = 'SET'
        region = context.region
        self._region = region
        self._points = [
            (float(event.mouse_prev_press_x - region.x), float(event.mouse_prev_press_y - region.y)),
            (float(event.mouse_region_x), float(event.mouse_region_y)),
        ]
        self._handle = bpy.types.SpaceView3D.draw_handler_add(
            self._draw, (), 'WINDOW', 'POST_PIXEL')
        context.window_manager.modal_handler_add(self)
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'MOUSEMOVE':
            x, y = float(event.mouse_region_x), float(event.mouse_region_y)
            last = self._points[-1]
            if (x - last[0]) ** 2 + (y - last[1]) ** 2 >= _MIN_POINT_DISTANCE ** 2:
                self._points.append((x, y))
                context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self._remove_handle(context)
            return self._select(context)
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            self._remove_handle(context)
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        self._remove_handle(context)

    def _remove_handle(self, context):
        if getattr(self, "_handle", None) is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            self._handle = None
        if context.area is not None:
            context.area.tag_redraw()

    def _draw(self):
        if len(self._points) < 2:
            return
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        shader.bind()
        gpu.state.blend_set('ALPHA')
        gpu.state.line_width_set(1.5)
        shader.uniform_float("color", _LINE_COLOR)
        batch_for_shader(shader, 'LINE_STRIP', {"pos": self._points}).draw(shader)
        shader.uniform_float("color", _CLOSE_COLOR)
        batch_for_shader(shader, 'LINES', {"pos": [self._points[-1], self._points[0]]}).draw(shader)
        gpu.state.line_width_set(1.0)
        gpu.state.blend_set('NONE')

    def _select(self, context):
        if len(self._points) < 3:
            return {'CANCELLED'}
        obj = context.active_object
        mesh = obj.data
        count = len(mesh.vertices)
        if count == 0:
            return {'CANCELLED'}
        region, rv3d = self._region, context.region_data

        local, coords_world, sx, sy, in_front = pick.project(context, obj, region, rv3d)

        xs = [p[0] for p in self._points]
        ys = [p[1] for p in self._points]
        in_box = (in_front & (sx >= min(xs)) & (sx <= max(xs)) & (sy >= min(ys)) & (sy <= max(ys)))
        candidates = np.nonzero(in_box)[0]
        inside = np.zeros(count, dtype=np.bool_)
        if len(candidates):
            hit = _points_inside(sx[candidates], sy[candidates], self._points)
            candidates = candidates[hit]
            if len(candidates) and pick.occlusion_enabled(context):
                candidates = candidates[pick.visible_mask(context, obj, rv3d, local, coords_world, candidates)]
            inside[candidates] = True

        current = np.empty(count, dtype=np.bool_)
        mesh.vertices.foreach_get("select", current)
        if self.mode == 'ADD':
            result = current | inside
        elif self.mode == 'SUB':
            result = current & ~inside
        elif self.mode == 'AND':
            result = current & inside
        else:
            result = inside
        mesh.vertices.foreach_set("select", result)
        mesh.update()
        return {'FINISHED'}


def register():
    bpy.utils.register_class(SUPERSKIN_OT_weight_select_lasso)


def unregister():
    bpy.utils.unregister_class(SUPERSKIN_OT_weight_select_lasso)
