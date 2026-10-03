
import bpy
import numpy as np

from ....core.facade import CoreFacade
from ...weight_apply.public_api import read_vertex_select, write_vertex_select
from ....interface.utils.utils import (
    _has_layer_system,
    _resolve_layer_target,
    _enforce_visualizer_from_tab_state,
    exit_mask_mode_if_active,
    sync_layers_to_ui_collection,
    _run_in_object_context,
    _run_preserving_paint_session,
    _select_only_layer,
)
from ....interface.template_ui.select_ops import (
    get_adapter,
)
from . import object_selector
from .object_selector import _armature_for_mesh



class SUPERSKIN_OT_layer_init(bpy.types.Operator):
    """Set up layers on this mesh, starting from its current weights"""
    bl_idname = "superskin.layer_init"
    bl_label = "Initialize Layer"
    bl_description = "Set up layers on this mesh, starting from its current weights"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not CoreFacade.is_system_activated():
            return False
        obj = object_selector.get_effective_mesh(context)
        if obj is None:
            return False
        if _has_layer_system(obj):
            return False
        if _armature_for_mesh(obj) is None:
            cls.poll_message_set("This mesh has no Armature Modifier assigned")
            return False
        return True

    def execute(self, context):
        obj = object_selector.get_effective_mesh(context)
        if obj is None:
            return {'CANCELLED'}

        def _do_init():
            ctrl = CoreFacade(context)
            _run_in_object_context(context, ctrl.init_layer_system)
            sync_layers_to_ui_collection(obj)
            _enforce_visualizer_from_tab_state(context)

        object_selector.run_with_object_active(context, obj, _do_init)
        return {'FINISHED'}


class SUPERSKIN_OT_layer_remove_data(bpy.types.Operator):
    """Bake the layers into this mesh's vertex group weights and remove its layer data"""
    bl_idname = "superskin.layer_remove_data"
    bl_label = "Remove Data"
    bl_description = "Bake the layers into this mesh's vertex group weights and remove its SuperSkinPro layer data"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = object_selector.get_effective_mesh(context)
        return bool(
            CoreFacade.is_system_activated()
            and obj is not None
            and _has_layer_system(obj)
        )

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(
            self, event,
            title="Delete this mesh's layer data?",
            message="Permanently deletes all SuperSkinPro layer/mask data on this mesh. Native Vertex Group weights are kept. This cannot be undone from here.",
        )

    def execute(self, context):
        obj = object_selector.get_effective_mesh(context)
        if obj is None:
            return {'CANCELLED'}

        def _do_remove():
            ctrl = CoreFacade(context)

            def _teardown():
                exit_mask_mode_if_active(context, obj)
                ctrl.remove_layer_system()

            _run_in_object_context(context, _teardown)
            sync_layers_to_ui_collection(obj)
            _enforce_visualizer_from_tab_state(context)

        object_selector.run_with_object_active(context, obj, _do_remove)
        return {'FINISHED'}


class SUPERSKIN_OT_layer_remove_all_data(bpy.types.Operator):
    """Bake the layers of every mesh in the scene and remove their layer data"""
    bl_idname = "superskin.layer_remove_all_data"
    bl_label = "Remove All Layer Data"
    bl_description = "Bake the layers into the vertex group weights of every mesh in the scene and remove their SuperSkinPro layer data"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not CoreFacade.is_system_activated():
            return False
        return any(
            o.type == 'MESH' and "ss_layers_meta" in o.data
            for o in context.scene.objects
        )

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(
            self, event,
            title="Delete layer data for every mesh in this scene?",
            message="Permanently deletes all SuperSkinPro layer/mask data on every mesh in this scene. Native Vertex Group weights are kept. This cannot be undone from here.",
        )

    def execute(self, context):
        targets = [
            o for o in context.scene.objects
            if o.type == 'MESH' and "ss_layers_meta" in o.data
        ]

        def _remove_one(obj):
            ctrl = CoreFacade(context)

            def _teardown():
                exit_mask_mode_if_active(context, obj)
                ctrl.remove_layer_system()

            _run_in_object_context(context, _teardown)
            sync_layers_to_ui_collection(obj)

        for obj in targets:
            object_selector.run_with_object_active(context, obj, lambda o=obj: _remove_one(o))

        _enforce_visualizer_from_tab_state(context)
        self.report({'INFO'}, f"Removed layer data from {len(targets)} mesh(es)")
        return {'FINISHED'}



