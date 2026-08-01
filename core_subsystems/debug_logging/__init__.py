"""debug_logging -- toggleable, category-gated debug console output subsystem.

Public surface: only ``DebugLogService`` is exported from this package.

``SSPrefDebug`` (property_groups.py) is intentionally NOT exported via
``__all__``. It is consumed directly by
``core_subsystems/preferences/property_groups.py``, which nests it into
``SSPrefRoot.debug`` and owns its ``bpy.utils.register_class()`` lifecycle --
this package defines no ``register()``/``unregister()`` of its own.
"""

from importlib import reload

from . import debug_log_service as _dls
from . import property_groups as _pg

__all__ = ["DebugLogService"]

# Cascading reload: debug_log_service first (property_groups depends on its
# CATEGORIES constant), then property_groups.
#
# The export binding below must come AFTER this loop -- see
# core_subsystems/layer_compositor/__init__.py's comment for why binding it
# first permanently captures a pre-reload class object (this is the exact
# "Reload-Ordering Footgun" core_subsystems/profiler/__init__.py's own README
# section documents and fixes for itself; every other single-class subsystem
# in this codebase had the same un-fixed anti-pattern).
for _mod in (_dls, _pg):
    try:
        reload(_mod)
    except Exception:
        pass

from .debug_log_service import DebugLogService
