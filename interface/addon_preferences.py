
import bpy

from .. import ADDON_PACKAGE


class SSP_AddonPreferences(bpy.types.AddonPreferences):
    """Defines no draw() so Blender omits the Preferences box under the add-on entry."""

    bl_idname = ADDON_PACKAGE


def register():
    bpy.utils.register_class(SSP_AddonPreferences)


def unregister():
    bpy.utils.unregister_class(SSP_AddonPreferences)
