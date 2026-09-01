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


class SUPERSKIN_OT_reset_all_shortcuts(bpy.types.Operator):
    """Restore every SuperSkinPro keyboard shortcut to its addon default —
    the bulk counterpart to the per-item restore icon drawn by
    ``interface/utils/keymap_editor.py``. Gathers every registered
    domain's items via ``UnifiedFeatureExtension.get_keymap_items()``,
    resolves each to its live ``wm.keyconfigs.user`` counterpart via that
    same module's ``_resolve_user_kmi()`` (idname + creation-rank, NOT
    ``.id`` equality -- see that module's docstring for why addon-side
    ``.id`` is not stable across a Blender restart), and calls
    ``KeyMap.restore_item_to_default()`` directly (the same underlying
    call ``preferences.keyitem_restore`` wraps) rather than invoking that
    operator once per item.

    Restores every resolved item unconditionally rather than filtering on
    ``kmi.is_user_modified`` first -- verified empirically (headless
    Blender 5.1) that a scripted property change doesn't reliably flip
    that flag outside a real interactive UI session (no window/screen
    context in ``--background`` mode), so gating this bulk path on it
    risked silently skipping genuinely-modified items. Restoring an
    already-default item is a harmless no-op either way.
    """
    bl_idname = "superskin.reset_all_shortcuts"
    bl_label = "Reset All Shortcuts"
    bl_options = {'REGISTER'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(
            self, event,
            title="Reset all shortcuts?",
            message=(
                "This restores every SuperSkinPro keyboard shortcut to its "
                "default binding, undoing any rebinding you've done. Are "
                "you sure?"
            ),
            confirm_text="Reset Shortcuts",
        )

    def execute(self, context):
        from .registry.register_api import UnifiedRegistry
        from .utils.keymap_editor import _resolve_user_kmi

        user_kc = context.window_manager.keyconfigs.user
        count = 0
        ranks = {}
        for ext in UnifiedRegistry.get_all():
            for km, kmi, _label in ext.get_keymap_items():
                key = (km.name, kmi.idname)
                rank = ranks.get(key, 0)
                ranks[key] = rank + 1

                user_km = user_kc.keymaps.get(km.name)
                user_kmi = _resolve_user_kmi(context, km, kmi, rank)
                if user_km is None or user_kmi is None:
                    continue
                user_km.restore_item_to_default(user_kmi)
                count += 1

        self.report({'INFO'}, f"Reset {count} shortcut(s) to default")
        return {'FINISHED'}


_REBIND_IGNORED_TYPES = {
    'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE', 'TIMER', 'TIMER_JOBS',
    'TIMER_AUTOSAVE', 'TIMER_REPORT', 'TIMERREGION', 'WINDOW_DEACTIVATE',
    'NDOF_MOTION', 'NONE',
}
_REBIND_MODIFIER_ONLY_TYPES = {
    'LEFT_SHIFT', 'RIGHT_SHIFT', 'LEFT_CTRL', 'RIGHT_CTRL',
    'LEFT_ALT', 'RIGHT_ALT', 'OSKEY',
}


class SUPERSKIN_OT_rebind_shortcut(bpy.types.Operator):
    """Click-to-rebind a single SuperSkinPro shortcut: click this button,
    then press the new key or mouse button (with optional Alt/Ctrl/Shift)
    to bind it -- Escape cancels, Backspace/Delete clears the binding to
    unassigned.

    Replaces the native ``kmi.prop("type", full_event=True)`` widget
    ``draw_keymap_section()`` (``interface/utils/keymap_editor.py``) used
    to draw -- that widget's own built-in key-capture state can't be
    reused with different button text, so this operator reimplements the
    capture as a modal handler instead, purely so the button can display
    ``format_binding(sep=" + ")``'s "Alt + 1" style rather than Blender's
    native single-space "Alt 1" rendering.

    Identifies the target ``KeyMapItem`` by ``km_name`` + ``kmi_id`` -- the
    live *user*-keyconfig item's own ``.id``, captured fresh at draw time.
    This is the SAME identifier the per-item "restore to default" icon
    right next to this button already uses (``preferences.keyitem_restore``'s
    ``item_id``), not the addon-keyconfig rank-based resolution
    ``_resolve_user_kmi()`` uses elsewhere in that module -- that one
    exists specifically to survive an addon-id/user-id mismatch across a
    Blender restart (see that module's top docstring). Within a single
    running session the user-keyconfig item's own ``.id`` never changes,
    so the simpler direct lookup is safe here.
    """
    bl_idname = "superskin.rebind_shortcut"
    bl_label = "Rebind Shortcut"
    bl_options = {'INTERNAL'}

    km_name: bpy.props.StringProperty()
    kmi_id: bpy.props.IntProperty()

    _kmi = None

    def invoke(self, context, event):
        user_km = context.window_manager.keyconfigs.user.keymaps.get(self.km_name)
        self._kmi = user_km.keymap_items.from_id(self.kmi_id) if user_km else None
        if self._kmi is None:
            return {'CANCELLED'}
        context.workspace.status_text_set(
            "Press a new key or mouse button to rebind  "
            "(Esc: cancel, Backspace: clear)"
        )
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _finish(self, context, result):
        context.workspace.status_text_set(None)
        for area in context.window.screen.areas:
            area.tag_redraw()
        return result

    def modal(self, context, event):
        if event.type in _REBIND_IGNORED_TYPES or event.type in _REBIND_MODIFIER_ONLY_TYPES:
            return {'RUNNING_MODAL'}
        if event.value != 'PRESS' and not event.type.startswith('WHEEL'):
            return {'RUNNING_MODAL'}

        if event.type == 'ESC':
            return self._finish(context, {'CANCELLED'})

        if event.type in {'BACK_SPACE', 'DEL'}:
            self._kmi.type = 'NONE'
            self._kmi.shift = self._kmi.ctrl = self._kmi.alt = self._kmi.oskey = False
            return self._finish(context, {'FINISHED'})

        self._kmi.type = event.type
        self._kmi.shift = event.shift
        self._kmi.ctrl = event.ctrl
        self._kmi.alt = event.alt
        self._kmi.oskey = event.oskey
        return self._finish(context, {'FINISHED'})


class SUPERSKIN_PT_shortcuts_editor(bpy.types.Panel):
    """Popover content for the "Edit Shortcuts" button in the settings
    popover's System Actions row -- the full click-to-rebind list (used
    to be drawn inline there, see `widget_preferences.py`'s
    `_draw_preferences()`) plus "Reset All Shortcuts", both moved here so
    the always-visible System Actions row stays a single compact button
    instead of a permanently-expanded list. That button used to jump
    straight to Blender's native Preferences > Keymap tab
    (`screen.userpref_show`, section='KEYMAP') since SuperSkinPro's items
    live in Blender's own built-in `'Mesh'` keymap category and can't be
    deep-linked to a custom section there -- no longer needed now that
    every shortcut is editable in-panel via `keymap_editor.py`.

    Rows are grouped into the same category headers ("Vertex Selection",
    "Edit Weight", "Display", "Misc", ...) as the viewport shortcut-hint
    HUD (`interface/utils/shortcut_overlay.py`) -- both read
    `keymap_editor.DOMAIN_CATEGORIES`/`category_for()`, one shared
    grouping definition, not two that could drift apart."""
    bl_idname = "SUPERSKIN_PT_shortcuts_editor"
    bl_label = "Edit Shortcuts"
    bl_space_type = 'VIEW_3D'
    # HEADER, not UI -- see SUPERSKIN_PT_mirror_options's docstring
    # (features/mirror/mirror_feature.py) for why a popover-only Panel
    # must use this region type.
    bl_region_type = 'HEADER'
    bl_ui_units_x = 22

    def draw(self, context):
        layout = self.layout
        from .utils import keymap_editor

        grouped = keymap_editor.grouped_keymap_items_by_category()
        if not grouped:
            layout.label(text="No shortcuts registered.")
        else:
            for i, (category, items) in enumerate(grouped):
                if i > 0:
                    layout.separator(factor=0.5)
                layout.label(text=category)
                keymap_editor.draw_keymap_section(layout, context, items)

        layout.separator(factor=0.6)
        layout.operator(
            "superskin.reset_all_shortcuts", text="Reset All Shortcuts",
            icon='LOOP_BACK',
        )


_classes = [
    SUPERSKIN_OT_reset_prefs,
    SUPERSKIN_OT_activate_license,
    SUPERSKIN_OT_reset_license_activation,
    SUPERSKIN_OT_override_dev_defaults,
    SUPERSKIN_OT_reset_all_shortcuts,
    SUPERSKIN_OT_rebind_shortcut,
    SUPERSKIN_PT_shortcuts_editor,
]


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
