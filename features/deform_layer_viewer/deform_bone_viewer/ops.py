"""Deform Bone Viewer operators — per-row bone lock toggle, select all,
vertex selection by influence, Show Affecting Bones popup, and influence
popup menus.

Relocated from operators/ops_layers_tool.py (bone list operators) and
operators/ops_bones_tool.py (vertex selection, bone inspection, and popup
influence menu). ops_bones_tool.py has been removed entirely.
"""

import bpy
import bmesh
import traceback

from ....core.facade import CoreFacade
from ....interface.utils.utils import _is_valid_mesh, exit_mask_mode_if_active


# ==============================================================================
# BONE LIST OPERATORS
# ==============================================================================

class SUPERSKIN_OT_toggle_vg_lock(bpy.types.Operator):
    bl_idname = "superskin.toggle_vg_lock"
    bl_label = "Toggle Vertex Group Lock"
    bl_options = {'INTERNAL', 'UNDO'}

    index: bpy.props.IntProperty()
    vg_name: bpy.props.StringProperty()

    def execute(self, context):
        obj = context.active_object
        if not obj:
            return {'CANCELLED'}

        # index is only meaningful for real bone rows -- orphan rows are
        # drawn with item.vg_index == -1 (see sync_bones_to_ui_collection()),
        # since they have no real VertexGroup to index into. The rest of
        # this function only ever keys off vg_name (bone locks are stored
        # {bone_name: bool} in layer metadata, not by index), so validating
        # against vg_list here rejected every orphan-row lock click before
        # it could do anything -- clicked_name being empty is the only
        # actually-invalid case.
        clicked_name = self.vg_name
        if not clicked_name:
            return {'CANCELLED'}

        ctrl = CoreFacade(context)

        # Read current state from metadata — metadata is the single source of
        # truth for bone locks, not the native VertexGroup.lock_weight field.
        current_locks = ctrl.get_bone_locks()
        new_lock_state = not current_locks.get(clicked_name, False)

        # Mode-aware pool read — see get_selected_bones_pool()'s docstring.
        pool = ctrl.get_selected_bones_pool()
        new_locks = dict(current_locks)
        if clicked_name in pool:
            for item in obj.superskin_bones_collection:
                if item.name in pool:
                    new_locks[item.name] = new_lock_state
        else:
            new_locks[clicked_name] = new_lock_state

        ctrl.set_bone_locks(new_locks)

        # Sync the UI mirror collection so draw_extra_icon reflects the new
        # state immediately — without this, tag_redraw redraws stale values.
        for item in obj.superskin_bones_collection:
            item.lock_weight = new_locks.get(item.name, False)

        context.area.tag_redraw()
        return {'FINISHED'}


class SUPERSKIN_OT_cycle_bone_list_filter(bpy.types.Operator):
    """Step the Deform Bones list's ``bone_list_filter_mode`` forward
    (Show All -> Influence -> Show All). Orphan rows are always visible
    regardless of which state is active (see
    ``_extra_keep_predicate_impl()`` in ``ui.py``), so this cycle no
    longer needs a separate 'Orphan' state -- Influence already folds
    them in."""

    bl_idname = "superskin.cycle_bone_list_filter"
    bl_label = "Cycle Bone List Filter"
    bl_description = "Cycle the Deform Bones list filter between Show All and Influence"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        adv = context.scene.superskin_adv_settings
        adv.bone_list_filter_mode = (
            'NONE' if adv.bone_list_filter_mode == 'INFLUENCE' else 'INFLUENCE'
        )
        context.area.tag_redraw()
        return {'FINISHED'}


class SUPERSKIN_OT_select_all_vgs(bpy.types.Operator):
    bl_idname = "superskin.select_all_vgs"
    bl_label = "Select All Visible Influences"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        obj = context.active_object
        if not obj or not (obj.vertex_groups or obj.superskin_bones_collection):
            return {'CANCELLED'}

        # Real bones + orphan bones (weight ops already target orphans fine
        # via synthetic IDs -- get_unified_mapping(), docs/bug-history/0017 --
        # so Select All shouldn't silently drop them from the pool) + Mask.
        # Temp __ssp_* VGs are excluded -- they're never a real bone/row.
        names = {vg.name for vg in obj.vertex_groups if not vg.name.startswith("__ssp_")}
        mask_name = None
        for item in obj.superskin_bones_collection:
            if item.is_mask:
                mask_name = item.name
            elif item.is_orphan:
                names.add(item.name)
        if mask_name:
            names.add(mask_name)

        ctrl = CoreFacade(context)
        ctrl.set_selected_bones_pool(names)

        # Persist selection to the active layer
        try:
            ctrl.set_selected_bones(ctrl.get_selected_bones_pool_string())
        except Exception:
            pass

        context.area.tag_redraw()
        return {'FINISHED'}


