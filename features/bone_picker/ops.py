
import time

import bpy
from ...core.facade import CoreFacade
from mathutils import Vector
from mathutils.interpolate import poly_3d_calc
from bpy_extras import view3d_utils
from . import deform_overlay as _deform_overlay

_bone_picker_active = False

_HOVER_SCAN_INTERVAL = 1.0 / 60.0
_HOVER_APPLY_DELAY = 0.08
_HOVER_TIMER_STEP = 0.04


def is_active() -> bool:
    return _bone_picker_active


def _clear_mask_state(obj):
    storage = getattr(obj, "superskin_storage", None)
    if storage is not None:
        if storage.active_is_mask:
            storage.active_is_mask = False
        if storage.active_orphan_name:
            storage.active_orphan_name = ""


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

        ctrl.show_toast("CLEAN ALL MULTI SELECTION", duration=1.0)
        return {'FINISHED'}


class OBJECT_OT_mw_pick_bone(bpy.types.Operator):
    bl_idname = "object.mw_pick_bone"
    bl_label = "Pick Bone (Live Weight Visualize Hybrid)"

    @classmethod
    def poll(cls, context):
        if not CoreFacade.is_editing_weights():
            return False
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            return False
        return True

    bone_detect_dist: bpy.props.IntProperty(
        name="Bone Detect Distance (2D)",
        description="Detection radius around the mouse cursor for picking bones on screen, in pixels",
        default=30, min=5, max=200
    )

    def modal(self, context, event):
        obj = context.active_object

        if event.type == 'TIMER':
            if self._flush_pending_hover(context, time.perf_counter()):
                context.area.tag_redraw()
            return {'PASS_THROUGH'}

        context.area.tag_redraw()

        if event.type == 'MOUSEMOVE':
            self.mouse_x = event.mouse_region_x
            self.mouse_y = event.mouse_region_y

            mouse_pos = (self.mouse_x, self.mouse_y)

            now = time.perf_counter()
            do_scan = (now - self._last_hover_scan_time) >= _HOVER_SCAN_INTERVAL
            if do_scan:
                self._last_hover_scan_time = now

            if self.is_sweep_add or self.is_sweep_remove:
                nearby = self._nearby_bones(context) if do_scan else None
                if nearby is not None:
                    self.hovered_bone = min(nearby, key=lambda t: t[1])[0] if nearby else None
                _deform_overlay.set_hover(self.hovered_bone, mouse_pos)
                if nearby:
                    sweep = self._sweep_add_bone if self.is_sweep_add else self._sweep_remove_bone
                    for name, _ in nearby:
                        sweep(context, name)
            else:
                if do_scan:
                    self.hovered_bone = self._get_hovered_bone(context, bone_line_only=self._multi_mode)
                _deform_overlay.set_hover(self.hovered_bone, mouse_pos)
                if not self._multi_mode:
                    self._queue_hover_apply(self.hovered_bone, now)
                    self._flush_pending_hover(context, now)

        elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            self.is_sweep_add = True
            self._multi_mode = True
            self._pending_hover = None
            _deform_overlay.set_cursor_badge('add')
            self.hovered_bone = self._get_hovered_bone(context, bone_line_only=True)
            if self.hovered_bone:
                self._sweep_add_bone(context, self.hovered_bone)

        elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self.is_sweep_add = False
            _deform_overlay.set_cursor_badge('remove' if self.is_sweep_remove else None)

        elif event.type == 'RIGHTMOUSE':
            if event.value == 'RELEASE':
                self._end_session(context)
                self._cancel_revert(context, obj)
                return {'CANCELLED'}

        elif event.type == 'MIDDLEMOUSE' and event.value == 'PRESS':
            self.is_sweep_remove = True
            self._multi_mode = True
            self._pending_hover = None
            _deform_overlay.set_cursor_badge('remove')
            self.hovered_bone = self._get_hovered_bone(context, bone_line_only=True)
            if self.hovered_bone:
                self._sweep_remove_bone(context, self.hovered_bone)

        elif event.type == 'MIDDLEMOUSE' and event.value == 'RELEASE':
            self.is_sweep_remove = False
            _deform_overlay.set_cursor_badge('add' if self.is_sweep_add else None)

        elif event.type == 'TWO' and event.value == 'RELEASE':
            commit_bone = self.hovered_bone or self._last_hovered
            if not self._multi_mode and commit_bone:
                self._select_single_bone(context, commit_bone)
            self._end_session(context)
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

    def _end_session(self, context):
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
        _deform_overlay.set_holding(False)
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
        storage.active_orphan_name = self._initial_active_orphan_name
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
                        if not item.is_orphan and item.vg_index == vg_index), None)
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
        from ...core.facade import CoreFacade
        if CoreFacade(context).remove_vg_selected(obj, bone_name):
            self.session_pool.discard(bone_name)
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


    @staticmethod
    def _seg_dist_2d(p, a, b):
        ab = b - a
        denom = ab.dot(ab)
        if denom < 1e-10:
            return (p - a).length
        t = max(0.0, min(1.0, (p - a).dot(ab) / denom))
        return (p - (a + t * ab)).length

    def _collect_session_bones(self, obj):
        self._bone_segments = []
        self._proj_key = None
        self._proj_segments = []
        armature = self._get_armature(obj)
        if armature:
            vg_names = {vg.name for vg in obj.vertex_groups}
            deform_bones = {b.name for b in armature.data.bones if b.use_deform}
            mw = armature.matrix_world
            self._bone_segments = [
                (bone.name, mw @ bone.head, mw @ bone.tail)
                for bone in armature.pose.bones
                if bone.name in deform_bones and bone.name in vg_names
            ]
        self._pickable_names = {name for name, _, _ in self._bone_segments}
        self._obj_matrix = obj.matrix_world.copy()
        self._obj_matrix_inv = self._obj_matrix.inverted_safe()
        self._row_by_vg = {}
        for i, item in enumerate(obj.superskin_bones_collection):
            if not item.is_orphan:
                self._row_by_vg.setdefault(item.vg_index, i)

    def _projected_segments(self, context):
        region = context.region
        rv3d = context.region_data
        key = (region.width, region.height, tuple(tuple(row) for row in rv3d.perspective_matrix))
        if key != self._proj_key:
            self._proj_key = key
            projected = []
            for name, head, tail in self._bone_segments:
                head_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, head)
                tail_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, tail)
                if head_2d is not None and tail_2d is not None:
                    projected.append((name, head_2d, tail_2d))
            self._proj_segments = projected
        return self._proj_segments

    def _nearby_bones(self, context):
        mouse_vec = Vector((self.mouse_x, self.mouse_y))
        nearby = []
        for name, head_2d, tail_2d in self._projected_segments(context):
            dist = self._seg_dist_2d(mouse_vec, head_2d, tail_2d)
            if dist < self.bone_detect_dist:
                nearby.append((name, dist))
        return nearby

    def _get_hovered_bone(self, context, bone_line_only=False):
        obj = context.active_object
        if not obj or obj.type != 'MESH' or not self._bone_segments:
            return None

        nearby = self._nearby_bones(context)
        if nearby:
            return min(nearby, key=lambda t: t[1])[0]
        if bone_line_only:
            return None

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

        corners = list(eval_mesh.polygons[idx].vertices)
        try:
            corner_w = poly_3d_calc([eval_mesh.vertices[i].co for i in corners], loc)
        except Exception:
            return None

        scores = {}
        vgroups = obj.vertex_groups
        n_groups = len(vgroups)
        for v_idx, cw in zip(corners, corner_w):
            if cw <= 0.0:
                continue
            for g in mesh.vertices[v_idx].groups:
                if g.weight <= 0.0 or g.group >= n_groups:
                    continue
                g_name = vgroups[g.group].name
                if g_name in self._pickable_names:
                    scores[g_name] = scores.get(g_name, 0.0) + cw * g.weight
        if not scores:
            return None
        return max(scores, key=scores.get)

    def _get_armature(self, obj):
        for mod in obj.modifiers:
            if mod.type == 'ARMATURE':
                return mod.object
        return None

    def invoke(self, context, event):
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
        self.is_sweep_add = False
        self.is_sweep_remove = False
        self._multi_mode = False
        self._last_hovered = None
        self._last_hover_scan_time = 0.0
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
        self._initial_active_orphan_name = storage.active_orphan_name if storage else ""
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


_classes = (
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
