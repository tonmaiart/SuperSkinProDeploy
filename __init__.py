
import os
from importlib import reload

ADDON_PACKAGE = __package__


def _read_manifest():
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


from . import core
from . import core_subsystems
from . import interface
from . import features

for mod in (core_subsystems, core, interface, features):
    try:
        reload(mod)
    except Exception:
        pass

try:
    interface._ensure_deferred()
except Exception:
    pass


def _native_cache_dir():
    try:
        import bpy
        return bpy.utils.extension_path_user(ADDON_PACKAGE, path="native_cache", create=True)
    except Exception:
        import tempfile
        return os.path.join(tempfile.gettempdir(), "superskinpro_native_cache")


def register():
    try:
        unregister()
    except Exception:
        pass

    core_subsystems.rust_weight_engine.set_native_cache_dir(_native_cache_dir())
    core.register()
    features.register()
    interface.registry.register_operator()
    from .core_subsystems.preferences.preferences_service import PreferencesService
    PreferencesService.load()
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
