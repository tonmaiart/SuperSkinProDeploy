# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

"""Blender operators, popups, and handlers for the addon_updater feature domain.

These operators are standalone (not routed through the generic
SUPERSKIN_OT_execute_action dispatcher / run_domain_via_unified) because that
dispatcher constructs CoreFacade(context) as an instance, which raises
ValueError if SuperSkinPro is not Pro-activated or there is no active mesh
object. Update-checking must keep working regardless of activation/mesh
state -- the same reasoning that keeps debug_console's operators standalone.
"""

import os
import traceback

import bpy
from bpy.app.handlers import persistent

from ...core.facade import CoreFacade

# Safely import the updater engine.
# Prevents popups for users with invalid python installs e.g. missing libraries
# and will replace with a fake class instead if it fails (so UI draws work).
try:
    from .engine import Updater as updater
except Exception as e:
    print("ERROR INITIALIZING UPDATER")
    print(str(e))
    traceback.print_exc()

    class SingletonUpdaterNone(object):
        """Fake, bare minimum fields and functions for the updater object."""

        def __init__(self):
            self.invalid_updater = True  # Used to distinguish bad install.

            self.addon = None
            self.verbose = False
            self.use_print_traces = True
            self.error = None
            self.error_msg = None
            self.async_checking = None

        def clear_state(self):
            self.addon = None
            self.verbose = False
            self.invalid_updater = True
            self.error = None
            self.error_msg = None
            self.async_checking = None

        def run_update(self, force, callback, clean):
            pass

        def check_for_update(self, now):
            pass

    updater = SingletonUpdaterNone()
    updater.error = "Error initializing updater module"
    updater.error_msg = str(e)

# Must declare this before classes are loaded, otherwise the bl_idname's will
# not match and have errors. Must be all lowercase and no spaces! Should also
# be unique among any other addons that could exist (using this updater code),
# to avoid clashes in operator registration.
updater.addon = "superskinpro"


# -----------------------------------------------------------------------------
# Utils
# -----------------------------------------------------------------------------
def layout_split(layout, factor=0.0, align=False):
    """Intermediate method for pre and post blender 2.8 split UI function"""
    if not hasattr(bpy.app, "version") or bpy.app.version < (2, 80):
        return layout.split(percentage=factor, align=align)
    return layout.split(factor=factor, align=align)


def get_user_preferences(context=None):
    """Return this domain's WindowManager-scoped settings PropertyGroup.

    Previously read context.preferences.addons[__package__].preferences (a
    Blender-native AddonPreferences field); now these 5 settings are owned by
    the addon_updater feature domain per the project's Feature Preferences
    Rule -- see addon_updater_feature.py's SSPrefAddonUpdater.
    """
    if not context:
        context = bpy.context
    return context.window_manager.superskin_addon_updater_prefs


# -----------------------------------------------------------------------------
# Updater operators
# -----------------------------------------------------------------------------


