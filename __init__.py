"""Super Skin Pro — Professional weight painting layers system."""

import os
from importlib import reload

ADDON_PACKAGE = __package__


def _read_manifest():
    """Parse blender_manifest.toml once at addon load time."""
    manifest_path = os.path.join(os.path.dirname(__file__), "blender_manifest.toml")
    try:
        import tomllib
        with open(manifest_path, "rb") as fh:
            return tomllib.load(fh)
    except Exception:
        return {}


_manifest = _read_manifest()

ADDON_NAME = str(_manifest.get("name", "Super Skin Pro"))

ADDON_VERSION = str(_manifest.get("version", "0.0.0"))

# ==============================================================================
# FORCE RELOAD — bottom-up order: foundations → features → interface
# ==============================================================================

from . import core
from . import core_subsystems
from . import interface         # closed front-end subsystem (registry + UI + ops)
from . import features          # all Extra Domain packages

for mod in (core_subsystems, core, interface, features):
    try:
        reload(mod)
    except Exception:
        pass

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
