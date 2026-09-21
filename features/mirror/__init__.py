"""Mirror domain package."""

from importlib import reload

from . import logic
from . import ops
from . import mirror_feature

for mod in (logic, mirror_feature, ops):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    mirror_feature.register()
    ops.register()


def unregister():
    ops.unregister()
    mirror_feature.unregister()
