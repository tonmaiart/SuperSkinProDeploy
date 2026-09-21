"""SuperSkinPro — Extra Domains Package Initializer."""

import importlib
import os
from importlib import reload

_PRELOAD = (
    "addon_updater",  # see module docstring
)

_DISABLED = ()

_PKG_DIR = os.path.dirname(__file__)


def _discover_domain_names():
    """Every features/ subfolder with an __init__.py, alphabetically sorted."""
    names = []
    for entry in os.listdir(_PKG_DIR):
        if entry.startswith(("_", ".")):
            continue
        if not os.path.isfile(os.path.join(_PKG_DIR, entry, "__init__.py")):
            continue
        names.append(entry)
    return sorted(names)


def _import_domain(name):
    try:
        return importlib.import_module(f".{name}", __name__)
    except Exception as exc:
        print(f"[SuperSkinPro] features: failed to import domain {name!r}: {exc!r}")
        return None


_load_order = list(_PRELOAD) + [
    name for name in _discover_domain_names()
    if name not in _PRELOAD and name not in _DISABLED
]

_modules = tuple(
    mod for mod in (_import_domain(name) for name in _load_order)
    if mod is not None
)

for mod in _modules:
    try:
        reload(mod)
    except Exception:
        pass


def register():
    """Register every discovered domain's lifecycle."""
    for mod in _modules:
        if not hasattr(mod, "register"):
            continue
        try:
            mod.register()
        except Exception as exc:
            print(f"[SuperSkinPro] features: failed to register domain {mod.__name__!r}: {exc!r}")


def unregister():
    """Unregister every discovered domain's lifecycle in reverse order."""
    for mod in reversed(_modules):
        if not hasattr(mod, "unregister"):
            continue
        try:
            mod.unregister()
        except Exception as exc:
            print(f"[SuperSkinPro] features: failed to unregister domain {mod.__name__!r}: {exc!r}")