class SUPERSKIN_OT_layer_toggle_visible_by_item(bpy.types.Operator):
    """Show or hide this layer"""
    bl_idname = "superskin.layer_toggle_visible_by_item"
    bl_label = "Toggle Visibility from List"
    bl_options = {'INTERNAL', 'UNDO'}
    layer_index: bpy.props.IntProperty()

    @classmethod
    def poll(cls, context):
        if not CoreFacade.is_system_activated():
            return False
        obj = object_selector.get_effective_mesh(context)
        return obj is not None and _has_layer_system(obj)

    def execute(self, context):
        obj = object_selector.get_effective_mesh(context)
        if obj is None or not _has_layer_system(obj):
            return {'CANCELLED'}

        raw = obj.superskin_storage.layer_selected_indices
        selected_indices = [int(k) for k in raw.split(",") if k] if raw else []
        if len(selected_indices) >= 2 and self.layer_index in selected_indices:
            targets = selected_indices
        else:
            targets = [self.layer_index]

        def _do_toggle():
            ctrl = CoreFacade(context)
            clicked_item = next(
                (item for item in obj.superskin_layers_collection if item.index == self.layer_index),
                None,
            )
            new_visible = not clicked_item.visible if clicked_item is not None else None
            for idx in targets:
                if new_visible is not None:
                    item = next(
                        (it for it in obj.superskin_layers_collection if it.index == idx),
                        None,
                    )
                    if item is not None and item.visible == new_visible:
                        continue
                _run_preserving_paint_session(context, ctrl.toggle_visible, idx)
            sync_layers_to_ui_collection(obj)

        object_selector.run_with_object_active(context, obj, _do_toggle)
        CoreFacade.tag_redraw_areas(None, window_manager=context.window_manager)
        return {'FINISHED'}


class SUPERSKIN_OT_layer_toggle_group_collapsed(bpy.types.Operator):
    """Expand or collapse this group"""
    bl_idname = "superskin.layer_toggle_group_collapsed"
    bl_label = "Toggle Group Collapsed"
    bl_options = {'INTERNAL', 'UNDO'}
    group_index: bpy.props.IntProperty()

    @classmethod
    def poll(cls, context):
        if not CoreFacade.is_system_activated():
            return False
        obj = object_selector.get_effective_mesh(context)
        return obj is not None and _has_layer_system(obj)

    def execute(self, context):
        obj = object_selector.get_effective_mesh(context)
        if obj is None or not _has_layer_system(obj):
            return {'CANCELLED'}

        def _do():
            ctrl = CoreFacade(context)
            ctrl.toggle_group_collapsed(self.group_index)
            sync_layers_to_ui_collection(obj)

        object_selector.run_with_object_active(context, obj, _do)
        CoreFacade.tag_redraw_areas(None, window_manager=context.window_manager)
        return {'FINISHED'}



class SUPERSKIN_OT_layer_add(bpy.types.Operator):
    bl_idname = "superskin.layer_add"
    bl_label = "Add Weight Layer"
    bl_options = {'REGISTER', 'UNDO'}
    new_name: bpy.props.StringProperty(name="Name", default="")

    @classmethod
    def poll(cls, context):
        if not CoreFacade.is_system_activated():
            return False
        obj = object_selector.get_effective_mesh(context)
        if obj is None:
            return False
        if not _has_layer_system(obj) and _armature_for_mesh(obj) is None:
            cls.poll_message_set("This mesh has no Armature Modifier assigned")
            return False
        return True

    def invoke(self, context, event):
        obj = object_selector.get_effective_mesh(context)
        if obj is None:
            return {'CANCELLED'}

        def _peek_default_name():
            if not _has_layer_system(obj):
                return "Layer 1"
            return f"Layer {len(CoreFacade(context).layer_meta_list())}"

        self.new_name = object_selector.run_with_object_active(context, obj, _peek_default_name)
        return context.window_manager.invoke_props_dialog(self, width=250)

    def draw(self, context):
        self.layout.activate_init = True
        self.layout.prop(self, "new_name", text="Name")

    def execute(self, context):
        obj = object_selector.get_effective_mesh(context)
        if obj is None:
            return {'CANCELLED'}

        def _do_add():
            ctrl = CoreFacade(context)
            ctrl.init_layer_system()
            name = self.new_name.strip() or f"Layer {len(ctrl.layer_meta_list())}"

            new_idx = _run_preserving_paint_session(context, ctrl.create_layer, name)
            _select_only_layer(obj, new_idx)
            sync_layers_to_ui_collection(obj)
            _enforce_visualizer_from_tab_state(context)

        object_selector.run_with_object_active(context, obj, _do_add)
        return {'FINISHED'}


