"""Layer List UIList, row-click operator, adapter, and draw function."""

import bpy

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
from ....interface.utils.utils import (
    exit_mask_mode_if_active,
    _enforce_visualizer_from_tab_state,
    sync_layers_to_ui_collection,
    _get_visible_influence_bones,
)
from ....interface.utils.icons import (
    get_duplicate_icon_id,
    get_combine_icon_id,
    get_layer_state_icon_id,
    get_layer_swatch_icon_id,
    get_hide_icon_id,
    LAYER_ICON_COLORS,
)
from . import object_selector


# ==============================================================================
# Plain-Python visibility hooks twin
# ==============================================================================

class _LayerListVisibilityHooks(SuperSkinListMixin):
    """Plain-Python twin of SUPERSKIN_UL_layer_list_view's hooks."""

    def get_item_key(self, item):
        return str(item.index)

    def get_display_order(self, context, data):
        return list(range(len(data.superskin_layers_collection)))

    def extra_keep_predicate(self, context, data, item, original_idx):
        return True


_layer_visibility_hooks = _LayerListVisibilityHooks()


# ==============================================================================
# Layer List Adapter
# ==============================================================================

class LayerListAdapter(ListSelectionAdapter):
    """Selection adapter for the LAYERS domain."""

    def get_keys_in_visual_order(self, context, obj):
        return _layer_visibility_hooks.compute_visible_keys(
            context, obj, "superskin_layers_collection"
        )

    def read_selection(self, context, obj):
        storage = obj.superskin_storage

        raw = storage.layer_selected_indices
        if not raw or not raw.startswith(","):
            selected_keys = set()
        else:
            selected_keys = {k for k in raw.split(",") if k}

        hist = []
        raw_hist = storage.layer_selection_history
        if raw_hist:
            hist = [k for k in raw_hist.split(",") if k]

        last_key = hist[-1] if hist else None
        return selected_keys, last_key, hist

    def write_selection(self, context, obj, selected_keys, last_key, history):
        storage = obj.superskin_storage

        if selected_keys:
            storage.layer_selected_indices = (
                "," + ",".join(sorted(selected_keys, key=int)) + ","
            )
        else:
            storage.layer_selected_indices = ""

        storage.layer_selection_history = ",".join(history)

    def on_single_select(self, context, obj, key):
        """Switch the active layer to *key* and apply every layer-switch side
        effect. Called once per click by the row operator."""
        obj_mesh_ok = obj and obj.type == 'MESH' and "ss_layers_meta" in obj.data
        if not obj_mesh_ok:
            return
        layer_index = int(key)

        from ....core.facade import CoreFacade
        ctrl = CoreFacade(context)
        already_active = ctrl.get_active_layer_index() == layer_index

        context.scene.superskin_internal_transaction = True
        try:
            exit_mask_mode_if_active(context, obj)
            ctrl.switch_to_layer(layer_index)
            _enforce_visualizer_from_tab_state(context)
            sync_layers_to_ui_collection(obj)
            if not already_active and not obj.superskin_storage.active_is_mask:
                self._ensure_active_bone_visible(context, obj)
            for window in context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.tag_redraw()
        finally:
            context.scene.superskin_internal_transaction = False

    def _ensure_active_bone_visible(self, context, obj):
        """Per explicit user request: after switching the active Layer, always select the first
        bone that actually has."""
        bones_adapter = get_adapter('BONES')
        visible_keys = bones_adapter.get_keys_in_visual_order(context, obj)
        influenced_bones = _get_visible_influence_bones(context, obj)
        influence_keys = [k for k in visible_keys if k in influenced_bones]
        if not influence_keys:
            return
        first_key = influence_keys[0]
        bones_adapter.write_selection(context, obj, {first_key}, first_key, [first_key])
        bones_adapter.on_single_select(context, obj, first_key)
        for i, item in enumerate(obj.superskin_bones_collection):
            if item.name == first_key:
                obj.superskin_bones_idx = i
                break


# ==============================================================================
# Layer Row-Click Operator
# ==============================================================================

_layer_select_busy = False



