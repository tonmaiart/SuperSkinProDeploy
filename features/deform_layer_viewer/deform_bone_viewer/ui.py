"""Deform Bone List UIList, row-click operator, adapter, and draw function."""

import bpy
import traceback

from ....interface.template_ui import (
    SuperSkinListMixin,
    register_adapter,
    draw_list_with_sidebar,
)
from ....interface.template_ui.select_ops import (
    ListSelectionAdapter,
    get_adapter,
    resolve_row_click_selection,
    is_double_click,
    reset_double_click,
    set_last_active_domain,
)
from ....interface.utils.utils import _get_visible_influence_bones
from ....interface.utils.icons import get_lock_icon_id, get_bone_icon_id


# ==============================================================================
# Shared hook bodies — plain functions, no bpy.types.UIList dependency.
# Used by both MESH_UL_influence_list_view (the real UIList) and
# _BoneListVisibilityHooks (a plain-Python twin used outside the draw cycle).
# ==============================================================================

def _extra_keep_predicate_impl(context, data, item, original_idx):
    """Draw-time row filter for the unified (real + orphan + mask) mirror list."""
    adv = context.scene.superskin_adv_settings
    mode = adv.bone_list_filter_mode

    if getattr(item, 'is_mask', False):
        return True

    if getattr(item, 'is_orphan', False):
        return True

    if mode == 'INFLUENCE':
        influenced_bones = _get_visible_influence_bones(context, data)
        return item.name in influenced_bones
    return True


# ==============================================================================
# Plain-Python visibility hooks twin
# ==============================================================================

class _BoneListVisibilityHooks(SuperSkinListMixin):
    """Plain-Python twin of MESH_UL_influence_list_view's hooks, used only by
    BoneListAdapter.get_keys_in_visual_order() for off-draw-cycle visibility computation."""

    def get_item_key(self, item):
        return item.name

    def get_display_order(self, context, data):
        items = getattr(data, 'superskin_bones_collection', ())
        return list(range(len(items)))

    def extra_keep_predicate(self, context, data, item, original_idx):
        return _extra_keep_predicate_impl(context, data, item, original_idx)


_bone_visibility_hooks = _BoneListVisibilityHooks()


# ==============================================================================
# Adapter
# ==============================================================================

def _mask_key(obj):
    """Return the mask row's list key (its ``.name``), or None if the mirror collection has no
    mask row yet."""
    for item in obj.superskin_bones_collection:
        if item.is_mask:
            return item.name
    return None


class BoneListAdapter(ListSelectionAdapter):
    """Bridge between the generic row-click operator and the bone-list
    selection storage (``obj.superskin_storage.selected_names`` etc.)."""

    def get_keys_in_visual_order(self, context, obj):
        return _bone_visibility_hooks.compute_visible_keys(
            context, obj, "superskin_bones_collection"
        )

    def read_selection(self, context, obj):
        storage = obj.superskin_storage
        vg_list = obj.vertex_groups

        raw = storage.selected_names
        if not raw or not raw.startswith(","):
            selected_keys = set()
        else:
            selected_keys = {n for n in raw.split(",") if n}

        last_key = None
        if storage.active_is_mask:
            last_key = _mask_key(obj)
        elif storage.active_orphan_name:
            last_key = storage.active_orphan_name
        elif 0 <= storage.last_clicked_index < len(vg_list):
            last_key = vg_list[storage.last_clicked_index].name

        hist = []
        raw_hist = storage.selection_history
        if raw_hist:
            for part in raw_hist.split(","):
                part = part.strip()
                if part:
                    try:
                        idx = int(part)
                        if 0 <= idx < len(vg_list):
                            hist.append(vg_list[idx].name)
                    except ValueError:
                        pass

        return selected_keys, last_key, hist

    def write_selection(self, context, obj, selected_keys, last_key, history):
        storage = obj.superskin_storage
        vg_list = obj.vertex_groups
        name_to_idx = {vg.name: i for i, vg in enumerate(vg_list)}

        if selected_keys:
            storage.selected_names = (
                "," + ",".join(sorted(selected_keys, key=lambda n: name_to_idx.get(n, 9999))) + ","
            )
        else:
            storage.selected_names = ","

        mask_key = _mask_key(obj)
        orphan_names = {item.name for item in obj.superskin_bones_collection if item.is_orphan}
        if last_key is not None and last_key == mask_key:
            storage.last_clicked_index = -1
            storage.active_is_mask = True
            storage.active_orphan_name = ""
        elif last_key in name_to_idx:
            storage.last_clicked_index = name_to_idx[last_key]
            storage.active_is_mask = False
            storage.active_orphan_name = ""
        elif last_key in orphan_names:
            storage.last_clicked_index = -1
            storage.active_is_mask = False
            storage.active_orphan_name = last_key
        else:
            storage.last_clicked_index = -1
            storage.active_is_mask = False
            storage.active_orphan_name = ""

        hist_indices = []
        for k in history:
            idx = name_to_idx.get(k)
            if idx is not None:
                hist_indices.append(str(idx))
        storage.selection_history = ",".join(hist_indices)

    def on_single_select(self, context, obj, key):
        """Tail of a row click."""
        storage = obj.superskin_storage
        storage.active_orphan_name = ""
        mask_key = _mask_key(obj)

        try:
            from ....core.facade import CoreFacade
            CoreFacade(context).clear_orphan_weight_preview()
        except Exception:
            pass

        if key is not None and key == mask_key:
            try:
                from ....core.facade import CoreFacade
                CoreFacade(context).apply_active_bone()
            except Exception:
                traceback.print_exc()
            return

        from ....interface.utils.utils import exit_mask_mode_if_active
        exit_mask_mode_if_active(context, obj)

        try:
            from ....core.facade import CoreFacade
            ctrl = CoreFacade(context)
            ctrl.set_selected_bones(ctrl.get_selected_bones_pool_string())
            vg_list = obj.vertex_groups
            if 0 <= storage.last_clicked_index < len(vg_list):
                ctrl.set_active_bone_name(vg_list[storage.last_clicked_index].name)
            try:
                ctrl.apply_active_bone()
            except Exception:
                pass
        except Exception:
            traceback.print_exc()


