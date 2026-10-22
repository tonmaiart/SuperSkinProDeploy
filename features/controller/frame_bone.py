
import math
import time

import bpy
import numpy as np
from mathutils import Vector

from ...core.facade import CoreFacade
from ..deform_layer_viewer.layer_viewer.public_api import get_effective_armature


_WEIGHT_THRESHOLD = 0.1
_VIEW_MARGIN = 1.4
_SENSOR_WIDTH = 36.0
_MIN_RADIUS = 0.01


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


classes = (SUPERSKIN_OT_frame_active_bone,)
