import bpy


class SuperSkinAdvancedSettings(bpy.types.PropertyGroup):
    bone_list_filter_mode: bpy.props.EnumProperty(
        name="Bone List Filter",
        description="Filter mode for the Deform Bones list — a single "
                    "cycle button steps through these two states",
        items=[
            ('NONE', "Show All", "Show every bone row, no filtering applied",
             'LONGDISPLAY', 0),
            ('INFLUENCE', "Influence",
             "Show only bones that possess weight inside this layer",
             'BONE_DATA', 1),
        ],
        default='NONE',
    )

class SuperSkinSelectionStorage(bpy.types.PropertyGroup):
    last_clicked_index: bpy.props.IntProperty(
        default=-1,
        description="Single source of truth for the active vertex group "
                    "index. Drives list-row highlight, the bone picker, "
                    "weight-op targeting (_active_vg_id), and the GPU "
                    "visualizer. Also synced to vertex_groups.active_index "
                    "by apply_active_bone() for the native weight overlay.",
    )
    selection_history: bpy.props.StringProperty(default="")
    selected_names: bpy.props.StringProperty(default=",")
    active_bone_name: bpy.props.StringProperty(
        default="",
        description="Last real bone made active; kept while the Mask row is "
                    "active so leaving Mask mode returns to that bone. Shared "
                    "by every layer of the mesh.",
    )

    active_is_mask: bpy.props.BoolProperty(
        default=False,
        description="Set when the virtual Mask row is the active selection "
                    "in the Deform Bones list. Selecting the Mask row "
                    "clears last_clicked_index to -1, and selecting a bone "
                    "row clears this back to False.",
    )

    filter_name: bpy.props.StringProperty(
        name="Search", default="", options={'TEXTEDIT_UPDATE'}
    )

    layer_selected_indices: bpy.props.StringProperty(
        name="Layer Selected Indices",
        default="",
        description="Comma‑bounded string of selected layer slot indices, "
                    "e.g. \",2,4,5,\".",
    )
    layer_selection_history: bpy.props.StringProperty(
        name="Layer Selection History",
        default="",
        description="Comma‑separated slot indices in click order, mirroring "
                    "selection_history.",
    )
    layer_filter_name: bpy.props.StringProperty(
        name="Layer Search",
        default="",
        description="Wildcard filter for the layer list (separate from the "
                    "bone list's filter_name).",
        options={'TEXTEDIT_UPDATE'},
    )


class SuperSkinLayerItem(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Layer Name")
    index: bpy.props.IntProperty(name="Layer Index")
    visible: bpy.props.BoolProperty(name="Layer Visible", default=True)
    is_group: bpy.props.BoolProperty(
        name="Is Group",
        default=False,
        description="Mirrors ss_layers_meta's 'is_group' -- a Group header "
                    "row: mask-only, never carries per-bone deform weight.",
    )
    group_id: bpy.props.IntProperty(
        name="Group Index",
        default=-1,
        description="Owning Group's stable layer index, or -1 if this row "
                    "is ungrouped (mirrors ss_layers_meta's 'group_id', "
                    "None mapped to -1 since RNA has no null int).",
    )
    collapsed: bpy.props.BoolProperty(
        name="Group Collapsed",
        default=False,
        description="Group rows only -- mirrors ss_layers_meta's "
                    "'collapsed'; hides this Group's member rows from the "
                    "list when True (see SUPERSKIN_UL_layer_list_view.filter_items()).",
    )
    icon: bpy.props.StringProperty(
        name="Layer Icon Color",
        default="NONE",
        description="Color id (one of interface/utils/icons.py's "
                    "LAYER_ICON_COLORS -- 'blue', 'cyan', 'green', 'orange', "
                    "'pink', 'purple', 'white', 'yellow') for the layer's "
                    "mask-state icon in the Layer list, sourced from "
                    "assets/layer_icons/. 'NONE' (the default for a layer "
                    "whose color was never explicitly set) or any other "
                    "unrecognized value falls back to 'white' at draw time "
                    "-- see SUPERSKIN_UL_layer_list_view.draw_main_icon_value() "
                    "(features/deform_layer_viewer/layer_viewer/ui.py). The "
                    "icon actually shown also depends on the layer's live "
                    "mask data (White/Black/Edited state, see "
                    "CoreFacade.get_layer_mask_state()), not just this color. "
                    "Mirrors ss_layers_meta's own per-layer 'icon' field — "
                    "see core_subsystems/layer_compositor/layer_compositor.py's "
                    "get_icon()/set_icon().",
    )


class SuperSkinBoneListItem(bpy.types.PropertyGroup):
    """Mirror row for the Deform Bones list (``sync_bones_to_ui_collection``), kept in
    hierarchy display order, plus the virtual Mask row."""
    name: bpy.props.StringProperty(name="Bone Name")
    vg_index: bpy.props.IntProperty(name="Vertex Group Index", default=-1)
    is_mask: bpy.props.BoolProperty(name="Is Mask", default=False)
    lock_weight: bpy.props.BoolProperty(name="Lock Weight", default=False)


classes = [
    SuperSkinAdvancedSettings,
    SuperSkinSelectionStorage,
    SuperSkinLayerItem,
    SuperSkinBoneListItem,
]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.superskin_adv_settings = bpy.props.PointerProperty(type=SuperSkinAdvancedSettings)

    bpy.types.Scene.superskin_is_mask_mode = bpy.props.BoolProperty(default=False)
    bpy.types.Scene.superskin_internal_transaction = bpy.props.BoolProperty(default=False)

    bpy.types.Object.superskin_storage = bpy.props.PointerProperty(type=SuperSkinSelectionStorage)
    bpy.types.Object.superskin_layers_collection = bpy.props.CollectionProperty(type=SuperSkinLayerItem)
    bpy.types.Object.superskin_layers_idx = bpy.props.IntProperty(name="Layer List Index", default=0)
    bpy.types.Object.superskin_bones_collection = bpy.props.CollectionProperty(type=SuperSkinBoneListItem)
    bpy.types.Object.superskin_bones_idx = bpy.props.IntProperty(name="Bone List Index", default=0)


def unregister():
    del bpy.types.Object.superskin_bones_idx
    del bpy.types.Object.superskin_bones_collection
    del bpy.types.Object.superskin_layers_idx
    del bpy.types.Object.superskin_layers_collection
    del bpy.types.Object.superskin_storage
    del bpy.types.Scene.superskin_internal_transaction
    del bpy.types.Scene.superskin_is_mask_mode
    del bpy.types.Scene.superskin_adv_settings

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
