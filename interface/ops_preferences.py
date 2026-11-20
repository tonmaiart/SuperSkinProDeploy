
import bpy
from .. import ADDON_NAME
from ..core_subsystems.preferences.preferences_service import PreferencesService

_IS_DEV_BUILD = "Dev" in ADDON_NAME


class SUPERSKIN_OT_reset_prefs(bpy.types.Operator):
    """Reset all settings to their defaults"""
    bl_idname = "superskin.reset_prefs"
    bl_label = "Reset Preferences"
    bl_options = {'REGISTER'}

    def execute(self, context):
        PreferencesService.reset_to_default()
        from ..core.shaders.shader_manager import ShaderManager
        ShaderManager().invalidate_color_only()
        PreferencesService.save_to_user_file()
        return {'FINISHED'}


class SUPERSKIN_OT_load_factory_preferences(bpy.types.Operator):
    """Restore every SuperSkinPro setting and shortcut to its default"""
    bl_idname = "superskin.load_factory_preferences"
    bl_label = "Load Factory Preferences"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(
            self, event,
            title="Load factory preferences?",
            message="This restores every SuperSkinPro setting and shortcut to its default.",
            confirm_text="Load Factory Preferences",
        )

    def execute(self, context):
        from .utils import keymap_editor
        bpy.ops.superskin.reset_prefs()
        context.window_manager.keyconfigs.update()
        for row in keymap_editor.shortcut_rows():
            keymap_editor.reset_row(context, row)
        context.window_manager.keyconfigs.update()
        context.preferences.is_dirty = True
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
        return {'FINISHED'}


class SUPERSKIN_OT_override_dev_defaults(bpy.types.Operator):
    """Promote every opted-in domain's current live settings to its own shipped
    default_config.json."""
    bl_idname = "superskin.override_dev_defaults"
    bl_label = "Save Current Settings As Default"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return _IS_DEV_BUILD

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(
            self, event,
            title="Overwrite shipped default settings?",
            message=(
                "This permanently overwrites this addon's shipped "
                "default_config.json files with the current live settings "
                "for every domain that supports it. This cannot be undone "
                "from the UI."
            ),
            confirm_text="Overwrite Defaults",
        )

    def execute(self, context):
        from ..core_subsystems.preferences import io
        from .registry.register_api import UnifiedRegistry

        updated = []
        for ext in UnifiedRegistry.get_all():
            if not ext.supports_developer_override():
                continue
            defaults_path = ext.get_defaults_path()
            if not defaults_path:
                continue
            tmp = {}
            try:
                ext.serialize_into(tmp)
                flat_data = tmp
                for key in ext.get_json_path():
                    flat_data = flat_data[key]
                io.save_json(defaults_path, flat_data)
                updated.append(ext.get_id())
            except Exception as e:
                self.report({'WARNING'}, f"Failed to save defaults for '{ext.get_id()}': {e}")

        if updated:
            self.report({'INFO'}, f"Saved current settings as default for: {', '.join(updated)}")
        else:
            self.report({'WARNING'}, "No domains support saving developer defaults")
        return {'FINISHED'}


class SUPERSKIN_OT_open_keymap_prefs(bpy.types.Operator):
    """Open Blender's keymap settings showing SuperSkinPro shortcuts"""
    bl_idname = "superskin.open_keymap_prefs"
    bl_label = "Open in Blender Keymap"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        bpy.ops.screen.userpref_show(section='KEYMAP')
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type != 'PREFERENCES':
                    continue
                space = area.spaces.active
                space.filter_type = 'NAME'
                space.filter_text = "superskin"
                area.tag_redraw()
        return {'FINISHED'}


_classes = [
    SUPERSKIN_OT_reset_prefs,
    SUPERSKIN_OT_load_factory_preferences,
    SUPERSKIN_OT_override_dev_defaults,
    SUPERSKIN_OT_open_keymap_prefs,
]


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