# Simple popup to prompt use to check for update & offer install if available.
class AddonUpdaterInstallPopup(bpy.types.Operator):
    """Check and install update if available"""
    bl_label = "Update {x} addon".format(x=updater.addon)
    bl_idname = updater.addon + ".updater_install_popup"
    bl_description = "Popup to check and display current updates available"
    bl_options = {'REGISTER', 'INTERNAL'}

    # if true, run clean install - ie remove all files before adding new
    # equivalent to deleting the addon and reinstalling, except the
    # updater folder remains
    clean_install: bpy.props.BoolProperty(
        name="Clean install",
        description=("If enabled, completely clear the addon's folder before "
                     "installing new update, creating a fresh install"),
        default=False,
        options={'HIDDEN'}
    )

    ignore_enum: bpy.props.EnumProperty(
        name="Process update",
        description="Decide to install, ignore, or defer new addon update",
        items=[
            ("install", "Update Now", "Install update now"),
            ("ignore", "Ignore", "Ignore this update to prevent future popups"),
            ("defer", "Defer", "Defer choice till next blender session")
        ],
        options={'HIDDEN'}
    )

    def check(self, context):
        return True

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        if updater.invalid_updater:
            layout.label(text="Updater module error")
            return
        elif updater.update_ready:
            col = layout.column()
            col.scale_y = 0.7
            col.label(text="Update {} ready!".format(updater.update_version),
                      icon="LOOP_FORWARDS")
            col.label(text="Choose 'Update Now' & press OK to install, ",
                      icon="BLANK1")
            col.label(text="or click outside window to defer", icon="BLANK1")
            row = col.row()
            row.prop(self, "ignore_enum", expand=True)
            col.split()
        elif not updater.update_ready:
            col = layout.column()
            col.scale_y = 0.7
            col.label(text="No updates available")
            col.label(text="Press okay to dismiss dialog")
        else:
            # Case: updater.update_ready = None
            # we have not yet checked for the update.
            layout.label(text="Check for update now?")

    def execute(self, context):
        CoreFacade.debug_log("feature_domains", "addon_updater.AddonUpdaterInstallPopup.execute(): entry")
        # In case of error importing updater.
        if updater.invalid_updater:
            return {'CANCELLED'}

        if updater.manual_only:
            bpy.ops.wm.url_open(url=updater.website)
        elif updater.update_ready:

            # Action based on enum selection.
            if self.ignore_enum == 'defer':
                return {'FINISHED'}
            elif self.ignore_enum == 'ignore':
                updater.ignore_update()
                return {'FINISHED'}

            res = updater.run_update(force=False,
                                     callback=post_update_callback,
                                     clean=self.clean_install)

            # Should return 0, if not something happened.
            if updater.verbose:
                if res == 0:
                    print("Updater returned successful")
                else:
                    print("Updater returned {}, error occurred".format(res))
        elif updater.update_ready is None:
            _ = updater.check_for_update(now=True)

            # Re-launch this dialog.
            atr = AddonUpdaterInstallPopup.bl_idname.split(".")
            getattr(getattr(bpy.ops, atr[0]), atr[1])('INVOKE_DEFAULT')
        else:
            updater.print_verbose("Doing nothing, not ready for update")
        return {'FINISHED'}


# User preference check-now operator
class AddonUpdaterCheckNow(bpy.types.Operator):
    bl_label = "Check now for " + updater.addon + " update"
    bl_idname = updater.addon + ".updater_check_now"
    bl_description = "Check now for an update to the {} addon".format(
        updater.addon)
    bl_options = {'REGISTER', 'INTERNAL'}

    def execute(self, context):
        CoreFacade.debug_log("feature_domains", "addon_updater.AddonUpdaterCheckNow.execute(): entry")
        if updater.invalid_updater:
            return {'CANCELLED'}

        if updater.async_checking and updater.error is None:
            # Check already happened.
            # Used here to just avoid constant applying settings below.
            # Ignoring if error, to prevent being stuck on the error screen.
            CoreFacade.debug_log(
                "feature_domains", "addon_updater.AddonUpdaterCheckNow.execute(): already checking, skipped")
            return {'CANCELLED'}

        # apply the UI settings
        settings = get_user_preferences(context)
        updater.set_check_interval(
            enabled=settings.auto_check_update,
            months=settings.updater_interval_months,
            days=settings.updater_interval_days,
            hours=settings.updater_interval_hours,
            minutes=settings.updater_interval_minutes)

        # Input is an optional callback function. This function should take a
        # bool input. If true: update ready, if false: no update ready.
        updater.check_for_update_now(ui_refresh)

        return {'FINISHED'}


