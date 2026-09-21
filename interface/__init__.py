"""Interface subsystem — closed package for SuperSkinPro."""

from importlib import reload

# ── Safe foundation layers (no core/ or core_subsystems/ module-level deps)

from . import registry
from . import template_ui

for _pkg in (registry, template_ui):
    try:
        reload(_pkg)
    except Exception:
        pass



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
    "panel_dev_debugger",
)

_deferred_loaded = False


def _ensure_deferred():
    """Import and reload deferred modules once. Idempotent."""
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

    # 2. Operators
    ops_preferences_lists.register()
    ops_preferences.register()

    # 3. Layout frames
    addon_preferences.register()
    panel_main.register()
    panel_dev_debugger.register()
    # widget_preferences has no bpy.types classes; nothing to register


def unregister():
    """Reverse-order unregistration."""
    _ensure_deferred()

    panel_dev_debugger.unregister()
    panel_main.unregister()
    addon_preferences.unregister()
    ops_preferences.unregister()
    ops_preferences_lists.unregister()
    utils.shortcut_overlay.unregister_overlay()
    utils.unregister()
