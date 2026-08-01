"""Layer list CRUD operators — add, remove, move, duplicate, merge, rename,
and per-row visibility toggle.

Relocated from operators/ops_layers_tool.py as part of the layer_viewer
domain extraction.
"""

import bpy

from ...core.facade import CoreFacade
from ...interface.utils.utils import (
    _has_layer_system,
    _resolve_layer_target,
    _enforce_visualizer_from_tab_state,
    exit_mask_mode_if_active,
    sync_layers_to_ui_collection,
    _run_in_object_context,
    _select_only_layer,
)
from . import object_selector
from .object_selector import _armature_for_mesh


# ==============================================================================
# SYSTEM INIT / TEARDOWN
# ==============================================================================

class SUPERSKIN_OT_layer_init(bpy.types.Operator):
    """Initialise the layer system from the mesh's current Vertex Group
    weights. Locked once a layer system already exists -- use "Remove Data"
    first to re-initialise from scratch."""
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
            # Initialising seeds the base Layer from the mesh's current
            # Vertex Group weights via an Armature Modifier -- with no
            # Armature Modifier assigned, there's nothing meaningful to
            # seed, so this stays locked rather than creating a Layer
            # system with no real deform data behind it.
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
    """Tear down the layer system entirely, reverting the mesh to plain
    native Vertex Groups. The real deform weights already baked onto the
    mesh are untouched -- only SuperSkinPro's own layer history is deleted."""
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
    """Tear down the layer system on every mesh in the current scene that has
    one. Same per-mesh teardown as ``superskin.layer_remove_data``, just
    scoped to the whole scene instead of one mesh."""
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
    """Switch to Pose Mode on the mesh's rigged Armature, or back to Object
    Mode (re-selecting the rigged mesh) if already posing.

    Ported from ``features/controller/ops_scene_modes.py``'s
    ``object.mw_force_pose_mode`` -- duplicated rather than referenced by
    bl_idname because that domain is slated for future removal and
    layer_viewer needs this button to keep working on its own afterward.

    Simplified relative to the original: that operator also bakes any
    in-progress ``__ssp_*`` temp-VG edits back to the layer before switching,
    for a case reachable from its own "Edit Layer Weight" flow. That case
    cannot occur here -- the LAYER tab (and this button) is only visible
    while ``superskin_active_interface == 'LAYER'``, and entering an actual
    layer-editing Edit Mode session flips that state to 'SKINNING'
    (`_enter_edit_mode()`), which hides this button until the session ends
    and flips it back. The only way to reach this button while
    `context.mode == 'EDIT_MESH'` is a bare native Tab press that never went
    through "Edit Layer Weight" -- which never creates temp VGs in the first
    place (see interface/README.md's Interface State section). This is
    checked defensively via `has_pending_edit_mode_changes()` rather than
    assumed, and refuses (instead of silently baking or discarding) on the
    off chance it's ever wrong.
    """
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
            # Always allow toggling back to Object Mode regardless of
            # armature resolution below -- there's nothing to resolve, the
            # Armature is already the active object.
            return True

        # Mirrors execute()'s own armature-resolution chain -- if none of
        # these can find an Armature, entering Pose Mode has nothing to
        # target, so the button stays locked instead of reporting a
        # WARNING after the fact.
        selector_state = context.window_manager.superskin_layer_viewer_selector
        armature = selector_state.target_armature
        if armature is None and obj and obj.type == 'ARMATURE':
            armature = obj
        if armature is None and obj and obj.type == 'MESH':
            armature = _armature_for_mesh(obj)
        if armature is None:
            armature_name = context.scene.get("mw_last_active_armature", "")
            if armature_name and armature_name in context.view_layer.objects:
                armature = context.view_layer.objects[armature_name]

        if armature is None:
            cls.poll_message_set("This mesh has no Armature assigned")
            return False
        return True

    def execute(self, context):
        obj = context.active_object

        if obj and obj.type == 'ARMATURE' and obj.mode == 'POSE':
            # Resolved while the Armature is still active/in Pose Mode, so
            # get_effective_mesh() falls back to the selector's remembered
            # target_mesh rather than the (non-mesh) active object.
            mesh = object_selector.get_effective_mesh(context)
            bpy.ops.object.mode_set(mode='OBJECT')
            if mesh is not None:
                bpy.ops.object.select_all(action='DESELECT')
                mesh.select_set(True)
                context.view_layer.objects.active = mesh
            return {'FINISHED'}

        selector_state = context.window_manager.superskin_layer_viewer_selector
        armature = selector_state.target_armature

        if armature is None and obj and obj.type == 'ARMATURE':
            armature = obj
        if armature is None and obj and obj.type == 'MESH':
            armature = _armature_for_mesh(obj)
        if armature is None:
            armature_name = context.scene.get("mw_last_active_armature", "")
            if armature_name and armature_name in context.view_layer.objects:
                armature = context.view_layer.objects[armature_name]

        if armature is None:
            self.report({'WARNING'}, "No Armature found for the active mesh.")
            return {'CANCELLED'}

        context.scene["mw_last_active_armature"] = armature.name

        if obj and obj.type == 'MESH' and obj.mode == 'EDIT':
            if CoreFacade(context).has_pending_edit_mode_changes():
                self.report({'WARNING'}, "Save your weight edits (Save Weights) before entering Pose Mode.")
                return {'CANCELLED'}
            bpy.ops.object.mode_set(mode='OBJECT')

        if context.active_object and context.active_object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        if armature.hide_get():
            # A hidden object can't be made the active object -- the
            # assignment below would silently no-op, leaving
            # context.active_object None and crashing mode_set() with
            # "Context missing active object". Reveal it instead of locking
            # the button, since the user's intent (posing this armature) is
            # unambiguous once it's been resolved this far.
            armature.hide_set(False)

        bpy.ops.object.select_all(action='DESELECT')
        context.view_layer.objects.active = armature
        armature.select_set(True)
        bpy.ops.object.mode_set(mode='POSE')

        # Unlike the original controller operator, deliberately does NOT call
        # force_close_tab() here -- the LAYER tab (Armature/Mesh selectors +
        # layer list) must stay visible and usable in Pose Mode, since this
        # button exists precisely to let the user work on the rig without
        # losing the panel that got them there.
        return {'FINISHED'}


