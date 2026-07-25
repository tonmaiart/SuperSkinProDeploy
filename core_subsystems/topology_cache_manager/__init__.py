"""topology_cache_manager -- encapsulated mesh topology caching and bone proximity subsystem.

Public surface: only ``TopologyCacheManager`` is exported from this package.
"""

from importlib import reload

from . import proximity_analyzer as _pa
from . import topology_cache_manager as _tcm

__all__ = ["TopologyCacheManager"]

from .topology_cache_manager import TopologyCacheManager

for _mod in (_pa, _tcm):
    try:
        reload(_mod)
    except Exception:
        pass
