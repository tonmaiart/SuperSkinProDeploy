"""PreferencesService — sole authority bridging JSON files and the live PropertyGroup.

External code accesses core preferences exclusively through the resolved accessor
methods on this class. Feature-domain preferences are accessed via their own
WindowManager properties, but they register with UnifiedRegistry so
load/save/reset still routes through this service.
"""

import bpy

from . import io

# UnifiedRegistry is deliberately function-scoped at every call site below,
# not hoisted to module scope. core_subsystems is imported (and its own
# reload cascade runs) BEFORE interface/__init__.py's cascade reloads
# interface.registry.register_api -- a module-level `from
# ...interface.registry.register_api import UnifiedRegistry` here would bind
# to that pre-reload UnifiedRegistry class, permanently disconnected from
# the one every features/*/*_feature.py module actually registers into
# (which import register_api only after features.register() runs, once
# interface's reload has already settled). The symptom: UnifiedRegistry
# .get_all() always returns [] from inside this file, so every feature
# extension's populate()/serialize_into() silently never runs -- see
# docs/bug-history for the mirror-keywords-never-persist report this was
# diagnosed from. Importing inside each method instead resolves the name
# at call time, after all reload cascades have finished.
# MirrorPreferencesService kept function-scoped in mirror accessor methods —
# hoisting it triggers loading the entire features package during core_subsystems
# initialisation, creating a circular import back through ui.utils.


# Cache the default dict (loaded once, never changes at runtime).
_default_dict = None


def _get_default_dict() -> dict:
    global _default_dict
    if _default_dict is None:
        path = io.default_json_path()
        _default_dict = io.load_json_safe(path)
        if not _default_dict:
            _safe_print(f"⚠️ [SuperSkinPro] default_prefs.json not found or empty at {path} "
                        f"— preferences will start blank.")
    return _default_dict


def _get_nested(d: dict, path: tuple) -> dict:
    """Navigate a nested dict by a tuple of keys, returning {} on any miss."""
    node = d
    for key in path:
        if not isinstance(node, dict):
            return {}
        node = node.get(key, {})
    return node if isinstance(node, dict) else {}


def _safe_print(message: str) -> None:
    """print() that never raises.

    stdout can go away out from under a running Blender process (e.g.
    WinError 233 "No process is on the other end of the pipe" if the
    console window Blender was launched from gets closed) -- these are
    load/save/reset error-reporting prints, already inside an
    ``except Exception`` block reacting to a *different* per-extension
    failure; a failed console mirror of that failure must not itself
    escape and crash whatever operator triggered the load/save/reset.
    """
    try:
        print(message)
    except OSError:
        pass