# ==============================================================================
# LAYER LIST ROW OPERATORS
# ==============================================================================

class SUPERSKIN_OT_layer_toggle_visible_by_item(bpy.types.Operator):
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

        def _do_toggle():
            _run_in_object_context(context, CoreFacade(context).toggle_visible, self.layer_index)
            sync_layers_to_ui_collection(obj)

        object_selector.run_with_object_active(context, obj, _do_toggle)
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
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
        # Same guard as superskin.layer_init -- this button auto-initialises
        # the layer system on first use (see execute() below), so it must be
        # locked for exactly the same reason on a mesh with no Armature
        # Modifier, or it would be a back door around that guard.
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
                # Not yet initialised -- execute() auto-inits first (seeding
                # a "Base" layer at index 0), so the real created layer will
                # land one index higher than an uninitialised peek can see.
                # invoke() must not mutate before the user confirms, so this
                # is a best-effort preview only.
                return "Layer 1"
            return f"Layer {len(CoreFacade(context).layer_meta_list())}"

        self.new_name = object_selector.run_with_object_active(context, obj, _peek_default_name)
        return context.window_manager.invoke_props_dialog(self, width=250)

    def draw(self, context):
        # activate_init=True puts the "Name" field straight into text-edit
        # mode with the cursor ready as soon as the dialog opens, so the
        # user can type the new layer's name and press Enter to confirm
        # without an extra click into the field first.
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

            new_idx = _run_in_object_context(context, ctrl.create_layer, name)
            _select_only_layer(obj, new_idx)
            sync_layers_to_ui_collection(obj)
            _enforce_visualizer_from_tab_state(context)

        object_selector.run_with_object_active(context, obj, _do_add)
        return {'FINISHED'}


class SUPERSKIN_OT_layer_remove(bpy.types.Operator):
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

        def _do():
            target = _resolve_layer_target(obj, self.layer_index)
            ctrl = CoreFacade(context)
            resolved_target = target if target >= 0 else ctrl.active_layer_index

            def _remove():
                exit_mask_mode_if_active(context, obj)
                ctrl.remove_layer(resolved_target)

            _run_in_object_context(context, _remove)
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

            moved = _run_in_object_context(context, ctrl.move_layer, resolved_target, self.direction)

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

        def _do():
            target = _resolve_layer_target(obj, self.layer_index)
            ctrl = CoreFacade(context)
            resolved_target = target if target >= 0 else ctrl.active_layer_index

            new_idx = _run_in_object_context(context, ctrl.duplicate_layer, resolved_target)
            if new_idx is not None and new_idx >= 0:
                _select_only_layer(obj, new_idx)
            sync_layers_to_ui_collection(obj)
            _enforce_visualizer_from_tab_state(context)

        object_selector.run_with_object_active(context, obj, _do)
        return {'FINISHED'}


class SUPERSKIN_OT_layer_merge_selected(bpy.types.Operator):
    """Merge every currently multi-selected layer into the active one.

    Requires at least 2 layers selected via the Layers list multi-select
    pool (``layer_selected_indices``). Composites the selected subset
    top-down (same stack-order algorithm as a normal flatten), writes the
    result into the active layer's storage slot, and deletes the other
    selected layers. The button this is wired to is disabled (via
    ``poll()``) whenever fewer than 2 layers are selected.

    Disabled (eye-icon-off) layers among the selection are excluded from
    the composited output -- their weights never contribute to the merge
    result -- but they are still deleted along with every other
    non-active selected layer, same as if they'd been visible.
    """
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
                # Should not normally happen — on_single_select() always
                # keeps the active layer inside the selection pool after
                # Item 2's changes — but fall back defensively rather than
                # crash.
                target = max(selected_indices)

            # Disabled (eye-icon-off) layers among the selection are
            # excluded from the composited output, but must still end up
            # deleted along with the rest of the merge. Tracked by name, not
            # index -- merge_selected_layers() reindexes the stack, and
            # names stay stable across that reshuffle while indices don't.
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
                # Disabled layers were excluded from compose_indices, so the
                # merge above never touched them -- delete them explicitly
                # now, re-resolving their (possibly shifted) index by name.
                sync_layers_to_ui_collection(obj)
                for name in disabled_names:
                    for item in obj.superskin_layers_collection:
                        if item.name == name:
                            ctrl.remove_layer(item.index)
                            sync_layers_to_ui_collection(obj)
                            break
                return True

            ok = _run_in_object_context(context, _do_merge)
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
        # activate_init=True puts the "Name" field straight into text-edit
        # mode with the existing name selected as soon as the dialog opens
        # (same treatment as SUPERSKIN_OT_layer_add.draw()), so the user can
        # just type over it and press Enter to confirm.
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
    SUPERSKIN_OT_layer_add,
    SUPERSKIN_OT_layer_remove,
    SUPERSKIN_OT_layer_move,
    SUPERSKIN_OT_layer_duplicate,
    SUPERSKIN_OT_layer_merge_selected,
    SUPERSKIN_OT_layer_rename_active,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
