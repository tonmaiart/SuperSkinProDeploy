"""Layer List UIList, row-click operator, adapter, and draw function.

Moved from ui/widget_tools.py as part of the LAYER tab domain extraction.
All imports that previously pointed at ui/list_widget/ now point at the
canonical shared/list_widget/ package.
"""

import bpy
import time

from ...interface.template_ui import (
    SuperSkinListMixin,
    register_adapter,
    draw_list_with_sidebar,
)
from ...interface.template_ui.select_ops import (
    ListSelectionAdapter,
    get_adapter,
    resolve_row_click_selection,
)
from ...interface.utils.utils import (
    exit_mask_mode_if_active,
    _enforce_visualizer_from_tab_state,
    sync_layers_to_ui_collection,
)
from ...interface.utils.icons import get_duplicate_icon_id, get_combine_icon_id
from . import object_selector


# ==============================================================================
# Plain-Python visibility hooks twin
# ==============================================================================

class _LayerListVisibilityHooks(SuperSkinListMixin):
    """Plain-Python twin of SUPERSKIN_UL_layer_list_view's hooks.
    Used only by LayerListAdapter.get_keys_in_visual_order() for
    off-draw-cycle visibility computation."""

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
    """Selection adapter for the LAYERS domain.

    Storage fields:
      ``obj.superskin_storage.layer_selected_indices`` — comma-bounded
        string of selected layer slot indices (e.g. ``",2,4,"``).
      ``obj.superskin_storage.layer_selection_history`` — comma-separated
        slot indices in click order.
    """

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

        from ...core.facade import CoreFacade
        ctrl = CoreFacade(context)

        context.scene.superskin_internal_transaction = True
        try:
            exit_mask_mode_if_active(context, obj)
            ctrl.switch_to_layer(layer_index)
            _enforce_visualizer_from_tab_state(context)
            sync_layers_to_ui_collection(obj)
            # SuperSkinPro's own UI only ever lives in VIEW_3D's N-panel --
            # same scoping _enforce_visualizer_from_tab_state() already uses
            # above -- so tagging every area of every editor type (Timeline,
            # Outliner, Graph Editor, etc.) on every single layer-switch
            # click was pure overhead.
            for window in context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.tag_redraw()
        finally:
            context.scene.superskin_internal_transaction = False


# ==============================================================================
# Layer Row-Click Operator
# ==============================================================================

# Module-level re-entrance guard (see docs/bug-history/0008 and the
# equivalent guard in the original widget_tools.py).
_layer_select_busy = False

# Double-click-to-rename detection. Blender does not reliably deliver
# event.value == 'DOUBLE_CLICK' to a plain operator invoked by a UI button
# click (that value is only meaningful inside a running modal's own event
# loop) — so double-click is detected manually here by comparing the click
# timestamp and row key against the previous click, the same technique used
# by other Blender addons that need double-click on a custom (non-native)
# list row.
_last_click_time = 0.0
_last_click_key = None
_DOUBLE_CLICK_THRESHOLD = 0.35  # seconds


class SUPERSKIN_OT_layer_select_by_item(bpy.types.Operator):
    """Select (and optionally multi-select) a layer row in the Layers list.

    Supports Ctrl-click (toggle), Shift-click (range-select), and
    Alt+Shift-click (select all visible). Dedicated bl_idname — NOT shared
    with the Bones tab operator (cross-domain operator-identity bleed).
    """

    bl_idname = "superskin.layer_select_by_item"
    bl_label = "Select Layer from List"
    bl_options = {'INTERNAL', 'UNDO'}
    layer_index: bpy.props.IntProperty(name="Layer Slot Index")

    def invoke(self, context, event):
        global _layer_select_busy, _last_click_time, _last_click_key
        if _layer_select_busy:
            return {'CANCELLED'}
        obj = object_selector.get_effective_mesh(context)
        if not obj or "ss_layers_meta" not in obj.data:
            return {'CANCELLED'}

        item_key = str(self.layer_index)

        now = time.time()
        is_double_click = (
            item_key == _last_click_key
            and (now - _last_click_time) < _DOUBLE_CLICK_THRESHOLD
            and not event.ctrl and not event.shift and not event.alt
        )
        _last_click_time = now
        _last_click_key = item_key

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

            # CoreFacade (constructed inside on_single_select()) requires
            # context.active_object to already be obj -- true whenever obj
            # came from a real click here, but not while an Armature is
            # active (e.g. Pose Mode) and obj is the effective-mesh
            # fallback, so this must run with obj temporarily made active.
            object_selector.run_with_object_active(context, obj, _do_select)
        finally:
            context.scene.superskin_internal_transaction = was_suppressing
            _layer_select_busy = False

        # No redraw loop here -- adapter.on_single_select() (inside
        # _do_select() above) already tags every window/area itself; this
        # used to redundantly repeat that exact same full loop a second time
        # on every single layer-switch click.

        # Double-click-to-rename: the row is already active from the
        # selection resolution above, so just pop the existing rename dialog
        # on top of it — no separate rename/persistence path needed.
        if is_double_click:
            _last_click_time = 0.0  # don't let a 3rd quick click chain into another rename
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

    def get_filter_query(self, context, data) -> str:
        storage = getattr(data, 'superskin_storage', None)
        if storage is not None:
            return getattr(storage, 'layer_filter_name', '')
        return ''

    def draw_extra_icon(self, context, layout, data, item, index: int):
        eye_icon = 'HIDE_OFF' if item.visible else 'HIDE_ON'
        op_eye = layout.operator(
            "superskin.layer_toggle_visible_by_item",
            text="", icon=eye_icon, emboss=False,
        )
        op_eye.layer_index = item.index