# ==============================================================================
# SELECT AFFECTED VERTICES
# ==============================================================================

_WEIGHT_EPSILON = 0.001  # matches core_subsystems' own "has weight" epsilon (vertices_with_weight)


class OBJECT_OT_mw_select_affect_vertices(bpy.types.Operator):
    bl_idname = "object.mw_select_affect_vertices"
    bl_label = "Select Affect Vertices"
    bl_description = (
        "Select all vertices affected in the current context — "
        "bone weight > 0 on the active bone (Deform Bones tab), "
        "or explicit mask override on the active layer (Layers tab)"
    )
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if not _is_valid_mesh(obj):
            return {'CANCELLED'}

        if obj.mode != 'EDIT':
            bpy.ops.object.mode_set(mode='EDIT')

        try:
            ctrl = CoreFacade(context)
            data_ops = CoreFacade.get_clipboard_data_ops()
            if ctrl.is_mask_context():
                mask_dict = ctrl.get_active_mask_dict()
                affected_indices = data_ops.vertices_with_mask_override(mask_dict)
            else:
                active_id = ctrl.get_active_vg_id()
                if active_id is None:
                    raise ValueError("No active Vertex Group selected")
                active_name = obj.vertex_groups[active_id].name
                layer_dict = ctrl.read_active_layer()
                affected_indices = data_ops.vertices_with_weight(layer_dict, active_name)
        except ValueError as e:
            self.report({'WARNING'}, str(e))
            return {'CANCELLED'}

        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()

        for v in bm.verts:
            v.select = (v.index in affected_indices)

        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data)

        return {'FINISHED'}


# ==============================================================================
# SELECT AFFECT BOUNDARY (junction between weight-0 and weighted regions)
# ==============================================================================

class OBJECT_OT_mw_select_affect_boundary(bpy.types.Operator):
    """Select vertices sitting at the boundary/junction between the
    unweighted (0) and weighted region of the active bone/mask context --
    every *weighted* vertex that has at least one zero-weight neighbor.
    Only the weighted side of the boundary is selected -- zero-weight
    vertices are never selected, even the ones directly touching the
    influence region. No adjustable properties, so no redo/options popup."""
    bl_idname = "object.mw_select_affect_boundary"
    bl_label = "Select Affect Boundary Vertices"
    bl_description = (
        "Select weighted vertices sitting at the edge of the active "
        "bone/mask context's influence region (bordering unweighted "
        "vertices), excluding the unweighted vertices themselves"
    )
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if not _is_valid_mesh(obj):
            return {'CANCELLED'}

        if obj.mode != 'EDIT':
            bpy.ops.object.mode_set(mode='EDIT')

        try:
            ctrl = CoreFacade(context)
            if ctrl.is_mask_context():
                mask_dict = ctrl.get_active_mask_dict()
                weight_of = lambda v_idx: mask_dict.get(v_idx, 0.0)
            else:
                active_id = ctrl.get_active_vg_id()
                if active_id is None:
                    raise ValueError("No active Vertex Group selected")
                active_name = obj.vertex_groups[active_id].name
                layer_dict = ctrl.read_active_layer()
                weight_of = lambda v_idx: layer_dict.get(v_idx, {}).get(active_name, 0.0)
            neighbors = ctrl.get_cached_mesh_neighbors()
        except ValueError as e:
            self.report({'WARNING'}, str(e))
            return {'CANCELLED'}

        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()

        has_weight = {v.index: weight_of(v.index) > _WEIGHT_EPSILON for v in bm.verts}
        boundary_indices = set()
        for v in bm.verts:
            if not has_weight[v.index]:
                # Skip the unweighted side entirely -- only the weighted
                # vertices right at the edge of the influence region get
                # selected, not their zero-weight neighbors across it.
                continue
            for n_idx in neighbors.get(v.index, ()):
                if not has_weight.get(n_idx, False):
                    boundary_indices.add(v.index)
                    break

        for v in bm.verts:
            v.select = (v.index in boundary_indices)

        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data)

        return {'FINISHED'}


