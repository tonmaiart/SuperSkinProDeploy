
import bpy
import numpy as np
import traceback

from ....core.facade import CoreFacade
from ...weight_apply.public_api import read_vertex_select, write_vertex_select
from ....interface.utils.utils import _is_valid_mesh, exit_mask_mode_if_active
from ....interface.template_ui.select_ops import (
    get_adapter,
)



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

        clicked_name = self.vg_name
        if not clicked_name:
            return {'CANCELLED'}

        ctrl = CoreFacade(context)

        current_locks = ctrl.get_bone_locks()
        new_lock_state = not current_locks.get(clicked_name, False)

        pool = ctrl.get_selected_bones_pool()
        new_locks = dict(current_locks)
        if clicked_name in pool:
            for item in obj.superskin_bones_collection:
                if item.name in pool:
                    new_locks[item.name] = new_lock_state
        else:
            new_locks[clicked_name] = new_lock_state

        ctrl.set_bone_locks(new_locks)

        for item in obj.superskin_bones_collection:
            item.lock_weight = new_locks.get(item.name, False)

        context.area.tag_redraw()
        return {'FINISHED'}


_LOCK_ACTION_ITEMS = (
    ('LOCK_ALL', "Lock All", "Lock every bone"),
    ('LOCK_SELECTED', "Lock Selected", "Lock the selected bones"),
    ('LOCK_UNSELECTED', "Lock Unselected", "Lock the unselected bones"),
    ('UNLOCK_ALL', "Unlock All", "Unlock every bone"),
    ('UNLOCK_SELECTED', "Unlock Selected", "Unlock the selected bones"),
    ('UNLOCK_UNSELECTED', "Unlock Unselected", "Unlock the unselected bones"),
    ('INVERT', "Invert Locks", "Invert the lock state of every bone"),
)


class SUPERSKIN_OT_bone_list_set_locks(bpy.types.Operator):
    """Lock, unlock or invert bone weight locks by selection"""

    bl_idname = "superskin.bone_list_set_locks"
    bl_label = "Set Bone Locks"
    bl_options = {'INTERNAL', 'UNDO'}

    action: bpy.props.EnumProperty(items=_LOCK_ACTION_ITEMS)

    @classmethod
    def description(cls, context, properties):
        for ident, _name, desc in _LOCK_ACTION_ITEMS:
            if ident == properties.action:
                return desc
        return cls.__doc__

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.type == 'MESH'
                and hasattr(obj, "superskin_bones_collection")
                and "ss_layers_meta" in obj.data)

    def execute(self, context):
        obj = context.active_object
        ctrl = CoreFacade(context)
        current_locks = ctrl.get_bone_locks()
        pool = ctrl.get_selected_bones_pool()
        action = self.action

        new_locks = dict(current_locks)
        for item in obj.superskin_bones_collection:
            if item.is_mask:
                continue
            name = item.name
            selected = name in pool
            locked = current_locks.get(name, False)
            if action == 'LOCK_ALL':
                locked = True
            elif action == 'LOCK_SELECTED':
                locked = locked or selected
            elif action == 'LOCK_UNSELECTED':
                locked = locked or not selected
            elif action == 'UNLOCK_ALL':
                locked = False
            elif action == 'UNLOCK_SELECTED':
                locked = locked and not selected
            elif action == 'UNLOCK_UNSELECTED':
                locked = locked and selected
            elif action == 'INVERT':
                locked = not locked
            new_locks[name] = locked

        ctrl.set_bone_locks(new_locks)

        for item in obj.superskin_bones_collection:
            item.lock_weight = new_locks.get(item.name, False)

        if context.area:
            context.area.tag_redraw()
        return {'FINISHED'}


_SELECT_BY_LOCK_ITEMS = (
    ('ALL', "Select All", "Select every bone"),
    ('LOCKED', "Select Locked", "Select the locked bones"),
    ('UNLOCKED', "Select Unlocked", "Select the unlocked bones"),
    ('INVERT', "Invert Selection", "Invert the bone selection"),
)


