"""AddonUpdaterFeature — Unified Component Architecture implementation for the addon-update-
checker domain."""

import os

import bpy

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...interface.utils.icons import get_update_icon_id
from ...core.facade import CoreFacade
from . import ops
from .engine import Updater as updater, configure

_DEFAULTS_PATH = os.path.join(os.path.dirname(__file__), "default_config.json")


# ==============================================================================
# Property Group
# ==============================================================================

def _on_changed(self, context):
    CoreFacade.debug_log("feature_domains", "addon_updater.SSPrefAddonUpdater: setting changed, saving prefs")
    CoreFacade.save_prefs()


class SSPrefAddonUpdater(bpy.types.PropertyGroup):
    """Settings for the addon-update-checker."""

    auto_check_update: bpy.props.BoolProperty(
        name="Auto-check for Update",
        description=(
            "Reserved for a future manual/interval-based check path. Does NOT "
            "control the mandatory launch-time check -- see "
            "ops.check_for_update_background()"
        ),
        default=True,
        update=_on_changed,
    )
    updater_interval_months: bpy.props.IntProperty(
        name="Months", description="Number of months between update checks",
        default=0, min=0,
        update=_on_changed,
    )
    updater_interval_days: bpy.props.IntProperty(
        name="Days", description="Number of days between update checks",
        default=7, min=0, max=31,
        update=_on_changed,
    )
    updater_interval_hours: bpy.props.IntProperty(
        name="Hours", description="Number of hours between update checks",
        default=0, min=0, max=23,
        update=_on_changed,
    )
    updater_interval_minutes: bpy.props.IntProperty(
        name="Minutes", description="Number of minutes between update checks",
        default=0, min=0, max=59,
        update=_on_changed,
    )


# ==============================================================================
# Confirm popover — docked Panel popup, not a floating operator popup
# ==============================================================================

class SUPERSKIN_PT_addon_update_confirm(bpy.types.Panel):
    """Two-choice confirm popover, stage 1 of 2, for installing the update ("Update and
    Restart" / "Later"), opened from the update button via `wm.call_panel`."""
    bl_idname = "SUPERSKIN_PT_addon_update_confirm"
    bl_label = "Update Available"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_ui_units_x = 15

    def draw(self, context):
        layout = self.layout
        if updater.invalid_updater:
            layout.label(text="Updater module error")
            return
        if not updater.update_ready:
            layout.label(text="No updates available")
            return

        col = layout.column(align=True)
        col.scale_y = 0.85
        col.label(text="Update {} is ready".format(updater.update_version), icon="INFO")
        col.separator()
        col.label(text="Blender will restart immediately after")
        col.label(text="updating, and this requires an internet")
        col.label(text="connection. Continue?")
        col.separator(factor=1.2)

        row = layout.row(align=True)
        row.scale_y = 1.4

        later_zone = row.row(align=True)
        later_zone.operator_context = 'EXEC_DEFAULT'
        later = later_zone.operator(ops.AddonUpdaterInstallPopup.bl_idname, text="Later")
        later.confirmed = False

        go_row = row.row(align=True)
        go_row.alert = True
        go_row.operator_context = 'INVOKE_DEFAULT'
        go_row.operator(ops.AddonUpdaterQuitConfirm.bl_idname, text="Update and Restart")


# ==============================================================================
# AddonUpdaterFeature — UnifiedFeatureExtension
# ==============================================================================

