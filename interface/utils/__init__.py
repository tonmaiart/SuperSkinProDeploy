
from importlib import reload

from . import gpu_utils

from . import icons

for _pkg in (gpu_utils, icons):
    try:
        reload(_pkg)
    except Exception:
        pass



def register():
    icons.register()
    from . import utils as _leaf
    _leaf.register()


def unregister():
    from . import utils as _leaf
    _leaf.unregister()
    icons.unregister()
