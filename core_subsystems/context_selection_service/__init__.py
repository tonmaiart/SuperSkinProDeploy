"""context_selection_service -- encapsulated viewport context and weight normalisation subsystem.

Public surface: only ``ContextSelectionService`` is exported from this package.
"""

from importlib import reload

from . import context_selection_service as _css

__all__ = ["ContextSelectionService"]

# Export binding must come AFTER reload -- see core_subsystems/
# layer_compositor/__init__.py's comment for the "Reload-Ordering Footgun"
# this avoids (binding before reload permanently captures a stale,
# pre-reload class object on every F3 Reload Scripts after the first).
for _mod in (_css,):
    try:
        reload(_mod)
    except Exception:
        pass

from .context_selection_service import ContextSelectionService
