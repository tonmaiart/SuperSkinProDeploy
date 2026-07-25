"""PropertyGroup classes for SuperSkinPro core preferences.

Registered on ``bpy.types.WindowManager.superskin_prefs`` — not Scene,
because these values are per-machine, not saved with the .blend file.

Feature domains own their own PropertyGroups (see features/<domain>/prefs.py).
"""

import bpy

# PreferencesService is deliberately NOT hoisted. This file is reloaded
# *before* preferences_service.py in this package's own __init__.py reload
# loop (`for mod in (io, property_groups, preferences_service): reload(mod)`),
# so a module-level `from .preferences_service import PreferencesService`
# here would bind to the pre-reload class, permanently disconnected from the
# one `load()` sets `_loading` on — silently breaking the
# load()/save_to_user_file() reentrancy guard for every update= callback in
# this file, letting a mid-populate save write a torn snapshot over another
# extension's already-correct data. See docs/bug-history for the
# mirror-keywords-persistence report this was diagnosed from. Importing
# inside each callback instead resolves the name at call time, after all
# reload cascades have finished.
#
# Intentional one-way exception to sibling-subsystem isolation: `preferences`
# nests SSPrefDebug into SSPrefRoot.debug so the "Developer / Debug Tools"
# panel has a single PropertyGroup root to draw from, the same way license
# settings are nested here rather than living as their own top-level
# WindowManager properties.
from ..debug_logging.property_groups import SSPrefDebug


class SSPrefLicense(bpy.types.PropertyGroup):
    """Gumroad license key + cached activation token (per-machine, in user.json).

    ``activation_token`` is an HMAC signature computed by the Rust core
    (``rust_verify_gumroad_license``) — it is NOT a trusted boolean flag.
    ``LicenseService.is_pro()`` always re-derives and compares it via Rust
    rather than reading a stored True/False, so hand-editing this value (or
    user_prefs.json) can't unlock Pro features.

    Unlike every other preference field in this file, ``license_key``
    deliberately has NO write-through ``update=`` callback. Every other
    field auto-saves on change because there's no reliable popup-close event
    to defer to (see ``_on_visual_pref_changed``'s docstring), but doing that
    here would persist whatever the user is mid-typing — including a stale
    or rejected key — straight to ``user.json``, which then comes back on
    the very next F3 Reload Scripts. Persisting this field is instead the
    explicit responsibility of ``LicenseGateway.activate()``, and only on a
    *successful* verification — see that method's docstring.
    """
    license_key: bpy.props.StringProperty(
        name="License Key",
        default="",
    )
    activation_token: bpy.props.StringProperty(
        name="Activation Token",
        default="",
        options={'HIDDEN'},
    )
    status_message: bpy.props.StringProperty(
        name="Status Message",
        default="",
    )


class SSPrefCustomizeUIState(bpy.types.PropertyGroup):
    """Ephemeral UI-only state — section collapse/expand. Never persisted to JSON."""
    single_ramp_expanded:   bpy.props.BoolProperty(default=True)
    multi_palette_expanded: bpy.props.BoolProperty(default=True)
    mask_ramp_expanded:     bpy.props.BoolProperty(default=True)
    apply_toolkit_expanded: bpy.props.BoolProperty(default=False)


class SSPrefRoot(bpy.types.PropertyGroup):
    """Root PropertyGroup bound to WindowManager.superskin_prefs."""
    ui_state:  bpy.props.PointerProperty(type=SSPrefCustomizeUIState)
    license:   bpy.props.PointerProperty(type=SSPrefLicense)
    debug:     bpy.props.PointerProperty(type=SSPrefDebug)


# ── Registration helpers ──

_classes = [
    SSPrefCustomizeUIState,
    SSPrefLicense,
    SSPrefDebug,
    SSPrefRoot,
]


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    # SKIP_SAVE: WindowManager is itself saved inside every .blend file, so
    # without this flag Blender would serialize whatever this PointerProperty
    # held at save time into the file, and restore that (possibly blank, if
    # the file predates this property, or stale) state on every later load —
    # turning "per-machine" preferences into accidental per-file ones. The
    # live values are repopulated from user.json by a load_post handler
    # instead (see core/preferences/__init__.py) — never from the .blend file.
    bpy.types.WindowManager.superskin_prefs = bpy.props.PointerProperty(
        type=SSPrefRoot, options={'SKIP_SAVE'},
    )


def unregister():
    del bpy.types.WindowManager.superskin_prefs
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
