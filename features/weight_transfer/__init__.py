from importlib import reload

from . import transfer_core
from . import ops
from . import io_ops
from . import weight_transfer_feature
from . import ui

for mod in (transfer_core, ops, io_ops, weight_transfer_feature, ui):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    ops.register()
    io_ops.register()
    weight_transfer_feature.register()


def unregister():
    weight_transfer_feature.unregister()
    io_ops.unregister()
    ops.unregister()
