
import math
import time

import bpy
import numpy as np
from mathutils import Quaternion, Vector

from ...core.facade import CoreFacade
from ..deform_layer_viewer.layer_viewer.public_api import get_effective_armature
from ..weight_apply.public_api import clear_last_stroke_point, last_stroke_point


_WEIGHT_THRESHOLD = 0.1
_VIEW_MARGIN = 1.4
_SENSOR_WIDTH = 36.0
_MIN_RADIUS = 0.01

_bone_changed_at = None
_bone_pivot_cache = None
_seen_active_bone = {}


def _weighted_world_points(context, obj, bone_name):
    if bone_name not in CoreFacade.managed_vg_names(obj):
        return None
    vg = obj.vertex_groups.get(bone_name)
    if vg is None:
        return None
    mesh = obj.data
    eval_obj = obj.evaluated_get(context.evaluated_depsgraph_get())
    eval_mesh = eval_obj.data
    n = len(mesh.vertices)
    if n == 0 or len(eval_mesh.vertices) != n:
        return None

    gi = vg.index
    strong, weak = [], []
    for v_idx, groups in CoreFacade.iter_vertex_group_weights(mesh):
        for g, w in groups:
            if g == gi:
                if w >= _WEIGHT_THRESHOLD:
                    strong.append(v_idx)
                elif w > 0.0:
                    weak.append(v_idx)
                break
    picked = strong or weak
    if not picked:
        return None

    co = np.empty(n * 3, dtype=np.float32)
    eval_mesh.vertices.foreach_get("co", co)
    pts = co.reshape(n, 3)[np.asarray(picked, dtype=np.int64)].astype(np.float64)
    mw = np.array(eval_obj.matrix_world, dtype=np.float64)
    return pts @ mw[:3, :3].T + mw[:3, 3]


def _bone_world_segment(context, bone_name):
    arm = get_effective_armature(context)
    if arm is None or arm.pose is None:
        return None
    pb = arm.pose.bones.get(bone_name)
    if pb is None:
        return None
    mw = arm.matrix_world
    return mw @ pb.head, mw @ pb.tail


def compute_frame_target(context, obj, bone_name):
    segment = _bone_world_segment(context, bone_name)
    min_radius = _MIN_RADIUS
    if segment is not None:
        min_radius = max(min_radius, (segment[1] - segment[0]).length * 0.5)

    pts = _weighted_world_points(context, obj, bone_name)
    if pts is not None:
        lo, hi = pts.min(axis=0), pts.max(axis=0)
        center = Vector(((lo + hi) * 0.5).tolist())
        radius = float(np.max(hi - lo)) * 0.5
        return center, max(radius, min_radius)
    if segment is not None:
        head, tail = segment
        return (head + tail) * 0.5, min_radius
    return None


def _radius_to_dist(space, region, rv3d, radius):
    angle = 2.0 * math.atan(_SENSOR_WIDTH / (2.0 * max(space.lens, 1e-3)))
    w, h = max(region.width, 1), max(region.height, 1)
    angle = 2.0 * math.atan(math.tan(angle * 0.5) * min(w, h) / max(w, h))
    radius *= _VIEW_MARGIN
    if rv3d.is_perspective:
        return radius / math.sin(angle * 0.5)
    return radius / math.tan(angle * 0.5)


