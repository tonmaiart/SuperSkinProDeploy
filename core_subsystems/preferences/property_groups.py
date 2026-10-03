
import bpy

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



_classes = [
    SSPrefCustomizeUIState,
    SSPrefLicense,
    SSPrefDebug,
    SSPrefRoot,
]


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.superskin_prefs = bpy.props.PointerProperty(
        type=SSPrefRoot, options={'SKIP_SAVE'},
    )


def unregister():
    del bpy.types.WindowManager.superskin_prefs
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