# ==============================================================================
# SHOW AFFECTING BONES
# ==============================================================================

class MESH_OT_show_affect_bone(bpy.types.Operator):
    """List bones influencing the current vertex selection; selecting an
    entry from the popup menu activates that vertex group."""

    bl_idname = "mesh.show_affect_bone"
    bl_label = "Show Affecting Bones"
    bl_options = {'REGISTER', 'UNDO'}

    bone_name: bpy.props.StringProperty()

    @classmethod
    def poll(cls, context):
        return (
            context.mode == 'EDIT_MESH'
            and context.active_object
            and context.active_object.type == 'MESH'
        )

    def execute(self, context):
        if self.bone_name:
            obj = context.active_object
            if self.bone_name in obj.vertex_groups:
                vg = obj.vertex_groups[self.bone_name]
                storage = obj.superskin_storage
                ctrl = CoreFacade(context)
                # Mode-aware -- routes to __ssp_pool in Edit Mode instead of
                # writing storage.selected_names directly (not reliably
                # undo-tracked while in Edit Mode).
                ctrl.clear_all_selected(obj)
                ctrl.add_vg_selected(obj, self.bone_name)
                storage.selection_history = str(vg.index)
                storage.last_clicked_index = vg.index
                try:
                    ctrl.set_selected_bones(ctrl.get_selected_bones_pool_string())
                    ctrl.set_active_bone_name(self.bone_name)
                except Exception:
                    pass
                self.report({'INFO'}, f"Selected Vertex Group: {self.bone_name}")
            else:
                self.report({'WARNING'}, f"ไม่พบ Vertex Group ชื่อ {self.bone_name} ใน Object นี้")
            return {'FINISHED'}

        context.window_manager.popup_menu(self.draw_menu, title="Bones Influencing Selection")
        return {'FINISHED'}

    def draw_menu(self, menu, context):
        layout = menu.layout
        obj = context.active_object

        obj.update_from_editmode()
        selected_v_indices = {v.index for v in obj.data.vertices if v.select}

        influencing_bones = set()

        if selected_v_indices:
            try:
                ctrl = CoreFacade(context)
                layer_dict = ctrl.get_active_layer_weights_for_display()
            except ValueError:
                layer_dict = {}

            for v_idx in selected_v_indices:
                for bone_name, weight in layer_dict.get(v_idx, {}).items():
                    if weight > 0.001:
                        influencing_bones.add(bone_name)

        if influencing_bones:
            for bone in sorted(influencing_bones):
                props = layout.operator(self.bl_idname, text=bone, icon='GROUP_VERTEX')
                props.bone_name = bone
        else:
            layout.label(text="No influencing bones found or no vertex selected", icon='ERROR')


# ==============================================================================
# POPUP INFLUENCES DIALOG
# ==============================================================================

class OBJECT_OT_mw_popup_affect_influences(bpy.types.Operator):
    bl_idname = "object.mw_popup_affect_influences"
    bl_label = "Affecting Influences"
    bl_description = "Show influences affecting selected vertices"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if not _is_valid_mesh(obj):
            self.report({'WARNING'}, "No active mesh")
            return {'CANCELLED'}

        if obj.mode != 'EDIT':
            bpy.ops.object.mode_set(mode='EDIT')

        obj.update_from_editmode()
        selected_verts = [v for v in obj.data.vertices if v.select]

        if not selected_verts:
            self.report({'WARNING'}, "No vertices selected")
            return {'CANCELLED'}

        group_indices = set()
        for v in selected_verts:
            for g in v.groups:
                if g.weight > 0.001:
                    group_indices.add(g.group)

        if not group_indices:
            self.report({'WARNING'}, "Selected vertices have no weights")
            return {'CANCELLED'}

        self._group_names = [
            obj.vertex_groups[i].name
            for i in sorted(group_indices)
            if i < len(obj.vertex_groups)
        ]

        return context.window_manager.invoke_props_dialog(self, width=280)

    def invoke(self, context, event):
        self._group_names = []
        return self.execute(context)

    def draw(self, context):
        layout = self.layout
        layout.label(text="Influences on selection:", icon='BONE_DATA')
        layout.separator()

        if not self._group_names:
            layout.label(text="Nothing found", icon='INFO')
            return

        box = layout.box()
        for name in self._group_names:
            row = box.row(align=True)
            op = row.operator(
                "object.mw_select_specific_vertex_group",
                text=name,
                icon='VERTEX_GROUP'
            )
            op.group_name = name