class SUPERSKIN_OT_bone_list_select_by_lock(bpy.types.Operator):
    """Select bones by lock state, or invert the bone selection"""

    bl_idname = "superskin.bone_list_select_by_lock"
    bl_label = "Select Bones"
    bl_options = {'INTERNAL', 'UNDO'}

    action: bpy.props.EnumProperty(items=_SELECT_BY_LOCK_ITEMS)

    @classmethod
    def description(cls, context, properties):
        for ident, _name, desc in _SELECT_BY_LOCK_ITEMS:
            if ident == properties.action:
                return desc
        return cls.__doc__

    @classmethod
    def poll(cls, context):
        return SUPERSKIN_OT_bone_list_set_locks.poll(context)

    def execute(self, context):
        obj = context.active_object
        ctrl = CoreFacade(context)
        locks = ctrl.get_bone_locks()
        adapter = get_adapter('BONES')
        pool, active_key, _history = adapter.read_selection(context, obj)
        if active_key is not None:
            pool = pool | {active_key}
        action = self.action

        names = set()
        for item in obj.superskin_bones_collection:
            if item.is_mask:
                continue
            name = item.name
            locked = locks.get(name, False)
            if action == 'ALL':
                keep = True
            elif action == 'LOCKED':
                keep = locked
            elif action == 'UNLOCKED':
                keep = not locked
            else:
                keep = name not in pool
            if keep:
                names.add(name)

        if not names or active_key in names or obj.superskin_storage.active_is_mask:
            ctrl.set_selected_bones_pool(names)
        else:
            visual_order = adapter.get_keys_in_visual_order(context, obj)
            history = [k for k in visual_order if k in names]
            new_active = history[0] if history else min(names)
            was_suppressing = context.scene.superskin_internal_transaction
            context.scene.superskin_internal_transaction = True
            try:
                adapter.write_selection(context, obj, names, new_active, history or [new_active])
                adapter.on_single_select(context, obj, new_active)
            finally:
                context.scene.superskin_internal_transaction = was_suppressing
            for i, item in enumerate(obj.superskin_bones_collection):
                if item.name == new_active and not item.is_mask:
                    obj.superskin_bones_idx = i
                    break

        if context.area:
            context.area.tag_redraw()
        return {'FINISHED'}


