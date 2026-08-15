"""PropertyGroup data models for SuperSkinPro.

Contains all bpy.types.PropertyGroup subclasses and their scene/object
property registrations.  Operators read/write these properties; the UI
folds them into panels.
"""
import bpy


class SuperSkinAdvancedSettings(bpy.types.PropertyGroup):
    bone_list_filter_mode: bpy.props.EnumProperty(
        name="Bone List Filter",
        description="Filter mode for the Deform Bones list — mutually "
                    "exclusive, only one can be active at a time",
        items=[
            ('NONE', "No Filter", "Show every bone row, no filtering applied",
             'LONGDISPLAY', 0),
            ('INFLUENCE', "Filter Influence Bones",
             "Show only bones that possess weight inside this layer",
             'BONE_DATA', 1),
            ('ORPHAN', "Filter Orphan Bones",
             "Show only orphaned-bone rows (weight data whose bone no "
             "longer matches a vertex group), hiding every real "
             "vertex-group row",
             'ERROR', 2),
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
    selected_orphan_names: bpy.props.StringProperty(
        default="",
        description="Comma-bounded subset of the multi-select pool that are "
                    "orphan bones (no real vertex group, so they can't use "
                    "the __ssp_pool vertex-index-as-slot undo-safe encoding "
                    "real bones use in Edit Mode — see "
                    "core/layer_storage/temp_vg_bridge.py's POOL_VG_NAME). "
                    "Kept as a separate, narrower field specifically so "
                    "this pre-existing untracked-custom-property undo gap "
                    "stays isolated to the rare orphan case instead of "
                    "affecting every pool member.",
    )

    active_orphan_name: bpy.props.StringProperty(
        default="",
        description="Set when an orphaned-bone row (no real vertex group) "
                    "is the active selection in the Deform Bones list — "
                    "the bone-list 'Remap'/'Delete Orphaned Weight' menu "
                    "actions resolve their target from this. Selecting an "
                    "orphan row clears last_clicked_index to -1 (an orphan "
                    "has no real vertex group to be 'active' for paint "
                    "ops), and selecting a real bone row clears this back "
                    "to empty — only one of the two is ever populated.",
    )

    active_is_mask: bpy.props.BoolProperty(
        default=False,
        description="Set when the virtual Mask row is the active selection "
                    "in the Deform Bones list. Extends the last_clicked_index "
                    "/ active_orphan_name tri-state — exactly one of the "
                    "three is ever populated at a time. Selecting the Mask "
                    "row clears the other two to -1/empty, and selecting a "
                    "real or orphan bone row clears this back to False.",
    )

    filter_name: bpy.props.StringProperty(
        name="Search", default="", options={'TEXTEDIT_UPDATE'}
    )

    # ── Layer multi‑select pool (added 2026‑06 for unified list widget) ──
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


class SuperSkinBoneListItem(bpy.types.PropertyGroup):
    """Mirror row for the Deform Bones list — see ``ui.utils.sync_bones_to_ui_collection``.

    Blender's ``template_list`` can only iterate a real RNA collection, and
    orphaned-weight pseudo-rows (see ``core.bone_identity``) have no real
    ``VertexGroup`` to back them, so this PropertyGroup mirrors both real
    vertex groups (in hierarchy display order) and orphan rows into one
    collection the list can bind to — mirroring how ``SuperSkinLayerItem``
    already does the same for JSON-stored layer metadata.
    """
    name: bpy.props.StringProperty(name="Bone Name")
    vg_index: bpy.props.IntProperty(name="Vertex Group Index", default=-1)
    is_orphan: bpy.props.BoolProperty(name="Is Orphan", default=False)
    is_mask: bpy.props.BoolProperty(name="Is Mask", default=False)
    lock_weight: bpy.props.BoolProperty(name="Lock Weight", default=False)
    classification: bpy.props.StringProperty(name="Orphan Classification", default="")
    suggested_target: bpy.props.StringProperty(name="Suggested Remap Target", default="")


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