# ==============================================================================
# Empty-state placeholder — backs the list when there's no mesh at all
# ==============================================================================

class _SSEmptyListItem(bpy.types.PropertyGroup):
    """Zero-field item type for ``WindowManager.superskin_layer_list_placeholder``.

    ``template_list()`` requires an actual ``CollectionProperty`` to point
    at -- it can't be handed ``data=None`` -- so when there's no mesh at
    all to source a real ``superskin_layers_collection`` from, this
    permanently-empty WindowManager-level collection stands in instead.
    Always rendered with zero rows in that case, so this item type's
    fields (there are none) are never actually touched by any UIList's
    ``draw_item()``.
    """
    pass


_LAYER_LIST_BUTTON_DEFS = [
    ("operator", "superskin.layer_add",       'ADD',    "", {}),
    ("operator", "superskin.layer_remove",    'REMOVE', "", {}),
    
    ("separator", 1.0),
    ("operator", "superskin.layer_move", 'TRIA_UP',   "", {"direction": -1}),
    ("operator", "superskin.layer_move", 'TRIA_DOWN', "", {"direction":  1}),
    ("separator", 1.0),
    # Split out from the old "More" overflow menu (SUPERSKIN_MT_layer_rename_overflow,
    # removed) so it's a direct one-click button at the bottom of the side
    # column instead of two clicks through a menu -- it was the overflow
    # menu's only remaining entry, so the menu itself no longer served a
    # purpose once this moved out.
    ("operator", "superskin.layer_duplicate",
         lambda ctx: get_duplicate_icon_id() or 'DUPLICATE', "", {}),
    ("operator", "superskin.layer_merge_selected",
     lambda ctx: get_combine_icon_id() or 'AUTOMERGE_ON', "", {}),
]


# ==============================================================================
# Draw function (called by prefs.py draw_section_fn)
# ==============================================================================

def draw_layer_list(layout, context, rows=8, obj=None):
    """Draw the layer list with a search box and side buttons.

    *obj* lets the caller pass the mesh to display explicitly (e.g.
    ``object_selector.get_effective_mesh()``, which may differ from
    ``context.active_object`` while an Armature is active in Pose Mode).
    Falls back to ``context.active_object`` when omitted."""
    if obj is None:
        obj = context.active_object
    if not obj or obj.type != 'MESH':
        # No mesh at all -- there's no real object to source
        # superskin_layers_collection from, and template_list() needs an
        # actual CollectionProperty (can't be handed data=None). Point it
        # at the permanently-empty WindowManager-level placeholder instead,
        # so the exact same widget (search box, side buttons, row height)
        # still renders -- just disabled and empty -- instead of vanishing
        # and collapsing the section's height (see
        # layer_viewer_feature.py's docstring / this domain's README).
        wm = context.window_manager
        placeholder = layout.column()
        placeholder.enabled = False
        draw_list_with_sidebar(
            placeholder, context,
            ui_list_idname="SUPERSKIN_UL_layer_list_view",
            data=wm,
            collection_prop="superskin_layer_list_placeholder",
            active_data=wm,
            active_prop="superskin_layer_list_placeholder_idx",
            search_owner=wm,
            search_prop="superskin_layer_list_placeholder_search",
            rows=rows,
            button_defs=_LAYER_LIST_BUTTON_DEFS,
        )
        return

    list_layout = layout
    if "ss_layers_meta" not in obj.data:
        # Mesh has no layer system yet -- keep the list/search/side-buttons
        # drawn (so the panel's height stays stable) but fully disabled,
        # rather than interactable over an empty, meaningless collection.
        list_layout = layout.column()
        list_layout.enabled = False

    draw_list_with_sidebar(
        list_layout, context,
        ui_list_idname="SUPERSKIN_UL_layer_list_view",
        data=obj,
        collection_prop="superskin_layers_collection",
        active_data=obj,
        active_prop="superskin_layers_idx",
        search_owner=obj.superskin_storage,
        search_prop="layer_filter_name",
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
    bpy.types.WindowManager.superskin_layer_list_placeholder = bpy.props.CollectionProperty(
        type=_SSEmptyListItem,
    )
    bpy.types.WindowManager.superskin_layer_list_placeholder_idx = bpy.props.IntProperty(
        options={'SKIP_SAVE'},
    )
    bpy.types.WindowManager.superskin_layer_list_placeholder_search = bpy.props.StringProperty(
        # Search box is drawn disabled anyway (see draw_layer_list()'s
        # placeholder branch), but draw_list_with_sidebar() only draws the
        # search row at all when it's given a real search_owner/search_prop
        # -- omitting this dropped the search box entirely for the no-mesh
        # case, leaving a visibly shorter/inconsistent footprint versus the
        # "mesh with no layer system yet" disabled-list case.
        options={'SKIP_SAVE'},
    )


def unregister():
    try:
        del bpy.types.WindowManager.superskin_layer_list_placeholder_search
    except Exception:
        pass
    try:
        del bpy.types.WindowManager.superskin_layer_list_placeholder_idx
    except Exception:
        pass
    try:
        del bpy.types.WindowManager.superskin_layer_list_placeholder
    except Exception:
        pass
    bpy.utils.unregister_class(SUPERSKIN_UL_layer_list_view)
    bpy.utils.unregister_class(SUPERSKIN_OT_layer_select_by_item)
    bpy.utils.unregister_class(_SSEmptyListItem)
