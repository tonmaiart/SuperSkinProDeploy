"""SuperSkinPro sidebar panel — single-panel interface.

Always visible in the viewport N-panel (Super Skin Pro tab), regardless of
activation state or interaction mode. A top row (a plain "Super Skin Pro
v{version}" label, standing in for the native ``bl_label`` this Panel
deliberately leaves blank -- see ``VIEW3D_PT_mw_master_modular_panel``'s
class-attribute comment -- + settings popover trigger, see
``_draw_top_row()``) is drawn into the panel's own native header strip via
``draw_header()`` (see that method's docstring for this row's placement
history), separate from ``draw()``'s body, which draws the LAYER/SKINNING
artwork, gated by ``WindowManager.superskin_active_interface``. While not
yet activated, the license-entry prompt (``ActivateFeature.
draw_activate_prompt()`` in ``features/activate/``) is drawn in place of
the artwork instead -- its own box, including the "Activate to continue"
label itself, NOT inside the top row's box:

  LAYER    -> LayerViewer (+ Armature/Mesh selectors) + "Edit Layer
              Weight" gate button + the addon-update checker's compact
              control at the very bottom (see ``_draw_update_row()``,
              LAYER-only, never drawn on SKINNING). Drawn in **every**
              mode, including Pose Mode -- the Armature/Mesh selectors
              exist precisely to keep this panel usable while the active
              object is an Armature, so gating this tab on
              ``context.mode`` would defeat their purpose. Each LAYER-tab
              extension already guards its own content against a
              missing/non-mesh active object.
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
(``addon_updater``) moved into the bottom of the LAYER tab's own body (see
``_draw_update_row()``), license activation (``activate``) moved into the
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
    (``widget_preferences.draw_preferences_body()``) -- the System Actions
    box now also carries a "Docs" button (``wm.url_open`` to
    ``_DOCS_URL``'s duplicate in ``widget_preferences.py``), moved here from
    the top row's own button, which no longer exists (see
    ``_draw_top_row()``). No "Updates" section
    here -- the addon-update checker's full detail was removed from this
    popover entirely; the compact control at the bottom of the LAYER tab
    (``AddonUpdaterFeature.draw_update_button()``, see ``_draw_update_row()``)
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
    bl_label = ""
    bl_order = 1000000

    def draw_header(self, context):
        """Draws the top row (``_draw_top_row()``) into the panel's native
        header strip (shared between the panel's expanded and collapsed
        states, per Blender's ``Panel.draw_header()`` contract), so it
        stays reachable even while the "Super Skin Pro" panel itself is
        collapsed via its own title-bar arrow.

        This row previously broke the whole panel's layout (widened the
        N-panel sidebar out past where it should sit) -- but the actual
        cause was ``separator_spacer()`` specifically, not this placement:
        the same row reproduced the identical bug after moving into
        ``draw()``'s body too, with ``separator_spacer()`` still in it.
        ``_draw_top_row()`` now uses ``layout.split(factor=0.85)`` instead
        (see that method's docstring) -- a fixed-ratio split, not a
        growing/undefined-width spacer -- which is safe in any layout
        context, so this row moved back here once that was confirmed.
        """
        from ..core.facade import CoreFacade

        activated = CoreFacade.is_system_activated()
        self._draw_top_row(self.layout, context, activated)

    def draw(self, context):
        from ..core.facade import CoreFacade

        layout = self.layout
        activated = CoreFacade.is_system_activated()

        self._draw_skin_tab(layout, context, activated)

    def _draw_top_row(self, layout, context, activated):
        """"Super Skin Pro v{version}" label + settings popover trigger, in
        one row, drawn into the panel's native header strip by
        ``draw_header()`` (see that method's docstring for the placement
        history). No longer ``align=True`` -- a regular (unaligned) row so
        Blender's normal inter-widget gaps show up between the pieces
        instead of them reading as one fused strip:
          1. a plain label reading ``"Super Skin Pro v{_ADDON_VERSION}"`` --
             stands in for the native panel title, since ``bl_label`` is
             deliberately left blank (``bl_label = ""``, see the class
             attribute comment) rather than edited to carry this text
             directly, per CLAUDE.md's "Never Edit bl_label" rule. The
             literal "Super Skin Pro" text is hardcoded (not ``ADDON_NAME``,
             which reads "Super Skin Pro Dev" for the dev-repo build) to
             read the same regardless of which repo built this copy.
          2. an icon-only settings popover trigger (``PREFERENCES`` gear
             icon, ``LOCKED`` instead while not activated), pushed to the
             row's right edge via ``layout.split(factor=0.85)`` -- the same
             fixed-factor-split idiom
             ``features/tool_socket/tool_socket_feature.py`` already uses
             for its own right-hugging info button. ``separator_spacer()``
             was tried first and reverted: it made Blender miscompute the
             sidebar's required width, widening the whole N-panel out past
             where it should sit (reported as "the whole panel
             shifted/popped out of the screen edge") -- reproduced
             identically whether this row lived in ``draw_header()`` or
             ``draw()``'s body, so the spacer itself was the actual cause,
             not the placement. ``split()`` is a fixed-ratio division with
             no growing/undefined-width behavior, so it's safe in either
             context. Opens ``SUPERSKIN_PT_settings_popup`` via
             a plain ``wm.call_panel`` operator button (``name``/
             ``keep_open`` props set to the panel's ``bl_idname``/``True``)
             rather than ``layout.popover()`` -- ``popover()`` always draws a
             small dropdown-menu arrow next to its icon, which a plain
             operator icon button doesn't get; ``wm.call_panel`` is the
             identical operator ``popover()`` invokes internally, so the
             popup itself is unchanged. Disabled (``enabled=False``) until
             the system is activated, since the popover's own content
             (feature-domain settings, System Actions) isn't meaningful to
             touch before then.

        The Docs button that used to sit in this row (labeled with this
        same "Super Skin Pro v{version}" text) moved into the settings
        popover's System Actions box instead -- see
        ``widget_preferences.py``'s ``_draw_preferences()``, which now owns
        the only ``_DOCS_URL`` constant (this module no longer needs one).

        License activation is NOT drawn here anymore -- see
        ``_draw_skin_tab()``'s activation prompt instead. The addon-update
        checker's compact control is NOT drawn here anymore either -- it
        moved to the bottom of the LAYER tab's own body content, see
        ``_draw_skin_tab()``'s LAYER branch.
        """
        top_row = layout.row()

        # Fixed-factor split instead of separator_spacer() -- see this
        # method's docstring (item 2) for why. The left zone (label) gets
        # the lion's share; the right zone is just wide enough for one icon
        # button, alignment='RIGHT' packing it against that zone's own
        # right edge.
        split = top_row.split(factor=0.85)

        left = split.row()
        left.label(text=f"Super Skin Pro v{_ADDON_VERSION}")

        settings_sub = split.row(align=True)
        settings_sub.alignment = 'RIGHT'
        settings_sub.enabled = activated
        # Plain operator button (wm.call_panel, the exact operator
        # layout.popover() invokes internally) instead of layout.popover()
        # itself -- popover() always draws a small dropdown-menu arrow next
        # to its icon (Blender's native "this opens a submenu" decoration
        # for that specific button type), which a plain operator icon
        # button doesn't get. Opens the identical SUPERSKIN_PT_settings_popup
        # popover either way.
        call_panel = settings_sub.operator(
            "wm.call_panel", text="",
            icon='PREFERENCES' if activated else 'LOCKED',
        )
        call_panel.name = SUPERSKIN_PT_settings_popup.bl_idname
        call_panel.keep_open = True

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
            self._draw_update_row(layout, context)
        elif context.mode in ('OBJECT', 'EDIT_MESH'):
            obj = context.active_object
            if not (obj and obj.type == "MESH"):
                layout.label(text="No mesh active", icon="ERROR")
            else:
                widget_preferences.draw_mode_split_ui(layout, context)

    def _draw_update_row(self, layout, context):
        """The addon-update checker's compact control, at the very bottom of
        the LAYER tab's body only -- not drawn on SKINNING, and not in the
        top-row header (see ``_draw_top_row()``'s docstring for why it moved
        out of there). Draws nothing at all unless an update is actually
        confirmed ready. See ``AddonUpdaterFeature.draw_update_button()``
        for the button's own behavior (opens ``AddonUpdaterInstallPopup``, a
        two-choice "Update and Restart" / "Later" confirm) -- this call site
        only decides placement, not behavior.
        """
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
    bpy.utils.register_class(SUPERSKIN_PT_settings_popup)
    bpy.utils.register_class(VIEW3D_PT_mw_master_modular_panel)


def unregister():
    bpy.utils.unregister_class(VIEW3D_PT_mw_master_modular_panel)
    bpy.utils.unregister_class(SUPERSKIN_PT_settings_popup)
    del bpy.types.WindowManager.superskin_active_interface
