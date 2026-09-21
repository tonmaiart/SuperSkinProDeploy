"""Layer list CRUD operators — add, remove, move, duplicate, merge, rename, and per-row
visibility toggle."""

import bpy

from ....core.facade import CoreFacade
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
    invert_selection,
    get_last_active_domain,
)
from . import object_selector
from .object_selector import _armature_for_mesh


# ==============================================================================
# SYSTEM INIT / TEARDOWN
# ==============================================================================

class SUPERSKIN_OT_layer_init(bpy.types.Operator):
    """Initialise the layer system from the mesh's current Vertex Group weights."""
    bl_idname = "superskin.layer_init"
    bl_label = "Initialize Layer"
    bl_description = "Initialize the layer weight-painting system on this mesh from its current Vertex Group weights"
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
    """Tear down the layer system entirely, reverting the mesh to plain native Vertex Groups."""
    bl_idname = "superskin.layer_remove_data"
    bl_label = "Remove Data"
    bl_description = "Permanently delete all SuperSkinPro layer/mask data on this mesh (native Vertex Group weights are kept)"
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
    """Tear down the layer system on every mesh in the current scene that has one."""
    bl_idname = "superskin.layer_remove_all_data"
    bl_label = "Remove All Layer Data"
    bl_description = "Permanently delete all SuperSkinPro layer/mask data on every mesh in this scene (native Vertex Group weights are kept)"
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


# ==============================================================================
# SCENE MODE — Enter Pose Mode
# ==============================================================================

class SUPERSKIN_OT_layer_enter_pose_mode(bpy.types.Operator):
    """Switch to Pose Mode on the mesh's rigged Armature, or back to Object Mode (re-selecting
    the rigged mesh) if already posing."""
    bl_idname = "superskin.layer_enter_pose_mode"
    bl_label = "Enter Pose Mode"
    bl_description = "Switch to Pose Mode on this mesh's Armature (or back to Object Mode if already posing)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not CoreFacade.is_system_activated():
            return False

        obj = context.active_object
        if obj and obj.type == 'ARMATURE' and obj.mode == 'POSE':
            return True

        armature = object_selector.get_effective_armature(context)

        if armature is None:
            cls.poll_message_set("This mesh has no Armature assigned")
            return False
        return True

    def execute(self, context):
        obj = context.active_object

        if obj and obj.type == 'ARMATURE' and obj.mode == 'POSE':
            mesh = object_selector.get_effective_mesh(context)
            bpy.ops.object.mode_set(mode='OBJECT')
            if mesh is not None:
                bpy.ops.object.select_all(action='DESELECT')
                mesh.select_set(True)
                context.view_layer.objects.active = mesh
            return {'FINISHED'}

        armature = object_selector.get_effective_armature(context)

        if armature is None:
            self.report({'WARNING'}, "No Armature found for the active mesh.")
            return {'CANCELLED'}

        if obj and obj.type == 'MESH' and obj.mode == 'WEIGHT_PAINT':
            return bpy.ops.object.mw_force_pose_mode()

        if context.active_object and context.active_object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        if armature.hide_get():
            armature.hide_set(False)

        bpy.ops.object.select_all(action='DESELECT')
        context.view_layer.objects.active = armature
        armature.select_set(True)
        bpy.ops.object.mode_set(mode='POSE')

        return {'FINISHED'}


# ==============================================================================
# LAYER LIST ROW OPERATORS
# ==============================================================================

class SUPERSKIN_OT_layer_toggle_visible_by_item(bpy.types.Operator):
    """Toggle the eye icon for this row."""
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
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
        return {'FINISHED'}


class SUPERSKIN_OT_layer_invert_selection(bpy.types.Operator):
    """Invert the Layer list's multi-selection (Ctrl+I). Shares a keymap slot with
    ``superskin.invert_vg_selection``."""

    bl_idname = "superskin.layer_invert_selection"
    bl_label = "Invert Layer Selection"
    bl_options = {'INTERNAL', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = object_selector.get_effective_mesh(context)
        return (
            obj is not None
            and _has_layer_system(obj)
            and get_last_active_domain() == 'LAYERS'
        )

    def execute(self, context):
        obj = object_selector.get_effective_mesh(context)
        if obj is None or not _has_layer_system(obj):
            return {'CANCELLED'}

        adapter = get_adapter('LAYERS')
        selected_keys, last_key, _history = adapter.read_selection(context, obj)
        visual_order = adapter.get_keys_in_visual_order(context, obj)
        if not visual_order:
            return {'CANCELLED'}

        new_selected, new_last_key, new_history = invert_selection(
            selected_keys, last_key, visual_order
        )
        adapter.write_selection(context, obj, new_selected, new_last_key, new_history)

        context.area.tag_redraw()
        return {'FINISHED'}


# ==============================================================================
# LAYER MANAGEMENT OPERATORS
# ==============================================================================

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
            ctrl.init_layer_system()  # no-op if already initialised
            name = self.new_name.strip() or f"Layer {len(ctrl.layer_meta_list())}"

            new_idx = _run_preserving_paint_session(context, ctrl.create_layer, name)
            _select_only_layer(obj, new_idx)
            sync_layers_to_ui_collection(obj)
            _enforce_visualizer_from_tab_state(context)

        object_selector.run_with_object_active(context, obj, _do_add)
        return {'FINISHED'}


class SUPERSKIN_OT_layer_remove(bpy.types.Operator):
    """Remove the active layer, or every currently multi-selected layer at once when 2+ layers
    are selected via the Layers list multi-select pool (``layer_selected_indices``."""
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
    """Duplicate the active layer, or every currently multi-selected layer at once when 2+
    layers are selected via the Layers list multi-select pool (``layer_selected_indices``."""
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

            new_idx = _run_preserving_paint_session(context, ctrl.duplicate_layer, resolved_target)
            if new_idx is not None and new_idx >= 0:
                _select_only_layer(obj, new_idx)
            sync_layers_to_ui_collection(obj)
            _enforce_visualizer_from_tab_state(context)

        object_selector.run_with_object_active(context, obj, _do)
        return {'FINISHED'}




class SUPERSKIN_OT_layer_merge_selected(bpy.types.Operator):
    """Merge every currently multi-selected layer into the active one."""
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
        count = len([k for k in raw.split(",") if k]) if raw else 0
        return count >= 2

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


class SUPERSKIN_OT_layer_icon_apply(bpy.types.Operator):
    """Set the layer's icon COLOR."""
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
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
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


# ==============================================================================
# REGISTRATION
# ==============================================================================

_classes = (
    SUPERSKIN_OT_layer_init,
    SUPERSKIN_OT_layer_remove_data,
    SUPERSKIN_OT_layer_remove_all_data,
    SUPERSKIN_OT_layer_enter_pose_mode,
    SUPERSKIN_OT_layer_toggle_visible_by_item,
    SUPERSKIN_OT_layer_invert_selection,
    SUPERSKIN_OT_layer_add,
    SUPERSKIN_OT_layer_remove,
    SUPERSKIN_OT_layer_move,
    SUPERSKIN_OT_layer_duplicate,
    SUPERSKIN_OT_layer_merge_selected,
    SUPERSKIN_OT_layer_icon_apply,
    SUPERSKIN_OT_layer_rename_active,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