class OBJECT_OT_mw_select_specific_vertex_group(bpy.types.Operator):
    bl_idname = "object.mw_select_specific_vertex_group"
    bl_label = "Select Vertex Group"
    bl_options = {'INTERNAL'}

    group_name: bpy.props.StringProperty()

    def execute(self, context):
        obj = context.active_object
        if obj and self.group_name in obj.vertex_groups:
            vg = obj.vertex_groups[self.group_name]
            storage = obj.superskin_storage
            ctrl = CoreFacade(context)
            # Mode-aware -- routes to __ssp_pool in Edit Mode instead of
            # writing storage.selected_names directly (not reliably
            # undo-tracked while in Edit Mode).
            ctrl.clear_all_selected(obj)
            ctrl.add_vg_selected(obj, self.group_name)
            storage.selection_history = str(vg.index)
            storage.last_clicked_index = vg.index
            try:
                ctrl.set_selected_bones(ctrl.get_selected_bones_pool_string())
                ctrl.set_active_bone_name(self.group_name)
            except Exception:
                pass
        return {'FINISHED'}


# ==============================================================================
# POPUP INFLUENCES MENU
# ==============================================================================

class MT_mw_popup_affect_influences_menu(bpy.types.Menu):
    bl_label = "Affecting Influences"
    bl_idname = "VIEW3D_MT_superskin_affect_influences"

    def draw(self, context):
        layout = self.layout
        obj = context.active_object

        if not _is_valid_mesh(obj):
            layout.label(text="No active mesh")
            return

        active_index = obj.superskin_storage.last_clicked_index
        if not (0 <= active_index < len(obj.vertex_groups)):
            layout.label(text="No active Vertex Group", icon='WARNING')
            return
        active_vg_name = obj.vertex_groups[active_index].name

        if obj.mode == 'EDIT':
            obj.update_from_editmode()

        target_vertices = []
        selected_verts = [v for v in obj.data.vertices if v.select]

        if selected_verts:
            for v in selected_verts:
                for g in v.groups:
                    if g.group == active_index and g.weight > 0.001:
                        target_vertices.append(v)
                        break
        else:
            for v in obj.data.vertices:
                for g in v.groups:
                    if g.group == active_index and g.weight > 0.001:
                        target_vertices.append(v)
                        break

        if not target_vertices:
            layout.label(text=f"'{active_vg_name}' has no weight on mesh", icon='INFO')
            return

        group_indices = set()
        for v in target_vertices:
            for g in v.groups:
                if g.weight > 0.001 and g.group != active_index:
                    group_indices.add(g.group)

        if not group_indices:
            layout.label(text="100% Clean Weight (No other influences)", icon='CHECKMARK')
            return

        layout.label(text=f"Shared Influences with '{active_vg_name}':", icon='BONE_DATA')
        layout.separator()

        for g_id in sorted(group_indices):
            if g_id < len(obj.vertex_groups):
                g_name = obj.vertex_groups[g_id].name
                prop = layout.operator(
                    "object.mw_select_specific_vertex_group",
                    text=g_name,
                    icon='VERTEX_GROUP'
                )
                prop.group_name = g_name


# ==============================================================================
# TOGGLE MASK MODE
# ==============================================================================