class SUPERSKIN_OT_layer_group_new(bpy.types.Operator):
    """Create a new empty group. Use Move to Group to add layers to it"""
    bl_idname = "superskin.layer_group_new"
    bl_label = "New Group"
    bl_options = {'REGISTER', 'UNDO'}
    new_name: bpy.props.StringProperty(name="Name", default="Group")

    @classmethod
    def poll(cls, context):
        if not CoreFacade.is_system_activated():
            return False
        obj = object_selector.get_effective_mesh(context)
        return obj is not None and _has_layer_system(obj)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=250)

    def draw(self, context):
        self.layout.activate_init = True
        self.layout.prop(self, "new_name", text="Name")

    def execute(self, context):
        obj = object_selector.get_effective_mesh(context)
        if obj is None or not _has_layer_system(obj):
            return {'CANCELLED'}

        def _do_add():
            ctrl = CoreFacade(context)
            name = self.new_name.strip() or "Group"
            new_idx = _run_preserving_paint_session(context, ctrl.create_group, name)
            _select_only_layer(obj, new_idx)
            sync_layers_to_ui_collection(obj)
            _enforce_visualizer_from_tab_state(context)

        object_selector.run_with_object_active(context, obj, _do_add)
        return {'FINISHED'}


class SUPERSKIN_OT_layer_remove(bpy.types.Operator):
    """Remove the active layer, or all selected layers"""
    bl_idname = "superskin.layer_remove"
    bl_label = "Remove Layer"
    bl_options = {'REGISTER', 'UNDO'}
    layer_index: bpy.props.IntProperty(default=-1)

    @classmethod
    def poll(cls, context):
        if not CoreFacade.is_system_activated():
            return False
        obj = object_selector.get_effective_mesh(context)
        return obj is not None and _has_layer_system(obj)

    def execute(self, context):
        obj = object_selector.get_effective_mesh(context)
        if obj is None or not _has_layer_system(obj):
            return {'CANCELLED'}

        raw = obj.superskin_storage.layer_selected_indices
        selected_indices = [int(k) for k in raw.split(",") if k] if raw else []

        if self.layer_index == -1 and len(selected_indices) >= 2:
            def _do_multi():
                ctrl = CoreFacade(context)
                names = [
                    item.name for item in obj.superskin_layers_collection
                    if item.index in selected_indices
                ]

                def _remove_all():
                    exit_mask_mode_if_active(context, obj)
                    for name in names:
                        for item in obj.superskin_layers_collection:
                            if item.name == name:
                                ctrl.remove_layer(item.index)
                                sync_layers_to_ui_collection(obj)
                                break

                _run_preserving_paint_session(context, _remove_all)
                _select_only_layer(obj, ctrl.active_layer_index)
                sync_layers_to_ui_collection(obj)
                _enforce_visualizer_from_tab_state(context)
                return len(names)

            count = object_selector.run_with_object_active(context, obj, _do_multi)
            self.report({'INFO'}, f"Removed {count} layer(s)")
            return {'FINISHED'}

        def _do():
            target = _resolve_layer_target(obj, self.layer_index)
            ctrl = CoreFacade(context)
            resolved_target = target if target >= 0 else ctrl.active_layer_index

            def _remove():
                exit_mask_mode_if_active(context, obj)
                ctrl.remove_layer(resolved_target)

            _run_preserving_paint_session(context, _remove)
            _select_only_layer(obj, ctrl.active_layer_index)
            sync_layers_to_ui_collection(obj)
            _enforce_visualizer_from_tab_state(context)

        object_selector.run_with_object_active(context, obj, _do)
        return {'FINISHED'}


