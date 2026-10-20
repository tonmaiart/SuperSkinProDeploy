
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
)
from . import object_selector
from .object_selector import _armature_for_mesh
from .fill_logic import DEFAULT_REPEAT, DEFAULT_SMOOTH, fill_transparent
from .extract_logic import SOURCE_FILL, SOURCE_KEEP, SOURCE_REMOVE, extract_to_new_layer, new_layer_name
from ...weight_toolkit.public_api import normalized_rows



class SUPERSKIN_OT_layer_init(bpy.types.Operator):
    """Set up layers on this mesh, starting from its current weights"""
    bl_idname = "superskin.layer_init"
    bl_label = "Initialize Layer"
    bl_description = "Set up layers on this mesh, starting from its current weights"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
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
            obj is not None
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


class SUPERSKIN_OT_layer_remove_selected_data(bpy.types.Operator):
    """Bake the layers of every selected mesh and remove their layer data"""
    bl_idname = "superskin.layer_remove_selected_data"
    bl_label = "Bake Selected Layer Data"
    bl_description = "Bake the layers into the vertex group weights of every selected mesh and remove their SuperSkinPro layer data"
    bl_options = {'REGISTER', 'UNDO'}

    @staticmethod
    def _targets(context):
        meshes = [o for o in context.selected_objects if o.type == 'MESH']
        effective = object_selector.get_effective_mesh(context)
        if effective is not None and effective not in meshes:
            meshes.append(effective)
        return [o for o in meshes if _has_layer_system(o)]

    @classmethod
    def poll(cls, context):
        return bool(cls._targets(context))

    def invoke(self, context, event):
        count = len(self._targets(context))
        return context.window_manager.invoke_confirm(
            self, event,
            title=f"Delete layer data for {count} selected mesh(es)?",
            message="Permanently deletes all SuperSkinPro layer/mask data on the selected meshes. Native Vertex Group weights are kept. This cannot be undone from here.",
        )

    def execute(self, context):
        targets = self._targets(context)

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


class SUPERSKIN_OT_layer_remove_all_data(bpy.types.Operator):
    """Bake the layers of every mesh in the scene and remove their layer data"""
    bl_idname = "superskin.layer_remove_all_data"
    bl_label = "Remove All Layer Data"
    bl_description = "Bake the layers into the vertex group weights of every mesh in the scene and remove their SuperSkinPro layer data"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
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
    """Create a new layer with empty bone and mask weights"""
    bl_idname = "superskin.layer_add"
    bl_label = "New Layer"
    bl_options = {'REGISTER', 'UNDO'}
    new_name: bpy.props.StringProperty(name="Name", default="")

    @classmethod
    def poll(cls, context):
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
    """Remove the selected layer(s)"""
    bl_idname = "superskin.layer_remove"
    bl_label = "Remove Layer"
    bl_options = {'REGISTER', 'UNDO'}
    layer_index: bpy.props.IntProperty(default=-1)

    @classmethod
    def poll(cls, context):
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
    bl_label = "Move Layer"
    bl_options = {'INTERNAL', 'UNDO'}
    layer_index: bpy.props.IntProperty(default=-1)
    direction: bpy.props.IntProperty(default=-1)

    @classmethod
    def description(cls, context, properties):
        if properties.direction < 0:
            return "Move the active layer up the stack"
        return "Move the active layer down the stack"

    @classmethod
    def poll(cls, context):
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
    """Duplicate the selected layer(s)"""
    bl_idname = "superskin.layer_duplicate"
    bl_label = "Duplicate Layer"
    bl_options = {'INTERNAL', 'UNDO'}
    layer_index: bpy.props.IntProperty(default=-1)

    @classmethod
    def poll(cls, context):
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


