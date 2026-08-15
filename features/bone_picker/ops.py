"""Bone Picker operators — moved from operators/ops_shortcuts.py."""

import bpy
from ...core.facade import CoreFacade
from mathutils import Vector
from bpy_extras import view3d_utils
from . import deform_overlay as _deform_overlay

_bone_picker_active = False


def is_active() -> bool:
    """True while ``object.mw_pick_bone``'s modal loop is running.

    Read by the shortcut-overlay HUD (``interface/utils/shortcut_overlay.py``,
    via ``bone_picker_feature.py``'s ``keymaps`` metadata) to swap the plain
    "Alt+2 Bone Picker" row for the modal's live sub-shortcuts while the
    gesture is actually in progress.
    """
    return _bone_picker_active


def _clear_mask_state(obj):
    """Mask is just another row now, not a separate mode — picking a real
    bone through the bone picker (hover preview, single pick, or multi
    sweep) must exit mask context the same way clicking a real bone row in
    the Deform Bones list does, or superskin_is_mask_mode stays stuck True
    (blocking Auto Assign, and leaving the list looking unrefreshed) even
    after a different bone becomes active. Caller is still responsible for
    calling CoreFacade.apply_active_bone() afterward — that's what actually
    re-derives superskin_is_mask_mode from this flag."""
    storage = getattr(obj, "superskin_storage", None)
    if storage is not None:
        storage.active_is_mask = False
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
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if not obj:
            return {'CANCELLED'}

        storage = getattr(obj, "superskin_storage", None)
        ctrl = CoreFacade(context)
        if storage:
            # Mode-aware -- routes to __ssp_pool in Edit Mode instead of
            # writing storage.selected_names directly (not reliably
            # undo-tracked while in Edit Mode).
            ctrl.clear_all_selected(obj)
            storage.selection_history = ""
            storage.last_clicked_index = -1

        ctrl.show_toast("CLEAN ALL MULTI SELECTION", duration=1.0)
        return {'FINISHED'}