class SUPERSKIN_OT_layer_move(bpy.types.Operator):
    bl_idname = "superskin.layer_move"
    bl_label = "Move Layer Up/Down"
    bl_options = {'INTERNAL', 'UNDO'}
    layer_index: bpy.props.IntProperty(default=-1)
    direction: bpy.props.IntProperty(default=-1)

    @classmethod
    def poll(cls, context):
        if not CoreFacade.is_system_activated():
            return False
        obj = object_selector.get_effective_mesh(context)
        return obj is not None and _has_layer_system(obj)

    def execute(self, context):
        obj = object_selector.get_effective_mesh(context)
        if obj is None or not _has_layer_system(obj):
            return {'CANCELLED'}

        def _do():
            target = _resolve_layer_target(obj, self.layer_index)
            ctrl = CoreFacade(context)
            resolved_target = target if target >= 0 else ctrl.active_layer_index

            moved = _run_preserving_paint_session(context, ctrl.move_layer, resolved_target, self.direction)

            if moved:
                _select_only_layer(obj, resolved_target)
            sync_layers_to_ui_collection(obj)
            _enforce_visualizer_from_tab_state(context)
            return moved

        moved = object_selector.run_with_object_active(context, obj, _do)

        if not moved:
            self.report({'INFO'}, "Layer is at stack boundary — cannot move further")
        return {'FINISHED'}


class SUPERSKIN_OT_layer_duplicate(bpy.types.Operator):
    """Duplicate the active layer, or all selected layers"""
    bl_idname = "superskin.layer_duplicate"
    bl_label = "Duplicate Layer"
    bl_options = {'INTERNAL', 'UNDO'}
    layer_index: bpy.props.IntProperty(default=-1)

    @classmethod
    def poll(cls, context):
        if not CoreFacade.is_system_activated():
            return False
        obj = object_selector.get_effective_mesh(context)
        return obj is not None and _has_layer_system(obj)

    def execute(self, context):
        obj = object_selector.get_effective_mesh(context)
        if obj is None or not _has_layer_system(obj):
            return {'CANCELLED'}

        raw = obj.superskin_storage.layer_selected_indices
        selected_indices = [int(k) for k in raw.split(",") if k] if raw else []
        group_indices = {item.index for item in obj.superskin_layers_collection if item.is_group}
        selected_indices = [i for i in selected_indices if i not in group_indices]

        if self.layer_index == -1 and len(selected_indices) >= 2:
            def _do_multi():
                ctrl = CoreFacade(context)
                new_indices = []
                offset = 0
                for idx in sorted(selected_indices):
                    actual = idx + offset
                    new_idx = _run_preserving_paint_session(context, ctrl.duplicate_layer, actual)
                    if new_idx is not None and new_idx >= 0:
                        new_indices.append(new_idx)
                        offset += 1

                if new_indices:
                    _select_only_layer(obj, new_indices[-1])
                    storage = obj.superskin_storage
                    storage.layer_selected_indices = (
                        "," + ",".join(str(i) for i in sorted(new_indices)) + ","
                    )
                    storage.layer_selection_history = ",".join(str(i) for i in new_indices)

                sync_layers_to_ui_collection(obj)
                _enforce_visualizer_from_tab_state(context)
                return len(new_indices)

            count = object_selector.run_with_object_active(context, obj, _do_multi)
            if not count:
                self.report({'WARNING'}, "Duplicate failed — selection invalid")
                return {'CANCELLED'}
            self.report({'INFO'}, f"Duplicated {count} layer(s)")
            return {'FINISHED'}

        def _do():
            target = _resolve_layer_target(obj, self.layer_index)
            ctrl = CoreFacade(context)
            resolved_target = target if target >= 0 else ctrl.active_layer_index
            if resolved_target in group_indices:
                return None

            new_idx = _run_preserving_paint_session(context, ctrl.duplicate_layer, resolved_target)
            if new_idx is not None and new_idx >= 0:
                _select_only_layer(obj, new_idx)
            sync_layers_to_ui_collection(obj)
            _enforce_visualizer_from_tab_state(context)
            return new_idx

        result = object_selector.run_with_object_active(context, obj, _do)
        if result is None:
            self.report({'WARNING'}, "A Group can't be duplicated")
            return {'CANCELLED'}
        return {'FINISHED'}