# ==============================================================================
# Bone Row-Click Operator
# ==============================================================================

def _focus_view_on_bone(context, obj, bone_name):
    """Recenter the active 3D viewport on *bone_name*'s pose-space midpoint."""
    arm_obj = None
    for mod in obj.modifiers:
        if mod.type == 'ARMATURE' and mod.object:
            arm_obj = mod.object
            break
    if arm_obj is None:
        return

    pose_bone = arm_obj.pose.bones.get(bone_name)
    if pose_bone is None:
        return

    midpoint = (pose_bone.head + pose_bone.tail) / 2
    world_pos = arm_obj.matrix_world @ midpoint

    space = context.space_data
    if space is None or space.type != 'VIEW_3D' or space.region_3d is None:
        return
    space.region_3d.view_location = world_pos
    context.area.tag_redraw()


class SUPERSKIN_OT_select_vertex_group_row(bpy.types.Operator):
    """Select a vertex-group row in the Deform Bones list."""

    bl_idname = "superskin.select_vertex_group_row"
    bl_label = "Select Vertex Group Row"
    bl_options = {'INTERNAL'}

    index: bpy.props.IntProperty(name="Vertex Group Index")

    def invoke(self, context, event):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            return {'CANCELLED'}

        vg_list = obj.vertex_groups
        if self.index < 0 or self.index >= len(vg_list):
            return {'CANCELLED'}

        item_key = vg_list[self.index].name
        is_dbl_click = is_double_click('BONES', item_key, event)
        set_last_active_domain('BONES')

        was_suppressing = context.scene.superskin_internal_transaction
        context.scene.superskin_internal_transaction = True
        try:
            adapter = get_adapter('BONES')
            selected_keys, last_key, history = adapter.read_selection(context, obj)
            visual_order = adapter.get_keys_in_visual_order(context, obj)

            selected_keys, last_key, history = resolve_row_click_selection(
                selected_keys, last_key, history, item_key, visual_order, event
            )

            adapter.write_selection(context, obj, selected_keys, last_key, history)
            adapter.on_single_select(context, obj, item_key)

        finally:
            context.scene.superskin_internal_transaction = was_suppressing

        _sync_bones_idx_to_real_bone(obj, vg_index=self.index)

        if is_dbl_click:
            reset_double_click('BONES')  # don't let a 3rd quick click chain into another focus
            _focus_view_on_bone(context, obj, item_key)

        context.area.tag_redraw()
        return {'FINISHED'}


def _sync_bones_idx_to_real_bone(obj, vg_index: int):
    """Point ``obj.superskin_bones_idx`` at the real-bone row that was just
    clicked. Drives only the blue row-highlight in the UIList."""
    for i, item in enumerate(obj.superskin_bones_collection):
        if not item.is_orphan and not item.is_mask and item.vg_index == vg_index:
            obj.superskin_bones_idx = i
            return


# ==============================================================================
# Mask Row-Click Operator
# ==============================================================================

