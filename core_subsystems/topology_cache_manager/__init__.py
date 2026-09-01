"""topology_cache_manager -- encapsulated mesh topology caching and bone proximity subsystem.

Public surface: only ``TopologyCacheManager`` is exported from this package.
"""

from importlib import reload

from . import proximity_analyzer as _pa
from . import topology_cache_manager as _tcm

__all__ = ["TopologyCacheManager"]

# Export binding must come AFTER reload -- see core_subsystems/
# layer_compositor/__init__.py's comment for the "Reload-Ordering Footgun"
# this avoids (binding before reload permanently captures a stale,
# pre-reload class object on every F3 Reload Scripts after the first).
for _mod in (_pa, _tcm):
    try:
        reload(_mod)
    except Exception:
        pass

from .topology_cache_manager import TopologyCacheManager