_MASK_EPSILON = 0.001


def _active_layer_mask_flags(ctrl, mesh):
    active_idx = ctrl.get_active_layer_index()
    mask_default = 1.0
    for m in ctrl.get_meta_list():
        if m.get("index", -1) == active_idx:
            mask_default = float(m.get("mask_default", 1.0))
            break
    mask_dict = ctrl.get_active_mask_dict()
    return np.fromiter(
        (mask_dict.get(i, mask_default) > _MASK_EPSILON for i in range(len(mesh.vertices))),
        dtype=np.bool_, count=len(mesh.vertices),
    )


class SUPERSKIN_OT_layer_select_affected_vertices(bpy.types.Operator):
    bl_idname = "superskin.layer_select_affected_vertices"
    bl_label = "Select Affected Vertices"
    bl_description = "Select all vertices covered by the active layer's mask"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not CoreFacade.is_system_activated():
            return False
        obj = object_selector.get_effective_mesh(context)
        return obj is not None and _has_layer_system(obj)

    def execute(self, context):
        obj = object_selector.get_effective_mesh(context)
        if obj is None or not _has_layer_system(obj):
            return {'CANCELLED'}

        def _do():
            mesh = obj.data
            flags = _active_layer_mask_flags(CoreFacade(context), mesh)
            write_vertex_select(mesh, flags)
            mesh.update()
            return int(flags.sum())

        count = object_selector.run_with_object_active(context, obj, _do)
        self.report({'INFO'}, f"Selected {count or 0} vertices")
        return {'FINISHED'}


class SUPERSKIN_OT_layer_deselect_affected_vertices(bpy.types.Operator):
    bl_idname = "superskin.layer_deselect_affected_vertices"
    bl_label = "Deselect Affected Vertices"
    bl_description = "Deselect the vertices covered by the active layer's mask, keeping the rest of the selection"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return SUPERSKIN_OT_layer_select_affected_vertices.poll(context)

    def execute(self, context):
        obj = object_selector.get_effective_mesh(context)
        if obj is None or not _has_layer_system(obj):
            return {'CANCELLED'}

        def _do():
            mesh = obj.data
            selected = read_vertex_select(mesh)
            removed = selected & _active_layer_mask_flags(CoreFacade(context), mesh)
            write_vertex_select(mesh, selected & ~removed)
            mesh.update()
            return int(removed.sum())

        count = object_selector.run_with_object_active(context, obj, _do)
        self.report({'INFO'}, f"Deselected {count or 0} vertices")
        return {'FINISHED'}


class SUPERSKIN_OT_layer_merge_selected(bpy.types.Operator):
    """Merge the selected layers into the active layer"""
    bl_idname = "superskin.layer_merge_selected"
    bl_label = "Merge Selected Layers"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not CoreFacade.is_system_activated():
            return False
        obj = object_selector.get_effective_mesh(context)
        if obj is None or not _has_layer_system(obj):
            return False
        raw = obj.superskin_storage.layer_selected_indices
        selected_indices = {int(k) for k in raw.split(",") if k} if raw else set()
        if len(selected_indices) < 2:
            return False
        if any(
            item.index in selected_indices and item.is_group
            for item in obj.superskin_layers_collection
        ):
            cls.poll_message_set("A Group has no weight data to merge")
            return False
        return True

    def execute(self, context):
        obj = object_selector.get_effective_mesh(context)
        if obj is None:
            return {'CANCELLED'}
        raw = obj.superskin_storage.layer_selected_indices
        selected_indices = [int(k) for k in raw.split(",") if k]
        if len(selected_indices) < 2:
            self.report({'WARNING'}, "Select at least 2 layers to merge")
            return {'CANCELLED'}

        def _do():
            ctrl = CoreFacade(context)
            target = ctrl.active_layer_index
            if target not in selected_indices:
                target = max(selected_indices)

            disabled_names = [
                item.name for item in obj.superskin_layers_collection
                if item.index in selected_indices and item.index != target and not item.visible
            ]
            disabled_idx_set = {
                item.index for item in obj.superskin_layers_collection
                if item.name in disabled_names
            }
            compose_indices = [i for i in selected_indices if i not in disabled_idx_set]

            def _do_merge():
                exit_mask_mode_if_active(context, obj)
                ok = ctrl.merge_selected_layers(compose_indices, target)
                if not ok:
                    return False
                sync_layers_to_ui_collection(obj)
                for name in disabled_names:
                    for item in obj.superskin_layers_collection:
                        if item.name == name:
                            ctrl.remove_layer(item.index)
                            sync_layers_to_ui_collection(obj)
                            break
                return True

            ok = _run_preserving_paint_session(context, _do_merge)
            if not ok:
                return False

            _select_only_layer(obj, target)
            sync_layers_to_ui_collection(obj)
            _enforce_visualizer_from_tab_state(context)
            return True

        ok = object_selector.run_with_object_active(context, obj, _do)
        if not ok:
            self.report({'WARNING'}, "Merge failed — selection invalid")
            return {'CANCELLED'}
        return {'FINISHED'}


