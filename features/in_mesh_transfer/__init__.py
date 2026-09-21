"""In-Mesh Transfer domain package — closest-surface-point weight/mask blend
within a single mesh's active Layer."""

from importlib import reload

from . import logic
from . import public_api
from . import ops
from .hammer import logic as hammer_logic
from .hammer import ops as hammer_ops
from . import in_mesh_transfer_feature

for mod in (logic, public_api, ops, hammer_logic, hammer_ops, in_mesh_transfer_feature):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    ops.register()
    hammer_ops.register()
    in_mesh_transfer_feature.register()


def unregister():
    in_mesh_transfer_feature.unregister()
    hammer_ops.unregister()
    ops.unregister()
