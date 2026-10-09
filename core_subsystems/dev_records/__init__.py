
from importlib import reload

from . import dev_records_service as _drs

for _mod in (_drs,):
    try:
        reload(_mod)
    except Exception:
        pass

__all__ = ["DevRecordsService"]

from .dev_records_service import DevRecordsService