class OBJECT_OT_mw_pick_bone(bpy.types.Operator):
    bl_idname = "object.mw_pick_bone"
    bl_label = "Pick Bone (Live Weight Visualize Hybrid)"
    # No 'REGISTER': this operator doesn't need the info-log/redo-panel
    # display that flag backs. 'UNDO' alone still gives its own selection
    # changes real undo support (Ctrl+Z reverts a bone pick).
    bl_options = {'UNDO'}

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
        description="ระยะรัศมีรอบเมาส์ในการตรวจจับกระดูกบนหน้าจอ (หน่วยเป็นพิกเซล)",
        default=30, min=5, max=200
    )

    vertex_search_radius: bpy.props.FloatProperty(
        name="Vertex Search Radius",
        description="รัศมีสูงสุดในการควานหา Vertex ใต้เมาส์หลังจาก Raycast โดนผิวเมช",
        default=50.0, min=1.0, max=500.0
    )

    def modal(self, context, event):
        global _bone_picker_active
        context.area.tag_redraw()
        obj = context.active_object

        # ── Mouse move: hover preview + sweep add ────────────────────────
        if event.type == 'MOUSEMOVE':
            self.mouse_x = event.mouse_region_x
            self.mouse_y = event.mouse_region_y

            mouse_pos = (self.mouse_x, self.mouse_y)

            if self.is_sweep_add:
                self.hovered_bone = self._get_hovered_bone(context, bone_line_only=True)
                _deform_overlay.set_hover(self.hovered_bone, mouse_pos)
                for name in self._get_hovered_bones_in_range(context):
                    self._sweep_add_bone(context, name)
            elif self.is_sweep_remove:
                self.hovered_bone = self._get_hovered_bone(context, bone_line_only=True)
                _deform_overlay.set_hover(self.hovered_bone, mouse_pos)
                for name in self._get_hovered_bones_in_range(context):
                    self._sweep_remove_bone(context, name)
            else:
                self.hovered_bone = self._get_hovered_bone(context, bone_line_only=self._multi_mode)
                _deform_overlay.set_hover(self.hovered_bone, mouse_pos)
                # Only live-apply the hovered bone as the real active bone
                # (mesh-surface weight-color preview + Release-confirms-this
                # UX) while still in single-select mode. Once _multi_mode is
                # True (a sweep add/remove already happened this session),
                # merely hovering elsewhere must NOT touch the active bone
                # at all -- ctrl.apply_active_bone() apparently couples the
                # active bone back into the selection pool on the core side,
                # which was silently re-adding whatever bone you happened to
                # be hovering the instant you released, even though the
                # TWO+RELEASE handler below already correctly skips calling
                # _select_single_bone() in multi-select mode.
                if not self._multi_mode and self.hovered_bone and self.hovered_bone != self._last_hovered:
                    self._last_hovered = self.hovered_bone
                    vg = obj.vertex_groups.get(self.hovered_bone)
                    if vg:
                        _clear_mask_state(obj)
                        ctrl = CoreFacade(context)
                        ctrl.set_active_bone_name(self.hovered_bone)
                        ctrl.apply_active_bone()
                        self._sync_list_idx(obj, vg.index)
                        CoreFacade.debug_log("adhoc:bone_picker", f"hover: {self.hovered_bone!r}")

        # ── Left click: sweep add to multi ───────────────────────────────
        elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            self.is_sweep_add = True
            self._multi_mode = True
            _deform_overlay.set_cursor_badge('add')
            self.hovered_bone = self._get_hovered_bone(context, bone_line_only=True)
            if self.hovered_bone:
                self._sweep_add_bone(context, self.hovered_bone)

        elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self.is_sweep_add = False
            _deform_overlay.set_cursor_badge('remove' if self.is_sweep_remove else None)

        # ── Right click: cancel, revert to initial bone ──────────────────
        elif event.type == 'RIGHTMOUSE':
            if event.value == 'RELEASE':
                _bone_picker_active = False
                # stop_bone_picker_draw() was a no-op; removed in Phase 8
                self._restore_cursor(context)
                _deform_overlay.clear_hover()
                _deform_overlay.clear_active_override()
                _deform_overlay.set_holding(False)
                self._cancel_revert(context, obj)
                return {'CANCELLED'}

        # ── Middle mouse drag: sweep remove from multi-selection ─────────
        elif event.type == 'MIDDLEMOUSE' and event.value == 'PRESS':
            self.is_sweep_remove = True
            self._multi_mode = True
            _deform_overlay.set_cursor_badge('remove')
            self.hovered_bone = self._get_hovered_bone(context, bone_line_only=True)
            if self.hovered_bone:
                self._sweep_remove_bone(context, self.hovered_bone)

        elif event.type == 'MIDDLEMOUSE' and event.value == 'RELEASE':
            self.is_sweep_remove = False
            _deform_overlay.set_cursor_badge('add' if self.is_sweep_add else None)

        # ── Release 2: single-select only when no multi-selection active ──
        elif event.type == 'TWO' and event.value == 'RELEASE':
            if not self._multi_mode and self.hovered_bone:
                self._select_single_bone(context, self.hovered_bone)
            CoreFacade.debug_log(
                "adhoc:bone_picker",
                f"confirm: single_mode={not self._multi_mode} bone={self.hovered_bone!r}",
            )
            _bone_picker_active = False
            # stop_bone_picker_draw() was a no-op; removed in Phase 8
            self._restore_cursor(context)
            _deform_overlay.clear_hover()
            _deform_overlay.clear_active_override()
            _deform_overlay.set_holding(False)
            return {'FINISHED'}

        # ── ESC: cancel ───────────────────────────────────────────────────
        elif event.type == 'ESC':
            _bone_picker_active = False
            # stop_bone_picker_draw() was a no-op; removed in Phase 8
            self._restore_cursor(context)
            _deform_overlay.clear_hover()
            _deform_overlay.clear_active_override()
            _deform_overlay.set_holding(False)
            self._cancel_revert(context, obj)
            return {'CANCELLED'}

        elif event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            return {'PASS_THROUGH'}

        return {'RUNNING_MODAL'}

    def cancel(self, context):
        """Blender calls this (not modal()) when it force-terminates a
        running modal operator outside its own RIGHTMOUSE/Release-2/ESC
        exit paths above -- e.g. a workspace/window switch, or another
        operator grabbing the modal stack. Without this override, none of
        modal()'s exit cleanup would run in that case, leaving the cursor
        stuck on the plain-arrow override (and the overlay's hover/active-
        override state stuck) until Blender restarts."""
        global _bone_picker_active
        _bone_picker_active = False
        self._restore_cursor(context)
        _deform_overlay.clear_hover()
        _deform_overlay.clear_active_override()
        _deform_overlay.set_holding(False)
        self._cancel_revert(context, context.active_object)

    # ── Selection helpers ─────────────────────────────────────────────────────

    def _set_picker_cursor(self, context):
        """Explicit save-then-override: record that *this* session pushed a
        modal cursor override, then push it. Hides the native OS cursor
        entirely ('NONE') instead of swapping it for a plain arrow --
        deform_overlay.py draws its own crosshair at the tracked mouse
        position instead (_draw_crosshair_cursor(), fed by set_hover()
        every MOUSEMOVE below), so this addon fully owns what's drawn
        instead of depending on whatever native cursor bitmap Blender
        decides to show. Blender's Python API still has no getter for
        "the cursor currently shown" -- cursor_modal_set()/
        cursor_modal_restore() remains Blender's own paired push/pop
        mechanism and the only way to get back to whatever was showing
        before without knowing its name. self._cursor_overridden guards
        _restore_cursor() so a restore is only ever attempted when this
        session actually pushed one (never double-restore, never skip
        it), and both calls are exception-safe so a failure here can't
        abort the rest of modal's exit-path cleanup (hover/override/holding
        state, _cancel_revert)."""
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
        """Undo everything this picking session did to selection state —
        single-pick and multi-select sweeps alike — restoring the exact
        pre-invoke() snapshot. Used by both RIGHTMOUSE and ESC so the two
        cancel paths behave identically.

        Hover preview (MOUSEMOVE handler above) live-applies the currently
        hovered bone as the active bone via set_active_bone_name() /
        apply_active_bone() / _sync_list_idx() and clears mask state as it
        goes — that part of the pre-invoke snapshot is restored here too,
        not just the multi-select storage strings, or the active bone
        would stay stuck on whatever was last hovered even after a
        cancel."""
        if not obj:
            return
        storage = obj.superskin_storage
        ctrl = CoreFacade(context)
        # Mode-aware -- routes to __ssp_pool in Edit Mode instead of writing
        # storage.selected_names directly (not reliably undo-tracked while
        # in Edit Mode).
        initial_pool = {n for n in self._initial_selected_names.split(",") if n}
        ctrl.set_selected_bones_pool(initial_pool)
        storage.selection_history = self._initial_selection_history
        storage.last_clicked_index = self.initial_vg_index
        storage.active_is_mask = self._initial_active_is_mask
        storage.active_orphan_name = self._initial_active_orphan_name
        try:
            ctrl.set_selected_bones(self._initial_selected_names)
            if self._initial_active_bone_name:
                ctrl.set_active_bone_name(self._initial_active_bone_name)
                self._sync_list_idx(obj, self.initial_vg_index)
            ctrl.apply_active_bone()
        except Exception:
            pass
        CoreFacade.debug_log(
            "adhoc:bone_picker",
            f"cancel_revert: back to bone={self._initial_active_bone_name!r} "
            f"vg_index={self.initial_vg_index} mask={self._initial_active_is_mask}",
        )

    def _sync_list_idx(self, obj, vg_index: int):
        """Sync superskin_bones_idx so template_list highlights the selected row."""
        for i, item in enumerate(obj.superskin_bones_collection):
            if not item.is_orphan and item.vg_index == vg_index:
                obj.superskin_bones_idx = i
                return

    def _select_single_bone(self, context, bone_name):
        """Release 2: clear multi, make bone_name the only selected bone."""
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
        # Mode-aware -- routes to __ssp_pool in Edit Mode instead of writing
        # storage.selected_names directly (not reliably undo-tracked while
        # in Edit Mode).
        ctrl.set_selected_bones_pool({bone_name})
        storage.last_clicked_index = vg.index
        storage.selection_history  = str(vg.index)
        self.session_recent_bone   = bone_name
        self._sync_list_idx(obj, vg.index)

        # Sync layer meta — same two calls the row-click operator makes via
        # on_single_select so the active bone is persisted per-layer.
        try:
            ctrl.set_selected_bones(ctrl.get_selected_bones_pool_string())
            ctrl.set_active_bone_name(bone_name)
            ctrl.apply_active_bone()
        except Exception:
            pass

    def _sweep_add_bone(self, context, bone_name):
        """Alt+Middle sweep: add bone_name to multi-selection pool."""
        obj = context.active_object
        storage = getattr(obj, "superskin_storage", None)
        if not storage:
            return
        # self.session_pool is this modal's own in-memory mirror of the
        # live pool, kept in sync at every add/remove call site below --
        # cheaper and mode-safe compared to re-reading storage.selected_names
        # (stale in Edit Mode; see add_vg_selected()'s docstring).
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
        """Alt+Right sweep: remove bone_name from multi-selection pool."""
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

    # ── Detection helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _seg_dist_2d(p, a, b):
        """Perpendicular distance from 2D point p to segment a-b."""
        ab = b - a
        denom = ab.dot(ab)
        if denom < 1e-10:
            return (p - a).length
        t = max(0.0, min(1.0, (p - a).dot(ab) / denom))
        return (p - (a + t * ab)).length

    def _iter_nearby_bones(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            return
        armature = self._get_armature(obj)
        if not armature:
            return
        region = context.region
        rv3d = context.region_data
        mouse_vec = Vector((self.mouse_x, self.mouse_y))
        vg_names = {vg.name for vg in obj.vertex_groups}
        deform_bones = {b.name for b in armature.data.bones if b.use_deform}
        for bone in armature.pose.bones:
            if bone.name not in deform_bones or bone.name not in vg_names:
                continue
            head_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, armature.matrix_world @ bone.head)
            tail_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, armature.matrix_world @ bone.tail)
            if head_2d is None or tail_2d is None:
                continue
            dist = self._seg_dist_2d(mouse_vec, head_2d, tail_2d)
            if dist < self.bone_detect_dist:
                yield bone.name, dist

    def _get_hovered_bones_in_range(self, context):
        return [name for name, _ in self._iter_nearby_bones(context)]

    def _get_hovered_bone(self, context, bone_line_only=False):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            return None
        armature = self._get_armature(obj)
        if not armature:
            return None

        if bone_line_only:
            nearby = list(self._iter_nearby_bones(context))
            return min(nearby, key=lambda t: t[1])[0] if nearby else None

        region = context.region
        rv3d = context.region_data
        mouse_vec = Vector((self.mouse_x, self.mouse_y))
        vg_names = {vg.name for vg in obj.vertex_groups}
        deform_bones = {b.name for b in armature.data.bones if b.use_deform}

        closest_bone = None
        closest_dist = float('inf')

        for bone in armature.pose.bones:
            if bone.name not in deform_bones or bone.name not in vg_names:
                continue
            head_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, armature.matrix_world @ bone.head)
            tail_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, armature.matrix_world @ bone.tail)
            if head_2d is None or tail_2d is None:
                continue
            dist = self._seg_dist_2d(mouse_vec, head_2d, tail_2d)
            if dist < closest_dist and dist < self.bone_detect_dist:
                closest_dist = dist
                closest_bone = bone.name

        if closest_bone:
            return closest_bone

        ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, mouse_vec)
        ray_dir    = view3d_utils.region_2d_to_vector_3d(region, rv3d, mouse_vec)
        depsgraph  = context.evaluated_depsgraph_get()
        hit, loc, norm, idx, hit_obj, matrix = context.scene.ray_cast(depsgraph, ray_origin, ray_dir)

        # hit_obj is the evaluated object; compare via .original to match the base object
        if hit and hit_obj is not None and hit_obj.original is obj:
            # Use original mesh for vertex group data (evaluated mesh may lack .groups)
            mesh = obj.data
            poly = mesh.polygons[idx]
            closest_v = None
            closest_v_dist = float('inf')
            for v_idx in poly.vertices:
                v = mesh.vertices[v_idx]
                v_world = obj.matrix_world @ v.co
                v_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, v_world)
                if v_2d:
                    d = (v_2d - mouse_vec).length
                    if d < closest_v_dist and d < self.vertex_search_radius:
                        closest_v_dist = d
                        closest_v = v
            if closest_v and closest_v.groups:
                max_w = -1.0
                best = None
                for g in closest_v.groups:
                    if g.group < len(obj.vertex_groups):
                        g_name = obj.vertex_groups[g.group].name
                        if g_name in deform_bones and g_name in vg_names and g.weight > max_w:
                            max_w = g.weight
                            best = g_name
                return best

        return None

    def _get_armature(self, obj):
        for mod in obj.modifiers:
            if mod.type == 'ARMATURE':
                return mod.object
        return None

    def invoke(self, context, event):
        obj = context.active_object
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
        # Mode-aware -- this modal only ever runs in Edit Mode (poll()
        # requires CoreFacade.is_editing_weights()), so the live pool lives
        # in __ssp_pool, not storage.selected_names directly (not reliably
        # undo-tracked while in Edit Mode).
        self._initial_selected_names = ctrl_snapshot.get_selected_bones_pool_string() if obj else ""
        self._initial_selection_history = obj.superskin_storage.selection_history if obj else ""
        self.is_sweep_add = False
        self.is_sweep_remove = False
        self._multi_mode = False
        self._last_hovered = None

        storage = getattr(obj, "superskin_storage", None)
        self.session_pool = ctrl_snapshot.get_selected_bones_pool() if obj else set()

        self.session_recent_bone = None
        if storage and 0 <= storage.last_clicked_index < len(obj.vertex_groups):
            self.session_recent_bone = obj.vertex_groups[storage.last_clicked_index].name

        # Snapshot of the active-bone/mask state hover preview is about to
        # start mutating live, so a RIGHTMOUSE/ESC cancel can restore it
        # exactly (see _cancel_revert).
        self._initial_active_is_mask = storage.active_is_mask if storage else False
        self._initial_active_orphan_name = storage.active_orphan_name if storage else ""
        self._initial_active_bone_name = ""
        if storage and not self._initial_active_is_mask and 0 <= storage.last_clicked_index < len(obj.vertex_groups):
            self._initial_active_bone_name = obj.vertex_groups[storage.last_clicked_index].name

        # Pin the overlay's active-color display to the pre-session bone --
        # the MOUSEMOVE hover handler below live-applies the hovered bone as
        # the real active bone (for the mesh-surface weight-color preview),
        # which would otherwise repaint the overlay's wedge on every hover
        # move too. Only real commits (_sweep_add_bone/_sweep_remove_bone)
        # advance this override during the session; _cancel_revert()/the
        # confirm path clear it again on exit.
        _deform_overlay.set_active_override(self._initial_active_bone_name or None)

        self._set_picker_cursor(context)
        # Seed the crosshair's position immediately at the invoke() mouse
        # location -- otherwise, since the native cursor is now fully
        # hidden ('NONE'), there'd be a visible gap with no cursor at all
        # until the first MOUSEMOVE event updates _hover_mouse_pos below.
        _deform_overlay.set_hover(None, (self.mouse_x, self.mouse_y))
        _deform_overlay.set_cursor_badge(None)
        _deform_overlay.set_holding(True)
        context.area.tag_redraw()
        # start_bone_picker_draw() was a no-op; removed in Phase 8
        global _bone_picker_active
        _bone_picker_active = True
        context.window_manager.modal_handler_add(self)
        CoreFacade.debug_log(
            "adhoc:bone_picker",
            f"invoke: initial_bone={self._initial_active_bone_name!r} "
            f"vg_index={self.initial_vg_index} mask={self._initial_active_is_mask}",
        )
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
