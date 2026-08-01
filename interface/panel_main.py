"""SuperSkinPro sidebar panel — single-panel interface.

Always visible in the viewport N-panel (Super Skin Pro tab), regardless of
activation state or interaction mode. A top row (version label + compact
update control + wider Docs link + settings popover trigger, see
``_draw_top_row()``) is drawn first, then the main artwork body, gated by
``WindowManager.superskin_active_interface``. While not yet activated, the
license-entry prompt (``ActivateFeature.draw_activate_prompt()`` in
``features/activate/``) is drawn in place of the artwork instead -- its own
box, including the "Activate to continue" label itself, NOT inside the top
row's box:

  LAYER    -> LayerViewer (+ Armature/Mesh selectors) + "Edit Layer
              Weight" gate button. Drawn in **every** mode, including
              Pose Mode -- the Armature/Mesh selectors exist precisely
              to keep this panel usable while the active object is an
              Armature, so gating this tab on ``context.mode`` would
              defeat their purpose. Each LAYER-tab extension already
              guards its own content against a missing/non-mesh active
              object.
  SKINNING -> DeformBoneViewer + tool sections + "Save Weights" gate
              button. Only drawn when ``context.mode`` is one of
              ``OBJECT``/``EDIT_MESH`` -- these extensions have not been
              audited for safety with a non-mesh active object.

This state is deliberately decoupled from ``context.mode`` — pressing Tab
does not by itself change which interface is shown; only the explicit
"Edit Layer Weight" / "Save Weights" operators (and the auto-save guard's
unguarded-exit detection) flip it. See
``features/controller/ops_scene_modes.py``.

There is no separate "Preference" tab anymore -- the addon-update checker
(``addon_updater``) moved into the always-visible top row (see
``_draw_top_row()``), license activation (``activate``) moved into the
artwork body's own activation prompt (see ``_draw_skin_tab()``), and the
remaining System/Customize settings (feature-domain PREFERENCE-tab
extensions + System Actions) moved into a settings popover
(``SUPERSKIN_PT_settings_popup``) opened from the top row's gear icon,
replacing the old two-segment tab body driven by
``WindowManager.superskin_top_tab``.
"""

import os
import bpy

from .. import ADDON_NAME
from . import widget_preferences
from .registry.register_api import UnifiedRegistry

_DOCS_URL = "https://docs.superskinpro.com/"


def _read_addon_version() -> str:
    """Parse this addon's version out of ``blender_manifest.toml``.

    Self-contained rather than reusing ``features/addon_updater/engine.py``'s
    ``read_current_version()`` -- ``interface/`` never imports from
    ``features/*`` directly (extensions are only reached through
    ``UnifiedRegistry``, per this package's README), so this small bit of
    duplication is the cost of keeping that boundary intact for a one-line
    TOML read. Computed once at import time since the manifest can't change
    mid-session."""
    manifest_path = os.path.join(os.path.dirname(__file__), "..", "blender_manifest.toml")
    try:
        import tomllib
        with open(manifest_path, "rb") as fh:
            manifest = tomllib.load(fh)
        return str(manifest.get("version", "?"))
    except Exception:
        return "?"


_ADDON_VERSION = _read_addon_version()


class SUPERSKIN_PT_settings_popup(bpy.types.Panel):
    """Settings popover opened from the top row's gear icon.

    Replaces the old ``superskin.toggle_top_tab`` toggle button + full
    second "Preference" tab body -- a popover needs no backing operator or
    ``WindowManager`` state, unlike the toggle it replaces. Draws every
    PREFERENCE-tab feature extension plus the System Actions box
    (``widget_preferences.draw_preferences_body()``). No "Updates" section
    here -- the addon-update checker's full detail was removed from this
    popover entirely; the compact top-row control
    (``AddonUpdaterFeature.draw_top_row_button()``, see ``_draw_top_row()``)
    is the only update-checking entry point now. License activation is
    likewise NOT included here -- it lives in its own top-row line instead
    (see ``features/activate/README.md``), reachable without opening this
    popover at all. Every section this popover does draw renders as a
    non-collapsible plain-label header with its body always shown (see
    ``widget_preferences._draw_preferences()``'s ``force_locked_expanded``
    argument) -- nothing in here can be collapsed away.

    ``HEADER``, not ``UI`` -- ``UI`` is the N-panel sidebar region itself,
    so a Panel registered against it (with no ``bl_category`` restricting
    which tab it docks under) would get auto-listed as its own separate
    docked panel in addition to being invocable via ``layout.popover()``.
    ``HEADER``-region panels are only ever drawn when explicitly invoked
    (popover/menu), never auto-docked -- the same convention this project's
    other popovers already use (``features/mirror``, ``features/debug_console``,
    ``features/weight_transfer``).
    """
    bl_idname = "SUPERSKIN_PT_settings_popup"
    bl_label = "Preferences"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'

    def draw(self, context):
        widget_preferences.draw_preferences_body(self.layout, context)