class SUPERSKIN_OT_layer_select_by_item(bpy.types.Operator):
    """Select (and optionally multi-select) a layer row in the Layers list."""

    bl_idname = "superskin.layer_select_by_item"
    bl_label = "Select Layer from List"
    bl_options = {'INTERNAL', 'UNDO'}
    layer_index: bpy.props.IntProperty(name="Layer Slot Index")

    def invoke(self, context, event):
        global _layer_select_busy
        if _layer_select_busy:
            return {'CANCELLED'}
        obj = object_selector.get_effective_mesh(context)
        if not obj or "ss_layers_meta" not in obj.data:
            return {'CANCELLED'}

        item_key = str(self.layer_index)
        is_dbl_click = is_double_click('LAYERS', item_key, event)
        set_last_active_domain('LAYERS')

        _layer_select_busy = True
        was_suppressing = context.scene.superskin_internal_transaction
        try:
            def _do_select():
                adapter = get_adapter('LAYERS')
                selected_keys, last_key, history = adapter.read_selection(context, obj)
                visual_order = adapter.get_keys_in_visual_order(context, obj)
                selected_keys, last_key, history = resolve_row_click_selection(
                    selected_keys, last_key, history, item_key, visual_order, event
                )
                adapter.write_selection(context, obj, selected_keys, last_key, history)
                adapter.on_single_select(context, obj, last_key)

            object_selector.run_with_object_active(context, obj, _do_select)
        finally:
            context.scene.superskin_internal_transaction = was_suppressing
            _layer_select_busy = False


        if is_dbl_click:
            reset_double_click('LAYERS')  # don't let a 3rd quick click chain into another rename
            bpy.ops.superskin.layer_rename_active('INVOKE_DEFAULT')

        return {'FINISHED'}


# ==============================================================================
# Layer List UIList
# ==============================================================================

class SUPERSKIN_UL_layer_list_view(SuperSkinListMixin, bpy.types.UIList):

    def domain(self) -> str:
        return 'LAYERS'

    def get_item_key(self, item) -> str:
        return str(item.index)

    def get_display_order(self, context, data):
        items = getattr(data, 'superskin_layers_collection', ())
        return list(range(len(items)))

    def is_selected(self, context, data, key: str) -> bool:
        return f",{key}," in data.superskin_storage.layer_selected_indices

    def draw_main_icon(self, context, data, item) -> str:
        return 'NONE'

    def get_row_operator_id(self, item=None) -> str:
        return "superskin.layer_select_by_item"

    def set_row_operator_props(self, op, item):
        op.layer_index = item.index

    def draw_extra_icon(self, context, layout, data, item, index: int):
        if item.visible:
            color = item.icon if item.icon in LAYER_ICON_COLORS else 'white'
            try:
                from ....core.facade import CoreFacade
                state = CoreFacade.get_layer_mask_state(data, item.index)
                icon_value = get_layer_state_icon_id(state, color)
            except Exception:
                # Never let a mask-state read failure break the whole list's
                # draw -- fall back to the built-in icon instead.
                icon_value = 0
            fallback_icon = 'HIDE_OFF'
        else:
            icon_value = get_hide_icon_id()
            fallback_icon = 'HIDE_ON'

        icon_kwargs = {"icon_value": icon_value} if icon_value else {"icon": fallback_icon}
        op_eye = layout.operator(
            "superskin.layer_toggle_visible_by_item",
            text="", emboss=False, **icon_kwargs,
        )
        op_eye.layer_index = item.index


# ==============================================================================
# Empty-state placeholder — backs the list when there's no mesh at all
# ==============================================================================

class _SSEmptyListItem(bpy.types.PropertyGroup):
    """Zero-field item type for ``WindowManager.superskin_layer_list_placeholder``."""
    pass


_LAYER_ICON_MENU_CHOICES = tuple(
    (color, color.capitalize()) for color in LAYER_ICON_COLORS
)


