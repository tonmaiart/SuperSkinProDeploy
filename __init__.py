"""Super Skin Pro — Professional weight painting layers system.

Unified Component Architecture: feature domains extend UnifiedFeatureExtension
and register with UnifiedRegistry (action dispatch + UI layout + persistence).

ST_STRICT: core/ is never touched except core/facade.py (public API surface).

All Extra Domain packages live under features/ and are registered through
features/__init__.py.

The interface/ package consolidates all front-end assets (panel, widgets,
operators, registry, template components, and utilities) into a single
closed subsystem.
"""

import os
from importlib import reload

# Blender Extensions assign a repository-namespaced runtime package name
# (e.g. "bl_ext.user_default.superskinpro"), not the plain folder/manifest id.
# AddonPreferences.bl_idname must match this exactly or Blender silently shows
# no preferences panel for the addon (no error) -- see interface/addon_preferences.py.
ADDON_PACKAGE = __package__


def _read_manifest():
    """Parse blender_manifest.toml once at addon load time.

    Single source of truth for ADDON_NAME/ADDON_VERSION below -- several
    downstream modules (interface/panel_main.py, features/addon_updater/
    engine.py, core_subsystems/support_report/environment_collector.py)
    used to each open and parse this same file independently. They now
    import the values computed here instead.
    """
    manifest_path = os.path.join(os.path.dirname(__file__), "blender_manifest.toml")
    try:
        import tomllib
        with open(manifest_path, "rb") as fh:
            return tomllib.load(fh)
    except Exception:
        return {}


_manifest = _read_manifest()

# Single source of truth for the "Super Skin Pro" / "Super Skin Pro Dev"
# label surfaced in the N-panel tab and preferences text -- see
# interface/panel_main.py and interface/utils/utils.py. Keeps the dev repo
# (manifest name "Super Skin Pro Dev") and the release repo (renamed back
# to "Super Skin Pro" by the release workflow) visually distinguishable
# without any hardcoded literal drifting out of sync with the manifest.
ADDON_NAME = str(_manifest.get("name", "Super Skin Pro"))

# Single source of truth for the addon's version string, e.g. "1.4.2" --
# see interface/panel_main.py (N-panel label), features/addon_updater/
# engine.py (update-check comparisons), and core_subsystems/support_report/
# environment_collector.py (diagnostic bundle).
ADDON_VERSION = str(_manifest.get("version", "0.0.0"))

# ==============================================================================
# FORCE RELOAD — bottom-up order: foundations → features → interface
# ==============================================================================

from . import core
from . import core_subsystems   # backend pillars — no bpy classes, no register()
from . import interface         # closed front-end subsystem (registry + UI + ops)
from . import features          # all Extra Domain packages

for mod in (core_subsystems, core, interface, features):
    try:
        reload(mod)
    except Exception:
        pass

# After interface is reloaded, pull in its deferred leaf modules
# (utils, ops_preferences, widget_preferences, addon_preferences, panel_main)
# now that core_subsystems is available.
try:
    interface._ensure_deferred()
except Exception:
    pass

# ==============================================================================
# REGISTRATION
# ==============================================================================

def register():
    try:
        unregister()
    except Exception:
        pass

    core.register()
    features.register()
    # Register the universal action proxy operator (SUPERSKIN_OT_execute_action)
    interface.registry.register_operator()
    # Load preferences after both core PropertyGroups and all feature
    # domains are registered with UnifiedRegistry, so every extension gets populated.
    from .core_subsystems.preferences.preferences_service import PreferencesService
    PreferencesService.load()
    # Interface operators and panels (ops_preferences, addon_preferences, panel_main)
    interface.register()


def unregister():
    for component in (interface, features, core):
        try:
            component.unregister()
        except Exception:
            pass
    try:
        interface.registry.unregister_operator()
    except Exception:
        pass