class SUPERSKIN_OT_select_mask_row(bpy.types.Operator):
    """Select the virtual Mask row in the Deform Bones list."""

    bl_idname = "superskin.select_mask_row"
    bl_label = "Select Layer Mask Row"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            return {'CANCELLED'}

        item_key = _mask_key(obj)
        if item_key is None:
            return {'CANCELLED'}
        set_last_active_domain('BONES')

        from ....core.facade import CoreFacade
        CoreFacade.debug_log("bone_id", f"select_mask_row.invoke() ENTRY: obj={obj.name!r}")

        was_suppressing = context.scene.superskin_internal_transaction
        context.scene.superskin_internal_transaction = True
        try:
            adapter = get_adapter('BONES')
            selected_keys, last_key, history = adapter.read_selection(context, obj)
            visual_order = adapter.get_keys_in_visual_order(context, obj)

            selected_keys, last_key, history = resolve_row_click_selection(
                selected_keys, last_key, history, item_key, visual_order, event
            )

            adapter.write_selection(context, obj, selected_keys, last_key, history)
            adapter.on_single_select(context, obj, item_key)
        finally:
            context.scene.superskin_internal_transaction = was_suppressing

        _sync_bones_idx_to_mask(obj)

        storage = obj.superskin_storage
        CoreFacade.debug_log(
            "bone_id",
            f"select_mask_row.invoke() EXIT: active_is_mask={storage.active_is_mask} "
            f"superskin_bones_idx={obj.superskin_bones_idx} "
            f"superskin_is_mask_mode={getattr(context.scene, 'superskin_is_mask_mode', None)}",
        )

        context.area.tag_redraw()
        return {'FINISHED'}


def _sync_bones_idx_to_mask(obj):
    """Point ``obj.superskin_bones_idx`` at the mask row in the mirror
    collection. Drives only the blue row-highlight in the UIList."""
    for i, item in enumerate(obj.superskin_bones_collection):
        if item.is_mask:
            obj.superskin_bones_idx = i
            return


# ==============================================================================
# UIList: Influence List View
# ==============================================================================

class MESH_UL_influence_list_view(SuperSkinListMixin, bpy.types.UIList):

    def domain(self) -> str:
        return 'BONES'

    def get_item_key(self, item) -> str:
        return item.name

    def get_display_order(self, context, data):
        items = getattr(data, 'superskin_bones_collection', ())
        return list(range(len(items)))

    def is_selected(self, context, data, key: str) -> bool:
        return f",{key}," in data.superskin_storage.selected_names

    def draw_main_icon(self, context, data, item) -> str:
        if item.is_mask:
            return 'MOD_MASK'
        if item.is_orphan:
            return 'ERROR'
        return 'NONE'

    def get_row_operator_id(self, item=None) -> str:
        if item is not None and item.is_mask:
            return "superskin.select_mask_row"
        if item is not None and item.is_orphan:
            return "superskin.select_orphan_bone_row"
        return "superskin.select_vertex_group_row"

    def set_row_operator_props(self, op, item):
        if item.is_mask:
            return
        if item.is_orphan:
            op.orphan_name = item.name
        else:
            op.index = item.vg_index

    def _ensure_filter_dependencies(self, context, data):
        adv = context.scene.superskin_adv_settings
        _ = adv.bone_list_filter_mode

    def extra_keep_predicate(self, context, data, item, original_idx: int) -> bool:
        return _extra_keep_predicate_impl(context, data, item, original_idx)

    def draw_extra_icon(self, context, layout, data, item, index: int):
        if item.is_mask:
            return
        influenced_set = _get_visible_influence_bones(context, data)
        is_influenced = item.name in influenced_set
        icon_value = get_lock_icon_id(item.lock_weight, is_influenced)
        if icon_value:
            op_lock = layout.operator(
                "superskin.toggle_vg_lock", text="", icon_value=icon_value, emboss=False,
            )
        else:
            lock_icon = 'LOCKED' if item.lock_weight else 'UNLOCKED'
            op_lock = layout.operator(
                "superskin.toggle_vg_lock", text="", icon=lock_icon, emboss=False,
            )
        op_lock.index = item.vg_index
        op_lock.vg_name = item.name


# ==============================================================================
# "More" overflow menu (Plane Copy clipboard entries, ported from
# features/clipboard -- see clipboard_logic.py and
# docs/domains/deform_layer_viewer.md)
# ==============================================================================

class SUPERSKIN_MT_bone_list_more_options(bpy.types.Menu):
    """"More" overflow menu for the Deform Bones list sidebar (per explicit user request)."""
    bl_label = "More"
    bl_idname = "SUPERSKIN_MT_bone_list_more_options"

    def draw(self, context):
        layout = self.layout
        layout.operator("superskin.deform_copy_bone_weight", text="Copy Bone Weight", icon='COPYDOWN')
        layout.operator("superskin.deform_cut_bone_weight", text="Cut Bone Weight", icon='TRASH')
        layout.separator()
        layout.operator("superskin.deform_paste_bone_weight_add", text="Paste to Bone Weight (Add)", icon='ADD')
        layout.operator("superskin.deform_paste_bone_weight_subtract", text="Paste to Bone Weight (Subtract)", icon='REMOVE')
        layout.operator("superskin.deform_paste_bone_weight_replace", text="Paste to Bone Weight (Replace)", icon='PASTEDOWN')