class PreferencesService:
    """Stateless service for reading/writing preferences via JSON files."""

    # Reentrancy guard: True while load()/reset_to_default() is populating
    # PropertyGroups. Many fields (e.g. SSPrefMirrorSRItem.search_text) carry
    # a write-through `update=` callback that calls save_to_user_file()
    # immediately on assignment. Populating a multi-field group (or a
    # CollectionProperty rebuilt item-by-item) therefore fires that save
    # repeatedly mid-populate, each time serializing whatever is *currently*
    # sitting in every OTHER extension's PropertyGroup -- including
    # extensions the outer loop hasn't reached yet, which still hold the
    # blank type-defaults a fresh WindowManager starts with. That mid-flight
    # snapshot gets written to user.json, permanently dropping sections for
    # extensions processed later in the same load() call (see
    # docs/bug-history/0014 for the single-field-order version of this same
    # write-through hazard; this is the general form). Suppressing saves for
    # the duration of load()/reset_to_default() removes the reentrancy.
    _loading = False

    @classmethod
    def load(cls) -> None:
        """Read default.json + user.json, merge, populate core + extension PropertyGroups.

        Function-scoped import: UnifiedRegistry (see module docstring above).
        """
        from ...interface.registry.register_api import UnifiedRegistry

        cls._loading = True
        try:
            default = _get_default_dict()
            user    = io.load_json_safe(io.user_json_path())
            merged  = io.deep_merge(default, user)

            prefs = bpy.context.window_manager.superskin_prefs
            cls._populate_from_dict(prefs, merged)

            # Load each feature extension using its own default_config.json + user section.
            # UnifiedRegistry path
            for ext in UnifiedRegistry.get_all():
                defaults_path = ext.get_defaults_path()
                if defaults_path is None:
                    continue
                ext_defaults = io.load_json_safe(defaults_path)
                user_section = _get_nested(user, ext.get_json_path())
                ext_data     = io.deep_merge(ext_defaults, user_section)
                try:
                    ext.populate(ext_data)
                except Exception as e:
                    _safe_print(f"⚠️ [SuperSkinPro] Failed to load unified prefs for '{ext.get_id()}': {e}")
        finally:
            cls._loading = False

    @classmethod
    def save_to_user_file(cls) -> None:
        """Serialize core + all extension prefs and write to user.json.

        No-op while load()/reset_to_default() is populating PropertyGroups
        (see the `_loading` docstring above) -- those callers already end
        with every PropertyGroup in its final, fully-consistent state, so
        nothing needs saving mid-populate, and doing so would only persist a
        torn snapshot.

        Function-scoped import: UnifiedRegistry (see module docstring above).
        """
        if cls._loading:
            return

        from ...interface.registry.register_api import UnifiedRegistry

        prefs = bpy.context.window_manager.superskin_prefs
        data  = cls._dict_from_property_group(prefs)

        # UnifiedRegistry path
        for ext in UnifiedRegistry.get_all():
            try:
                ext.serialize_into(data)
            except Exception as e:
                _safe_print(f"⚠️ [SuperSkinPro] Failed to serialize unified prefs for '{ext.get_id()}': {e}")

        io.save_json(io.user_json_path(), data)

    @classmethod
    def reset_to_default(cls) -> None:
        """Load default values directly (no merge) into all PropertyGroups.

        Does not write to disk -- see the `_loading` guard docstring above;
        this suppresses the same write-through reentrancy that load() does.

        Function-scoped import: UnifiedRegistry (see module docstring above).
        """
        from ...interface.registry.register_api import UnifiedRegistry

        cls._loading = True
        try:
            default = _get_default_dict()
            prefs   = bpy.context.window_manager.superskin_prefs
            cls._populate_from_dict(prefs, default)

            # UnifiedRegistry path
            for ext in UnifiedRegistry.get_all():
                if not ext.supports_reset_to_default_action():
                    continue
                defaults_path = ext.get_defaults_path()
                if defaults_path is None:
                    continue
                ext_defaults = io.load_json_safe(defaults_path)
                try:
                    ext.populate(ext_defaults)
                except Exception as e:
                    _safe_print(f"⚠️ [SuperSkinPro] Failed to reset unified prefs for '{ext.get_id()}': {e}")
        finally:
            cls._loading = False

    # ── Mirror accessors — delegate to MirrorPreferencesService ──

    @classmethod
    def get_mirror_axis(cls) -> str:
        # Kept function-scoped — hoisting triggers circular import through features → ui.utils.
        from ...features.mirror.mirror_feature import MirrorPreferencesService
        return MirrorPreferencesService.get_mirror_axis()

    @classmethod
    def get_mirror_direction(cls) -> str:
        from ...features.mirror.mirror_feature import MirrorPreferencesService
        return MirrorPreferencesService.get_mirror_direction()

    @classmethod
    def get_mirror_data(cls) -> str:
        from ...features.mirror.mirror_feature import MirrorPreferencesService
        return MirrorPreferencesService.get_mirror_data()

    @classmethod
    def get_mirror_search_replace_pairs(cls) -> list:
        from ...features.mirror.mirror_feature import MirrorPreferencesService
        return MirrorPreferencesService.get_mirror_search_replace_pairs()

    # ── License accessors ──

    @classmethod
    def get_license_key(cls) -> str:
        return bpy.context.window_manager.superskin_prefs.license.license_key

    @classmethod
    def get_activation_token(cls) -> str:
        return bpy.context.window_manager.superskin_prefs.license.activation_token

    @classmethod
    def get_license_status_message(cls) -> str:
        return bpy.context.window_manager.superskin_prefs.license.status_message

    @classmethod
    def set_license_activation(cls, license_key: str, activation_token: str, status_message: str) -> None:
        prefs = bpy.context.window_manager.superskin_prefs
        prefs.license.activation_token = activation_token
        prefs.license.status_message   = status_message
        prefs.license.license_key      = license_key
        cls.save_to_user_file()

    @classmethod
    def set_license_status_message(cls, status_message: str) -> None:
        """Report a failed activation attempt for this session only.

        Deliberately does not touch ``license_key``/``activation_token`` and
        never calls ``save_to_user_file()`` -- a rejected key must not be
        written to ``user.json``, or it would come back pre-filled on the
        next F3 Reload Scripts. See ``LicenseGateway.activate()``.
        """
        bpy.context.window_manager.superskin_prefs.license.status_message = status_message

    # ═══════════════════════════════════════════════════════════════════════
    #  Internal helpers — core PropertyGroup ↔ dict conversion
    # ═══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _populate_from_dict(prefs, data: dict) -> None:
        """Write merged dict values into the core PropertyGroup."""
        # ── License ──
        lic = data.get("license", {})
        prefs.license.activation_token = lic.get("activation_token", "")
        prefs.license.status_message   = lic.get("status_message",   "")
        prefs.license.license_key      = lic.get("license_key",      "")

        # ── Debug Tools ──
        from ..debug_logging.debug_log_service import CATEGORIES
        debug_data = data.get("debug", {})
        for cat in CATEGORIES:
            setattr(prefs.debug, cat, bool(debug_data.get(cat, False)))

    @staticmethod
    def _dict_from_property_group(prefs) -> dict:
        """Serialize the core PropertyGroup back into a dict for user.json.

        Extensions add their own sections afterward via serialize_into_fn.
        """
        lic = prefs.license

        from ..debug_logging.debug_log_service import CATEGORIES
        debug = {cat: getattr(prefs.debug, cat) for cat in CATEGORIES}

        return {
            "_schema_version": 1,
            "license": {
                "license_key":      lic.license_key,
                "activation_token": lic.activation_token,
                "status_message":   lic.status_message,
            },
            "debug": debug,
        }
