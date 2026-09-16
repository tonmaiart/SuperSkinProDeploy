"""SuperSkinPro sidebar panel — single-panel interface.

Always visible in the viewport N-panel (Super Skin Pro tab), regardless of
activation state or interaction mode. A top row (just a plain "Super Skin
Pro v{version}" label, standing in for the native ``bl_label`` this Panel
deliberately leaves blank -- see ``VIEW3D_PT_mw_master_modular_panel``'s
class-attribute comment -- see ``_draw_top_row()``) is drawn into the
panel's own native header strip via ``draw_header()`` (see that method's
docstring for this row's placement history), separate from ``draw()``'s
body, which draws the LAYER/SKINNING artwork, gated by
``WindowManager.superskin_active_interface``. While not yet activated, the
license-entry prompt (``ActivateFeature.draw_activate_prompt()`` in
``features/activate/``) is drawn in place of the artwork instead -- its
own box, including the "Activate to continue" label itself, NOT inside the
top row's box:

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
extensions + System Actions) draw via the "Settings" button -- a
``wm.call_panel`` popover trigger opening ``SUPERSKIN_PT_settings_popup``
(``interface/ops_preferences.py``), reverted back to a real popup
(2026-09-14, per explicit user request) after a period of drawing that
same content inline via a ``WindowManager.superskin_settings_expanded``
(``SKIP_SAVE`` bool) toggle instead -- see
``docs/core-interfaces/interface.md`` for that intervening shape's own
history. A sibling second button, "How to use", is still the inline
toggle pattern (``WindowManager.superskin_how_to_use_expanded``) expanding
four items (Quick Start Guide, Quick Start Tutorials, Read Documentation,
Join Discord Server) -- UNCHANGED by the Settings popup reversion, aside
from losing the now-moot mutual-exclusivity ``update=`` callback it used
to share with "Settings" (nothing left for it to stay exclusive with once
"Settings" no longer has an expanded/collapsed state of its own).
**Neither button is drawn by this module anymore** (2026-09-14, per
explicit user request) -- ``superskin_how_to_use_expanded`` is still
registered here (see ``register()``), but the actual toggle row (and
"Settings"' popover trigger) moved to
``interface.widget_preferences.draw_settings_toggle_row()``, reached from
``features/deform_layer_viewer/deform_layer_viewer_feature.py``'s own
top row (right after its "bind mesh" dropdown) via the sanctioned
``UnifiedRegistry.draw_settings_toggle_row()`` wrapper
(``interface/registry/register_api.py``) -- see that function's docstring
for the full history. A consequence of this move: the row (and hence
"How to use") no longer renders on every tab/state the way it used to --
it now only appears wherever ``deform_layer_viewer``'s own top row does
(after activation, LAYER/SKINNING tab, mesh selected), same as the "bind
mesh" dropdown it now sits next to.
This replaces the old two-segment tab body driven by
``WindowManager.superskin_top_tab`` and the docked
``SUPERSKIN_PT_preferences`` panel -- see ``docs/core-interfaces/interface.md``
for the full history, including the Settings half's own back-and-forth
between this docked panel, a popover, an inline toggle, and now a popover
again.
"""

import bpy

from .. import ADDON_NAME, ADDON_VERSION
from .registry.register_api import UnifiedRegistry


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
        Removing the spacer (the row is now just a plain label, see
        ``_draw_top_row()``) resolved it regardless of placement, so this
        row stayed here once that was confirmed.
        """
        self._draw_top_row(self.layout, context)

    def draw(self, context):
        from ..core.facade import CoreFacade

        layout = self.layout
        activated = CoreFacade.is_system_activated()

        self._draw_skin_tab(layout, context, activated)

    def _draw_top_row(self, layout, context):
        """"Super Skin Pro v{version}" label, drawn into the panel's native
        header strip by ``draw_header()`` (see that method's docstring for
        the placement history). Just a plain label now -- the icon-only
        settings popover trigger that used to share this row (``PREFERENCES``
        gear icon, opening a popup) moved to a "Settings" toggle button
        instead, which expands its content inline rather than opening
        anything -- see ``interface.widget_preferences.draw_settings_toggle_row()``
        for where that button lives now.

        License activation is NOT drawn here -- see ``_draw_skin_tab()``'s
        activation prompt instead. The addon-update checker's compact
        control is NOT drawn here either -- it moved to the bottom of the
        LAYER tab's own body content, see ``_draw_skin_tab()``'s LAYER
        branch.
        """
        layout.row().label(text=f"Super Skin Pro v{ADDON_VERSION}")

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
            #
            # The actual section-drawing loop is owned by the
            # `object_tools_ui` feature domain now (see
            # docs/domains/object_tools_ui.md), not drawn inline here --
            # this call site only decides WHEN to draw it (activated,
            # active_interface == 'LAYER').
            object_tools_ext = UnifiedRegistry.get_by_id("object_tools_ui")
            if object_tools_ext is not None:
                object_tools_ext.draw_section(layout, context)
            self._draw_update_row(layout, context)
        elif context.mode in ('OBJECT', 'EDIT_MESH'):
            obj = context.active_object
            if not (obj and obj.type == "MESH"):
                layout.label(text="No mesh active", icon="ERROR")
            else:
                # Same as above, but for the SKINNING interface -- owned by
                # `skin_tools_ui` (see docs/domains/skin_tools_ui.md).
                skin_tools_ext = UnifiedRegistry.get_by_id("skin_tools_ui")
                if skin_tools_ext is not None:
                    skin_tools_ext.draw_section(layout, context)

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
    bpy.types.WindowManager.superskin_how_to_use_expanded = bpy.props.BoolProperty(
        name="How To Use Expanded",
        description="Show the SuperSkinPro tutorials/docs/Discord links "
                    "inline, wherever draw_settings_toggle_row() is drawn",
        default=False,
        options={'SKIP_SAVE'},
    )
    bpy.utils.register_class(VIEW3D_PT_mw_master_modular_panel)


def unregister():
    bpy.utils.unregister_class(VIEW3D_PT_mw_master_modular_panel)
    del bpy.types.WindowManager.superskin_how_to_use_expanded
    del bpy.types.WindowManager.superskin_active_interface
