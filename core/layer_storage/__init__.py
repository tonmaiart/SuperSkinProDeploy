"""SuperSkinPro Layer Storage — closed module.

External code must only import ``LayerStorageService`` from this package.
``storage_service``, ``geometry``, and ``live_snapshot`` are private
implementation details and must never be imported directly from outside
this folder.
"""

from importlib import reload

from . import storage_service
from . import flatten
from . import geometry
from . import topology_heal
from . import live_snapshot
from . import temp_vg_bridge

# Reload BEFORE binding LayerStorageService below -- same Reload-Ordering
# Footgun documented in core_subsystems/profiler/__init__.py and fixed this
# session in six core_subsystems packages plus core/facade/__init__.py.
# Binding the class before reload(storage_service) runs would permanently
# capture the pre-reload LayerStorageService, silently keeping every method
# on it one generation behind on every F3 Reload Scripts after the first --
# significant here because CoreFacade.__init__ constructs a fresh
# LayerStorageService on every single operator call.
for mod in (storage_service, flatten, geometry, topology_heal,
            live_snapshot, temp_vg_bridge):
    try:
        reload(mod)
    except Exception:
        pass

from .storage_service import LayerStorageService


def register():
    pass


def unregister():
    pass
