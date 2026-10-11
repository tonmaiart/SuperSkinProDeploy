
import math

import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from .. import mesh_flags
from . import pick
from .common import poll_session_mesh

CIRCLE_SELECT_ENABLED = False

_RADIUS_PX = 25.0
_CIRCLE_SEGMENTS = 48
_CIRCLE_COLOR = (1.0, 1.0, 1.0, 0.9)


class SUPERSKIN_OT_weight_select_circle(bpy.types.Operator):
    """Drag to add or remove vertices from the selection"""
    bl_idname = "superskin.weight_select_circle"
    bl_label = "Circle Select Vertices"
    bl_options = {'UNDO'}

    mode: bpy.props.EnumProperty(
        items=(('ADD', "Extend", ""), ('SUB', "Subtract", "")),
        default='SUB',
    )

    @classmethod
    def poll(cls, context):
        return (poll_session_mesh(context)
                and context.region is not None and context.region_data is not None)

    def invoke(self, context, event):
        try:
            return self._invoke(context, event)
        except Exception:
            self._remove_handle(context)
            raise

    def _invoke(self, context, event):
        obj = context.active_object
        if len(obj.data.vertices) == 0:
            return {'CANCELLED'}
        self._local, self._world, self._sx, self._sy, self._in_front = pick.project(
            context, obj, context.region, context.region_data)
        self._occlusion = pick.occlusion_enabled(context)
        self._radius = _RADIUS_PX * context.preferences.system.ui_scale
        self._trigger = event.type
        self._mouse = (float(event.mouse_region_x), float(event.mouse_region_y))
        self._changed = False
        self._handle = bpy.types.SpaceView3D.draw_handler_add(
            self._draw, (), 'WINDOW', 'POST_PIXEL')
        self._apply(context)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        try:
            return self._modal(context, event)
        except Exception:
            self._remove_handle(context)
            return {'CANCELLED'}

    def _modal(self, context, event):
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            self._mouse = (float(event.mouse_region_x), float(event.mouse_region_y))
            self._apply(context)
            return {'RUNNING_MODAL'}
        if event.type == self._trigger and event.value == 'RELEASE':
            self._remove_handle(context)
            return {'FINISHED'} if self._changed else {'CANCELLED'}
        if event.type == 'ESC':
            self._remove_handle(context)
            return {'FINISHED'} if self._changed else {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        self._remove_handle(context)

    def _remove_handle(self, context):
        if getattr(self, "_handle", None) is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            self._handle = None
        if context.area is not None:
            context.area.tag_redraw()

    def _apply(self, context):
        mx, my = self._mouse
        r2 = self._radius * self._radius
        dist2 = (self._sx - mx) ** 2 + (self._sy - my) ** 2
        candidates = np.nonzero(self._in_front & (dist2 <= r2))[0]
        mesh = context.active_object.data
        if len(candidates):
            current = mesh_flags.vertex_select(mesh)
            want = self.mode == 'ADD'
            candidates = candidates[current[candidates] != want]
            if len(candidates) and self._occlusion:
                candidates = candidates[pick.visible_mask(
                    context, context.active_object, context.region_data,
                    self._local, self._world, candidates)]
            if len(candidates):
                current[candidates] = want
                mesh_flags.write_vertex_select(mesh, current)
                mesh.update()
                self._changed = True
        if context.area is not None:
            context.area.tag_redraw()

    def _draw(self):
        mx, my = self._mouse
        step = 2.0 * math.pi / _CIRCLE_SEGMENTS
        points = [(mx + self._radius * math.cos(i * step), my + self._radius * math.sin(i * step))
                  for i in range(_CIRCLE_SEGMENTS + 1)]
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        shader.bind()
        gpu.state.blend_set('ALPHA')
        try:
            shader.uniform_float("color", _CIRCLE_COLOR)
            batch_for_shader(shader, 'LINE_STRIP', {"pos": points}).draw(shader)
        finally:
            gpu.state.blend_set('NONE')


def register():
    if CIRCLE_SELECT_ENABLED:
        bpy.utils.register_class(SUPERSKIN_OT_weight_select_circle)


def unregister():
    if CIRCLE_SELECT_ENABLED:
        bpy.utils.unregister_class(SUPERSKIN_OT_weight_select_circle)