class SUPERSKIN_OT_frame_active_bone(bpy.types.Operator):
    """Center the view on the active bone's weighted skin, so orbiting turns around it"""
    bl_idname = "superskin.frame_active_bone"
    bl_label = "Frame Active Bone"

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            CoreFacade.is_editing_weights()
            and context.area is not None and context.area.type == 'VIEW_3D'
            and context.region_data is not None
            and obj is not None and obj.type == 'MESH' and obj.mode == 'WEIGHT_PAINT'
        )

    def invoke(self, context, event):
        obj = context.active_object
        bone_name = CoreFacade.active_vg_name_of(obj)
        target = compute_frame_target(context, obj, bone_name) if bone_name else None
        if target is None:
            return {'PASS_THROUGH'}

        global _bone_changed_at
        clear_last_stroke_point()
        _bone_changed_at = None
        rv3d = context.region_data
        if rv3d.view_perspective == 'CAMERA':
            rv3d.view_perspective = 'PERSP'
        center, radius = target
        self._rv3d = rv3d
        self._area = context.area
        self._from_loc = rv3d.view_location.copy()
        self._from_dist = rv3d.view_distance
        self._to_loc = center
        self._to_dist = _radius_to_dist(context.space_data, context.region, rv3d, radius)

        self._duration = context.preferences.view.smooth_view / 1000.0
        if self._duration <= 0.0:
            self._apply(1.0)
            return {'FINISHED'}
        self._start = time.perf_counter()
        self._timer = context.window_manager.event_timer_add(1.0 / 60.0, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _apply(self, t):
        t = t * t * (3.0 - 2.0 * t)
        self._rv3d.view_location = self._from_loc.lerp(self._to_loc, t)
        self._rv3d.view_distance = self._from_dist + (self._to_dist - self._from_dist) * t
        self._area.tag_redraw()

    def _finish(self, context):
        context.window_manager.event_timer_remove(self._timer)
        try:
            self._apply(1.0)
        except ReferenceError:
            pass

    def modal(self, context, event):
        if event.type == 'TIMER':
            t = (time.perf_counter() - self._start) / self._duration
            if t >= 1.0:
                self._finish(context)
                return {'FINISHED'}
            try:
                self._apply(t)
            except ReferenceError:
                context.window_manager.event_timer_remove(self._timer)
                return {'CANCELLED'}
            return {'RUNNING_MODAL'}
        if event.value == 'PRESS':
            self._finish(context)
            return {'FINISHED', 'PASS_THROUGH'}
        return {'PASS_THROUGH'}


def note_active_bone(obj):
    global _bone_changed_at
    bone_name = CoreFacade.active_vg_name_of(obj)
    if _seen_active_bone.get(obj.name) != bone_name:
        _seen_active_bone[obj.name] = bone_name
        _bone_changed_at = time.monotonic()


@bpy.app.handlers.persistent
def watch_active_bone(scene, depsgraph):
    obj = bpy.context.active_object
    if obj is not None and obj.type == 'MESH' and obj.mode == 'WEIGHT_PAINT':
        note_active_bone(obj)


def _bone_pivot(context, obj):
    global _bone_pivot_cache
    bone_name = CoreFacade.active_vg_name_of(obj)
    if not bone_name:
        return None
    key = (obj.name, bone_name, _bone_changed_at)
    if _bone_pivot_cache is None or _bone_pivot_cache[0] != key:
        target = compute_frame_target(context, obj, bone_name)
        _bone_pivot_cache = (key, target[0] if target is not None else None)
    center = _bone_pivot_cache[1]
    return center.copy() if center is not None else None


def orbit_pivot(context, obj):
    note_active_bone(obj)
    stroke = last_stroke_point(obj.name)
    if _bone_changed_at is not None and (stroke is None or _bone_changed_at > stroke[1]):
        return _bone_pivot(context, obj)
    return stroke[0] if stroke is not None else None


def turntable_step(rotation, dx, dy, sensitivity, reverse):
    up = Vector((0.0, 0.0, 1.0))
    right = rotation @ Vector((1.0, 0.0, 0.0))
    back = rotation @ Vector((0.0, 0.0, 1.0))
    xaxis = right
    if (up - back).length_squared > 0.001:
        horizon = up.cross(back)
        if horizon.dot(right) < 0.0:
            horizon.negate()
        fac = (abs(up.angle(back, 0.0) / math.pi - 0.5) * 2.0) ** 2
        blended = horizon.lerp(right, fac)
        if blended.length_squared > 1e-12:
            xaxis = blended
    tilt = Quaternion(xaxis.normalized(), sensitivity * dy)
    turn = Quaternion((0.0, 0.0, 1.0), -sensitivity * reverse * dx)
    return (turn @ tilt @ rotation).normalized()


def orbit_location(location, rotation_start, rotation, pivot):
    delta = rotation @ rotation_start.inverted()
    return pivot + delta @ (location - pivot)


class SUPERSKIN_OT_session_orbit(bpy.types.Operator):
    """Orbit the view around its center"""
    bl_idname = "superskin.session_orbit"
    bl_label = "Orbit View"

    @classmethod
    def poll(cls, context):
        return (
            CoreFacade.is_editing_weights()
            and context.area is not None and context.area.type == 'VIEW_3D'
        )

    def invoke(self, context, event):
        obj = context.active_object
        rv3d = context.region_data
        inputs = context.preferences.inputs
        pivot = orbit_pivot(context, obj) if obj is not None and obj.type == 'MESH' else None
        if (
            pivot is None or event.type != 'MIDDLEMOUSE' or rv3d is None
            or inputs.view_rotate_method != 'TURNTABLE' or rv3d.lock_rotation
            or rv3d.view_perspective == 'CAMERA'
        ):
            return self._native_orbit(context)

        self._persp_start = rv3d.view_perspective
        self._side_view_start = rv3d.is_orthographic_side_view
        if inputs.use_auto_perspective and rv3d.is_orthographic_side_view:
            rv3d.view_perspective = 'PERSP'
        self._rv3d = rv3d
        self._pivot = pivot
        self._loc_start = rv3d.view_location.copy()
        self._rot_start = rv3d.view_rotation.copy()
        self._rotation = self._rot_start.copy()
        self._prev = (event.mouse_x, event.mouse_y)
        ui_scale = context.preferences.system.ui_scale
        self._sensitivity = inputs.view_rotate_sensitivity_turntable / (ui_scale or 1.0)
        self._reverse = -1.0 if rv3d.perspective_matrix[1][2] < 0.0 else 1.0
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    @staticmethod
    def _native_orbit(context):
        inputs = context.preferences.inputs
        if not inputs.use_rotate_around_active:
            bpy.ops.view3d.rotate('INVOKE_DEFAULT')
            return {'FINISHED'}
        was_dirty = context.preferences.is_dirty
        inputs.use_rotate_around_active = False
        try:
            bpy.ops.view3d.rotate('INVOKE_DEFAULT')
        finally:
            inputs.use_rotate_around_active = True
            context.preferences.is_dirty = was_dirty
        return {'FINISHED'}

    def modal(self, context, event):
        try:
            if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
                self._apply(context, event)
            elif event.type == 'MIDDLEMOUSE' and event.value == 'RELEASE':
                return {'FINISHED'}
            elif event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
                self._restore(context)
                return {'CANCELLED'}
        except ReferenceError:
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def _apply(self, context, event):
        dx = event.mouse_x - self._prev[0]
        dy = event.mouse_y - self._prev[1]
        if not dx and not dy:
            return
        self._prev = (event.mouse_x, event.mouse_y)
        rv3d = self._rv3d
        self._rotation = turntable_step(self._rotation, dx, dy, self._sensitivity, self._reverse)
        if rv3d.is_orthographic_side_view:
            rv3d.is_orthographic_side_view = False
        rv3d.view_rotation = self._rotation
        rv3d.view_location = orbit_location(
            self._loc_start, self._rot_start, self._rotation, self._pivot)
        if context.area is not None:
            context.area.tag_redraw()

    def _restore(self, context):
        rv3d = self._rv3d
        rv3d.view_rotation = self._rot_start
        rv3d.view_location = self._loc_start
        rv3d.view_perspective = self._persp_start
        rv3d.is_orthographic_side_view = self._side_view_start
        if context.area is not None:
            context.area.tag_redraw()


classes = (SUPERSKIN_OT_frame_active_bone, SUPERSKIN_OT_session_orbit)