class AddonUpdaterUpdateNow(bpy.types.Operator):
    bl_label = "Update " + updater.addon + " addon now"
    bl_idname = updater.addon + ".updater_update_now"
    bl_description = "Update to the latest version of the {x} addon".format(
        x=updater.addon)
    bl_options = {'REGISTER', 'INTERNAL'}

    # If true, run clean install - ie remove all files before adding new
    # equivalent to deleting the addon and reinstalling, except the updater
    # folder remains.
    clean_install: bpy.props.BoolProperty(
        name="Clean install",
        description=("If enabled, completely clear the addon's folder before "
                     "installing new update, creating a fresh install"),
        default=False,
        options={'HIDDEN'}
    )

    def execute(self, context):
        CoreFacade.debug_log("feature_domains", "addon_updater.AddonUpdaterUpdateNow.execute(): entry")

        # in case of error importing updater
        if updater.invalid_updater:
            return {'CANCELLED'}

        if updater.manual_only:
            bpy.ops.wm.url_open(url=updater.website)
        if updater.update_ready:
            # if it fails, offer to open the website instead
            try:
                res = updater.run_update(force=False,
                                         callback=post_update_callback,
                                         clean=self.clean_install)

                # Should return 0, if not something happened.
                if updater.verbose:
                    if res == 0:
                        print("Updater returned successful")
                    else:
                        print("Updater error response: {}".format(res))
            except Exception as expt:
                updater._error = "Error trying to run update"
                updater._error_msg = str(expt)
                updater.print_trace()
                CoreFacade.debug_log(
                    "feature_domains",
                    f"addon_updater.AddonUpdaterUpdateNow.execute(): exception running update: {expt}")
                atr = AddonUpdaterInstallManually.bl_idname.split(".")
                getattr(getattr(bpy.ops, atr[0]), atr[1])('INVOKE_DEFAULT')
        elif updater.update_ready is None:
            (update_ready, version, link) = updater.check_for_update(now=True)
            # Re-launch this dialog.
            atr = AddonUpdaterInstallPopup.bl_idname.split(".")
            getattr(getattr(bpy.ops, atr[0]), atr[1])('INVOKE_DEFAULT')

        elif not updater.update_ready:
            self.report({'INFO'}, "Nothing to update")
            return {'CANCELLED'}
        else:
            self.report(
                {'ERROR'}, "Encountered a problem while trying to update")
            return {'CANCELLED'}

        return {'FINISHED'}


class AddonUpdaterUpdateTarget(bpy.types.Operator):
    bl_label = updater.addon + " version target"
    bl_idname = updater.addon + ".updater_update_target"
    bl_description = "Install a targeted version of the {x} addon".format(
        x=updater.addon)
    bl_options = {'REGISTER', 'INTERNAL'}

    def target_version(self, context):
        # In case of error importing updater.
        if updater.invalid_updater:
            ret = []

        ret = []
        i = 0
        for tag in updater.tags:
            ret.append((tag, tag, "Select to install " + tag))
            i += 1
        return ret

    target: bpy.props.EnumProperty(
        name="Target version to install",
        description="Select the version to install",
        items=target_version
    )

    # If true, run clean install - ie remove all files before adding new
    # equivalent to deleting the addon and reinstalling, except the
    # updater folder remains.
    clean_install: bpy.props.BoolProperty(
        name="Clean install",
        description=("If enabled, completely clear the addon's folder before "
                     "installing new update, creating a fresh install"),
        default=False,
        options={'HIDDEN'}
    )

    @classmethod
    def poll(cls, context):
        if updater.invalid_updater:
            return False
        return updater.update_ready is not None and len(updater.tags) > 0

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        if updater.invalid_updater:
            layout.label(text="Updater error")
            return
        split = layout_split(layout, factor=0.5)
        sub_col = split.column()
        sub_col.label(text="Select install version")
        sub_col = split.column()
        sub_col.prop(self, "target", text="")

    def execute(self, context):
        CoreFacade.debug_log(
            "feature_domains",
            f"addon_updater.AddonUpdaterUpdateTarget.execute(): entry target={self.target!r}")
        # In case of error importing updater.
        if updater.invalid_updater:
            return {'CANCELLED'}

        res = updater.run_update(
            force=False,
            revert_tag=self.target,
            callback=post_update_callback,
            clean=self.clean_install)

        # Should return 0, if not something happened.
        if res == 0:
            updater.print_verbose("Updater returned successful")
        else:
            updater.print_verbose(
                "Updater returned {}, , error occurred".format(res))
            return {'CANCELLED'}

        return {'FINISHED'}