class SUPERSKIN_OT_layer_move_to_group(bpy.types.Operator):
    """Move the selected layers into a group, or out of their group"""
    bl_idname = "superskin.layer_move_to_group"
    bl_label = "Move to Group"
    bl_options = {'REGISTER', 'UNDO'}
    target_group_index: bpy.props.IntProperty(
        default=-1,
        description="An existing Group's layer index, or -1 to ungroup",
    )

    @classmethod
    def poll(cls, context):
        if not CoreFacade.is_system_activated():
            return False
        obj = object_selector.get_effective_mesh(context)
        if obj is None or not _has_layer_system(obj):
            return False
        raw = obj.superskin_storage.layer_selected_indices
        selected_indices = {int(k) for k in raw.split(",") if k} if raw else set()
        if not selected_indices:
            return False
        if any(
            item.index in selected_indices and item.is_group
            for item in obj.superskin_layers_collection
        ):
            cls.poll_message_set("A Group can't be moved into another Group")
            return False
        return True

    def execute(self, context):
        obj = object_selector.get_effective_mesh(context)
        if obj is None:
            return {'CANCELLED'}
        raw = obj.superskin_storage.layer_selected_indices
        selected_indices = [int(k) for k in raw.split(",") if k]
        if not selected_indices:
            return {'CANCELLED'}

        target = self.target_group_index if self.target_group_index >= 0 else None

        def _do():
            ctrl = CoreFacade(context)
            ok = _run_preserving_paint_session(
                context, ctrl.move_layers_to_group, selected_indices, target,
            )
            sync_layers_to_ui_collection(obj)
            _enforce_visualizer_from_tab_state(context)
            return ok

        ok = object_selector.run_with_object_active(context, obj, _do)
        if not ok:
            self.report({'WARNING'}, "Move to Group failed")
            return {'CANCELLED'}
        return {'FINISHED'}


class SUPERSKIN_OT_layer_group_new_from_selection(bpy.types.Operator):
    """Create a new group containing the selected layers"""
    bl_idname = "superskin.layer_group_new_from_selection"
    bl_label = "New Group"
    bl_options = {'REGISTER', 'UNDO'}
    new_name: bpy.props.StringProperty(name="Name", default="Group")

    @classmethod
    def poll(cls, context):
        return SUPERSKIN_OT_layer_move_to_group.poll(context)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=250)

    def draw(self, context):
        self.layout.activate_init = True
        self.layout.prop(self, "new_name", text="Name")

    def execute(self, context):
        obj = object_selector.get_effective_mesh(context)
        if obj is None:
            return {'CANCELLED'}
        raw = obj.superskin_storage.layer_selected_indices
        selected_indices = [int(k) for k in raw.split(",") if k]
        if not selected_indices:
            return {'CANCELLED'}

        def _do():
            ctrl = CoreFacade(context)
            name = self.new_name.strip() or "Group"

            def _create_and_move():
                new_idx = ctrl.create_group(name)
                ctrl.move_layers_to_group(selected_indices, new_idx)
                return new_idx

            new_idx = _run_preserving_paint_session(context, _create_and_move)
            _select_only_layer(obj, new_idx)
            sync_layers_to_ui_collection(obj)
            _enforce_visualizer_from_tab_state(context)

        object_selector.run_with_object_active(context, obj, _do)
        return {'FINISHED'}


