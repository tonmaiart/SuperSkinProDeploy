
import time

import bpy
import numpy as np
from ...core.facade import CoreFacade
from ...interface.utils.session_shortcuts import ToolScopedShortcut, blocked
from mathutils import Vector
from mathutils.interpolate import poly_3d_calc
from bpy_extras import view3d_utils
from . import bone_axis as _bone_axis
from . import deform_overlay as _deform_overlay

_bone_picker_active = False

_HOVER_SCAN_INTERVAL = 1.0 / 60.0
_HOVER_APPLY_DELAY = 0.08
_HOVER_TIMER_STEP = 0.04
_SURFACE_MIN_WEIGHT = 0.01
_PICK_RADIUS = 12.0
_LASSO_MIN_STEP = 3.0
_LASSO_SAMPLES = 5
_LASSO_BUTTONS = {'LEFTMOUSE': 'add', 'MIDDLEMOUSE': 'remove'}
_LASSO_MARGIN = 20.0
_SWEEP_START_RADIUS = 20.0


def _points_in_polygon(points, poly):
    x = points[:, 0:1]
    y = points[:, 1:2]
    x1, y1 = poly[:, 0], poly[:, 1]
    x2, y2 = np.roll(x1, -1), np.roll(y1, -1)
    crosses = (y1 > y) != (y2 > y)
    dy = np.where(y2 != y1, y2 - y1, 1.0)
    x_at = x1 + (y - y1) * (x2 - x1) / dy
    return ((crosses & (x < x_at)).sum(axis=1) % 2) == 1


def _distance_to_path(points, path):
    a, ab = path[:-1], path[1:] - path[:-1]
    ap = points[:, None, :] - a[None, :, :]
    t = np.clip((ap * ab).sum(axis=2) / np.maximum((ab * ab).sum(axis=1), 1e-10), 0.0, 1.0)
    return np.linalg.norm(ap - t[:, :, None] * ab, axis=2).min(axis=1)


def _lasso_encloses(heads, tails, poly, margin=_LASSO_MARGIN):
    t = np.linspace(0.0, 1.0, _LASSO_SAMPLES, dtype=np.float32)
    samples = (heads[:, None, :] + t[None, :, None] * (tails - heads)[:, None, :]).reshape(-1, 2)
    hit = _points_in_polygon(samples, poly)
    if hit.any():
        hit[hit] = _distance_to_path(samples[hit], poly) >= margin
    return hit.reshape(len(heads), -1).any(axis=1)


def is_active() -> bool:
    return _bone_picker_active


def _clear_mask_state(obj):
    storage = getattr(obj, "superskin_storage", None)
    if storage is not None:
        if storage.active_is_mask:
            storage.active_is_mask = False


class OBJECT_OT_ssp_toggle_color_bone_style(bpy.types.Operator):
    bl_idname = "superskin.toggle_color_bone_style"
    bl_label = "Toggle Color Bone Style"

    def execute(self, context):
        from ...interface.utils.op_exec import run_domain_via_unified
        return run_domain_via_unified(context, "overlay_color", "toggle_multi_color")


class OBJECT_OT_ssp_clear_multi_selection(bpy.types.Operator):
    bl_idname = "superskin.clear_multi_selection"
    bl_label = "Clear Multi Bone Selection"
    bl_options = {'REGISTER'}

    def execute(self, context):
        obj = context.active_object
        if not obj:
            return {'CANCELLED'}

        storage = getattr(obj, "superskin_storage", None)
        ctrl = CoreFacade(context)
        if storage:
            ctrl.clear_all_selected(obj)
            storage.selection_history = ""
            storage.last_clicked_index = -1

        ctrl.show_report("CLEAN ALL MULTI SELECTION")
        return {'FINISHED'}