class SUPERSKIN_OT_toggle_mask_mode(bpy.types.Operator):
    """Switch bone-weight/mask editing sub-mode without disturbing the
    Deform Bones list's own bone selection (last_clicked_index /
    active_orphan_name / selected_names are left untouched either way).
    Replaces the former virtual Mask row -- mirrors
    BoneListAdapter.on_single_select()'s two branches exactly, just without
    going through write_selection().

    **Current call site (2026-09-13, several revisions the same day --
    see ``docs/core-interfaces/mode-edit-toggle-widget.md`` for the full
    history):** dispatched ONLY by the standalone "Edit Mask" button
    (``interface/utils/mode_edit_toggle.py::draw_edit_mask_button()``),
    always with ``target='TOGGLE'`` -- a genuine blind flip of whatever
    ``active_is_mask`` currently holds, same as the Alt+1 keymap's own
    (now-removed) call used. This is NOT optional: an earlier revision had
    this button pass ``target='MASK'`` explicitly, which made ``execute()``
    a GUARANTEED no-op once the mesh was already in Mask sub-mode (see the
    ``is_explicit and storage.active_is_mask == want_mask`` early-return
    below) -- with no other button left to switch back to Bone sub-mode
    (the former separate "Edit Bone" button was folded into the
    Object/Pose/Edit-Bone cycle button, whose "Edit Bone" state exits the
    whole session instead of switching sub-mode), that left NO way back
    once Mask mode was entered -- reported as "I can't toggle the Edit
    Mask button." Fixed by switching to ``target='TOGGLE'``.
    ``draw_edit_mask_button()`` also only ever draws this button while
    already inside a live session, so the ``target='WEIGHT'``/``'MASK'``
    explicit-landing behavior below is effectively unreachable from the UI
    today (kept on the ``EnumProperty`` for backward compatibility and any
    future caller that still wants an explicit-target landing, e.g. one
    that draws separate Bone/Mask buttons again).

    ``enter_edit_idname`` additionally lets this operator be used from a
    NOT-yet-editing state: when given and ``CoreFacade.is_editing_weights()``
    is False, ``execute()`` dispatches that operator by bl_idname (e.g.
    ``superskin.enter_layer_edit`` / ``object.mw_toggle_edit_mode``) to
    enter the session first. If ``target`` is still ``'TOGGLE'``, it
    returns immediately WITHOUT touching ``active_is_mask`` -- entering
    already lands in whichever sub-mode was last active (``active_is_mask``
    is a flag persisted on the mesh, not session state, so it survives
    across Edit Mode entries/exits on its own). If ``target`` is
    ``'WEIGHT'``/``'MASK'``, execution falls through to also land the
    freshly-opened session in that exact sub-mode. In practice this
    cold-entry branch is dead code too now, since ``draw_edit_mask_button()``
    never draws while not already editing -- kept for the same
    backward-compatibility reason as the explicit ``target`` values above.
    A cross-domain bl_idname reference, same as every other operator
    string this widget already dispatches by name -- not a Python import,
    so this does not violate Zero Cross-Imports between feature packages."""

    bl_idname = "superskin.toggle_mask_mode"
    bl_label = "Toggle Mask Edit"
    bl_options = {'INTERNAL'}

    enter_edit_idname: bpy.props.StringProperty(
        name="Enter Edit Operator",
        description="bl_idname to dispatch first if not already editing",
        default="",
    )
    target: bpy.props.EnumProperty(
        name="Target Sub-Mode",
        description=(
            "Sub-mode to land in explicitly ('TOGGLE' = blind flip of "
            "whatever is currently active, the Alt+1 keymap's own default)"
        ),
        items=(
            ('TOGGLE', "Toggle", "Blind flip of the current sub-mode"),
            ('WEIGHT', "Bone Weight", "Land in bone-weight editing"),
            ('MASK', "Mask", "Land in mask editing"),
        ),
        default='TOGGLE',
    )

    @classmethod
    def poll(cls, context):
        # Deliberately permissive (just "some active object exists") --
        # this operator is also used to ENTER editing from Object/Pose Mode
        # (see enter_edit_idname above), where context.active_object may
        # legitimately be an Armature in Pose Mode rather than the Mesh
        # itself (layer_viewer's Pose-Mode-compatible entry flow -- see
        # docs/domains/deform_layer_viewer.md's "Edit Layer Weight is also
        # unlocked in Pose Mode"). poll() cannot see this instance's own
        # enter_edit_idname (Blender operator poll() is a bare classmethod,
        # no access to per-button property overrides set after
        # layout.operator() returns), so real validation happens in
        # execute() instead -- both for the direct mask-toggle case (the
        # `obj.type != 'MESH'` guard below) and for entering (the wrapped
        # operator's own poll() is honored when dispatched, and a failure
        # there is caught and reported rather than left to crash).
        return context.active_object is not None

    def execute(self, context):
        if self.enter_edit_idname and not CoreFacade.is_editing_weights():
            mod_name, op_name = self.enter_edit_idname.split(".", 1)
            try:
                result = getattr(getattr(bpy.ops, mod_name), op_name)()
            except RuntimeError as e:
                self.report({'WARNING'}, str(e))
                return {'CANCELLED'}
            if 'FINISHED' not in result:
                return result
            if self.target == 'TOGGLE':
                # Blind flip requested (no explicit target) -- entering
                # already preserved whatever active_is_mask already was
                # (see the class docstring) -- nothing left to flip.
                return {'FINISHED'}
            # An explicit target was requested (one of the two split "Edit
            # Bone"/"Edit Mask" buttons) -- fall through below to also
            # land the freshly-opened session in that exact sub-mode, even
            # if it differs from whatever was last stored on the mesh.

        obj = context.active_object
        if not obj or obj.type != 'MESH':
            return {'CANCELLED'}

        storage = obj.superskin_storage
        ctrl = CoreFacade(context)

        is_explicit = self.target != 'TOGGLE'
        want_mask = (self.target == 'MASK') if is_explicit else (not storage.active_is_mask)
        if is_explicit and storage.active_is_mask == want_mask:
            # Already in the requested sub-mode -- an explicit-target
            # button is a no-op when clicked redundantly, unlike the
            # blind Alt+1 toggle.
            return {'FINISHED'}

        if want_mask:
            try:
                ctrl.clear_orphan_weight_preview()
            except Exception:
                pass
            storage.active_is_mask = True
            try:
                ctrl.apply_active_bone()
                ctrl.finish()
            except Exception:
                traceback.print_exc()
        else:
            exit_mask_mode_if_active(context, obj)
            storage.active_is_mask = False
            try:
                ctrl.set_selected_bones(ctrl.get_selected_bones_pool_string())
                vg_list = obj.vertex_groups
                if 0 <= storage.last_clicked_index < len(vg_list):
                    ctrl.set_active_bone_name(vg_list[storage.last_clicked_index].name)
                ctrl.apply_active_bone()
                # apply_active_bone() only repoints the active VG/preview --
                # it does not reflatten storage layers onto the real deform
                # Vertex Groups. Without an explicit finish() here, whatever
                # mask painting just did stays invisible on the actual
                # Armature-deformed mesh until something else forces a full
                # pipeline pass (previously only "Save Weights & Exit" did),
                # which reads as "the deform doesn't bake until I hit Save".
                ctrl.finish()
            except Exception:
                traceback.print_exc()

        context.area.tag_redraw()
        return {'FINISHED'}