class VIEW3D_PT_mw_master_modular_panel(bpy.types.Panel):
    bl_idname = "VIEW3D_PT_superskin_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    # bl_category (the N-panel tab name) follows the manifest's "name" field
    # (ADDON_NAME) so the dev repo ("Super Skin Pro Dev") and release repo
    # ("Super Skin Pro") show up as visually distinct tabs -- see __init__.py.
    # bl_label is intentionally left as a static literal per CLAUDE.md's
    # "Never Edit bl_label" rule.
    bl_category = ADDON_NAME
    bl_label = "Super Skin Pro"
    bl_order = 1000000

    def draw(self, context):
        from ..core.facade import CoreFacade

        layout = self.layout
        activated = CoreFacade.is_system_activated()

        self._draw_top_row(layout, context, activated)
        layout.separator(factor=0.4)
        self._draw_skin_tab(layout, context, activated)

    def _draw_top_row(self, layout, context, activated):
        """Version label + compact update control + wider Docs link +
        settings popover trigger, all in one row, inside a box for visual
        separation from the body below. No longer ``align=True`` -- a
        regular (unaligned) row so Blender's normal inter-widget gaps show
        up between the three buttons (update / Docs / settings) instead of
        them reading as one fused strip:
          1. plain label reading the addon version (``_ADDON_VERSION``)
          2. the addon-update checker's compact top-row control
             (``AddonUpdaterFeature.draw_top_row_button()``) -- draws
             nothing at all unless an update is actually confirmed ready,
             in which case it's a text-only "Update" button (no icon --
             tried, took up too much space) that opens the existing
             install-choice dialog. There is no manual "check now" button
             anywhere anymore; checking happens automatically via a
             background timer, see ``AddonUpdaterFeature.register()``.
          3. a wider "Docs" button (text + ``HELP`` icon, scaled up) opening
             ``_DOCS_URL`` via the native ``wm.url_open`` operator
          4. an icon-only settings popover trigger (``PREFERENCES`` gear
             icon, ``LOCKED`` instead while not activated) opening
             ``SUPERSKIN_PT_settings_popup`` -- disabled (``enabled=False``)
             until the system is activated, since the popover's own content
             (feature-domain settings, System Actions) isn't meaningful to
             touch before then.

        License activation is NOT drawn here anymore -- see
        ``_draw_skin_tab()``'s activation prompt instead.
        """
        box = layout.box()
        top_row = box.row()
        top_row.label(text=f"v{_ADDON_VERSION}")

        updater_ext = UnifiedRegistry.get_by_id("addon_updater")
        if updater_ext is not None:
            updater_ext.draw_top_row_button(top_row, context)

        docs_sub = top_row.row(align=True)
        docs_sub.scale_x = 1.3
        docs_sub.operator("wm.url_open", text="docs.superskinpro").url = _DOCS_URL

        settings_sub = top_row.row(align=True)
        settings_sub.enabled = activated
        settings_sub.popover(
            SUPERSKIN_PT_settings_popup.bl_idname,
            text="",
            icon='PREFERENCES' if activated else 'LOCKED',
        )

    def _draw_skin_tab(self, layout, context, activated):
        if not activated:
            # The "Activate to continue" label lives inside
            # ActivateFeature.draw_activate_prompt()'s own box now, not
            # drawn separately here, so it reads as part of the same
            # visually distinct block as the license field below it.
            activate_ext = UnifiedRegistry.get_by_id("activate")
            if activate_ext is not None:
                activate_ext.draw_activate_prompt(layout, context)
            return

        active_interface = context.window_manager.superskin_active_interface
        if active_interface == 'LAYER':
            # The LAYER tab must stay visible and usable in every mode
            # (Pose Mode especially) -- its Armature/Mesh selectors exist
            # precisely to recover from losing mesh selection while
            # working on the rig, and the panel disappearing on entering
            # Pose Mode would defeat that. Each LAYER-tab extension
            # (layer_viewer, weight_transfer) already guards its own
            # content against a missing/non-mesh active object.
            widget_preferences.draw_mode_split_ui(layout, context)
        elif context.mode in ('OBJECT', 'EDIT_MESH'):
            obj = context.active_object
            if not (obj and obj.type == "MESH"):
                layout.label(text="No mesh active", icon="ERROR")
            else:
                widget_preferences.draw_mode_split_ui(layout, context)


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
    bpy.utils.register_class(SUPERSKIN_PT_settings_popup)
    bpy.utils.register_class(VIEW3D_PT_mw_master_modular_panel)


def unregister():
    bpy.utils.unregister_class(VIEW3D_PT_mw_master_modular_panel)
    bpy.utils.unregister_class(SUPERSKIN_PT_settings_popup)
    del bpy.types.WindowManager.superskin_active_interface