class SUPERSKIN_MT_layer_list_more_options(bpy.types.Menu):
    """"More" overflow menu for the Layer list sidebar (per explicit user request)."""
    bl_label = "More"
    bl_idname = "SUPERSKIN_MT_layer_list_more_options"

    def draw(self, context):
        layout = self.layout

        dup_icon = get_duplicate_icon_id()
        if dup_icon:
            layout.operator("superskin.layer_duplicate", text="Duplicate Layer", icon_value=dup_icon)
        else:
            layout.operator("superskin.layer_duplicate", text="Duplicate Layer", icon='DUPLICATE')

        merge_icon = get_combine_icon_id()
        if merge_icon:
            layout.operator("superskin.layer_merge_selected", text="Merge Selected Layers", icon_value=merge_icon)
        else:
            layout.operator("superskin.layer_merge_selected", text="Merge Selected Layers", icon='AUTOMERGE_ON')

        layout.separator()

        layout.operator("superskin.deform_copy_layer_weight", text="Copy Layer Weight", icon='COPYDOWN')
        layout.operator("superskin.deform_cut_layer_weight", text="Cut Layer Weight", icon='TRASH')
        layout.separator()
        layout.operator("superskin.deform_paste_layer_weight_add", text="Paste to Layer Weight (Add)", icon='ADD')
        layout.operator("superskin.deform_paste_layer_weight_subtract", text="Paste to Layer Weight (Subtract)", icon='REMOVE')
        layout.operator("superskin.deform_paste_layer_weight_replace", text="Paste to Layer Weight (Replace)", icon='PASTEDOWN')

        layout.separator()
        for color_id, label in _LAYER_ICON_MENU_CHOICES:
            swatch_icon = get_layer_swatch_icon_id(color_id)
            if swatch_icon:
                op = layout.operator("superskin.layer_icon_apply", text=label, icon_value=swatch_icon)
            else:
                op = layout.operator("superskin.layer_icon_apply", text=label, icon='NONE')
            op.icon_name = color_id


_LAYER_LIST_BUTTON_DEFS = [
    ("operator", "superskin.layer_add",       'ADD',    "", {}),
    ("operator", "superskin.layer_remove",    'REMOVE', "", {}),

    ("separator", 1.0),
    ("operator", "superskin.layer_move", 'TRIA_UP',   "", {"direction": -1}),
    ("operator", "superskin.layer_move", 'TRIA_DOWN', "", {"direction":  1}),
    ("separator", 1.0),
    ("menu", "SUPERSKIN_MT_layer_list_more_options", 'COLLAPSEMENU', ""),
]


# ==============================================================================
# Draw function (called by prefs.py draw_section_fn)
# ==============================================================================

def draw_layer_list(layout, context, rows=8, obj=None):
    """Draw the layer list with a button toolbar beside it."""
    if obj is None:
        obj = context.active_object
    if not obj or obj.type != 'MESH' or "ss_layers_meta" not in obj.data:
        wm = context.window_manager
        draw_list_with_sidebar(
            layout, context,
            ui_list_idname="SUPERSKIN_UL_layer_list_view",
            data=wm,
            collection_prop="superskin_layer_list_placeholder",
            active_data=wm,
            active_prop="superskin_layer_list_placeholder_idx",
            rows=rows,
            button_defs=_LAYER_LIST_BUTTON_DEFS,
            list_enabled=False,
        )
        return

    draw_list_with_sidebar(
        layout, context,
        ui_list_idname="SUPERSKIN_UL_layer_list_view",
        data=obj,
        collection_prop="superskin_layers_collection",
        active_data=obj,
        active_prop="superskin_layers_idx",
        rows=rows,
        button_defs=_LAYER_LIST_BUTTON_DEFS,
    )


# ==============================================================================
# Registration
# ==============================================================================

def register():
    register_adapter('LAYERS', LayerListAdapter())
    bpy.utils.register_class(_SSEmptyListItem)
    bpy.utils.register_class(SUPERSKIN_OT_layer_select_by_item)
    bpy.utils.register_class(SUPERSKIN_UL_layer_list_view)
    bpy.utils.register_class(SUPERSKIN_MT_layer_list_more_options)
    bpy.types.WindowManager.superskin_layer_list_placeholder = bpy.props.CollectionProperty(
        type=_SSEmptyListItem,
    )
    bpy.types.WindowManager.superskin_layer_list_placeholder_idx = bpy.props.IntProperty(
        options={'SKIP_SAVE'},
    )


def unregister():
    try:
        del bpy.types.WindowManager.superskin_layer_list_placeholder_idx
    except Exception:
        pass
    try:
        del bpy.types.WindowManager.superskin_layer_list_placeholder
    except Exception:
        pass
    bpy.utils.unregister_class(SUPERSKIN_MT_layer_list_more_options)
    bpy.utils.unregister_class(SUPERSKIN_UL_layer_list_view)
    bpy.utils.unregister_class(SUPERSKIN_OT_layer_select_by_item)
    bpy.utils.unregister_class(_SSEmptyListItem)
