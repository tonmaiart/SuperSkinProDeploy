
from importlib import reload


from . import registry
from . import template_ui

for _pkg in (registry, template_ui):
    try:
        reload(_pkg)
    except Exception:
        pass



_deferred_module_names = (
    "utils",
    "utils.utils",
    "utils.viewport_overrides",
    "utils.op_exec",
    "utils.shortcut_overlay",
    "ops_preferences_lists",
    "ops_preferences",
    "widget_preferences",
    "addon_preferences",
    "panel_main",
    "panel_dev_debugger",
)

_deferred_loaded = False


def _ensure_deferred():
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



def register():
    _ensure_deferred()

    utils.register()
    utils.shortcut_overlay.register_overlay()

    ops_preferences_lists.register()
    ops_preferences.register()

    addon_preferences.register()
    panel_main.register()
    panel_dev_debugger.register()


def unregister():
    _ensure_deferred()

    panel_dev_debugger.unregister()
    panel_main.unregister()
    addon_preferences.unregister()
    ops_preferences.unregister()
    ops_preferences_lists.unregister()
    utils.shortcut_overlay.unregister_overlay()
    utils.unregister()
