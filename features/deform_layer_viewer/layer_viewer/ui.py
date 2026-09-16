"""Layer List UIList, row-click operator, adapter, and draw function.

Moved from ui/widget_tools.py as part of the LAYER tab domain extraction.
All imports that previously pointed at ui/list_widget/ now point at the
canonical shared/list_widget/ package.
"""

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
)
from ....interface.utils.utils import (
    exit_mask_mode_if_active,
    _enforce_visualizer_from_tab_state,
    sync_layers_to_ui_collection,
)
from ....interface.utils.icons import (
    get_duplicate_icon_id,
    get_combine_icon_id,
    get_layer_state_icon_id,
    get_layer_swatch_icon_id,
    LAYER_ICON_COLORS,
)
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

        from ....core.facade import CoreFacade
        ctrl = CoreFacade(context)

        context.scene.superskin_internal_transaction = True
        try:
            exit_mask_mode_if_active(context, obj)
            ctrl.switch_to_layer(layer_index)
            _enforce_visualizer_from_tab_state(context)
            sync_layers_to_ui_collection(obj)
            self._ensure_active_bone_visible(context, obj)
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

    def _ensure_active_bone_visible(self, context, obj):
        """Per explicit user request: after switching the active Layer,
        always select the first bone in the Deform Bones list's
        currently-filtered view (e.g. the "Influence" filter mode, which
        only shows bones with weight on the active Layer -- see
        ``_extra_keep_predicate_impl`` in ``deform_bone_viewer/ui.py``) --
        mirroring a plain click on that row, via the same 'BONES' adapter
        the row-click operator itself uses (no cross-domain Python
        import). Does nothing if the filtered list is currently empty
        (no new bone is selected in that case)."""
        bones_adapter = get_adapter('BONES')
        visible_keys = bones_adapter.get_keys_in_visual_order(context, obj)
        if not visible_keys:
            return
        first_key = visible_keys[0]
        bones_adapter.write_selection(context, obj, {first_key}, first_key, [first_key])
        bones_adapter.on_single_select(context, obj, first_key)
        for i, item in enumerate(obj.superskin_bones_collection):
            if item.name == first_key:
                obj.superskin_bones_idx = i
                break


# ==============================================================================
# Layer Row-Click Operator
# ==============================================================================

# Module-level re-entrance guard (see docs/bug-history/0008 and the
# equivalent guard in the original widget_tools.py).
_layer_select_busy = False

# Double-click-to-rename detection now lives in
# interface/template_ui/select_ops.py's is_double_click()/reset_double_click()
# (shared with the Deform Bones list's double-click-to-focus, domain key
# 'LAYERS' here vs 'BONES' there) — see that module's docstring for why this
# has to be detected manually rather than via event.value == 'DOUBLE_CLICK'.


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
        global _layer_select_busy
        if _layer_select_busy:
            return {'CANCELLED'}
        obj = object_selector.get_effective_mesh(context)
        if not obj or "ss_layers_meta" not in obj.data:
            return {'CANCELLED'}

        item_key = str(self.layer_index)
        is_dbl_click = is_double_click('LAYERS', item_key, event)

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
        # Fallback only -- used when draw_main_icon_value() below can't
        # supply a custom icon_value (e.g. the source PNG failed to load).
        return 'GREASEPENCIL_LAYER_GROUP'

    def draw_main_icon_value(self, context, data, item) -> int:
        # The Layer list's icon shows two independent things at once: a
        # user-picked color (item.icon, one of LAYER_ICON_COLORS, sourced
        # from assets/layer_icons/ -- see SUPERSKIN_MT_layer_list_more_options
        # .draw() below) and a live mask-coverage state derived from the
        # layer's actual data (WhiteMask/BlackMask/EditedMask -- every vert
        # at 1.0 / every vert at 0.0 / anything else, per explicit user
        # request). A layer whose color was never explicitly set (still the
        # "NONE" default written by core_subsystems/layer_compositor/
        # layer_compositor.py's create_layer()/duplicate_layer()) falls back
        # to 'white' here for display only -- the stored ss_layers_meta
        # value itself is left as "NONE" until the user actually picks a
        # color via the "More" menu.
        color = item.icon if item.icon in LAYER_ICON_COLORS else 'white'
        obj = data
        try:
            from ....core.facade import CoreFacade
            state = CoreFacade.get_layer_mask_state(obj, item.index)
        except Exception:
            # Never let a mask-state read failure break the whole list's
            # draw -- fall back to the generic built-in icon instead (see
            # draw_main_icon() above).
            return 0
        return get_layer_state_icon_id(state, color)

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