class OBJECT_OT_mw_pick_bone(ToolScopedShortcut, bpy.types.Operator):
    bl_idname = "object.mw_pick_bone"
    bl_label = "Pick Bone (Live Weight Visualize Hybrid)"

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_skinning_session(context)

    def modal(self, context, event):
        obj = context.active_object

        if event.type == 'TIMER':
            if self._flush_pending_hover(context, time.perf_counter()):
                context.area.tag_redraw()
            return {'PASS_THROUGH'}

        context.area.tag_redraw()

        if self._lasso_mode:
            if event.type == 'MOUSEMOVE':
                self.mouse_x = event.mouse_region_x
                self.mouse_y = event.mouse_region_y
                self._extend_lasso(context)
                return {'RUNNING_MODAL'}
            if event.type == self._lasso_button and event.value == 'RELEASE':
                self._commit_lasso(context)
                return {'RUNNING_MODAL'}
            if event.type == 'TWO' and event.value == 'RELEASE':
                self._commit_lasso(context)
            elif event.type in _LASSO_BUTTONS:
                return {'RUNNING_MODAL'}

        if event.value == 'PRESS' and event.type in _LASSO_BUTTONS:
            clicked = self.hovered_bone = self._bone_shape_under_cursor(context, _SWEEP_START_RADIUS)
            if clicked:
                if _LASSO_BUTTONS[event.type] == 'add':
                    self._sweep_add_bone(context, clicked)
                else:
                    self._sweep_remove_bone(context, clicked)
            self._start_lasso(event.type)
            return {'RUNNING_MODAL'}

        if event.type == 'MOUSEMOVE':
            self.mouse_x = event.mouse_region_x
            self.mouse_y = event.mouse_region_y

            mouse_pos = (self.mouse_x, self.mouse_y)

            now = time.perf_counter()
            do_scan = (now - self._last_hover_scan_time) >= _HOVER_SCAN_INTERVAL
            if do_scan:
                self._last_hover_scan_time = now

            if do_scan:
                self.hovered_bone = self._get_hovered_bone(context, bone_only=self._multi_mode)
            _deform_overlay.set_hover(self.hovered_bone, mouse_pos, self._hover_on_shape)
            if not self._multi_mode:
                self._queue_hover_apply(self.hovered_bone, now)
                self._flush_pending_hover(context, now)

        elif event.type == 'RIGHTMOUSE':
            if event.value == 'RELEASE':
                self._end_session(context)
                self._cancel_revert(context, obj)
                return {'CANCELLED'}

        elif event.type == 'TWO' and event.value == 'RELEASE':
            commit_bone = self.hovered_bone or self._last_hovered
            if not self._multi_mode and commit_bone:
                self._select_single_bone(context, commit_bone)
            self._end_session(context, fade=True)
            return {'FINISHED'}

        elif event.type == 'ESC':
            self._end_session(context)
            self._cancel_revert(context, obj)
            return {'CANCELLED'}

        elif event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            return {'PASS_THROUGH'}

        return {'RUNNING_MODAL'}

    def cancel(self, context):
        self._end_session(context)
        self._cancel_revert(context, context.active_object)

    def _end_session(self, context, fade=False):
        global _bone_picker_active
        _bone_picker_active = False
        timer = getattr(self, "_timer", None)
        if timer is not None:
            self._timer = None
            try:
                context.window_manager.event_timer_remove(timer)
            except Exception:
                pass
        self._restore_cursor(context)
        _deform_overlay.clear_hover()
        _deform_overlay.clear_active_override()
        _deform_overlay.set_holding(False, fade=fade)
        if context.area is not None:
            context.area.tag_redraw()


    def _queue_hover_apply(self, name, now):
        if not name or name == self._last_hovered:
            self._pending_hover = None
        elif name != self._pending_hover:
            self._pending_hover = name
            self._pending_since = now

    def _flush_pending_hover(self, context, now) -> bool:
        name = self._pending_hover
        if not name or self._multi_mode or name != self.hovered_bone:
            return False
        if now - self._pending_since < _HOVER_APPLY_DELAY:
            return False
        self._pending_hover = None
        self._last_hovered = name
        obj = context.active_object
        vg = obj.vertex_groups.get(name) if obj else None
        if vg is None:
            return False
        _clear_mask_state(obj)
        ctrl = CoreFacade(context)
        ctrl.set_active_bone_name(name)
        ctrl.apply_active_bone()
        self._sync_list_idx(obj, vg.index)
        return True


    def _set_picker_cursor(self, context):
        self._cursor_overridden = False
        try:
            context.window.cursor_modal_set('NONE')
            self._cursor_overridden = True
        except Exception:
            pass

    def _restore_cursor(self, context):
        if not getattr(self, "_cursor_overridden", False):
            return
        self._cursor_overridden = False
        try:
            context.window.cursor_modal_restore()
        except Exception:
            pass

    def _cancel_revert(self, context, obj):
        if not obj:
            return
        storage = obj.superskin_storage
        ctrl = CoreFacade(context)
        initial_pool = {n for n in self._initial_selected_names.split(",") if n}
        ctrl.set_selected_bones_pool(initial_pool)
        storage.selection_history = self._initial_selection_history
        storage.last_clicked_index = self.initial_vg_index
        storage.active_is_mask = self._initial_active_is_mask
        try:
            if self._initial_active_bone_name:
                ctrl.set_active_bone_name(self._initial_active_bone_name)
                self._sync_list_idx(obj, self.initial_vg_index)
            ctrl.apply_active_bone()
        except Exception:
            pass

    def _sync_list_idx(self, obj, vg_index: int):
        row = getattr(self, "_row_by_vg", {}).get(vg_index)
        if row is None:
            row = next((i for i, item in enumerate(obj.superskin_bones_collection)
                        if not item.is_mask and item.vg_index == vg_index), None)
        if row is not None and obj.superskin_bones_idx != row:
            obj.superskin_bones_idx = row

    def _select_single_bone(self, context, bone_name):
        obj = context.active_object
        storage = getattr(obj, "superskin_storage", None)
        if not storage:
            return
        self.session_pool.clear()
        self.session_recent_bone = None

        vg = obj.vertex_groups.get(bone_name)
        if not vg:
            return

        _clear_mask_state(obj)

        ctrl = CoreFacade(context)
        ctrl.set_selected_bones_pool({bone_name})
        storage.last_clicked_index = vg.index
        storage.selection_history  = str(vg.index)
        self.session_recent_bone   = bone_name
        self._sync_list_idx(obj, vg.index)

        try:
            ctrl.set_active_bone_name(bone_name)
            ctrl.apply_active_bone()
        except Exception:
            pass

    def _sweep_add_bone(self, context, bone_name):
        obj = context.active_object
        storage = getattr(obj, "superskin_storage", None)
        if not storage:
            return
        if bone_name in self.session_pool:
            return
        _clear_mask_state(obj)
        ctrl = CoreFacade(context)
        ctrl.add_vg_selected(obj, bone_name)
        vg = obj.vertex_groups.get(bone_name)
        if vg:
            storage.last_clicked_index = vg.index
            storage.selection_history = str(vg.index)
            self.session_recent_bone = bone_name
            self.session_pool.add(bone_name)
            self._sync_list_idx(obj, vg.index)
            _deform_overlay.set_active_override(bone_name)
            try:
                ctrl.set_active_bone_name(bone_name)
                ctrl.apply_active_bone()
            except Exception:
                pass

    def _sweep_remove_bone(self, context, bone_name):
        obj = context.active_object
        storage = getattr(obj, "superskin_storage", None)
        if not storage:
            return
        if bone_name not in self.session_pool:
            return
        if CoreFacade(context).remove_vg_selected(obj, bone_name):
            self.session_pool.discard(bone_name)
            self._update_recent_after_remove(obj, storage)

    def _update_recent_after_remove(self, obj, storage):
        remaining = sorted(self.session_pool)
        if remaining:
            last = remaining[-1]
            vg = obj.vertex_groups.get(last)
            if vg:
                storage.last_clicked_index = vg.index
                storage.selection_history = str(vg.index)
                self.session_recent_bone = last
                _deform_overlay.set_active_override(last)
        else:
            self.session_recent_bone = None
            _deform_overlay.set_active_override(None)


    def _start_lasso(self, button):
        self._lasso_button = button
        self._lasso_mode = _LASSO_BUTTONS[button]
        self._lasso_points = [(self.mouse_x, self.mouse_y)]
        self._drag_kind = 'sweep' if self.hovered_bone else None
        self._hover_on_shape = self.hovered_bone is not None
        self._multi_mode = True
        self._pending_hover = None
        _deform_overlay.set_hover(self.hovered_bone, (self.mouse_x, self.mouse_y), self._hover_on_shape)
        _deform_overlay.set_cursor_badge(self._lasso_mode)

    def _extend_lasso(self, context):
        pos = (self.mouse_x, self.mouse_y)
        lx, ly = self._lasso_points[-1]
        moved = (self.mouse_x - lx) ** 2 + (self.mouse_y - ly) ** 2 >= _LASSO_MIN_STEP ** 2
        if moved:
            self._lasso_points.append(pos)
        now = time.perf_counter()
        if self._drag_kind == 'sweep' and now - self._last_hover_scan_time >= _HOVER_SCAN_INTERVAL:
            self._last_hover_scan_time = now
            self.hovered_bone = self._get_hovered_bone(context, bone_only=True)
            if self.hovered_bone:
                sweep = self._sweep_add_bone if self._lasso_mode == 'add' else self._sweep_remove_bone
                sweep(context, self.hovered_bone)
        elif self._drag_kind is None and moved and self._lasso_would_change(context):
            self._drag_kind = 'lasso'
        if self._drag_kind == 'lasso':
            _deform_overlay.set_lasso(self._lasso_points)
            _deform_overlay.set_hover(None, pos)
        else:
            _deform_overlay.set_hover(self.hovered_bone, pos, self._hover_on_shape)

    def _lasso_would_change(self, context):
        if len(self._lasso_points) < 3:
            return False
        names, heads, tails = self._projected_segments(context)
        if not names:
            return False
        add = self._lasso_mode == 'add'
        candidate = np.array([(n not in self.session_pool) == add for n in names])
        if not candidate.any():
            return False
        idx = np.flatnonzero(candidate)
        poly = np.array(self._lasso_points, dtype=np.float32)
        return bool(_lasso_encloses(heads[idx], tails[idx], poly).any())

    def _commit_lasso(self, context):
        mode, points, kind = self._lasso_mode, self._lasso_points, self._drag_kind
        self._lasso_mode = None
        self._lasso_button = None
        self._lasso_points = []
        self._drag_kind = None
        _deform_overlay.set_lasso(None)
        _deform_overlay.set_cursor_badge(None)
        if kind != 'lasso' or len(points) < 3:
            return
        names = self._bones_in_lasso(context, np.array(points, dtype=np.float32))
        if mode == 'add':
            self._lasso_add(context, names)
        else:
            self._lasso_remove(context, names)

    def _bones_in_lasso(self, context, poly):
        names, heads, tails = self._projected_segments(context)
        if not names:
            return []
        t = np.linspace(0.0, 1.0, _LASSO_SAMPLES, dtype=np.float32)
        samples = heads[:, None, :] + t[None, :, None] * (tails - heads)[:, None, :]
        inside = _points_in_polygon(samples.reshape(-1, 2), poly).reshape(len(names), -1).any(axis=1)
        return [names[i] for i in np.flatnonzero(inside)]

    def _lasso_add(self, context, names):
        new = [n for n in names if n not in self.session_pool]
        obj = context.active_object
        storage = getattr(obj, "superskin_storage", None)
        if not new or not storage:
            return
        _clear_mask_state(obj)
        ctrl = CoreFacade(context)
        self.session_pool.update(new)
        ctrl.set_selected_bones_pool(set(self.session_pool))
        last = new[-1]
        vg = obj.vertex_groups.get(last)
        if vg is None:
            return
        storage.last_clicked_index = vg.index
        storage.selection_history = str(vg.index)
        self.session_recent_bone = last
        self._sync_list_idx(obj, vg.index)
        _deform_overlay.set_active_override(last)
        try:
            ctrl.set_active_bone_name(last)
            ctrl.apply_active_bone()
        except Exception:
            pass

    def _lasso_remove(self, context, names):
        hits = [n for n in names if n in self.session_pool]
        obj = context.active_object
        storage = getattr(obj, "superskin_storage", None)
        if not hits or not storage:
            return
        self.session_pool.difference_update(hits)
        CoreFacade(context).set_selected_bones_pool(set(self.session_pool))
        self._update_recent_after_remove(obj, storage)


    def _collect_session_bones(self, obj):
        self._bone_segments = []
        self._proj_key = None
        self._proj_segments = ([], np.zeros((0, 2), np.float32), np.zeros((0, 2), np.float32))
        armature = self._get_armature(obj)
        if armature:
            vg_names = {vg.name for vg in obj.vertex_groups}
            deform_bones = {b.name for b in armature.data.bones if b.use_deform}
            mw = armature.matrix_world
            pose_bones = armature.pose.bones
            tails = _bone_axis.display_tails(armature, pose_bones)
            self._bone_segments = [
                (bone.name, mw @ bone.head, mw @ (bone.tail if tails is None else Vector(tails[i])))
                for i, bone in enumerate(pose_bones)
                if bone.name in deform_bones and bone.name in vg_names
            ]
        self._pickable_names = {name for name, _, _ in self._bone_segments}
        self._obj_matrix = obj.matrix_world.copy()
        self._obj_matrix_inv = self._obj_matrix.inverted_safe()
        self._row_by_vg = {}
        for i, item in enumerate(obj.superskin_bones_collection):
            if not item.is_mask:
                self._row_by_vg.setdefault(item.vg_index, i)

    def _projected_segments(self, context):
        region = context.region
        rv3d = context.region_data
        key = (region.width, region.height, tuple(tuple(row) for row in rv3d.perspective_matrix))
        if key != self._proj_key:
            self._proj_key = key
            names, heads, tails = [], [], []
            for name, head, tail in self._bone_segments:
                head_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, head)
                tail_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, tail)
                if head_2d is not None and tail_2d is not None:
                    names.append(name)
                    heads.append(head_2d)
                    tails.append(tail_2d)
            self._proj_segments = (
                names,
                np.array(heads, dtype=np.float32).reshape(-1, 2),
                np.array(tails, dtype=np.float32).reshape(-1, 2),
            )
        return self._proj_segments

    def _bone_shape_under_cursor(self, context, reach=_PICK_RADIUS):
        names, heads, tails = self._projected_segments(context)
        if not names:
            return None
        p = np.array((self.mouse_x, self.mouse_y), dtype=np.float32)
        ab = tails - heads
        t = np.clip(((p - heads) * ab).sum(axis=1) / np.maximum((ab * ab).sum(axis=1), 1e-10), 0.0, 1.0)
        dist = np.linalg.norm(p - (heads + t[:, None] * ab), axis=1)
        hit = _deform_overlay.bone_shape_hits(heads, tails, p) | (dist <= reach)
        if not hit.any():
            return None
        idx = np.flatnonzero(hit)
        return names[idx[np.argmin(dist[idx])]]

    def _get_hovered_bone(self, context, bone_only=False):
        obj = context.active_object
        if not obj or obj.type != 'MESH' or not self._bone_segments:
            return None

        bone = self._bone_shape_under_cursor(context)
        self._hover_on_shape = bone is not None
        if bone or bone_only:
            return bone

        region = context.region
        rv3d = context.region_data
        mouse_vec = Vector((self.mouse_x, self.mouse_y))
        ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, mouse_vec)
        ray_dir    = view3d_utils.region_2d_to_vector_3d(region, rv3d, mouse_vec)
        local_origin = self._obj_matrix_inv @ ray_origin
        local_dir = self._obj_matrix_inv.to_3x3() @ ray_dir
        depsgraph = context.view_layer.depsgraph
        try:
            hit, loc, _norm, idx = obj.ray_cast(local_origin, local_dir, depsgraph=depsgraph)
            eval_mesh = obj.evaluated_get(depsgraph).data
        except Exception:
            return None

        mesh = obj.data
        if (not hit or not (0 <= idx < len(eval_mesh.polygons))
                or len(eval_mesh.vertices) != len(mesh.vertices)):
            return None
        layer = self._active_layer_weights(context)
        if layer is None:
            return None

        corners = list(eval_mesh.polygons[idx].vertices)
        try:
            corner_w = poly_3d_calc([eval_mesh.vertices[i].co for i in corners], loc)
        except Exception:
            return None

        scores = {}
        for v_idx, cw in zip(corners, corner_w):
            if cw <= 0.0:
                continue
            weights = layer.get(v_idx) or layer.get(str(v_idx))
            if not weights:
                continue
            for g_name, w in weights.items():
                if w > 0.0 and g_name in self._pickable_names:
                    scores[g_name] = scores.get(g_name, 0.0) + cw * w
        if not scores:
            return None
        best = max(scores, key=scores.get)
        return best if scores[best] >= _SURFACE_MIN_WEIGHT else None

    def _active_layer_weights(self, context):
        if self._layer_weights is None:
            try:
                self._layer_weights = CoreFacade(context).read_active_layer()
            except Exception:
                self._layer_weights = {}
        return self._layer_weights

    def _get_armature(self, obj):
        for mod in obj.modifiers:
            if mod.type == 'ARMATURE' and mod.object:
                return mod.object
        return None

    def invoke(self, context, event):
        if blocked(self, context):
            return {'PASS_THROUGH'}
        obj = context.active_object

        storage = getattr(obj, "superskin_storage", None) if obj else None
        if obj and getattr(context.scene, "superskin_is_mask_mode", False):
            from ...interface.utils.utils import exit_mask_mode_if_active
            exit_mask_mode_if_active(context, obj)
            _clear_mask_state(obj)
        if storage is not None and storage.active_is_mask:
            return {'CANCELLED'}

        self._saved_layer = 0
        ctrl_snapshot = CoreFacade(context)
        try:
            self._saved_layer = ctrl_snapshot.get_active_layer_index()
        except Exception:
            pass

        if not obj:
            return {'CANCELLED'}

        self.mouse_x = event.mouse_region_x
        self.mouse_y = event.mouse_region_y
        self.hovered_bone = None
        self.initial_vg_index = obj.superskin_storage.last_clicked_index if obj else -1
        self._initial_selected_names = ctrl_snapshot.get_selected_bones_pool_string() if obj else ""
        self._initial_selection_history = obj.superskin_storage.selection_history if obj else ""
        self._lasso_mode = None
        self._lasso_button = None
        self._lasso_points = []
        self._drag_kind = None
        self._multi_mode = False
        self._last_hovered = None
        self._last_hover_scan_time = 0.0
        self._hover_on_shape = False
        self._layer_weights = None
        self._pending_hover = None
        self._pending_since = 0.0
        self._timer = None
        self._collect_session_bones(obj)

        storage = getattr(obj, "superskin_storage", None)
        self.session_pool = ctrl_snapshot.get_selected_bones_pool() if obj else set()

        self.session_recent_bone = None
        if storage and 0 <= storage.last_clicked_index < len(obj.vertex_groups):
            self.session_recent_bone = obj.vertex_groups[storage.last_clicked_index].name

        self._initial_active_is_mask = storage.active_is_mask if storage else False
        self._initial_active_bone_name = ""
        if storage and not self._initial_active_is_mask and 0 <= storage.last_clicked_index < len(obj.vertex_groups):
            self._initial_active_bone_name = obj.vertex_groups[storage.last_clicked_index].name

        _deform_overlay.set_active_override(self._initial_active_bone_name or None)

        self._set_picker_cursor(context)
        from ..weight_apply.public_api import force_hide_brush_hover
        force_hide_brush_hover()
        _deform_overlay.set_hover(None, (self.mouse_x, self.mouse_y))
        _deform_overlay.set_cursor_badge(None)
        _deform_overlay.set_holding(True)
        context.area.tag_redraw()
        global _bone_picker_active
        _bone_picker_active = True
        self._timer = context.window_manager.event_timer_add(_HOVER_TIMER_STEP, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}


