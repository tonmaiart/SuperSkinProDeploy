"""Activate feature package — lifecycle and hot-reload bootstrap.

No ops.py/keymap.py -- this domain draws existing operators
(`superskin.activate_license`, `superskin.reset_license_activation`, both
still defined in `interface/ops_preferences.py`) by bl_idname string only;
it defines no operators or keymaps of its own.
"""

from importlib import reload

from . import activate_feature

for mod in (activate_feature,):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    activate_feature.register()


def unregister():
    activate_feature.unregister()
