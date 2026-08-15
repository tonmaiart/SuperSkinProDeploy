"""In-Mesh Transfer domain package — closest-surface-point weight/mask blend
within a single mesh's active Layer."""

from importlib import reload

from . import logic
from . import ui
from . import ops
from . import in_mesh_transfer_feature

for mod in (logic, ui, ops, in_mesh_transfer_feature):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    ops.register()
    in_mesh_transfer_feature.register()


def unregister():
    in_mesh_transfer_feature.unregister()
    ops.unregister()
