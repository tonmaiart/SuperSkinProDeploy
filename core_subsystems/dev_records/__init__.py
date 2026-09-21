"""dev_records -- encapsulated dev-output-folder-path subsystem.

Public surface: only ``DevRecordsService`` is exported from this package.
Stateless (see dev_records_service.py's module docstring), so unlike
profiler/'s two-class package, ordinary reload-then-bind is safe here.
"""

from importlib import reload

from . import dev_records_service as _drs

for _mod in (_drs,):
    try:
        reload(_mod)
    except Exception:
        pass

__all__ = ["DevRecordsService"]

from .dev_records_service import DevRecordsService
