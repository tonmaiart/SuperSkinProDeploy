
import bpy

from .. import ADDON_NAME
from .registry.register_api import UnifiedRegistry
from .utils.icons import get_logo_icon_id


class VIEW3D_PT_mw_master_modular_panel(bpy.types.Panel):
    bl_idname = "VIEW3D_PT_superskin_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = ADDON_NAME
    bl_label = ""
    bl_order = 1000000

    def draw_header(self, context):
        logo_id = get_logo_icon_id()
        if logo_id:
            self.layout.row().label(text="Super Skin Pro", icon_value=logo_id)
        else:
            self.layout.row().label(text="Super Skin Pro")

    def draw(self, context):
        self._draw_skin_tab(self.layout, context)

    def _draw_skin_tab(self, layout, context):
        active_interface = context.window_manager.superskin_active_interface
        if active_interface == 'LAYER':
            object_tools_ext = UnifiedRegistry.get_by_id("object_tools_ui")
            if object_tools_ext is not None:
                object_tools_ext.draw_section(layout, context)
        elif active_interface == 'POSE':
            viewer_ext = UnifiedRegistry.get_by_id("deform_layer_viewer")
            if viewer_ext is not None:
                viewer_ext.draw_section_for_tab(layout, context, "SKINNING")
        elif context.mode in ('OBJECT', 'PAINT_WEIGHT'):
            obj = context.active_object
            if not (obj and obj.type == "MESH"):
                layout.label(text="No mesh active", icon="ERROR")
            else:
                skin_tools_ext = UnifiedRegistry.get_by_id("skin_tools_ui")
                if skin_tools_ext is not None:
                    skin_tools_ext.draw_section(layout, context)


def register():
    bpy.types.WindowManager.superskin_active_interface = bpy.props.EnumProperty(
        name="Active Interface",
        description="Which SuperSkinPro panel is shown",
        items=[
            ('LAYER', "Layer", "Manage layers"),
            ('SKINNING', "Skinning", "Paint and edit weights"),
            ('POSE', "Pose", "Weight tools while posing"),
        ],
        default='LAYER',
        options={'SKIP_SAVE'},
    )
    bpy.utils.register_class(VIEW3D_PT_mw_master_modular_panel)


def unregister():
    bpy.utils.unregister_class(VIEW3D_PT_mw_master_modular_panel)
    del bpy.types.WindowManager.superskin_active_interface