class SUPERSKIN_OT_bone_hud_settings(bpy.types.Operator):
    """Bone HUD visibility and colors"""
    bl_idname = "superskin.bone_hud_settings"
    bl_label = "Bone HUD Settings"

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=220)

    def execute(self, context):
        return {'FINISHED'}

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        layout.prop(wm, _deform_overlay.HUD_LOCK_PROP, text="Always Show Bone HUD")
        prefs = getattr(wm, "superskin_bone_picker_prefs", None)
        if prefs is None:
            return
        layout.separator()
        col = layout.column(align=True)
        col.use_property_split = True
        col.use_property_decorate = False
        for prop, default in _deform_overlay.CATEGORY_COLOR_DEFAULTS.items():
            row = col.row(align=True)
            row.prop(prefs, prop)
            if any(abs(a - b) >= 1e-4 for a, b in zip(getattr(prefs, prop), default)):
                row.operator(SUPERSKIN_OT_reset_bone_hud_color.bl_idname, text="", icon='LOOP_BACK').prop = prop


class SUPERSKIN_OT_reset_bone_hud_color(bpy.types.Operator):
    """Reset to the default color"""
    bl_idname = "superskin.reset_bone_hud_color"
    bl_label = "Reset Bone HUD Color"
    bl_options = {'INTERNAL'}

    prop: bpy.props.StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        default = _deform_overlay.CATEGORY_COLOR_DEFAULTS.get(self.prop)
        prefs = getattr(context.window_manager, "superskin_bone_picker_prefs", None)
        if default is None or prefs is None:
            return {'CANCELLED'}
        setattr(prefs, self.prop, default)
        return {'FINISHED'}


_classes = (
    SUPERSKIN_OT_bone_hud_settings,
    SUPERSKIN_OT_reset_bone_hud_color,
    OBJECT_OT_ssp_toggle_color_bone_style,
    OBJECT_OT_ssp_clear_multi_selection,
    OBJECT_OT_mw_pick_bone,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
