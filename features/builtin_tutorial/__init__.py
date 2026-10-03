
from importlib import reload

from . import logic
from . import ops
from . import builtin_tutorial_feature

for mod in (logic, ops, builtin_tutorial_feature):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    logic.register()
    ops.register()
    builtin_tutorial_feature.register()


def unregister():
    builtin_tutorial_feature.unregister()
    ops.unregister()
    logic.unregister()
