
from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...interface.utils.mode_edit_toggle import draw_mode_tool_row, draw_edit_mask_button
from ..weight_apply.public_api import BRUSH_ENABLED, WEIGHT_BRUSH_TOOL_IDNAME, LASSO_TOOL_IDNAME
from .layer_viewer import object_selector
from .layer_viewer.public_api import draw_layer_list
from .deform_bone_viewer.ui import draw_influence_list_system
from .deform_bone_viewer.deform_bone_viewer_feature import DeformBoneViewerFeature



class DeformLayerViewerFeature(UnifiedFeatureExtension):
    """Non-collapsible viewer extension rendering the Layer List and the Deform Bones List
    together."""


    domain_id = "deform_layer_viewer"
    actions = DeformBoneViewerFeature.actions
    draw_tab = ("LAYER", "SKINNING")
    collapsible = True
    priority = 0
    expanded_by_default = True
    locked_expanded = True
    show_section_label = False
    keymaps = DeformBoneViewerFeature.keymaps


    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._bone_ext = DeformBoneViewerFeature()


    def execute(self, action: str, context, core_facade) -> dict:
        return self._bone_ext.execute(action, context, core_facade)

    def get_keymap_items(self) -> list:
        return self._bone_ext.get_keymap_items()


    def draw_section_for_tab(self, layout, context, tab_key: str) -> None:
        self._draw_combined(layout, context, tab_key)

    def draw_section(self, layout, context) -> None:
        self._draw_combined(layout, context, "LAYER")

    def _draw_combined(self, layout, context, tab_key: str) -> None:
        obj = object_selector.get_effective_mesh(context)
        is_edit = obj is not None and obj.mode == 'WEIGHT_PAINT'

        def draw_mesh_header(col):
            mesh_zone = col.row(align=True)
            if is_edit:
                mesh_zone.enabled = False
            object_selector.draw_object_selectors(mesh_zone, context)
            col.separator(factor=0.1)

        def draw_settings_header(col):
            UnifiedRegistry.draw_settings_toggle_row(col, context)
            col.separator(factor=0.1)

        draw_layer_list(
            layout.column(), context=context, rows=6, obj=obj,
            header_fn=draw_mesh_header, side_header_fn=draw_settings_header,
        )
        draw_influence_list_system(layout.column(), context=context, rows=7, obj=obj)

        layout.separator(factor=1.0)
        row = layout.row()
        row.scale_y = 1.4
        split = row.split(factor=0.45)
        draw_mode_tool_row(
            split.row(align=True), context,
            brush_tool_idname=WEIGHT_BRUSH_TOOL_IDNAME,
            lasso_tool_idname=LASSO_TOOL_IDNAME,
            show_brush=BRUSH_ENABLED,
            target_mesh_obj=obj,
        )
        mask_zone = split.row()
        mask_zone.alignment = 'RIGHT'
        draw_edit_mask_button(
            mask_zone, context,
            enter_edit_idname="superskin.enter_layer_edit",
            edit_mask_idname="superskin.toggle_mask_mode",
        )


    def populate(self, data: dict) -> None:
        pass

    def serialize_into(self, full_dict: dict) -> None:
        pass



def register():
    UnifiedRegistry.register(DeformLayerViewerFeature())


def unregister():
    UnifiedRegistry.unregister("deform_layer_viewer")