class SUPERSKIN_OT_layer_icon_apply(bpy.types.Operator):
    """Set the layer's icon color"""
    bl_idname = "superskin.layer_icon_apply"
    bl_label = "Set Layer Icon Color"
    bl_options = {'INTERNAL', 'UNDO'}
    icon_name: bpy.props.StringProperty(default="white")

    @classmethod
    def poll(cls, context):
        if not CoreFacade.is_system_activated():
            return False
        obj = object_selector.get_effective_mesh(context)
        return obj is not None and _has_layer_system(obj)

    def execute(self, context):
        obj = object_selector.get_effective_mesh(context)
        if obj is None or not _has_layer_system(obj):
            return {'CANCELLED'}

        raw = obj.superskin_storage.layer_selected_indices
        selected_indices = [int(k) for k in raw.split(",") if k] if raw else []

        def _do():
            ctrl = CoreFacade(context)
            targets = selected_indices if len(selected_indices) >= 2 else [ctrl.active_layer_index]
            for idx in targets:
                _run_preserving_paint_session(context, ctrl.set_layer_icon, idx, self.icon_name)
            sync_layers_to_ui_collection(obj)
            return len(targets)

        count = object_selector.run_with_object_active(context, obj, _do)
        if count > 1:
            self.report({'INFO'}, f"Set icon for {count} layer(s)")
        CoreFacade.tag_redraw_areas(None, window_manager=context.window_manager)
        return {'FINISHED'}


class SUPERSKIN_OT_layer_rename_active(bpy.types.Operator):
    bl_idname = "superskin.layer_rename_active"
    bl_label = "Rename Active Layer"
    bl_options = {'REGISTER', 'UNDO'}
    new_name: bpy.props.StringProperty(name="Name", default="Layer")

    @classmethod
    def poll(cls, context):
        if not CoreFacade.is_system_activated():
            return False
        obj = object_selector.get_effective_mesh(context)
        return obj is not None and _has_layer_system(obj)

    def execute(self, context):
        obj = object_selector.get_effective_mesh(context)
        if obj is None or not _has_layer_system(obj):
            return {'CANCELLED'}

        def _do_rename():
            ctrl = CoreFacade(context)
            ctrl.rename_layer(ctrl.active_layer_index, self.new_name)
            sync_layers_to_ui_collection(obj)

        object_selector.run_with_object_active(context, obj, _do_rename)
        return {'FINISHED'}

    def invoke(self, context, event):
        obj = object_selector.get_effective_mesh(context)
        if obj is None or not _has_layer_system(obj):
            return {'CANCELLED'}

        def _do_get_name():
            return CoreFacade(context).active_layer_name()

        self.new_name = object_selector.run_with_object_active(context, obj, _do_get_name)
        return context.window_manager.invoke_props_dialog(self, width=250)

    def draw(self, context):
        self.layout.activate_init = True
        self.layout.prop(self, "new_name", text="Name")



_classes = (
    SUPERSKIN_OT_layer_init,
    SUPERSKIN_OT_layer_remove_data,
    SUPERSKIN_OT_layer_remove_all_data,
    SUPERSKIN_OT_layer_toggle_visible_by_item,
    SUPERSKIN_OT_layer_toggle_group_collapsed,
    SUPERSKIN_OT_layer_add,
    SUPERSKIN_OT_layer_group_new,
    SUPERSKIN_OT_layer_remove,
    SUPERSKIN_OT_layer_move,
    SUPERSKIN_OT_layer_duplicate,
    SUPERSKIN_OT_layer_select_affected_vertices,
    SUPERSKIN_OT_layer_deselect_affected_vertices,
    SUPERSKIN_OT_layer_merge_selected,
    SUPERSKIN_OT_layer_move_to_group,
    SUPERSKIN_OT_layer_group_new_from_selection,
    SUPERSKIN_OT_layer_icon_apply,
    SUPERSKIN_OT_layer_rename_active,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
