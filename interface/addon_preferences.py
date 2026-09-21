"""Blender native Add-on Preferences panel for SuperSkinPro."""

import bpy

from .. import ADDON_NAME, ADDON_PACKAGE


class SSP_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_PACKAGE

    def draw(self, context):
        self.layout.label(
            text=f"{ADDON_NAME} settings have moved to the N-panel sidebar"
        )
        self.layout.label(text=f"({ADDON_NAME} tab > Preference panel).")


def register():
    bpy.utils.register_class(SSP_AddonPreferences)


def unregister():
    bpy.utils.unregister_class(SSP_AddonPreferences)