# ==============================================================================
# SAVE WEIGHT AND EXIT
# ==============================================================================

class SUPERSKIN_OT_save_weight_and_exit(bpy.types.Operator):
    """Bake temporary vertex group weights into custom properties storage and clear temp layers"""
    bl_idname = "superskin.save_weight_and_exit"
    bl_label = "Save"
    bl_description = "Commit modified temporary weights back into layer custom properties storage"
    # No 'UNDO' here — this operator no longer mutates anything itself, it
    # only dispatches to superskin.save_weights, which already pushes its
    # own single undo step. Adding 'UNDO' here too would push a second,
    # redundant snapshot of the same state change on every save.
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'MESH'

    def execute(self, context):
        obj = context.active_object

        # Delegates to superskin.save_weights (features/controller/ops_scene_modes.py)
        # instead of re-reading/re-writing temp VGs here directly. Features
        # are forbidden from importing each other's Python modules, but
        # dispatching through a registered operator's own bl_idname is the
        # sanctioned way to reuse another domain's logic without a hard
        # import -- and controller is explicitly the cross-cutting domain
        # for this kind of scene-mode transition (see the Domain Registry
        # table in CLAUDE.md).
        #
        # This used to be an independent reimplementation that: read temp
        # VGs via a different path than the Tab/auto-save-guard exit,
        # never called finish() (so the real deform vertex groups could go
        # stale until something else forced a reflatten), and pushed an
        # extra explicit ed.undo_push() on top of the automatic one every
        # UNDO-tagged operator already gets — that redundant undo snapshot
        # was a meaningful, avoidable chunk of the slowness reported
        # against a plain Tab exit. superskin.save_weights does none of
        # that: bake happens once, while still in Edit Mode, before a
        # single mode_set('OBJECT'), then finish() reflattens once.
        if obj.mode == 'EDIT':
            return bpy.ops.superskin.save_weights()
        return {'FINISHED'}


# ==============================================================================
# REGISTRATION
# ==============================================================================

_classes = (
    SUPERSKIN_OT_toggle_vg_lock,
    SUPERSKIN_OT_cycle_bone_list_filter,
    SUPERSKIN_OT_select_all_vgs,
    OBJECT_OT_mw_select_affect_vertices,
    OBJECT_OT_mw_select_affect_boundary,
    MESH_OT_show_affect_bone,
    OBJECT_OT_mw_popup_affect_influences,
    OBJECT_OT_mw_select_specific_vertex_group,
    MT_mw_popup_affect_influences_menu,
    SUPERSKIN_OT_toggle_mask_mode,
    SUPERSKIN_OT_save_weight_and_exit,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
