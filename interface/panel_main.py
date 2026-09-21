"""SuperSkinPro sidebar panel — single-panel interface."""

import bpy

from .. import ADDON_NAME, ADDON_VERSION
from .registry.register_api import UnifiedRegistry


class VIEW3D_PT_mw_master_modular_panel(bpy.types.Panel):
    bl_idname = "VIEW3D_PT_superskin_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = ADDON_NAME
    bl_label = ""
    bl_order = 1000000

    def draw_header(self, context):
        """Draws the top row (``_draw_top_row()``) into the panel's native header strip (shared
        between the panel's."""
        self._draw_top_row(self.layout, context)

    def draw(self, context):
        from ..core.facade import CoreFacade

        layout = self.layout
        activated = CoreFacade.is_system_activated()

        self._draw_skin_tab(layout, context, activated)

    def _draw_top_row(self, layout, context):
        """"Super Skin Pro v{version}" label, drawn into the panel's native header strip by
        ``draw_header()`` (see that method's docstring for the placement history)."""
        layout.row().label(text=f"Super Skin Pro v{ADDON_VERSION}")

    def _draw_skin_tab(self, layout, context, activated):
        if not activated:
            activate_ext = UnifiedRegistry.get_by_id("activate")
            if activate_ext is not None:
                activate_ext.draw_activate_prompt(layout, context)
            return

        active_interface = context.window_manager.superskin_active_interface
        if active_interface == 'LAYER':
            object_tools_ext = UnifiedRegistry.get_by_id("object_tools_ui")
            if object_tools_ext is not None:
                object_tools_ext.draw_section(layout, context)
            self._draw_update_row(layout, context)
        elif context.mode in ('OBJECT', 'PAINT_WEIGHT'):
            obj = context.active_object
            if not (obj and obj.type == "MESH"):
                layout.label(text="No mesh active", icon="ERROR")
            else:
                skin_tools_ext = UnifiedRegistry.get_by_id("skin_tools_ui")
                if skin_tools_ext is not None:
                    skin_tools_ext.draw_section(layout, context)

    def _draw_update_row(self, layout, context):
        """The addon-update checker's compact control, at the very bottom of the LAYER tab's
        body only."""
        updater_ext = UnifiedRegistry.get_by_id("addon_updater")
        if updater_ext is not None:
            updater_ext.draw_update_button(layout, context)


def register():
    bpy.types.WindowManager.superskin_active_interface = bpy.props.EnumProperty(
        name="Active Interface",
        description="Which SuperSkinPro sidebar interface is currently shown, "
                    "decoupled from Blender's native Object/Edit mode",
        items=[
            ('LAYER', "Layer", "Show the Layer weight-management interface"),
            ('SKINNING', "Skinning", "Show the Skinning/weight-painting interface"),
        ],
        default='LAYER',
        options={'SKIP_SAVE'},
    )
    bpy.utils.register_class(VIEW3D_PT_mw_master_modular_panel)


def unregister():
    bpy.utils.unregister_class(VIEW3D_PT_mw_master_modular_panel)
    del bpy.types.WindowManager.superskin_active_interface
