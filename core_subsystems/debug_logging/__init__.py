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

from .debug_log_service import DebugLogService

# Cascading reload: debug_log_service first (property_groups depends on its
# CATEGORIES constant), then property_groups.
for _mod in (_dls, _pg):
    try:
        reload(_mod)
    except Exception:
        pass