class AddonUpdaterFeature(UnifiedFeatureExtension):
    """Unified extension for the addon-update-checker domain."""

    # ── Configuration (class attributes) ──────────────────────────────────

    domain_id = "addon_updater"
    actions = []  # Standalone operators only, see module docstring.
    section_title = "Updates"
    draw_tab = "PREFERENCE"
    json_path = ("addon_updater",)
    defaults_path = _DEFAULTS_PATH
    expanded_by_default = True  # Update status should be visible without an extra click.

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        # No actions registered -- see module docstring's
        # "Why no dispatch actions" section.
        return {"status": "CANCELLED"}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_update_button(self, layout, context) -> None:
        """Compact update control."""
        if updater.invalid_updater or not updater.update_ready:
            return
        layout.separator(factor=1.5)
        box = layout.box()
        col = box.column()
        split = col.split(factor=0.67)
        split.label(text="Version {} is available.".format(updater.update_version))
        btn_zone = split.row()
        btn_zone.alignment = 'RIGHT'
        btn_zone.scale_y = 1.1
        icon_id = get_update_icon_id()
        icon_kwargs = {"icon_value": icon_id} if icon_id else {"icon": 'IMPORT'}
        call_panel = btn_zone.operator(
            "wm.call_panel", text="Update", **icon_kwargs,
        )
        call_panel.name = SUPERSKIN_PT_addon_update_confirm.bl_idname
        call_panel.keep_open = False

    def draw_section(self, layout, context) -> None:
        """Full-detail update-notice box + condensed check-now row. **Not called from anywhere
        anymore**."""
        if updater.invalid_updater:
            box = layout.box()
            box.label(text="Updater module error", icon='ERROR')
            box.label(text=str(updater.error_msg))
        else:
            saved_state = updater.json
            if not updater.auto_reload_post_update and saved_state.get("just_updated"):
                box = layout.box()
                col = box.column()
                alert_row = col.row()
                alert_row.alert = True
                alert_row.operator("wm.quit_blender", text="Restart blender", icon="ERROR")
                col.label(text="to complete update")
            elif not (saved_state.get("ignore")) and updater.update_ready:
                box = layout.box()
                col = box.column(align=True)
                col.alert = True
                col.label(text="Update ready!", icon="ERROR")
                col.alert = False
                col.separator()
                row = col.row(align=True)
                split = row.split(align=True)
                col_l = split.column(align=True)
                col_l.scale_y = 1.5
                col_l.operator(ops.AddonUpdaterIgnore.bl_idname, icon="X", text="Ignore")
                col_r = split.column(align=True)
                col_r.scale_y = 1.5
                if not updater.manual_only:
                    col_r.operator(ops.AddonUpdaterUpdateNow.bl_idname,
                                   text="Update", icon="LOOP_FORWARDS")
                    col.operator("wm.url_open", text="Open website").url = updater.website
                    col.operator(ops.AddonUpdaterInstallManually.bl_idname,
                                 text="Install manually")
                else:
                    col.operator("wm.url_open", text="Get it now").url = updater.website

        row = layout.row()
        if updater.invalid_updater:
            row.label(text="Error initializing updater code:")
            row.label(text=str(updater.error_msg))
            return

        if not updater.auto_reload_post_update and updater.json.get("just_updated"):
            row.alert = True
            row.operator("wm.quit_blender", text="Restart blender to complete update", icon="ERROR")
            return

        col = row.column()
        if updater.error is not None:
            sub_col = col.row(align=True)
            sub_col.scale_y = 1
            split = sub_col.split(align=True)
            split.scale_y = 2
            if "ssl" in updater.error_msg.lower():
                split.enabled = True
                split.operator(ops.AddonUpdaterInstallManually.bl_idname, text=updater.error)
            else:
                split.enabled = False
                split.operator(ops.AddonUpdaterCheckNow.bl_idname, text=updater.error)
            split = sub_col.split(align=True)
            split.scale_y = 2
            split.operator(ops.AddonUpdaterCheckNow.bl_idname, text="", icon="FILE_REFRESH")
        elif updater.update_ready is None and not updater.async_checking:
            col.scale_y = 2
            col.operator(ops.AddonUpdaterCheckNow.bl_idname)
        elif updater.update_ready is None:  # Async is running.
            sub_col = col.row(align=True)
            sub_col.scale_y = 1
            split = sub_col.split(align=True)
            split.enabled = False
            split.scale_y = 2
            split.operator(ops.AddonUpdaterCheckNow.bl_idname, text="Checking...")
            split = sub_col.split(align=True)
            split.scale_y = 2
            split.operator(ops.AddonUpdaterEndBackground.bl_idname, text="", icon="X")
        elif (updater.include_branches
              and len(updater.tags) == len(updater.include_branch_list)
              and not updater.manual_only):
            sub_col = col.row(align=True)
            sub_col.scale_y = 1
            split = sub_col.split(align=True)
            split.scale_y = 2
            now_txt = "Update directly to " + str(updater.include_branch_list[0])
            split.operator(ops.AddonUpdaterUpdateNow.bl_idname, text=now_txt)
            split = sub_col.split(align=True)
            split.scale_y = 2
            split.operator(ops.AddonUpdaterCheckNow.bl_idname, text="", icon="FILE_REFRESH")
        elif updater.update_ready and not updater.manual_only:
            sub_col = col.row(align=True)
            sub_col.scale_y = 1
            split = sub_col.split(align=True)
            split.scale_y = 2
            split.operator(ops.AddonUpdaterUpdateNow.bl_idname,
                           text="Update now to " + str(updater.update_version))
            split = sub_col.split(align=True)
            split.scale_y = 2
            split.operator(ops.AddonUpdaterCheckNow.bl_idname, text="", icon="FILE_REFRESH")
        elif updater.update_ready and updater.manual_only:
            col.scale_y = 2
            dl_txt = "Download " + str(updater.update_version)
            col.operator("wm.url_open", text=dl_txt).url = updater.website
        else:  # i.e. updater.update_ready == False.
            sub_col = col.row(align=True)
            sub_col.scale_y = 1
            split = sub_col.split(align=True)
            split.enabled = False
            split.scale_y = 2
            split.operator(ops.AddonUpdaterCheckNow.bl_idname, text="Addon is up to date")
            split = sub_col.split(align=True)
            split.scale_y = 2
            split.operator(ops.AddonUpdaterCheckNow.bl_idname, text="", icon="FILE_REFRESH")

        row = layout.row()
        row.scale_y = 0.7
        last_check = updater.json["last_check"]
        if updater.error is not None and updater.error_msg is not None:
            row.label(text=updater.error_msg)
        elif last_check != "" and last_check is not None:
            last_check = last_check[0: last_check.index(".")]
            row.label(text="Last check: " + last_check)
        else:
            row.label(text="Last check: Never")

    # ── JSON persistence ──────────────────────────────────────────────────

    def populate(self, data: dict) -> None:
        """Write section data dict into the live WindowManager property."""
        CoreFacade.debug_log("feature_domains", "addon_updater.populate(): loading settings")
        au = bpy.context.window_manager.superskin_addon_updater_prefs
        if "auto_check_update" in data:
            au.auto_check_update = bool(data["auto_check_update"])
        if "updater_interval_months" in data:
            au.updater_interval_months = int(data["updater_interval_months"])
        if "updater_interval_days" in data:
            au.updater_interval_days = int(data["updater_interval_days"])
        if "updater_interval_hours" in data:
            au.updater_interval_hours = int(data["updater_interval_hours"])
        if "updater_interval_minutes" in data:
            au.updater_interval_minutes = int(data["updater_interval_minutes"])

    def serialize_into(self, full_dict: dict) -> None:
        """Write current values into full_dict at the correct JSON path."""
        au = bpy.context.window_manager.superskin_addon_updater_prefs
        full_dict["addon_updater"] = {
            "auto_check_update": au.auto_check_update,
            "updater_interval_months": au.updater_interval_months,
            "updater_interval_days": au.updater_interval_days,
            "updater_interval_hours": au.updater_interval_hours,
            "updater_interval_minutes": au.updater_interval_minutes,
        }
        CoreFacade.debug_log("feature_domains", "addon_updater.serialize_into(): saved settings")


