"""Action operators behind the SuperSkinPro Preferences tab (drawn inline in
the N-panel by ``ui/widget_preferences.py`` — see that module for the draw
code; this file holds only the buttons' operator logic).
"""

import bpy
from ..core_subsystems.preferences.preferences_service import PreferencesService
from ..core_subsystems.license_gateway import LicenseGateway


class SUPERSKIN_OT_reset_prefs(bpy.types.Operator):
    """Reset all preferences to factory defaults immediately."""
    bl_idname = "superskin.reset_prefs"
    bl_label = "Reset Preferences"
    bl_options = {'REGISTER'}

    def execute(self, context):
        PreferencesService.reset_to_default()
        from ..core.shaders.shader_manager import ShaderManager
        ShaderManager().invalidate_color_only()
        PreferencesService.save_to_user_file()
        return {'FINISHED'}


def _decide_activation_flow(status: dict) -> str:
    """Pure decision helper for ``SUPERSKIN_OT_activate_license.invoke()``.

    Kept as a standalone function (rather than inlined in ``invoke()``) so
    this branching can be unit-tested directly with a plain dict -- Blender
    operator classes cannot be instantiated outside the operator system, and
    ``--background`` mode never actually calls ``invoke()`` at all (it always
    dispatches straight to ``execute()`` regardless of the call context
    string), so this logic is otherwise unreachable to a headless test.

    Returns:
        One of ``"deny_invalid"``, ``"deny_at_limit"``, ``"confirm"``,
        ``"proceed"``.
    """
    if not status["valid"]:
        return "deny_invalid"
    if status["at_limit"]:
        return "deny_at_limit"
    if status["uses"] > 0:
        return "confirm"
    return "proceed"


class SUPERSKIN_OT_activate_license(bpy.types.Operator):
    """Verify the entered license key against Gumroad and cache the result.

    ``invoke()`` runs a non-counting dry-run check (``check_activation_status``)
    first, then uses ``_decide_activation_flow()`` to decide what the user
    should see:
      - Key invalid / at the device limit already -- deny immediately, never
        reach ``execute()`` (so a denied attempt never gets counted either).
      - Key already active on 1..MAX_DEVICE_ACTIVATIONS-1 other devices --
        show a native confirm popup before counting this device too.
      - Never activated before -- proceed straight to ``execute()``.
    """
    bl_idname = "superskin.activate_license"
    bl_label = "Activate License"
    bl_options = {'REGISTER'}

    def invoke(self, context, event):
        prefs = context.window_manager.superskin_prefs
        key = prefs.license.license_key.strip()
        if not key:
            self.report({'WARNING'}, "Enter a license key first")
            return {'CANCELLED'}

        status = LicenseGateway.check_activation_status(key)
        decision = _decide_activation_flow(status)

        if decision == "deny_invalid":
            self.report({'WARNING'}, status["message"])
            return {'CANCELLED'}

        if decision == "deny_at_limit":
            self.report(
                {'ERROR'},
                f"This license is already activated on {status['uses']} "
                f"device(s), the maximum allowed ({status['max_uses']}). "
                f"Deactivate an older device before adding a new one.",
            )
            return {'CANCELLED'}

        if decision == "confirm":
            return context.window_manager.invoke_confirm(
                self, event,
                title="License already active elsewhere",
                message=(
                    f"This license key is already activated on "
                    f"{status['uses']} other device(s). Activate this "
                    f"device too? Please do not share your license key -- "
                    f"each purchase is for personal use."
                ),
                confirm_text="Activate This Device",
            )

        return self.execute(context)

    def execute(self, context):
        prefs = context.window_manager.superskin_prefs
        key = prefs.license.license_key.strip()
        if not key:
            self.report({'WARNING'}, "Enter a license key first")
            return {'CANCELLED'}

        success, message = LicenseGateway.activate(key)
        from ..core.facade import CoreFacade
        CoreFacade.invalidate_activation_cache()
        self.report({'INFO'} if success else {'WARNING'}, message)
        return {'FINISHED'}


class SUPERSKIN_OT_reset_license_activation(bpy.types.Operator):
    """Clear license key, activation token, and status message — debug/testing only."""
    bl_idname = "superskin.reset_license_activation"
    bl_label = "Reset All Activate"
    bl_options = {'REGISTER'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(
            self, event,
            title="Reset license activation?",
            message=(
                "This clears the license key and activation status. "
                "You will need to activate again to keep using SuperSkinPro. "
                "Are you sure?"
            ),
            confirm_text="Reset Activation",
        )

    def execute(self, context):
        PreferencesService.set_license_activation("", "", "")
        from ..core.facade import CoreFacade
        CoreFacade.invalidate_activation_cache()
        self.report({'INFO'}, "License activation data cleared")
        return {'FINISHED'}


class SUPERSKIN_OT_override_dev_defaults(bpy.types.Operator):
    """Promote every opted-in domain's current live settings to its own
    shipped default_config.json — a deliberate, permanent overwrite of
    factory defaults (distinct from the automatic per-machine user.json
    save that already happens on every edit). Only touches domains that set
    ``supports_dev_override = True`` on their UnifiedFeatureExtension (see
    ``interface/registry/register_api.py``) and only writes to each
    domain's own default_config.json file — never license/debug data or
    prefs/default_prefs.json itself.
    """
    bl_idname = "superskin.override_dev_defaults"
    bl_label = "Save Current Settings As Default"
    bl_options = {'REGISTER'}

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


_classes = [
    SUPERSKIN_OT_reset_prefs,
    SUPERSKIN_OT_activate_license,
    SUPERSKIN_OT_reset_license_activation,
    SUPERSKIN_OT_override_dev_defaults,
]


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