class AddonUpdaterInstallManually(bpy.types.Operator):
    """As a fallback, direct the user to download the addon manually"""
    bl_label = "Install update manually"
    bl_idname = updater.addon + ".updater_install_manually"
    bl_description = "Proceed to manually install update"
    bl_options = {'REGISTER', 'INTERNAL'}

    error: bpy.props.StringProperty(
        name="Error Occurred",
        default="",
        options={'HIDDEN'}
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self)

    def draw(self, context):
        layout = self.layout

        if updater.invalid_updater:
            layout.label(text="Updater error")
            return

        # Display error if a prior autoamted install failed.
        if self.error != "":
            col = layout.column()
            col.scale_y = 0.7
            col.label(text="There was an issue trying to auto-install",
                      icon="ERROR")
            col.label(text="Press the download button below and install",
                      icon="BLANK1")
            col.label(text="the zip file like a normal addon.", icon="BLANK1")
        else:
            col = layout.column()
            col.scale_y = 0.7
            col.label(text="Install the addon manually")
            col.label(text="Press the download button below and install")
            col.label(text="the zip file like a normal addon.")

        row = layout.row()

        if updater.update_link is not None:
            row.operator(
                "wm.url_open",
                text="Direct download").url = updater.update_link
        else:
            row.operator(
                "wm.url_open",
                text="(failed to retrieve direct download)")
            row.enabled = False

            if updater.website is not None:
                row = layout.row()
                ops = row.operator("wm.url_open", text="Open website")
                ops.url = updater.website
            else:
                row = layout.row()
                row.label(text="See source website to download the update")

    def execute(self, context):
        CoreFacade.debug_log("feature_domains", "addon_updater.AddonUpdaterInstallManually.execute(): entry")
        return {'FINISHED'}