class SUPERSKIN_OT_layer_fill_transparent(bpy.types.Operator):
    """Auto-fill empty weights using surrounding weights"""
    bl_idname = "superskin.layer_fill_transparent"
    bl_label = "Fill Transparent Weights"
    bl_options = {'REGISTER', 'UNDO'}

    smooth: bpy.props.FloatProperty(
        name="Smooth", description="Blend strength between bones along the surface; 0 keeps hard boundaries",
        default=DEFAULT_SMOOTH, min=0.0, max=2.0,
    )
    repeat: bpy.props.IntProperty(
        name="Repeat", description="Relax rounds; more rounds widen the blend", default=DEFAULT_REPEAT, min=0, max=50,
    )
    spread: bpy.props.EnumProperty(
        name="Spread",
        description="How the layer's weight reaches its empty vertices",
        items=(
            ('SURFACE', "Surface", "Spread the painted weight along the mesh surface only"),
            ('HYBRID', "Surface + Block Weight", "Also place bones by position where that area connects to "
                                                 "the bone's own paint or holds the bone"),
        ),
        default='SURFACE',
    )
    normalize: bpy.props.BoolProperty(
        name="Normalize", description="Also rescale partly weighted vertices so their bones sum to 1.0; locked bones are kept",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        obj = object_selector.get_effective_mesh(context)
        return obj is not None and _has_layer_system(obj)

    def execute(self, context):
        obj = object_selector.get_effective_mesh(context)
        if obj is None or not _has_layer_system(obj):
            return {'CANCELLED'}

        def _do():
            ctrl = CoreFacade(context)
            active = ctrl.active_layer_index
            if any(item.is_group and item.index == active for item in obj.superskin_layers_collection):
                return None
            layer = ctrl.read_active_layer()
            filled = fill_transparent(ctrl, layer, ctrl.get_num_verts(), self.smooth, self.repeat,
                                      hybrid=self.spread == 'HYBRID')
            layer.update(filled)
            normalized = normalized_rows(ctrl, layer) if self.normalize else {}
            layer.update(normalized)
            if filled or normalized:
                ctrl.write_active_layer(layer, color_only=False)
            return len(filled), len(normalized)

        result = object_selector.run_with_object_active(context, obj, _do)
        if result is None:
            self.report({'WARNING'}, "A Group has no bone weight to fill")
            return {'CANCELLED'}
        filled, normalized = result
        if not filled and not normalized:
            self.report({'INFO'}, "Nothing to fill")
            return {'FINISHED'}
        self.report({'INFO'}, f"Filled {filled} vertices, normalized {normalized}")
        return {'FINISHED'}


class SUPERSKIN_OT_layer_extract_bones(bpy.types.Operator):
    """Create a new layer using bone and mask weights from the selected active bones"""
    bl_idname = "superskin.layer_extract_bones"
    bl_label = "Extract Bones to New Layer"
    bl_options = {'REGISTER', 'UNDO'}

    source_mode: bpy.props.EnumProperty(
        name="Source Layer",
        description="What happens to the extracted bones' weight on the source layer",
        items=(
            (SOURCE_FILL, "Auto Fill", "Remove it and fill vertices left empty from the remaining bones"),
            (SOURCE_REMOVE, "Remove", "Subtract it and leave the remaining bones at their own weight"),
            (SOURCE_KEEP, "Keep", "Leave the source layer unchanged"),
        ),
        default=SOURCE_FILL,
    )
    layer_name: bpy.props.StringProperty(
        name="Name", description="Name of the new layer; empty uses the bone name", options={'SKIP_SAVE'},
    )

    @classmethod
    def poll(cls, context):
        obj = object_selector.get_effective_mesh(context)
        return obj is not None and _has_layer_system(obj)

    @staticmethod
    def _bones(ctrl, obj):
        active = ctrl.get_active_vg_name()
        return active, (set(ctrl.get_selected_bones_pool()) | {active}) & set(CoreFacade.managed_vg_names(obj))

    def invoke(self, context, event):
        obj = object_selector.get_effective_mesh(context)
        if obj is None or not _has_layer_system(obj):
            return {'CANCELLED'}
        active, bones = object_selector.run_with_object_active(
            context, obj, lambda: self._bones(CoreFacade(context), obj))
        if bones and not self.layer_name:
            self.layer_name = new_layer_name(bones, active)
        return context.window_manager.invoke_props_dialog(self, width=300, confirm_text="Extract")

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "layer_name")
        layout.prop(self, "source_mode")

    def execute(self, context):
        obj = object_selector.get_effective_mesh(context)
        if obj is None or not _has_layer_system(obj):
            return {'CANCELLED'}

        def _do():
            ctrl = CoreFacade(context)
            source = ctrl.active_layer_index
            if any(item.is_group and item.index == source for item in obj.superskin_layers_collection):
                return "A Group has no bone weight to extract"
            active, bones = self._bones(ctrl, obj)
            if not bones:
                return "Select the bones to extract in the Deform Bones list"

            def _extract():
                exit_mask_mode_if_active(context, obj)
                return extract_to_new_layer(ctrl, source, bones, active, source_mode=self.source_mode,
                                            name=self.layer_name.strip() or None)

            new_idx = _run_preserving_paint_session(context, _extract)
            if new_idx is None:
                return "The selected bones have no weight on this layer"
            if new_idx < 0:
                return "Could not create a new layer"
            _select_only_layer(obj, new_idx)
            sync_layers_to_ui_collection(obj)
            _enforce_visualizer_from_tab_state(context)
            return len(bones)

        result = object_selector.run_with_object_active(context, obj, _do)
        if isinstance(result, str):
            self.report({'WARNING'}, result)
            return {'CANCELLED'}
        self.report({'INFO'}, f"Extracted {result} bone(s) to a new layer")
        return {'FINISHED'}


class SUPERSKIN_OT_layer_merge_selected(bpy.types.Operator):
    """Merge the selected layers into the active layer"""
    bl_idname = "superskin.layer_merge_selected"
    bl_label = "Merge Selected Layers"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
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
    SUPERSKIN_OT_layer_remove_selected_data,
    SUPERSKIN_OT_layer_remove_all_data,
    SUPERSKIN_OT_layer_toggle_visible_by_item,
    SUPERSKIN_OT_layer_toggle_group_collapsed,
    SUPERSKIN_OT_layer_add,
    SUPERSKIN_OT_layer_group_new,
    SUPERSKIN_OT_layer_remove,
    SUPERSKIN_OT_layer_move,
    SUPERSKIN_OT_layer_duplicate,
    SUPERSKIN_OT_layer_merge_selected,
    SUPERSKIN_OT_layer_fill_transparent,
    SUPERSKIN_OT_layer_extract_bones,
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
