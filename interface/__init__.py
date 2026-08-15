"""Interface subsystem — closed package for SuperSkinPro.

All front-end assets, registration pools, and interface components live here.
This is a strictly closed subsystem; external feature packages under
``features/*`` are prohibited from importing internal layout widgets, panels,
or operators directly. External features communicate exclusively via the
public Registry API (``interface.registry.register_api``) and inherit from
template components (``interface.template_ui``).

Bottom-up micro-reload order (F3 > Reload Scripts safe):
  1. Foundations: registry, template_ui (no core/core_subsystems deps)
  2. Utilities: utils (deferred — depends on core_subsystems)
  3. Operators: ops_preferences_lists, ops_preferences
  4. Layout Frames: widget_preferences, addon_preferences, panel_main

Module-level imports are restricted to packages that do NOT import from
core/ or core_subsystems/ at module scope, because core/shaders/shader_utils
imports interface.utils.gpu_utils during core/ initialisation and any
transitive core_subsystems import at that point would fail.
"""

from importlib import reload

# ── Safe foundation layers (no core/ or core_subsystems/ module-level deps)

from . import registry
from . import template_ui

for _pkg in (registry, template_ui):
    try:
        reload(_pkg)
    except Exception:
        pass


# ── Deferred modules — imported during _ensure_deferred() only after
#    core/ and core_subsystems/ are fully loaded ────────────────────────────

_deferred_module_names = (
    "utils",                   # the sub-package (gpu_utils is already loaded)
    "utils.utils",             # imports core.facade, core_subsystems.* at module level
    "utils.op_exec",           # function-scoped core.facade import; needs registry
    "utils.shortcut_overlay",  # function-scoped core.facade import; needs registry
    "ops_preferences_lists",
    "ops_preferences",
    "widget_preferences",
    "addon_preferences",
    "panel_main",
)

_deferred_loaded = False


def _ensure_deferred():
    """Import and reload deferred modules once. Idempotent.

    Called by register() / unregister() and by the top-level __init__.py
    reload loop. Must NOT be called during core/ initialisation.
    """
    global _deferred_loaded
    if _deferred_loaded:
        return
    import importlib
    import sys

    for name in _deferred_module_names:
        full = f"{__package__}.{name}" if __package__ else f"interface.{name}"
        if full in sys.modules:
            mod = sys.modules[full]
            try:
                reload(mod)
            except Exception:
                pass
        else:
            try:
                mod = importlib.import_module(f".{name}", __package__ or "interface")
            except Exception:
                continue
        globals()[name] = mod

    _deferred_loaded = True


# ── Registration / Unregistration ─────────────────────────────────────────

def register():
    """Bottom-up registration: foundations → utils → operators → layout frames."""
    _ensure_deferred()

    # 1. Foundations
    utils.register()
    utils.shortcut_overlay.register_overlay()
    # registry.register_operator() is called by the top-level __init__.py
    # after features.register(), but we export it for backward compatibility.

    # 2. Operators
    ops_preferences_lists.register()
    ops_preferences.register()

    # 3. Layout frames
    addon_preferences.register()
    panel_main.register()
    # widget_preferences has no bpy.types classes; nothing to register


def unregister():
    """Reverse-order unregistration."""
    _ensure_deferred()

    panel_main.unregister()
    addon_preferences.unregister()
    ops_preferences.unregister()
    ops_preferences_lists.unregister()
    utils.shortcut_overlay.unregister_overlay()
    utils.unregister()