class AddonUpdaterUpdatedSuccessful(bpy.types.Operator):
    """Addon in place, popup telling user it completed or what went wrong"""
    bl_label = "Installation Report"
    bl_idname = updater.addon + ".updater_update_successful"
    bl_description = "Update installation response"
    bl_options = {'REGISTER', 'INTERNAL', 'UNDO'}

    error: bpy.props.StringProperty(
        name="Error Occurred",
        default="",
        options={'HIDDEN'}
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_popup(self, event)

    def draw(self, context):
        layout = self.layout

        if updater.invalid_updater:
            layout.label(text="Updater error")
            return

        saved = updater.json
        if self.error != "":
            col = layout.column()
            col.scale_y = 0.7
            col.label(text="Error occurred, did not install", icon="ERROR")
            if updater.error_msg:
                msg = updater.error_msg
            else:
                msg = self.error
            col.label(text=str(msg), icon="BLANK1")
            rw = col.row()
            rw.scale_y = 2
            rw.operator(
                "wm.url_open",
                text="Click for manual download.",
                icon="BLANK1").url = updater.website
        elif not updater.auto_reload_post_update:
            # Tell user to restart blender after an update/restore!
            if "just_restored" in saved and saved["just_restored"]:
                col = layout.column()
                col.label(text="Addon restored", icon="RECOVER_LAST")
                alert_row = col.row()
                alert_row.alert = True
                alert_row.operator(
                    "wm.quit_blender",
                    text="Restart blender to reload",
                    icon="BLANK1")
                updater.json_reset_restore()
            else:
                col = layout.column()
                col.label(
                    text="Addon successfully installed", icon="FILE_TICK")
                alert_row = col.row()
                alert_row.alert = True
                alert_row.operator(
                    "wm.quit_blender",
                    text="Restart blender to reload",
                    icon="BLANK1")

        else:
            # reload addon, but still recommend they restart blender
            if "just_restored" in saved and saved["just_restored"]:
                col = layout.column()
                col.scale_y = 0.7
                col.label(text="Addon restored", icon="RECOVER_LAST")
                col.label(
                    text="Consider restarting blender to fully reload.",
                    icon="BLANK1")
                updater.json_reset_restore()
            else:
                col = layout.column()
                col.scale_y = 0.7
                col.label(
                    text="Addon successfully installed", icon="FILE_TICK")
                col.label(
                    text="Consider restarting blender to fully reload.",
                    icon="BLANK1")

    def execute(self, context):
        CoreFacade.debug_log("feature_domains", "addon_updater.AddonUpdaterUpdatedSuccessful.execute(): entry")
        return {'FINISHED'}


class AddonUpdaterIgnore(bpy.types.Operator):
    """Ignore update to prevent future popups"""
    bl_label = "Ignore update"
    bl_idname = updater.addon + ".updater_ignore"
    bl_description = "Ignore update to prevent future popups"
    bl_options = {'REGISTER', 'INTERNAL'}

    @classmethod
    def poll(cls, context):
        if updater.invalid_updater:
            return False
        elif updater.update_ready:
            return True
        else:
            return False

    def execute(self, context):
        CoreFacade.debug_log(
            "feature_domains",
            f"addon_updater.AddonUpdaterIgnore.execute(): entry, marking version={updater.update_version!r} ignored")
        # in case of error importing updater
        if updater.invalid_updater:
            return {'CANCELLED'}
        updater.ignore_update()
        self.report({"INFO"}, "Open addon preferences for updater options")
        return {'FINISHED'}


class AddonUpdaterEndBackground(bpy.types.Operator):
    """Stop checking for update in the background"""
    bl_label = "End background check"
    bl_idname = updater.addon + ".end_background_check"
    bl_description = "Stop checking for update in the background"
    bl_options = {'REGISTER', 'INTERNAL'}

    def execute(self, context):
        CoreFacade.debug_log("feature_domains", "addon_updater.AddonUpdaterEndBackground.execute(): entry")
        # in case of error importing updater
        if updater.invalid_updater:
            return {'CANCELLED'}
        updater.stop_async_check_update()
        return {'FINISHED'}


# -----------------------------------------------------------------------------
# Handler related, to create popups
# -----------------------------------------------------------------------------

# global vars used to prevent duplicate popup handlers
ran_auto_check_install_popup = False
ran_update_success_popup = False

# global var for preventing successive calls
ran_background_check = False


@persistent
def updater_run_success_popup_handler(scene):
    global ran_update_success_popup
    ran_update_success_popup = True
    CoreFacade.debug_log("feature_domains", "addon_updater.updater_run_success_popup_handler(): entry")

    # in case of error importing updater
    if updater.invalid_updater:
        return

    try:
        if "scene_update_post" in dir(bpy.app.handlers):
            bpy.app.handlers.scene_update_post.remove(
                updater_run_success_popup_handler)
        else:
            bpy.app.handlers.depsgraph_update_post.remove(
                updater_run_success_popup_handler)
    except:
        pass

    atr = AddonUpdaterUpdatedSuccessful.bl_idname.split(".")
    getattr(getattr(bpy.ops, atr[0]), atr[1])('INVOKE_DEFAULT')


@persistent
def updater_run_install_popup_handler(scene):
    global ran_auto_check_install_popup
    ran_auto_check_install_popup = True
    updater.print_verbose("Running the install popup handler.")
    CoreFacade.debug_log("feature_domains", "addon_updater.updater_run_install_popup_handler(): entry")

    # in case of error importing updater
    if updater.invalid_updater:
        return

    try:
        if "scene_update_post" in dir(bpy.app.handlers):
            bpy.app.handlers.scene_update_post.remove(
                updater_run_install_popup_handler)
        else:
            bpy.app.handlers.depsgraph_update_post.remove(
                updater_run_install_popup_handler)
    except:
        pass

    if "ignore" in updater.json and updater.json["ignore"]:
        return  # Don't do popup if ignore pressed.
    elif "version_text" in updater.json and updater.json["version_text"].get("version"):
        version = updater.json["version_text"]["version"]
        ver_tuple = updater.version_tuple_from_text(version)

        if ver_tuple < updater.current_version:
            # User probably manually installed to get the up to date addon
            # in here. Clear out the update flag using this function.
            updater.print_verbose(
                "{} updater: appears user updated, clearing flag".format(
                    updater.addon))
            updater.json_reset_restore()
            return
    atr = AddonUpdaterInstallPopup.bl_idname.split(".")
    getattr(getattr(bpy.ops, atr[0]), atr[1])('INVOKE_DEFAULT')


def background_update_callback(update_ready):
    """Passed into the updater, background thread updater"""
    global ran_auto_check_install_popup
    updater.print_verbose("Running background update callback")
    CoreFacade.debug_log(
        "feature_domains", f"addon_updater.background_update_callback(): entry update_ready={update_ready}")

    # In case of error importing updater.
    if updater.invalid_updater:
        return
    if not updater.show_popups:
        return
    if not update_ready:
        return

    # See if we need add to the update handler to trigger the popup.
    handlers = []
    if "scene_update_post" in dir(bpy.app.handlers):  # 2.7x
        handlers = bpy.app.handlers.scene_update_post
    else:  # 2.8+
        handlers = bpy.app.handlers.depsgraph_update_post
    in_handles = updater_run_install_popup_handler in handlers

    if in_handles or ran_auto_check_install_popup:
        return

    if "scene_update_post" in dir(bpy.app.handlers):  # 2.7x
        bpy.app.handlers.scene_update_post.append(
            updater_run_install_popup_handler)
    else:  # 2.8+
        bpy.app.handlers.depsgraph_update_post.append(
            updater_run_install_popup_handler)
    ran_auto_check_install_popup = True
    updater.print_verbose("Attempted popup prompt")


def post_update_callback(module_name, res=None):
    """Callback for once the run_update function has completed.

    Only makes sense to use this if "auto_reload_post_update" == False,
    i.e. don't auto-restart the addon.

    Arguments:
        module_name: returns the module name from updater, but unused here.
        res: If an error occurred, this is the detail string.
    """
    CoreFacade.debug_log(
        "feature_domains", f"addon_updater.post_update_callback(): entry res={res!r}")

    # In case of error importing updater.
    if updater.invalid_updater:
        return

    if res is None:
        # This is the same code as in conditional at the end of the register
        # function, ie if "auto_reload_post_update" == True, skip code.
        updater.print_verbose(
            "{} updater: Running post update callback".format(updater.addon))

        atr = AddonUpdaterUpdatedSuccessful.bl_idname.split(".")
        getattr(getattr(bpy.ops, atr[0]), atr[1])('INVOKE_DEFAULT')
        global ran_update_success_popup
        ran_update_success_popup = True
    else:
        # Some kind of error occurred and it was unable to install, offer
        # manual download instead.
        atr = AddonUpdaterUpdatedSuccessful.bl_idname.split(".")
        getattr(getattr(bpy.ops, atr[0]), atr[1])('INVOKE_DEFAULT', error=res)
    return


def ui_refresh(update_status):
    """Redraw the ui once an async thread has completed"""
    for windowManager in bpy.data.window_managers:
        for window in windowManager.windows:
            for area in window.screen.areas:
                area.tag_redraw()


def check_for_update_background():
    """Function for asynchronous background check.

    *Could* be called on register, but would be bad practice as the bare
    minimum code should run at the moment of registration (addon ticked).

    Uses `check_for_update_now()` (not `check_for_update_async()`) so the
    check bypasses the configured day/hour/minute interval and always hits
    the network once per Blender launch / F3 Reload Scripts, per project
    preference that a newly published release must be detected on the very
    next startup instead of waiting out the interval. This is mandatory --
    unlike the vendored library's original design, `auto_check_update` does
    NOT gate this call. There is no user-facing way to disable the
    launch-time check (the settings toggle only ever fed `_check_interval_enabled`,
    which is no longer consulted by the `check_for_update_now()` path).
    """
    if updater.invalid_updater:
        return
    global ran_background_check
    if ran_background_check:
        # Global var ensures check only happens once.
        return
    elif updater.update_ready is not None or updater.async_checking:
        # Check already happened.
        # Used here to just avoid constant applying settings below.
        return

    CoreFacade.debug_log("feature_domains", "addon_updater.check_for_update_background(): starting background check")

    settings = get_user_preferences(bpy.context)

    # Apply the UI settings.
    updater.set_check_interval(enabled=settings.auto_check_update,
                               months=settings.updater_interval_months,
                               days=settings.updater_interval_days,
                               hours=settings.updater_interval_hours,
                               minutes=settings.updater_interval_minutes)

    # Input is an optional callback function. This function should take a bool
    # input, if true: update ready, if false: no update ready.
    updater.check_for_update_now(background_update_callback)
    ran_background_check = True


def check_for_update_nonthreaded(self, context):
    """Can be placed in front of other operators to launch when pressed"""
    if updater.invalid_updater:
        return

    CoreFacade.debug_log("feature_domains", "addon_updater.check_for_update_nonthreaded(): entry")

    # Only check if it's ready, ie after the time interval specified should
    # be the async wrapper call here.
    settings = get_user_preferences(bpy.context)
    updater.set_check_interval(enabled=settings.auto_check_update,
                               months=settings.updater_interval_months,
                               days=settings.updater_interval_days,
                               hours=settings.updater_interval_hours,
                               minutes=settings.updater_interval_minutes)

    (update_ready, version, link) = updater.check_for_update(now=False)
    if update_ready:
        atr = AddonUpdaterInstallPopup.bl_idname.split(".")
        getattr(getattr(bpy.ops, atr[0]), atr[1])('INVOKE_DEFAULT')
    else:
        updater.print_verbose("No update ready")
        self.report({'INFO'}, "No update ready")


def show_reload_popup():
    """For use in register only, to show popup after re-enabling the addon.

    Must be enabled by developer.
    """
    if updater.invalid_updater:
        return
    saved_state = updater.json
    global ran_update_success_popup

    has_state = saved_state is not None
    just_updated = "just_updated" in saved_state
    updated_info = saved_state["just_updated"]

    if not (has_state and just_updated and updated_info):
        return

    CoreFacade.debug_log("feature_domains", "addon_updater.show_reload_popup(): just_updated flag set, evaluating popup")

    updater.json_reset_postupdate()  # So this only runs once.

    # No handlers in this case.
    if not updater.auto_reload_post_update:
        return

    # See if we need add to the update handler to trigger the popup.
    handlers = []
    if "scene_update_post" in dir(bpy.app.handlers):  # 2.7x
        handlers = bpy.app.handlers.scene_update_post
    else:  # 2.8+
        handlers = bpy.app.handlers.depsgraph_update_post
    in_handles = updater_run_success_popup_handler in handlers

    if in_handles or ran_update_success_popup:
        return

    if "scene_update_post" in dir(bpy.app.handlers):  # 2.7x
        bpy.app.handlers.scene_update_post.append(
            updater_run_success_popup_handler)
    else:  # 2.8+
        bpy.app.handlers.depsgraph_update_post.append(
            updater_run_success_popup_handler)
    ran_update_success_popup = True


def skip_tag_function(self, tag):
    """A global function for tag skipping.

    A way to filter which tags are displayed, e.g. to limit downgrading too
    long ago.

    Args:
        self: The instance of the singleton addon update.
        tag: the text content of a tag from the repo, e.g. "v1.2.3".

    Returns:
        bool: True to skip this tag name (ie don't allow for downloading this
            version), or False if the tag is allowed.
    """

    # In case of error importing updater.
    if self.invalid_updater:
        return False

    if self.include_branches:
        for branch in self.include_branch_list:
            if tag["name"].lower() == branch:
                return False

    # Function converting string to tuple, ignoring e.g. leading 'v'.
    tupled = self.version_tuple_from_text(tag["name"])
    if not isinstance(tupled, tuple):
        return True

    # Select the min tag version - change tuple accordingly.
    if self.version_min_update is not None:
        if tupled < self.version_min_update:
            return True  # Skip if current version below this.

    # Select the max tag version.
    if self.version_max_update is not None:
        if tupled >= self.version_max_update:
            return True  # Skip if current version at or above this.

    return False


def select_link_function(self, tag):
    """Only customize if trying to leverage "attachments" in *GitHub* releases.

    A way to select from one or multiple attached downloadable files from the
    server, instead of downloading the default release/tag source code.
    """
    return tag["zipball_url"]


# -----------------------------------------------------------------------------
# Register / unregister the operator classes only.
#
# Engine configuration (repository, skip_tag/select_link
# wiring) lives in addon_updater_feature.py's register(), which calls
# engine.configure() with these two functions as parameters -- keeping this
# module free of a hard dependency on the feature-registration entry point,
# consistent with the Deep Matrix Reload bottom-up order (engine -> ops ->
# addon_updater_feature).
# -----------------------------------------------------------------------------
classes = (
    AddonUpdaterInstallPopup,
    AddonUpdaterCheckNow,
    AddonUpdaterUpdateNow,
    AddonUpdaterUpdateTarget,
    AddonUpdaterInstallManually,
    AddonUpdaterUpdatedSuccessful,
    AddonUpdaterIgnore,
    AddonUpdaterEndBackground,
)


def register():
    if updater.error:
        print("Exiting updater operator registration, " + updater.error)
        return
    for cls in classes:
        bpy.utils.register_class(cls)

    # Special situation: we just updated the addon, show a popup to tell the
    # user it worked.
    show_reload_popup()


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

    global ran_auto_check_install_popup
    ran_auto_check_install_popup = False

    global ran_update_success_popup
    ran_update_success_popup = False

    global ran_background_check
    ran_background_check = False
