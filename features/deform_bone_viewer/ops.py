"""Deform Bone Viewer operators — per-row bone lock toggle, select all,
vertex selection by influence, Show Affecting Bones popup, and influence
popup menus.

Relocated from operators/ops_layers_tool.py (bone list operators) and
operators/ops_bones_tool.py (vertex selection, bone inspection, and popup
influence menu). ops_bones_tool.py has been removed entirely.
"""

import bpy
import bmesh

from ...core.facade import CoreFacade
from ...interface.utils.utils import _is_valid_mesh

# Hoisted from function bodies — converted from absolute 'SuperSkinPro.*'
# imports to relative imports for Blender Extensions Platform compatibility.
from ...core_subsystems.layer_compositor import LayerCompositor


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

        new_locks = dict(current_locks)
        if f",{clicked_name}," in obj.superskin_storage.selected_names:
            for item in obj.superskin_bones_collection:
                if f",{item.name}," in obj.superskin_storage.selected_names:
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


class SUPERSKIN_OT_select_all_vgs(bpy.types.Operator):
    bl_idname = "superskin.select_all_vgs"
    bl_label = "Select All Visible Influences"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        obj = context.active_object
        if not obj or not (obj.vertex_groups or obj.superskin_bones_collection):
            return {'CANCELLED'}

        storage = obj.superskin_storage
        # Real bones + orphan bones (weight ops already target orphans fine
        # via synthetic IDs -- get_unified_mapping(), docs/bug-history/0017 --
        # so Select All shouldn't silently drop them from the pool) + Mask.
        names = [vg.name for vg in obj.vertex_groups]
        mask_name = None
        for item in obj.superskin_bones_collection:
            if item.is_mask:
                mask_name = item.name
            elif item.is_orphan:
                names.append(item.name)
        if mask_name:
            names.append(mask_name)
        storage.selected_names = f",{','.join(names)},"

        # Persist selection to the active layer
        try:
            ctrl = CoreFacade(context)
            ctrl.set_selected_bones(storage.selected_names)
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
                LayerCompositor.clear_all_selected(obj)
                LayerCompositor.add_vg_selected(obj, self.bone_name)
                storage.selection_history = str(vg.index)
                storage.last_clicked_index = vg.index
                try:
                    ctrl = CoreFacade(context)
                    ctrl.set_selected_bones(storage.selected_names)
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
            LayerCompositor.clear_all_selected(obj)
            LayerCompositor.add_vg_selected(obj, self.group_name)
            storage.selection_history = str(vg.index)
            storage.last_clicked_index = vg.index
            try:
                ctrl = CoreFacade(context)
                ctrl.set_selected_bones(storage.selected_names)
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
    SUPERSKIN_OT_select_all_vgs,
    OBJECT_OT_mw_select_affect_vertices,
    OBJECT_OT_mw_select_affect_boundary,
    MESH_OT_show_affect_bone,
    OBJECT_OT_mw_popup_affect_influences,
    OBJECT_OT_mw_select_specific_vertex_group,
    MT_mw_popup_affect_influences_menu,
    SUPERSKIN_OT_save_weight_and_exit,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