# (color id, menu label) pairs for the "More" menu's color-picker entries
# below -- moved here from the now-removed standalone toolbar button +
# popup (SUPERSKIN_OT_layer_icon_picker, ops.py), per explicit user
# request: picking a color is a flat, single-click list of menu entries
# instead of a separate popup. Colors are restricted to LAYER_ICON_COLORS
# (interface/utils/icons.py) -- the only colors with a full
# WhiteMask/BlackMask/EditedMask icon triplet under assets/layer_icons/,
# per explicit user request that the Layer list's icon only ever be one of
# those custom PNGs, never a built-in Blender icon.
_LAYER_ICON_MENU_CHOICES = tuple(
    (color, color.capitalize()) for color in LAYER_ICON_COLORS
)


class SUPERSKIN_MT_layer_list_more_options(bpy.types.Menu):
    """"More" overflow menu for the Layer list sidebar (per explicit user
    request) -- holds Duplicate, Merge Selected Layers, the Clipboard
    Layer Weight actions (Copy/Cut/Paste Add/Subtract/Replace, drawn flat
    instead of behind a nested "Clipboard Layer Weight" submenu -- the
    former SUPERSKIN_MT_deform_layer_weight_clipboard submenu class was
    removed, its five menu entries inlined directly here per a later
    explicit user request, with no "Clipboard Layer Weight" section label
    header either per a still-later explicit user request), and the
    icon-color-swatch entries (see _LAYER_ICON_MENU_CHOICES above, no
    "Layer Icon" section label above them either, removed the same day
    per a further explicit user request), each moved in from being its
    own direct/appended sidebar button."""
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

        # Operators stay defined/registered in deform_bone_viewer/
        # (clipboard_ops.py -- these target the active layer's mask, not
        # a bone), referenced here purely by bl_idname string -- same
        # cross-domain-by-string convention already used throughout this
        # domain (see docs/domains/deform_layer_viewer.md's "Cross-domain
        # button/operator references").
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
    # The flexible flush-right ("spacer",) entry that used to sit here
    # (pushing "More" to the toolbar's far right edge, 2026-09-13) was
    # removed the same day, per a further explicit user request, once this
    # domain's two lists started sharing a half-width column each
    # (`draw_lists_side_by_side()`) -- `UILayout.separator_spacer()`
    # requesting "fill all remaining space" inside an already
    # width-constrained split() column was overflowing the button row past
    # that column's actual boundary (reported as the toolbar's buttons
    # visibly spilling into the neighboring list/panel edge). A plain
    # fixed separator keeps "More" directly after Move Up/Down instead of
    # flush-right.
    #
    # Duplicate, Merge Selected Layers, and Clipboard Layer Weight moved
    # into this "More" overflow menu (per explicit user request) instead of
    # each being its own direct/appended sidebar button.
    ("menu", "SUPERSKIN_MT_layer_list_more_options", 'COLLAPSEMENU', ""),
]


# ==============================================================================
# Draw function (called by prefs.py draw_section_fn)
# ==============================================================================

def draw_layer_list(layout, context, rows=8, obj=None):
    """Draw the layer list with a search box and a button toolbar above it.

    *obj* lets the caller pass the mesh to display explicitly (e.g.
    ``object_selector.get_effective_mesh()``, which may differ from
    ``context.active_object`` while an Armature is active in Pose Mode).
    Falls back to ``context.active_object`` when omitted.

    No longer accepts a ``top_fn`` hook (removed 2026-09-13, per explicit
    user request) -- the Mesh dropdown that used to be drawn through it
    is now its own standalone row at the ``_draw_combined()`` call site
    (see ``docs/domains/deform_layer_viewer.md``'s "Armature / Mesh
    Selectors" section), and no other caller ever used the hook."""
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
        draw_list_with_sidebar(
            layout, context,
            ui_list_idname="SUPERSKIN_UL_layer_list_view",
            data=wm,
            collection_prop="superskin_layer_list_placeholder",
            active_data=wm,
            active_prop="superskin_layer_list_placeholder_idx",
            search_owner=wm,
            search_prop="superskin_layer_list_placeholder_search",
            rows=rows,
            button_defs=_LAYER_LIST_BUTTON_DEFS,
            list_enabled=False,
        )
        return

    # Mesh has no layer system yet -- keep the list/search/side-buttons
    # drawn (so the panel's height stays stable) but fully disabled,
    # rather than interactable over an empty, meaningless collection.
    draw_list_with_sidebar(
        layout, context,
        ui_list_idname="SUPERSKIN_UL_layer_list_view",
        data=obj,
        collection_prop="superskin_layers_collection",
        active_data=obj,
        active_prop="superskin_layers_idx",
        search_owner=obj.superskin_storage,
        search_prop="layer_filter_name",
        rows=rows,
        button_defs=_LAYER_LIST_BUTTON_DEFS,
        list_enabled="ss_layers_meta" in obj.data,
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
    bpy.utils.unregister_class(SUPERSKIN_MT_layer_list_more_options)
    bpy.utils.unregister_class(SUPERSKIN_UL_layer_list_view)
    bpy.utils.unregister_class(SUPERSKIN_OT_layer_select_by_item)
    bpy.utils.unregister_class(_SSEmptyListItem)