# ==============================================================================
# Registration (called from __init__.py)
# ==============================================================================

def register():
    """Register PropertyGroup, configure the engine, and register the extension."""
    if hasattr(bpy.types, SSPrefAddonUpdater.__name__):
        bpy.utils.unregister_class(SSPrefAddonUpdater)
    bpy.utils.register_class(SSPrefAddonUpdater)
    bpy.types.WindowManager.superskin_addon_updater_prefs = bpy.props.PointerProperty(
        type=SSPrefAddonUpdater, options={'SKIP_SAVE'},
    )

    configure(skip_tag=ops.skip_tag_function, select_link=ops.select_link_function)
    ops.register()
    bpy.utils.register_class(SUPERSKIN_PT_addon_update_confirm)

    if not bpy.app.timers.is_registered(ops.check_for_update_background):
        bpy.app.timers.register(ops.check_for_update_background, first_interval=1.0)

    UnifiedRegistry.register(AddonUpdaterFeature())


def unregister():
    """Unregister the extension, engine operators, and PropertyGroup."""
    UnifiedRegistry.unregister("addon_updater")
    if bpy.app.timers.is_registered(ops.check_for_update_background):
        bpy.app.timers.unregister(ops.check_for_update_background)
    bpy.utils.unregister_class(SUPERSKIN_PT_addon_update_confirm)
    ops.unregister()
    try:
        del bpy.types.WindowManager.superskin_addon_updater_prefs
    except Exception:
        pass
    bpy.utils.unregister_class(SSPrefAddonUpdater)
