"""Blender native Add-on Preferences panel for SuperSkinPro.

This class exists only so Blender has somewhere to resolve
``context.preferences.addons[ADDON_PACKAGE].preferences`` -- required for a
registered ``AddonPreferences`` subclass with this ``bl_idname`` to exist,
even though nothing reads or writes fields on it anymore.

It draws no user-facing settings — System/Customize settings (color ramps,
palette, PREFERENCE-tab feature extensions, debug tools, about) moved to the
collapsible "Preference" section of the viewport N-panel sidebar's single
panel (``interface/panel_main.py``), since the Add-on Preferences window was
hard to reach. The addon-updater's
own 5 settings (``auto_check_update``, ``updater_interval_*``) used to live
here too, but have since moved to
``features/addon_updater/addon_updater_feature.py``'s ``SSPrefAddonUpdater``
(a ``WindowManager`` PropertyGroup) per the project's Feature Preferences Rule.
"""

import bpy

from .. import ADDON_NAME, ADDON_PACKAGE


class SSP_AddonPreferences(bpy.types.AddonPreferences):
    # Must match the addon's actual runtime module name for Blender to locate
    # this class's instance in context.preferences.addons[...]. Blender
    # Extensions assign a repository-namespaced package name at install time
    # (e.g. "bl_ext.user_default.superskinpro"), not the manifest "id" or
    # folder name -- a hardcoded string here silently breaks the whole
    # preferences panel (no error, it just never shows). See __init__.py.
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