class SUPERSKIN_OT_bone_list_invert_selection_hover(bpy.types.Operator):
    """Invert the bone selection while the mouse is over the Deform Bones list"""

    bl_idname = "superskin.bone_list_invert_selection_hover"
    bl_label = "Invert Bone Selection"
    bl_options = {'INTERNAL', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ui_list = getattr(context, "ui_list", None)
        return (ui_list is not None
                and ui_list.bl_idname == "MESH_UL_influence_list_view"
                and SUPERSKIN_OT_bone_list_select_by_lock.poll(context))

    def execute(self, context):
        return bpy.ops.superskin.bone_list_select_by_lock(action='INVERT')


_HOVER_LIST_DOMAINS = {
    "MESH_UL_influence_list_view": 'BONES',
    "SUPERSKIN_UL_layer_list_view": 'LAYERS',
}


class SUPERSKIN_OT_list_select_all_hover(bpy.types.Operator):
    """Select every visible row of the Layer or Deform Bones list under the mouse"""

    bl_idname = "superskin.list_select_all_hover"
    bl_label = "Select All Rows"
    bl_options = {'INTERNAL', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ui_list = getattr(context, "ui_list", None)
        obj = context.active_object
        return (ui_list is not None
                and ui_list.bl_idname in _HOVER_LIST_DOMAINS
                and obj is not None and obj.type == 'MESH')

    def execute(self, context):
        obj = context.active_object
        adapter = get_adapter(_HOVER_LIST_DOMAINS[context.ui_list.bl_idname])
        _selected, last_key, _history = adapter.read_selection(context, obj)
        visual_order = list(adapter.get_keys_in_visual_order(context, obj))
        if not visual_order:
            return {'CANCELLED'}
        if last_key in visual_order:
            visual_order.remove(last_key)
            visual_order.append(last_key)
        adapter.write_selection(context, obj, set(visual_order), last_key, visual_order)
        if context.area:
            context.area.tag_redraw()
        return {'FINISHED'}


class SUPERSKIN_OT_cycle_bone_list_filter(bpy.types.Operator):
    """Toggle displaying only bones that influence the mesh"""

    bl_idname = "superskin.cycle_bone_list_filter"
    bl_label = "Toggle Influence Bones"
    bl_description = "Toggle displaying only bones that influence the mesh"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        wm = context.window_manager
        wm.superskin_bone_list_filter_mode = (
            'NONE' if wm.superskin_bone_list_filter_mode == 'INFLUENCE' else 'INFLUENCE'
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

        names = set(CoreFacade.managed_vg_names(obj))
        mask_name = None
        for item in obj.superskin_bones_collection:
            if item.is_mask:
                mask_name = item.name
        if mask_name:
            names.add(mask_name)

        ctrl = CoreFacade(context)
        ctrl.set_selected_bones_pool(names)

        context.area.tag_redraw()
        return {'FINISHED'}



_WEIGHT_EPSILON = 0.001


def _set_vertex_selection(obj, selected_indices) -> None:
    mesh = obj.data
    flags = np.zeros(len(mesh.vertices), dtype=np.bool_)
    flags[list(selected_indices)] = True
    write_vertex_select(mesh, flags)
    mesh.update()


class OBJECT_OT_mw_select_affect_vertices(bpy.types.Operator):
    bl_idname = "object.mw_select_affect_vertices"
    bl_label = "Select Affect Vertices"
    bl_description = (
        "Select the vertices influenced by the active bone, or by the active layer's mask"
    )
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if not _is_valid_mesh(obj):
            return {'CANCELLED'}

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

        _set_vertex_selection(obj, affected_indices)

        return {'FINISHED'}



class OBJECT_OT_mw_select_affect_boundary(bpy.types.Operator):
    """Select the weighted vertices along the edge of the active bone's or mask's area"""
    bl_idname = "object.mw_select_affect_boundary"
    bl_label = "Select Affect Boundary Vertices"
    bl_description = (
        "Select the weighted vertices along the edge of the active bone's or mask's area"
    )
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if not _is_valid_mesh(obj):
            return {'CANCELLED'}

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

        num_verts = len(obj.data.vertices)
        has_weight = {i: weight_of(i) > _WEIGHT_EPSILON for i in range(num_verts)}
        boundary_indices = set()
        for v_idx in range(num_verts):
            if not has_weight[v_idx]:
                continue
            for n_idx in neighbors.get(v_idx, ()):
                if not has_weight.get(n_idx, False):
                    boundary_indices.add(v_idx)
                    break

        _set_vertex_selection(obj, boundary_indices)

        return {'FINISHED'}



class MESH_OT_show_affect_bone(bpy.types.Operator):
    """Show the bones influencing the selected vertices; pick one to make it active"""

    bl_idname = "mesh.show_affect_bone"
    bl_label = "Show Affecting Bones"
    bl_options = {'REGISTER', 'UNDO'}

    bone_name: bpy.props.StringProperty()

    @classmethod
    def poll(cls, context):
        return (
            context.mode == 'PAINT_WEIGHT'
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
                ctrl.clear_all_selected(obj)
                ctrl.add_vg_selected(obj, self.bone_name)
                storage.selection_history = str(vg.index)
                storage.last_clicked_index = vg.index
                try:
                    ctrl.set_active_bone_name(self.bone_name)
                except Exception:
                    pass
                self.report({'INFO'}, f"Selected Vertex Group: {self.bone_name}")
            else:
                self.report({'WARNING'}, f"Vertex Group '{self.bone_name}' not found on this object")
            return {'FINISHED'}

        context.window_manager.popup_menu(self.draw_menu, title="Bones Influencing Selection")
        return {'FINISHED'}

    def draw_menu(self, menu, context):
        layout = menu.layout
        obj = context.active_object

        obj.update_from_editmode()
        selected_v_indices = set(np.flatnonzero(read_vertex_select(obj.data)).tolist())

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



class OBJECT_OT_mw_popup_affect_influences(bpy.types.Operator):
    bl_idname = "object.mw_popup_affect_influences"
    bl_label = "Affecting Influences"
    bl_description = "Show the bones influencing the selected vertices"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if not _is_valid_mesh(obj):
            self.report({'WARNING'}, "No active mesh")
            return {'CANCELLED'}

        selected_verts = [obj.data.vertices[i] for i in np.flatnonzero(read_vertex_select(obj.data))]

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

        managed = CoreFacade.managed_vg_names(obj)
        self._group_names = [
            obj.vertex_groups[i].name
            for i in sorted(group_indices)
            if i < len(obj.vertex_groups) and obj.vertex_groups[i].name in managed
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
            ctrl.clear_all_selected(obj)
            ctrl.add_vg_selected(obj, self.group_name)
            storage.selection_history = str(vg.index)
            storage.last_clicked_index = vg.index
            try:
                ctrl.set_active_bone_name(self.group_name)
            except Exception:
                pass
        return {'FINISHED'}



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

        target_vertices = []
        selected_verts = [obj.data.vertices[i] for i in np.flatnonzero(read_vertex_select(obj.data))]

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



class SUPERSKIN_OT_toggle_mask_mode(bpy.types.Operator):
    """Switch between editing bone weights and the layer mask"""

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
            "Edit bone weights or the layer mask"
        ),
        items=(
            ('TOGGLE', "Toggle", "Switch to the other one"),
            ('WEIGHT', "Bone Weight", "Edit bone weights"),
            ('MASK', "Mask", "Edit the layer mask"),
        ),
        default='TOGGLE',
    )

    @classmethod
    def poll(cls, context):
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
                return {'FINISHED'}

        obj = context.active_object
        if not obj or obj.type != 'MESH':
            return {'CANCELLED'}

        storage = obj.superskin_storage
        ctrl = CoreFacade(context)

        is_explicit = self.target != 'TOGGLE'
        want_mask = (self.target == 'MASK') if is_explicit else (not storage.active_is_mask)

        from ....core.layer_storage.temp_vg_bridge import is_group_layer
        if is_group_layer(ctrl.storage, ctrl.active_layer_index):
            want_mask = True
        if is_explicit and storage.active_is_mask == want_mask:
            return {'FINISHED'}

        if want_mask:
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
                vg_list = obj.vertex_groups
                if 0 <= storage.last_clicked_index < len(vg_list):
                    ctrl.set_active_bone_name(vg_list[storage.last_clicked_index].name)
                ctrl.apply_active_bone()
                ctrl.finish()
            except Exception:
                traceback.print_exc()

        context.area.tag_redraw()
        return {'FINISHED'}



class SUPERSKIN_OT_save_weight_and_exit(bpy.types.Operator):
    """Save your weight changes"""
    bl_idname = "superskin.save_weight_and_exit"
    bl_label = "Save"
    bl_description = "Save your weight changes"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'MESH'

    def execute(self, context):
        obj = context.active_object

        if obj.mode == 'WEIGHT_PAINT':
            return bpy.ops.superskin.save_weights()
        return {'FINISHED'}



_classes = (
    SUPERSKIN_OT_toggle_vg_lock,
    SUPERSKIN_OT_bone_list_set_locks,
    SUPERSKIN_OT_bone_list_select_by_lock,
    SUPERSKIN_OT_bone_list_invert_selection_hover,
    SUPERSKIN_OT_list_select_all_hover,
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