# ==============================================================================
# Draw function (called by prefs.py draw_section_fn)
# ==============================================================================

_bones_resync_pending = False

_bones_resync_attempted_vg_count = {}


def _force_bones_resync():
    global _bones_resync_pending
    _bones_resync_pending = False
    try:
        obj = bpy.context.active_object
        if obj and obj.type == 'MESH' and "ss_layers_meta" in obj.data:
            from ....interface.utils.utils import sync_bones_to_ui_collection
            sync_bones_to_ui_collection(obj)
            for window in bpy.context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.tag_redraw()
    except Exception:
        pass
    return None


_last_draw_log_state = None


def _bone_list_filter_icon(context):
    mode = context.scene.superskin_adv_settings.bone_list_filter_mode
    if mode == 'INFLUENCE':
        icon_value = get_bone_icon_id()
        return icon_value if icon_value else 'BONE_DATA'
    return 'LONGDISPLAY'


def _bone_list_filter_is_influence(context):
    return context.scene.superskin_adv_settings.bone_list_filter_mode == 'INFLUENCE'


def draw_influence_list_system(layout, context, rows=8, obj=None):
    """Draw the Deform Bones list with a search box and a button toolbar above it."""
    if obj is None:
        obj = context.active_object
    button_defs = [
        ("operator", "superskin.cycle_bone_list_filter",
         _bone_list_filter_icon, "", {}, _bone_list_filter_is_influence),
        ("separator", 1.0),
        ("menu", "SUPERSKIN_MT_bone_list_more_options", 'COLLAPSEMENU', ""),
    ]

    if (not obj or obj.type != 'MESH' or not hasattr(obj, "superskin_storage")
            or "ss_layers_meta" not in obj.data):
        wm = context.window_manager
        draw_list_with_sidebar(
            layout, context,
            ui_list_idname="MESH_UL_influence_list_view",
            data=wm,
            collection_prop="superskin_layer_list_placeholder",
            active_data=wm,
            active_prop="superskin_layer_list_placeholder_idx",
            rows=rows,
            button_defs=button_defs,
            list_enabled=False,
        )
        return

    from ....core.facade import CoreFacade
    col = obj.superskin_bones_collection

    global _last_draw_log_state
    log_state = (obj.name, len(col),
                 context.scene.superskin_adv_settings.bone_list_filter_mode)
    if log_state != _last_draw_log_state:
        _last_draw_log_state = log_state
        CoreFacade.debug_log(
            "bone_id",
            f"draw_influence_list_system(): obj={obj.name!r} "
            f"superskin_bones_collection has {len(col)} rows, "
            f"filter_mode={context.scene.superskin_adv_settings.bone_list_filter_mode!r}",
        )

    global _bones_resync_pending
    vg_count = len(obj.vertex_groups)
    bones_missing = len(col) == 0 and vg_count > 0
    already_attempted = _bones_resync_attempted_vg_count.get(obj.name) == vg_count
    if ("ss_layers_meta" in obj.data and bones_missing
            and not _bones_resync_pending and not already_attempted):
        _bones_resync_pending = True
        _bones_resync_attempted_vg_count[obj.name] = vg_count
        CoreFacade.debug_log(
            "bone_id",
            f"draw_influence_list_system(): scheduling _force_bones_resync() "
            f"timer for obj={obj.name!r} (bones_missing={bones_missing} "
            f"vg_count={vg_count})",
        )
        bpy.app.timers.register(_force_bones_resync, first_interval=0.0)

    layout = layout.column()
    layout.enabled = not getattr(context.scene, "superskin_is_mask_mode", False)

    draw_list_with_sidebar(
        layout, context,
        ui_list_idname="MESH_UL_influence_list_view",
        data=obj,
        collection_prop="superskin_bones_collection",
        active_data=obj,
        active_prop="superskin_bones_idx",
        rows=rows,
        button_defs=button_defs,
    )


# ==============================================================================
# Registration
# ==============================================================================

def register():
    register_adapter('BONES', BoneListAdapter())
    bpy.utils.register_class(SUPERSKIN_OT_select_vertex_group_row)
    bpy.utils.register_class(SUPERSKIN_OT_select_mask_row)
    bpy.utils.register_class(MESH_UL_influence_list_view)
    bpy.utils.register_class(SUPERSKIN_MT_bone_list_more_options)


def unregister():
    bpy.utils.unregister_class(SUPERSKIN_MT_bone_list_more_options)
    bpy.utils.unregister_class(MESH_UL_influence_list_view)
    bpy.utils.unregister_class(SUPERSKIN_OT_select_mask_row)
    bpy.utils.unregister_class(SUPERSKIN_OT_select_vertex_group_row)
