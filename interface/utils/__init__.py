"""Interface utilities sub-package."""

from importlib import reload

# gpu_utils is safe — only imports ctypes and gpu (Blender built-ins)
from . import gpu_utils

from . import icons

for _pkg in (gpu_utils, icons):
    try:
        reload(_pkg)
    except Exception:
        pass



def register():
    """Called by interface.register(). Leaf modules are already loaded."""
    icons.register()
    from . import utils as _leaf
    _leaf.register()


def unregister():
    """Called by interface.unregister(). Leaf modules are already loaded."""
    from . import utils as _leaf
    _leaf.unregister()
    icons.unregister()
