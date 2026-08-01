"""SuperSkinPro — Extra Domains Package Initializer.

This module is the central registry gateway for all feature domains. Every
domain folder under features/ is discovered automatically from disk —
adding, removing, or deleting a domain folder needs no edit to this file.

As of the Unified Component Architecture refactor, each feature domain owns
a ``*_feature.py`` file containing a ``UnifiedFeatureExtension`` subclass.
The domain's ``__init__.py`` calls ``<name>_feature.register()`` which
registers with ``UnifiedRegistry``.

UI insertion order within a tab is controlled entirely by
``UnifiedFeatureExtension.priority`` / ``is_collapsible()`` / ``get_id()``
(see ``UnifiedRegistry.get_by_tab()`` in
``interface/registry/register_api.py``), never by import or ``register()``
call order — so the order domains load in below has no effect on the
N-panel layout.

Two explicit buckets on top of autoload:

  _PRELOAD  — ordered tuple of domain folder names that must import and
              register before every autoloaded domain. Currently only
              ``addon_updater``: it is looked up directly by
              ``interface/panel_main.py``'s top row (bypassing the normal
              tab loop), and this project prefers "fail early, fail
              visibly" — keeping it first means its own operators register
              even if a later autoloaded domain's register() call fails.
              Add a name here only for a domain with a genuine, documented
              ordering dependency; everything else belongs in autoload.

  _DISABLED — folder names present on disk that must NOT be auto-registered.
              Currently only ``weight_info`` (temporarily disabled).

Every remaining subfolder with an ``__init__.py`` (i.e. anything not in
_PRELOAD or _DISABLED) is autoloaded, sorted alphabetically for a
deterministic (if arbitrary) order — arbitrary is safe here per the UI
ordering note above. A domain that fails to import, or whose
register()/unregister() raises, is skipped with a console warning instead
of blocking the rest of the addon — this is what makes deleting a dev-only
domain folder (profiler, debug_console) or hitting a bug in one domain safe
for every other domain.
"""

import importlib
import os
from importlib import reload

_PRELOAD = (
    "addon_updater",  # see module docstring
)

_DISABLED = (
    "weight_info",  # TEMPORARILY DISABLED -- SKINNING tab -- read-only selected-vertex active-layer weight viewer
)

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
    """Register every discovered domain's lifecycle.

    A domain whose register() raises is skipped rather than aborting the
    loop — one broken or missing domain must never take the rest of the
    addon down with it.
    """
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
